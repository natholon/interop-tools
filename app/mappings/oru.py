"""ORU (observation result) -> FHIR mapping.

R01, R30, R31, R32, and R40 all produce identical output here: the trigger
events differ in upstream *ordering* workflow semantics (whether a new order
should be placed for an unsolicited point-of-care result, or whether an
existing order should be searched for) which this stateless converter
doesn't model - it only converts the result data itself, which is shaped
identically across all five.
"""

import uuid

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.fhir_models.builders import build_codeable_concept_from_cwe, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError
from app.hl7.parser import field_repetitions, field_str, group_segments_by_leader, raw_field_str, require_segment
from app.mappings.base import MessageMapper
from app.mappings.common import (
    assemble_bundle,
    build_minimal_encounter,
    build_patient,
    build_practitioner_from_xcn,
)
from app.provenance.location import hl7_location

_INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0078"
# HL7 table 0203 - the v2-to-FHIR OBR[DiagnosticReport] map fixes PLAC/FILL
# as the identifier type for the placer and filler order numbers.
ORDER_IDENTIFIER_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0203"
_DEFAULT_STATUS = "unknown"

# HL7 table 0085 (OBX-11) / table 0123 (OBR-25) result status -> FHIR status.
# Both source tables share the same core codes; DiagnosticReport.status and
# Observation.status both accept every code this map produces. Codes with no
# FHIR equivalent (B, I, N, O, R, S, U, V, ...) fall back to "unknown" rather
# than guessing.
_RESULT_STATUS_MAP = {
    "A": "amended",
    "C": "corrected",
    "D": "entered-in-error",
    "F": "final",
    "P": "preliminary",
    "W": "entered-in-error",
    "X": "cancelled",
}


def _map_result_status(code: str) -> str:
    return _RESULT_STATUS_MAP.get(code.strip().upper(), _DEFAULT_STATUS) if code else _DEFAULT_STATUS


def _build_observation_value(obx, resource_id: str | None = None, recorder=None) -> dict:
    """Return the kwarg(s) for whichever Observation.value[x] choice fits
    OBX-2 (value type). An unrecognized/unsupported value type (SN, NA, ED,
    ...) leaves the value unset rather than guess - the NM/string/coded
    fallbacks below cover the overwhelming majority of real-world ORU
    traffic, and an Observation without a value is still valid FHIR.

    ST/FT/TX are unstructured free text, not HL7-composite - a literal '^'
    in the text is just a character, not a component separator, so this
    branch reads the field via raw_field_str (whole field) rather than
    field_str (component 1 only), which would otherwise silently truncate
    any free-text value containing a caret.

    `resource_id`/`recorder` are optional (see app/provenance/recorder.py) -
    when given, records against whichever value[x] key this call actually
    returns."""
    value_type = field_str(obx, 2).strip().upper()
    if value_type in ("ST", "FT", "TX"):
        text = raw_field_str(obx, 5)
        if not text:
            return {}
        if recorder and resource_id:
            recorder.record(resource_id, "valueString", hl7_location("OBX", 5), text)
        return {"valueString": text}
    raw_value = field_str(obx, 5)
    if not raw_value:
        return {}
    if value_type == "NM":
        try:
            quantity_value = float(raw_value)
        except ValueError:
            if recorder and resource_id:
                recorder.record(resource_id, "valueString", hl7_location("OBX", 5), raw_value)
            return {"valueString": raw_value}
        quantity = Quantity(value=quantity_value)
        if recorder and resource_id:
            recorder.record(resource_id, "valueQuantity.value", hl7_location("OBX", 5), quantity_value, source_value=raw_value)
        units = field_str(obx, 6)
        if units:
            quantity.unit = units
            if recorder and resource_id:
                recorder.record(resource_id, "valueQuantity.unit", hl7_location("OBX", 6), units)
        return {"valueQuantity": quantity}
    if value_type in ("CE", "CWE", "CNE", "IS"):
        concept = build_codeable_concept_from_cwe(
            obx, 5, resource_id=resource_id, relative_path="valueCodeableConcept", recorder=recorder
        )
        return {"valueCodeableConcept": concept} if concept else {}
    if value_type in ("DT", "DTM"):
        value = parse_hl7_datetime(raw_value)
        if not value:
            return {}
        if recorder and resource_id:
            recorder.record(resource_id, "valueDateTime", hl7_location("OBX", 5), value, source_value=raw_value)
        return {"valueDateTime": value}
    return {}


def _resolve_performers(
    obx, performer_cache: dict[str, Practitioner], recorder=None
) -> list[tuple[Practitioner, bool]]:
    """OBX-16 (Responsible Observer) -> one Practitioner per repetition
    (the field is 0..-1 in the v2-to-FHIR OBX[Observation] map), each
    reused across the
    whole message when the same XCN id (component 1) appears on more than
    one result - a single report panel commonly has every OBX-16 populated
    with the same verifying physician, and without this a 10-result panel
    would otherwise produce 10 near-identical Practitioner resources for one
    real person. Returns (practitioner, is_newly_built) so the caller only
    adds it to the Bundle once. `recorder` is only threaded into a newly
    built Practitioner - a cache hit's own fields were already recorded the
    first time it was built."""
    resolved: list[tuple[Practitioner, bool]] = []
    for index in range(len(field_repetitions(obx, 16)) or 1):
        performer_key = field_str(obx, 16, repetition=index, component=1)
        if performer_key and performer_key in performer_cache:
            resolved.append((performer_cache[performer_key], False))
            continue
        performer = build_practitioner_from_xcn(obx, 16, recorder=recorder, repetition=index)
        if performer is None:
            continue
        if performer_key:
            performer_cache[performer_key] = performer
        resolved.append((performer, True))
    return resolved

def build_observation(
    obx, patient_id: str, encounter_id: str | None, performer_cache: dict[str, Practitioner], recorder=None
) -> tuple[Observation, list[Resource]]:
    """OBX -> Observation. Returns the Observation plus any extra resources
    materialized for it (currently: a Practitioner for OBX-16, reusing the
    same build_practitioner_from_xcn already used for SIU's AIP - deduped
    across the message via performer_cache, see _resolve_performer)."""
    observation_id = str(uuid.uuid4())
    status = _map_result_status(field_str(obx, 11))
    code = build_codeable_concept_from_cwe(obx, 3, resource_id=observation_id, relative_path="code", recorder=recorder)
    observation = Observation(
        id=observation_id,
        status=status,
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )
    if recorder:
        recorder.record(observation_id, "status", hl7_location("OBX", 11), status, source_value=field_str(obx, 11))
    if encounter_id:
        observation.encounter = Reference(reference=f"urn:uuid:{encounter_id}")

    for key, value in _build_observation_value(obx, resource_id=observation_id, recorder=recorder).items():
        setattr(observation, key, value)

    reference_range_text = field_str(obx, 7)
    if reference_range_text:
        observation.referenceRange = [ObservationReferenceRange(text=reference_range_text)]
        if recorder:
            recorder.record(observation_id, "referenceRange[0].text", hl7_location("OBX", 7), reference_range_text)

    interpretation_code = field_str(obx, 8)
    if interpretation_code:
        observation.interpretation = [
            CodeableConcept(coding=[Coding(system=_INTERPRETATION_SYSTEM, code=interpretation_code)])
        ]
        if recorder:
            recorder.record(
                observation_id, "interpretation[0].coding[0].code", hl7_location("OBX", 8), interpretation_code
            )

    effective = parse_hl7_datetime(field_str(obx, 14))
    if effective:
        observation.effectiveDateTime = effective
        if recorder:
            recorder.record(observation_id, "effectiveDateTime", hl7_location("OBX", 14), effective, source_value=field_str(obx, 14))

    extra_resources: list[Resource] = []
    performers = _resolve_performers(obx, performer_cache, recorder=recorder)
    if performers:
        observation.performer = [Reference(reference=f"urn:uuid:{p.id}") for p, _ in performers]
        extra_resources.extend(p for p, is_new in performers if is_new)

    return observation, extra_resources


def build_diagnostic_report(
    obr, patient_id: str, encounter_id: str | None, observation_ids: list[str], recorder=None
) -> DiagnosticReport:
    """OBR -> DiagnosticReport. `result` references the Observations built
    from the OBX segments in this OBR's group - not any OBX anywhere in the
    message, which is exactly what group_segments_by_leader exists to get
    right."""
    report_id = str(uuid.uuid4())
    status = _map_result_status(field_str(obr, 25))
    code = build_codeable_concept_from_cwe(obr, 4, resource_id=report_id, relative_path="code", recorder=recorder)
    report = DiagnosticReport(
        id=report_id,
        status=status,
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )
    if recorder:
        recorder.record(report_id, "status", hl7_location("OBR", 25), status, source_value=field_str(obr, 25))
    # OBR-2/OBR-3 -> identifier[1]/identifier[2] with a fixed type coding,
    # per the v2-to-FHIR IG's own OBR[DiagnosticReport] segment map. Both
    # were dropped entirely before, which is what the drop register found.
    identifiers = []
    for field_num, type_code in ((2, "PLAC"), (3, "FILL")):
        raw_value = field_str(obr, field_num, component=1)
        if not raw_value:
            continue
        identifiers.append(
            Identifier(
                value=raw_value,
                type=CodeableConcept(coding=[Coding(system=ORDER_IDENTIFIER_TYPE_SYSTEM, code=type_code)]),
            )
        )
        if recorder:
            index = len(identifiers) - 1
            recorder.record(
                report_id,
                f"identifier[{index}].value",
                hl7_location("OBR", field_num, component=1),
                raw_value,
            )
            recorder.record_inferred(
                report_id,
                f"identifier[{index}].type.coding[0].code",
                f"Fixed to {type_code!r} by the v2-to-FHIR OBR[DiagnosticReport] map - "
                f"OBR-{field_num} is the placer/filler number by position, not by a code in the field.",
                type_code,
            )
    if identifiers:
        report.identifier = identifiers
    if encounter_id:
        report.encounter = Reference(reference=f"urn:uuid:{encounter_id}")
    if observation_ids:
        report.result = [Reference(reference=f"urn:uuid:{oid}") for oid in observation_ids]

    obr_start = parse_hl7_datetime(field_str(obr, 7))
    obr_end = parse_hl7_datetime(field_str(obr, 8))
    if obr_start and obr_end:
        report.effectivePeriod = Period(start=obr_start, end=obr_end)
        if recorder:
            recorder.record(report_id, "effectivePeriod.start", hl7_location("OBR", 7), obr_start, source_value=field_str(obr, 7))
            recorder.record(report_id, "effectivePeriod.end", hl7_location("OBR", 8), obr_end, source_value=field_str(obr, 8))
    elif obr_start:
        report.effectiveDateTime = obr_start
        if recorder:
            recorder.record(report_id, "effectiveDateTime", hl7_location("OBR", 7), obr_start, source_value=field_str(obr, 7))

    issued = parse_hl7_datetime(field_str(obr, 22))
    if issued:
        report.issued = issued
        if recorder:
            recorder.record(report_id, "issued", hl7_location("OBR", 22), issued, source_value=field_str(obr, 22))

    return report


class BaseOruMapper(MessageMapper):
    """Shared (in fact total - see module docstring) behavior for every ORU
    trigger event. Requires MSH/PID; PV1 is optional (builds a minimal
    Encounter when present). Requires at least one OBR-led group with result
    data; raises MissingSegmentError otherwise."""

    message_type = "ORU"

    def to_bundle(self, message, recorder=None) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        patient = build_patient(pid, recorder=recorder)

        encounter = None
        try:
            pv1 = require_segment(message, "PV1")
        except MissingSegmentError:
            pv1 = None
        # Declared before the encounter so PV1-3's Location chain can be
        # collected into it (see build_minimal_encounter).
        extra_resources: list[Resource] = []
        if pv1 is not None:
            encounter = build_minimal_encounter(
                pv1, patient.id, recorder=recorder, extra_resources=extra_resources
            )
        encounter_id = encounter.id if encounter is not None else None

        groups = group_segments_by_leader(message, "OBR", ["OBX"])
        if not groups:
            raise MissingSegmentError("ORU messages require at least one OBR segment with result data")

        diagnostic_reports: list[DiagnosticReport] = []
        performer_cache: dict[str, Practitioner] = {}
        for obr, obx_segments in groups:
            observation_ids = []
            for obx in obx_segments:
                observation, obs_extra_resources = build_observation(
                    obx, patient.id, encounter_id, performer_cache, recorder=recorder
                )
                extra_resources.append(observation)
                extra_resources.extend(obs_extra_resources)
                observation_ids.append(observation.id)
            diagnostic_reports.append(
                build_diagnostic_report(obr, patient.id, encounter_id, observation_ids, recorder=recorder)
            )

        resources_in_order = ([encounter] if encounter is not None else []) + diagnostic_reports + extra_resources
        return assemble_bundle(msh, patient, *resources_in_order, recorder=recorder)


class OruR01Mapper(BaseOruMapper):
    """R01 - Unsolicited transmission of an observation message."""

    trigger_event = "R01"


class OruR30Mapper(BaseOruMapper):
    """R30 - Unsolicited point-of-care observation message without existing order - place an order."""

    trigger_event = "R30"


class OruR40Mapper(BaseOruMapper):
    """R40 - Unsolicited point-of-care observation message without existing order - do not create a new order."""

    trigger_event = "R40"


class OruR31Mapper(BaseOruMapper):
    """R31 - Unsolicited new point-of-care observation message - search for an order."""

    trigger_event = "R31"


class OruR32Mapper(BaseOruMapper):
    """R32 - Unsolicited pre-ordered point-of-care observation."""

    trigger_event = "R32"
