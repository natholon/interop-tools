"""Medications section (templateId 2.16.840.1.113883.10.20.22.2.1.1) ->
MedicationRequest, per the official "C-CDA on FHIR" IG's CF-medications.html
guidance and its CCDA-FHIR MedicationRequest.csv mapping table
(build.fhir.org/ig/HL7/ccda-on-fhir/, github.com/HL7/ccda-on-fhir)."""

import uuid

from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.timing import Timing, TimingRepeat

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_quantity_from_pq,
    effective_time_location,
    parse_partial_ts,
    record_coding,
    record_quantity,
)
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds
from app.provenance.location import xpath_location

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as app/cda/problems.py's constants.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.1.1"
MEDICATION_ACTIVITY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.16"
FREE_TEXT_SIG_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.147"

# CF_MedicationStatus ConceptMap (build.fhir.org/ig/HL7/ccda-on-fhir) -
# CDA ActStatus -> FHIR MedicationRequest.status. "new"/"held" have no row
# in the published ConceptMap - disclosed, not guessed at; an unrecognized
# or absent statusCode falls back to "unknown" (a real status code), same
# fallback philosophy as ORU's OBX-11/DiagnosticReport status map.
STATUS_MAP = {
    "active": "active",
    "suspended": "on-hold",
    "aborted": "stopped",
    "completed": "completed",
    "nullified": "entered-in-error",
}
_DEFAULT_STATUS = "unknown"

# CF_MedActivityMood ConceptMap - moodCode -> MedicationRequest.intent. INT
# (intended, not yet given) -> "order"; EVN (already administered) ->
# "plan" - the IG's own mapping, despite reading backwards at first glance;
# both rows are marked "maps loosely to" since CDA mood codes are broader
# than FHIR intent codes. intent is FHIR-required (confirmed by
# constructing MedicationRequest directly - fhir.resources enforces this
# even though it doesn't show up via model_fields introspection, unlike
# Appointment's non-enforced status), so an unrecognized/absent moodCode
# falls back to "order" - the more common real-world case - rather than
# leaving a required field unset.
_MOOD_TO_INTENT = {"INT": "order", "EVN": "plan"}
_DEFAULT_INTENT = "order"

# The entry element's own relative path - accurate for the plain Medications
# section (entry/substanceAdministration directly, no wrapping Act, this
# function's own primary/first consumer). discharge_medications.py's own
# reuse wraps the identical substanceAdministration one level deeper (inside
# act[templateId=...4.35]/entryRelationship[SUBJ]/) - a real, disclosed
# simplification, not a bug: unlike Problems' own reused build_condition
# (whose two callers both genuinely have an outer <act>, just different
# templateIds, so "act/..." is accurate for both), Medications' own plain
# section has no outer act at all, so no single base prefix is literally
# accurate for both callers. This module deliberately doesn't thread a
# third parameter through the whole dosage-building chain just to
# distinguish the two - the recorded location is still close enough to be
# useful, and correct for the dominant, primary case.
_ENTRY_BASE = "substanceAdministration"


def _resolve_status(substance_administration) -> str:
    status_element = find_child(substance_administration, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _resolve_intent(substance_administration) -> str:
    mood_code = (substance_administration.get("moodCode") or "").strip().upper()
    return _MOOD_TO_INTENT.get(mood_code, _DEFAULT_INTENT)


def _resolve_patient_instruction(substance_administration) -> str | None:
    """Medication Free Text Sig (entryRelationship typeCode=COMP wrapping a
    nested substanceAdministration whose <text> holds free-text SIG
    instructions) -> Dosage.patientInstruction. Structured dosing
    (routeCode/doseQuantity/effectiveTime) and free-text SIG are
    alternatives in real C-CDA, not both always present."""
    for relationship in find_all(substance_administration, "entryRelationship"):
        if relationship.get("typeCode") != "COMP":
            continue
        nested = find_child(relationship, "substanceAdministration")
        if nested is None or not has_template_id(nested, FREE_TEXT_SIG_TEMPLATE_ID):
            continue
        text_element = find_child(nested, "text")
        if text_element is not None and text_element.text and text_element.text.strip():
            return text_element.text.strip()
    return None


def _build_dosage(
    substance_administration, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> Dosage | None:
    route_element = find_child(substance_administration, "routeCode")
    route = build_codeable_concept_from_cd(route_element)
    dose_quantity_element = find_child(substance_administration, "doseQuantity")
    dose_quantity = build_quantity_from_pq(dose_quantity_element)
    rate_quantity_element = find_child(substance_administration, "rateQuantity")
    rate_quantity = build_quantity_from_pq(rate_quantity_element)
    patient_instruction = _resolve_patient_instruction(substance_administration)
    effective_time = find_child(substance_administration, "effectiveTime")
    bounds_start, bounds_end = (parse_partial_ts(v) for v in ivl_ts_bounds(effective_time))

    if not any([route, dose_quantity, rate_quantity, patient_instruction, bounds_start, bounds_end]):
        return None

    dosage = Dosage()
    if route:
        dosage.route = route
        if resource_id and relative_path:
            record_coding(
                recorder, resource_id, f"{relative_path}.route", xpath_location(_ENTRY_BASE, "routeCode"), route
            )
    if patient_instruction:
        dosage.patientInstruction = patient_instruction
        if recorder and resource_id and relative_path:
            recorder.record(
                resource_id,
                f"{relative_path}.patientInstruction",
                xpath_location(_ENTRY_BASE, "entryRelationship[COMP]", "substanceAdministration", "text"),
                patient_instruction,
            )
    if dose_quantity or rate_quantity:
        dose_and_rate = DosageDoseAndRate()
        if dose_quantity:
            dose_and_rate.doseQuantity = dose_quantity
            if resource_id and relative_path:
                record_quantity(
                    recorder,
                    resource_id,
                    f"{relative_path}.doseAndRate[0].doseQuantity",
                    xpath_location(_ENTRY_BASE, "doseQuantity"),
                    dose_quantity,
                )
        if rate_quantity:
            dose_and_rate.rateQuantity = rate_quantity
            if recorder and resource_id and relative_path:
                recorder.record(
                    resource_id,
                    f"{relative_path}.doseAndRate[0].rateQuantity.value",
                    xpath_location(_ENTRY_BASE, "rateQuantity", "@value"),
                    rate_quantity_element.get("value"),
                )
        dosage.doseAndRate = [dose_and_rate]
    if bounds_start or bounds_end:
        period = Period()
        effective_time_base = xpath_location(_ENTRY_BASE, "effectiveTime")
        if bounds_start:
            period.start = bounds_start
            if recorder and resource_id and relative_path:
                recorder.record(
                    resource_id,
                    f"{relative_path}.timing.repeat.boundsPeriod.start",
                    effective_time_location(effective_time_base, effective_time, "low"),
                    bounds_start,
                )
        if bounds_end:
            period.end = bounds_end
            if recorder and resource_id and relative_path:
                recorder.record(
                    resource_id,
                    f"{relative_path}.timing.repeat.boundsPeriod.end",
                    effective_time_location(effective_time_base, effective_time, "high"),
                    bounds_end,
                )
        dosage.timing = Timing(repeat=TimingRepeat(boundsPeriod=period))
    return dosage


# Public (not module-private) - app/cda/discharge_medications.py became a
# second real consumer once that section was confirmed (against a real
# official HL7 example) to wrap the byte-for-byte identical Medication
# Activity template inside a different Act wrapper - only the Act template
# differs, so the per-entry builder itself is reused as-is, including its
# own recorder instrumentation (see _ENTRY_BASE's own docstring above for
# the one disclosed location-string simplification this reuse carries).
def build_medication_request(substance_administration, patient_id: str, recorder=None) -> MedicationRequest | None:
    if substance_administration.get("negationInd") == "true":
        # This specific administration/order did NOT happen - disclosed
        # limitation, not modeled as its own resource this slice, same
        # "skip rather than misrepresent" philosophy as Problems' negated
        # entries (see CLAUDE.md).
        return None

    consumable = find_child(substance_administration, "consumable")
    manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
    manufactured_material = (
        find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
    )
    code_element = find_child(manufactured_material, "code") if manufactured_material is not None else None
    medication_code = build_codeable_concept_from_cd(code_element)
    if medication_code is None:
        # medicationCodeableConcept (or medicationReference, not produced by
        # this converter) is required by fhir.resources - matching
        # Problems' "no resolvable code -> skip the entry" convention rather
        # than raising or guessing.
        return None

    medication_request_id = str(uuid.uuid4())
    status = _resolve_status(substance_administration)
    intent = _resolve_intent(substance_administration)
    request = MedicationRequest(
        id=medication_request_id,
        status=status,
        intent=intent,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        medicationCodeableConcept=medication_code,
    )

    if recorder:
        code_location = xpath_location(_ENTRY_BASE, "consumable", "manufacturedProduct", "manufacturedMaterial", "code")
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(medication_request_id, "medicationCodeableConcept.coding[0].code", f"{code_location}/@code", code_value)
        if display_value:
            recorder.record(
                medication_request_id,
                "medicationCodeableConcept.coding[0].display",
                f"{code_location}/@displayName",
                display_value,
            )

        status_element = find_child(substance_administration, "statusCode")
        status_code = status_element.get("code") if status_element is not None else None
        if status_code and status_code.strip().lower() in STATUS_MAP:
            recorder.record(medication_request_id, "status", xpath_location(_ENTRY_BASE, "statusCode", "@code"), status)
        else:
            recorder.record_inferred(
                medication_request_id,
                "status",
                f"statusCode was absent or not one of the recognized CF_MedicationStatus codes - defaults to the disclosed fallback \"{_DEFAULT_STATUS}\".",
                status,
            )

        mood_code = (substance_administration.get("moodCode") or "").strip().upper()
        if mood_code in _MOOD_TO_INTENT:
            recorder.record(medication_request_id, "intent", xpath_location(_ENTRY_BASE, "@moodCode"), intent)
        else:
            recorder.record_inferred(
                medication_request_id,
                "intent",
                f"moodCode was absent or not one of the two recognized CF_MedActivityMood codes (INT/EVN) - defaults to the disclosed fallback \"{_DEFAULT_INTENT}\".",
                intent,
            )

    dosage = _build_dosage(substance_administration, resource_id=medication_request_id, relative_path="dosageInstruction[0]", recorder=recorder)
    if dosage:
        request.dosageInstruction = [dosage]

    return request


def build_medication_requests(section, patient_id: str, recorder=None) -> list[MedicationRequest]:
    """One MedicationRequest per Medication Activity entry in the section -
    a section can (and commonly does) have multiple entries."""
    requests = []
    for entry in find_all(section, "entry"):
        substance_administration = find_child(entry, "substanceAdministration")
        if substance_administration is None or not has_template_id(
            substance_administration, MEDICATION_ACTIVITY_TEMPLATE_ID
        ):
            continue
        request = build_medication_request(substance_administration, patient_id, recorder=recorder)
        if request is not None:
            requests.append(request)
    return requests
