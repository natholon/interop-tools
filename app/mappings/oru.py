"""ORU (observation result) -> FHIR mapping.

R01, R30, and R40 all produce identical output here: the trigger events
differ in upstream *ordering* workflow semantics (whether a new order should
be placed for an unsolicited point-of-care result) which this stateless
converter doesn't model - it only converts the result data itself, which is
shaped identically across all three.
"""

import uuid

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.fhir_models.builders import build_codeable_concept_from_cwe, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError
from app.hl7.parser import field_str, group_segments_by_leader, raw_field_str, require_segment
from app.mappings.base import MessageMapper
from app.mappings.common import (
    assemble_bundle,
    build_minimal_encounter,
    build_patient,
    build_practitioner_from_xcn,
)

_INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0078"
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


def _build_observation_value(obx) -> dict:
    """Return the kwarg(s) for whichever Observation.value[x] choice fits
    OBX-2 (value type). An unrecognized/unsupported value type (SN, NA, ED,
    ...) leaves the value unset rather than guess - the NM/string/coded
    fallbacks below cover the overwhelming majority of real-world ORU
    traffic, and an Observation without a value is still valid FHIR.

    ST/FT/TX are unstructured free text, not HL7-composite - a literal '^'
    in the text is just a character, not a component separator, so this
    branch reads the field via raw_field_str (whole field) rather than
    field_str (component 1 only), which would otherwise silently truncate
    any free-text value containing a caret."""
    value_type = field_str(obx, 2).strip().upper()
    if value_type in ("ST", "FT", "TX"):
        text = raw_field_str(obx, 5)
        return {"valueString": text} if text else {}
    raw_value = field_str(obx, 5)
    if not raw_value:
        return {}
    if value_type == "NM":
        try:
            quantity_value = float(raw_value)
        except ValueError:
            return {"valueString": raw_value}
        quantity = Quantity(value=quantity_value)
        units = field_str(obx, 6)
        if units:
            quantity.unit = units
        return {"valueQuantity": quantity}
    if value_type in ("CE", "CWE", "CNE", "IS"):
        concept = build_codeable_concept_from_cwe(obx, 5)
        return {"valueCodeableConcept": concept} if concept else {}
    if value_type in ("DT", "DTM"):
        value = parse_hl7_datetime(raw_value)
        return {"valueDateTime": value} if value else {}
    return {}


def _resolve_performer(obx, performer_cache: dict[str, Practitioner]) -> tuple[Practitioner | None, bool]:
    """OBX-16 (Responsible Observer) -> a Practitioner, reused across the
    whole message when the same XCN id (component 1) appears on more than
    one result - a single report panel commonly has every OBX-16 populated
    with the same verifying physician, and without this a 10-result panel
    would otherwise produce 10 near-identical Practitioner resources for one
    real person. Returns (practitioner, is_newly_built) so the caller only
    adds it to the Bundle once."""
    performer_key = field_str(obx, 16, component=1)
    if performer_key and performer_key in performer_cache:
        return performer_cache[performer_key], False
    performer = build_practitioner_from_xcn(obx, 16)
    if performer is not None and performer_key:
        performer_cache[performer_key] = performer
    return performer, performer is not None


def build_observation(
    obx, patient_id: str, encounter_id: str | None, performer_cache: dict[str, Practitioner]
) -> tuple[Observation, list[Resource]]:
    """OBX -> Observation. Returns the Observation plus any extra resources
    materialized for it (currently: a Practitioner for OBX-16, reusing the
    same build_practitioner_from_xcn already used for SIU's AIP - deduped
    across the message via performer_cache, see _resolve_performer)."""
    observation = Observation(
        id=str(uuid.uuid4()),
        status=_map_result_status(field_str(obx, 11)),
        code=build_codeable_concept_from_cwe(obx, 3),
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )
    if encounter_id:
        observation.encounter = Reference(reference=f"urn:uuid:{encounter_id}")

    for key, value in _build_observation_value(obx).items():
        setattr(observation, key, value)

    reference_range_text = field_str(obx, 7)
    if reference_range_text:
        observation.referenceRange = [ObservationReferenceRange(text=reference_range_text)]

    interpretation_code = field_str(obx, 8)
    if interpretation_code:
        observation.interpretation = [
            CodeableConcept(coding=[Coding(system=_INTERPRETATION_SYSTEM, code=interpretation_code)])
        ]

    effective = parse_hl7_datetime(field_str(obx, 14))
    if effective:
        observation.effectiveDateTime = effective

    extra_resources: list[Resource] = []
    performer, is_new = _resolve_performer(obx, performer_cache)
    if performer is not None:
        observation.performer = [Reference(reference=f"urn:uuid:{performer.id}")]
        if is_new:
            extra_resources.append(performer)

    return observation, extra_resources


def build_diagnostic_report(
    obr, patient_id: str, encounter_id: str | None, observation_ids: list[str]
) -> DiagnosticReport:
    """OBR -> DiagnosticReport. `result` references the Observations built
    from the OBX segments in this OBR's group - not any OBX anywhere in the
    message, which is exactly what group_segments_by_leader exists to get
    right."""
    report = DiagnosticReport(
        id=str(uuid.uuid4()),
        status=_map_result_status(field_str(obr, 25)),
        code=build_codeable_concept_from_cwe(obr, 4),
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )
    if encounter_id:
        report.encounter = Reference(reference=f"urn:uuid:{encounter_id}")
    if observation_ids:
        report.result = [Reference(reference=f"urn:uuid:{oid}") for oid in observation_ids]

    obr_start = parse_hl7_datetime(field_str(obr, 7))
    obr_end = parse_hl7_datetime(field_str(obr, 8))
    if obr_start and obr_end:
        report.effectivePeriod = Period(start=obr_start, end=obr_end)
    elif obr_start:
        report.effectiveDateTime = obr_start

    issued = parse_hl7_datetime(field_str(obr, 22))
    if issued:
        report.issued = issued

    return report


class BaseOruMapper(MessageMapper):
    """Shared (in fact total - see module docstring) behavior for every ORU
    trigger event. Requires MSH/PID; PV1 is optional (builds a minimal
    Encounter when present). Requires at least one OBR-led group with result
    data; raises MissingSegmentError otherwise."""

    message_type = "ORU"

    def to_bundle(self, message) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        patient = build_patient(pid)

        encounter = None
        try:
            pv1 = require_segment(message, "PV1")
        except MissingSegmentError:
            pv1 = None
        if pv1 is not None:
            encounter = build_minimal_encounter(pv1, patient.id)
        encounter_id = encounter.id if encounter is not None else None

        groups = group_segments_by_leader(message, "OBR", ["OBX"])
        if not groups:
            raise MissingSegmentError("ORU messages require at least one OBR segment with result data")

        diagnostic_reports: list[DiagnosticReport] = []
        extra_resources: list[Resource] = []
        performer_cache: dict[str, Practitioner] = {}
        for obr, obx_segments in groups:
            observation_ids = []
            for obx in obx_segments:
                observation, obs_extra_resources = build_observation(obx, patient.id, encounter_id, performer_cache)
                extra_resources.append(observation)
                extra_resources.extend(obs_extra_resources)
                observation_ids.append(observation.id)
            diagnostic_reports.append(build_diagnostic_report(obr, patient.id, encounter_id, observation_ids))

        resources_in_order = ([encounter] if encounter is not None else []) + diagnostic_reports + extra_resources
        return assemble_bundle(msh, patient, *resources_in_order)


class OruR01Mapper(BaseOruMapper):
    """R01 - Unsolicited transmission of an observation message."""

    trigger_event = "R01"


class OruR30Mapper(BaseOruMapper):
    """R30 - Unsolicited point-of-care observation message without existing order - place an order."""

    trigger_event = "R30"


class OruR40Mapper(BaseOruMapper):
    """R40 - Unsolicited point-of-care observation message without existing order - do not create a new order."""

    trigger_event = "R40"
