"""Immunizations section (templateId 2.16.840.1.113883.10.20.22.2.2.1), per
the C-CDA on FHIR IG's CF-immunizations guidance and its CCDA-FHIR
Immunization.csv.

**The CSV splits Immunization Activity by moodCode into two targets**, and
both are built here: EVN (already administered or refused) ->
`Immunization`, INT (ordered or planned) -> `MedicationRequest`. The field
sets are largely disjoint, so the two builders share only the
consumable-code lookup.

EVN side: `statusCode` -> status via CF_ImmunizationStatus (STATUS_MAP),
with `negationInd="true"` overriding it to `not-done` unconditionally -
checked first, since every other source status already maps there anyway.
`occurrence[x]` is a required choice enforced at construction, falling back
to `occurrenceString = "Unknown"` when effectiveTime does not resolve.

INT side: `statusCode` -> status, `negationInd` -> `doNotPerform` (true and
false alike, rather than overriding status), `consumable` ->
`medicationCodeableConcept`, and routeCode/doseQuantity/repeatNumber ->
`dosageInstruction[0]`. `intent` is fixed to `"order"` by the moodCode.

**Two INT rows cannot be used as the CSV writes them**, and the
substitutions are disclosed rather than silent:

- `.statusCode` names the transform "CF_ImmunizationReqStatus", which is
  not published - the maps directory has CF-ImmunizationStatus for the EVN
  side and no Req variant. The MedicationRequest table maps the identical
  source (a CDA ActStatus statusCode) to the identical field on the
  identical resource via CF_MedStatus, which `app/cda/medications.py`
  already implements, so that map is reused.
- `.repeatNumber` names `protocolApplied.seriesDosesPositiveInt`, which
  does not exist on MedicationRequest - it is an Immunization field, and
  reads like a copy-paste from the EVN row above it. The MedicationRequest
  table maps repeatNumber to `dosageInstruction.timing.repeat.count`, a
  real field, so that is used instead.

`route` and `doseAndRate` are also written bare in the CSV but exist only
under `dosageInstruction`; that is shorthand rather than an error.

Scope limits:
- `primarySource` has a "fixed value: unknown" row, but the field is a
  plain boolean and `"unknown"` is not a valid boolean - passing the IG's
  own stated value through would produce an invalid resource, so it is
  skipped.
- `performer` and `author` are not mapped on either side: this module has
  no CDA-side `assignedEntity` -> Practitioner builder (`procedures.py`
  has one, but it is module-private and its PractitionerRole chain is
  scoped to that section's own IG rows).
- An entry in any mood other than EVN or INT is skipped."""

import uuid

from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.immunization import Immunization
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.timing import Timing, TimingRepeat

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_identifiers,
    build_quantity_from_pq,
    effective_time_location,
    parse_partial_ts,
    record_coding,
    record_quantity,
)
from app.cda.medications import STATUS_MAP as MEDICATION_STATUS_MAP
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds
from app.provenance.location import xpath_location

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as the other section modules.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.2.1"
IMMUNIZATION_ACTIVITY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.52"

# CF_ImmunizationStatus ConceptMap (build.fhir.org/ig/HL7/ccda-on-fhir) -
# CDA ActStatus -> FHIR Immunization.status, for the non-negated case only
# (negationInd="true" maps to "not-done" unconditionally regardless of
# statusCode, per the ConceptMap's own "Only map to not-done when CDA
# negation is present and true" comment on its completed->not-done row -
# every other source status already maps to not-done anyway, so negation
# is checked first, before this table is even consulted).
STATUS_MAP = {
    "completed": "completed",
    "nullified": "entered-in-error",
    "aborted": "not-done",
    "cancelled": "not-done",
    "held": "not-done",
    "new": "not-done",
    "obsolete": "not-done",
    "suspended": "not-done",
}
# Immunization.status has no "unknown" option (unlike most other status
# maps in this app) - only completed | entered-in-error | not-done. An
# unrecognized/absent statusCode (non-negated) defaults to "completed"
# rather than "not-done", since the entry is asserted in EVN (event
# already occurred) mood specifically and "completed" is by far the most
# common real-world value for immunization history - the same "default to
# the most common real value when no unknown option exists" judgment
# already made for MDM's TXA-19 (default "current") and Medications'
# moodCode (default "order").
_DEFAULT_STATUS = "completed"

# No wrapping Act - the entry element itself, same shape as Medications'
# own bare substanceAdministration (unlike Problems'/Allergies' own
# Concern-Act-wrapped entries).
_ENTRY_BASE = "substanceAdministration"

# CF_MedStatus's own fallback, matching app/cda/medications.py.
_DEFAULT_REQUEST_STATUS = "unknown"


def _resolve_status(substance_administration) -> str:
    if substance_administration.get("negationInd") == "true":
        return "not-done"
    status_element = find_child(substance_administration, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _build_immunization(substance_administration, patient_id: str, recorder=None) -> Immunization | None:
    consumable = find_child(substance_administration, "consumable")
    manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
    manufactured_material = (
        find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
    )
    code_element = find_child(manufactured_material, "code") if manufactured_material is not None else None
    vaccine_code = build_codeable_concept_from_cd(code_element)
    if vaccine_code is None:
        # vaccineCode is FHIR-required - skip the entry rather than
        # construct an invalid resource, matching every other section's
        # "no resolvable code -> skip" convention.
        return None

    # occurrence[x] (occurrenceDateTime | occurrenceString) is a required
    # one-of choice enforced eagerly at construction (same as Encounter.
    # class_fhir - must be a constructor kwarg, not set via attribute
    # assignment afterward). Falls back to occurrenceString = "Unknown"
    # when effectiveTime doesn't resolve, FHIR's own conventional
    # representation for "this vaccine was given, exact date unrecorded"
    # (see e.g. US Core's Immunization guidance), not a local guess.
    effective_time = find_child(substance_administration, "effectiveTime")
    occurrence, _ = ivl_ts_bounds(effective_time)
    occurrence_dt = parse_partial_ts(occurrence)
    occurrence_kwargs = {"occurrenceDateTime": occurrence_dt} if occurrence_dt else {"occurrenceString": "Unknown"}

    immunization_id = str(uuid.uuid4())
    status = _resolve_status(substance_administration)
    immunization = Immunization(
        id=immunization_id,
        status=status,
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        vaccineCode=vaccine_code,
        **occurrence_kwargs,
    )


    # The IG maps this entry's own <id> as a source value to
    # Immunization.identifier. Procedure already built its identifier; this one did
    # not, and the drop register flagged it as a gap against the IG.
    identifiers = build_identifiers(
        find_all(substance_administration, "id"),
        "urn:interop-tools:cda-immunization-id",
        resource_id=immunization_id,
        location_prefix=xpath_location(_ENTRY_BASE, "id"),
        recorder=recorder,
    )
    if identifiers:
        immunization.identifier = identifiers

    if recorder:
        code_location = xpath_location(_ENTRY_BASE, "consumable", "manufacturedProduct", "manufacturedMaterial", "code")
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(immunization_id, "vaccineCode.coding[0].code", f"{code_location}/@code", code_value)
        if display_value:
            recorder.record(immunization_id, "vaccineCode.coding[0].display", f"{code_location}/@displayName", display_value)

        if substance_administration.get("negationInd") == "true":
            recorder.record(immunization_id, "status", xpath_location(_ENTRY_BASE, "@negationInd"), status)
        else:
            status_element = find_child(substance_administration, "statusCode")
            status_code = status_element.get("code") if status_element is not None else None
            if status_code and status_code.strip().lower() in STATUS_MAP:
                recorder.record(immunization_id, "status", xpath_location(_ENTRY_BASE, "statusCode", "@code"), status)
            else:
                recorder.record_inferred(
                    immunization_id,
                    "status",
                    f"statusCode was absent or not one of the recognized CF_ImmunizationStatus codes, and negationInd wasn't \"true\" - defaults to the disclosed fallback \"{_DEFAULT_STATUS}\".",
                    status,
                )

        if occurrence_dt:
            recorder.record(
                immunization_id,
                "occurrenceDateTime",
                effective_time_location(xpath_location(_ENTRY_BASE, "effectiveTime"), effective_time, "low"),
                occurrence_dt,
            )
        else:
            recorder.record_inferred(
                immunization_id,
                "occurrenceString",
                "effectiveTime was absent or unparseable - occurrenceString falls back to FHIR's own conventional \"Unknown\" representation rather than fabricating a fake timestamp.",
                "Unknown",
            )

    if manufactured_material is not None:
        lot_element = find_child(manufactured_material, "lotNumberText")
        if lot_element is not None and lot_element.text and lot_element.text.strip():
            immunization.lotNumber = lot_element.text.strip()
            if recorder:
                recorder.record(
                    immunization_id,
                    "lotNumber",
                    xpath_location(_ENTRY_BASE, "consumable", "manufacturedProduct", "manufacturedMaterial", "lotNumberText"),
                    immunization.lotNumber,
                )

    route_element = find_child(substance_administration, "routeCode")
    route = build_codeable_concept_from_cd(route_element)
    if route:
        immunization.route = route
        record_coding(recorder, immunization_id, "route", xpath_location(_ENTRY_BASE, "routeCode"), route)

    dose_quantity_element = find_child(substance_administration, "doseQuantity")
    dose_quantity = build_quantity_from_pq(dose_quantity_element)
    if dose_quantity:
        immunization.doseQuantity = dose_quantity
        record_quantity(
            recorder, immunization_id, "doseQuantity", xpath_location(_ENTRY_BASE, "doseQuantity"), dose_quantity
        )

    return immunization


# INT-mood entries are a *request*, so the IG sends them to
# MedicationRequest with its own field list. Two of those rows cannot be
# used as written, and the substitutions are disclosed rather than silent:
#
# - `.statusCode` names the transform "CF_ImmunizationReqStatus", which is
#   not published (the maps directory has CF-ImmunizationStatus, for the
#   EVN side, and no Req variant). The sibling MedicationRequest table
#   maps the identical source - a CDA ActStatus statusCode - to the
#   identical field on the identical resource via CF_MedStatus, which
#   app/cda/medications.py already implements, so that map is reused.
# - `.repeatNumber` names `protocolApplied.seriesDosesPositiveInt`, which
#   does not exist on MedicationRequest at all (it is an Immunization
#   field, and reads like a copy-paste from the EVN row above it). The
#   MedicationRequest table maps repeatNumber to
#   `dosageInstruction.timing.repeat.count`, a real field, so that is used.
#
# `route` and `doseAndRate` are likewise written bare in the CSV but exist
# only under `dosageInstruction`; that is shorthand rather than an error.
_INT_INTENT = "order"


def _build_immunization_request(
    substance_administration, patient_id: str, recorder=None
) -> MedicationRequest | None:
    """An INT-mood (planned/ordered) Immunization Activity -> MedicationRequest."""
    consumable = find_child(substance_administration, "consumable")
    manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
    manufactured_material = (
        find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
    )
    code_element = find_child(manufactured_material, "code") if manufactured_material is not None else None
    medication = build_codeable_concept_from_cd(code_element)
    if medication is None:
        # medicationCodeableConcept is required, same skip rule as the EVN
        # side's vaccineCode and Medications' own entries.
        return None

    request_id = str(uuid.uuid4())
    status_element = find_child(substance_administration, "statusCode")
    raw_status = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    status = MEDICATION_STATUS_MAP.get(raw_status, _DEFAULT_REQUEST_STATUS)

    request = MedicationRequest(
        id=request_id,
        status=status,
        intent=_INT_INTENT,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        medicationCodeableConcept=medication,
    )

    if recorder:
        code_location = xpath_location(
            _ENTRY_BASE, "consumable", "manufacturedProduct", "manufacturedMaterial", "code"
        )
        record_coding(recorder, request_id, "medicationCodeableConcept", code_location, medication)
        if raw_status in MEDICATION_STATUS_MAP:
            recorder.record(request_id, "status", xpath_location(_ENTRY_BASE, "statusCode", "@code"), status)
        else:
            recorder.record_inferred(
                request_id,
                "status",
                f"statusCode was absent or not one of the recognized CF_MedStatus codes - defaults to "
                f'the disclosed fallback "{_DEFAULT_REQUEST_STATUS}".',
                status,
            )
        recorder.record_inferred(
            request_id,
            "intent",
            'moodCode is INT (a request), which the IG maps to intent "order".',
            _INT_INTENT,
        )

    identifiers = build_identifiers(
        find_all(substance_administration, "id"),
        "urn:interop-tools:cda-immunization-id",
        resource_id=request_id,
        location_prefix=xpath_location(_ENTRY_BASE, "id"),
        recorder=recorder,
    )
    if identifiers:
        request.identifier = identifiers

    # negationInd maps to doNotPerform directly, true and false alike -
    # unlike the EVN side, where it overrides status instead.
    negation = (substance_administration.get("negationInd") or "").strip().lower()
    if negation in ("true", "false"):
        request.doNotPerform = negation == "true"
        if recorder:
            recorder.record(
                request_id,
                "doNotPerform",
                xpath_location(_ENTRY_BASE, "@negationInd"),
                str(request.doNotPerform),
                source_value=substance_administration.get("negationInd"),
            )

    dosage = _build_request_dosage(substance_administration, request_id, recorder)
    if dosage is not None:
        request.dosageInstruction = [dosage]

    return request


def _build_request_dosage(substance_administration, request_id: str, recorder=None) -> Dosage | None:
    """routeCode/doseQuantity/repeatNumber -> Dosage, or None when the
    entry carries none of them."""
    dosage = Dosage()
    populated = False

    route = build_codeable_concept_from_cd(find_child(substance_administration, "routeCode"))
    if route:
        dosage.route = route
        record_coding(
            recorder, request_id, "dosageInstruction[0].route", xpath_location(_ENTRY_BASE, "routeCode"), route
        )
        populated = True

    dose_quantity = build_quantity_from_pq(find_child(substance_administration, "doseQuantity"))
    if dose_quantity:
        dosage.doseAndRate = [DosageDoseAndRate(doseQuantity=dose_quantity)]
        record_quantity(
            recorder,
            request_id,
            "dosageInstruction[0].doseAndRate[0].doseQuantity",
            xpath_location(_ENTRY_BASE, "doseQuantity"),
            dose_quantity,
        )
        populated = True

    repeat_element = find_child(substance_administration, "repeatNumber")
    repeat_value = (repeat_element.get("value") or "").strip() if repeat_element is not None else ""
    if repeat_value.isdigit():
        dosage.timing = Timing(repeat=TimingRepeat(count=int(repeat_value)))
        if recorder:
            recorder.record(
                request_id,
                "dosageInstruction[0].timing.repeat.count",
                xpath_location(_ENTRY_BASE, "repeatNumber", "@value"),
                repeat_value,
            )
        populated = True

    return dosage if populated else None


def build_immunizations(
    section, patient_id: str, recorder=None
) -> list[Immunization | MedicationRequest]:
    """One resource per Immunization Activity entry, dispatched on
    moodCode the way the IG's own table splits: EVN (administered or
    refused) -> Immunization, INT (planned or ordered) -> MedicationRequest.
    A section can, and commonly does, carry several entries. An entry in
    any other mood is skipped."""
    resources: list[Immunization | MedicationRequest] = []
    for entry in find_all(section, "entry"):
        substance_administration = find_child(entry, "substanceAdministration")
        if substance_administration is None or not has_template_id(
            substance_administration, IMMUNIZATION_ACTIVITY_TEMPLATE_ID
        ):
            continue
        mood = substance_administration.get("moodCode")
        if mood == "EVN":
            resource = _build_immunization(substance_administration, patient_id, recorder=recorder)
        elif mood == "INT":
            resource = _build_immunization_request(substance_administration, patient_id, recorder=recorder)
        else:
            continue
        if resource is not None:
            resources.append(resource)
    return resources
