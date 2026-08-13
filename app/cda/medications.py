"""Medications section (templateId 2.16.840.1.113883.10.20.22.2.1.1) ->
MedicationRequest, per the official "C-CDA on FHIR" IG's CF-medications.html
guidance and its CCDA-FHIR MedicationRequest.csv mapping table
(build.fhir.org/ig/HL7/ccda-on-fhir/, github.com/HL7/ccda-on-fhir)."""

import uuid

from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.timing import Timing, TimingRepeat

from app.cda.common import build_codeable_concept_from_cd, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds

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


def _resolve_status(substance_administration) -> str:
    status_element = find_child(substance_administration, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _resolve_intent(substance_administration) -> str:
    mood_code = (substance_administration.get("moodCode") or "").strip().upper()
    return _MOOD_TO_INTENT.get(mood_code, _DEFAULT_INTENT)


def _resolve_pq(element) -> Quantity | None:
    """doseQuantity/rateQuantity are PQ-shaped (@value/@unit directly on the
    element) in the common case; an IVL_PQ range (low/high children instead
    of a bare @value) is left unmapped rather than guessed at - the IG's own
    mapping table doesn't disambiguate which shape governs the target field,
    and picking one bound silently would misrepresent a range as a fixed
    dose."""
    if element is None:
        return None
    value = element.get("value")
    if not value:
        return None
    quantity = Quantity(value=value)
    unit = element.get("unit")
    if unit:
        quantity.unit = unit
    return quantity


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


def _build_dosage(substance_administration) -> Dosage | None:
    route = build_codeable_concept_from_cd(find_child(substance_administration, "routeCode"))
    dose_quantity = _resolve_pq(find_child(substance_administration, "doseQuantity"))
    rate_quantity = _resolve_pq(find_child(substance_administration, "rateQuantity"))
    patient_instruction = _resolve_patient_instruction(substance_administration)
    bounds_start, bounds_end = (
        parse_partial_ts(v) for v in ivl_ts_bounds(find_child(substance_administration, "effectiveTime"))
    )

    if not any([route, dose_quantity, rate_quantity, patient_instruction, bounds_start, bounds_end]):
        return None

    dosage = Dosage()
    if route:
        dosage.route = route
    if patient_instruction:
        dosage.patientInstruction = patient_instruction
    if dose_quantity or rate_quantity:
        dose_and_rate = DosageDoseAndRate()
        if dose_quantity:
            dose_and_rate.doseQuantity = dose_quantity
        if rate_quantity:
            dose_and_rate.rateQuantity = rate_quantity
        dosage.doseAndRate = [dose_and_rate]
    if bounds_start or bounds_end:
        period = Period()
        if bounds_start:
            period.start = bounds_start
        if bounds_end:
            period.end = bounds_end
        dosage.timing = Timing(repeat=TimingRepeat(boundsPeriod=period))
    return dosage


def _build_medication_request(substance_administration, patient_id: str) -> MedicationRequest | None:
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
    medication_code = build_codeable_concept_from_cd(
        find_child(manufactured_material, "code") if manufactured_material is not None else None
    )
    if medication_code is None:
        # medicationCodeableConcept (or medicationReference, not produced by
        # this converter) is required by fhir.resources - matching
        # Problems' "no resolvable code -> skip the entry" convention rather
        # than raising or guessing.
        return None

    request = MedicationRequest(
        id=str(uuid.uuid4()),
        status=_resolve_status(substance_administration),
        intent=_resolve_intent(substance_administration),
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        medicationCodeableConcept=medication_code,
    )

    dosage = _build_dosage(substance_administration)
    if dosage:
        request.dosageInstruction = [dosage]

    return request


def build_medication_requests(section, patient_id: str) -> list[MedicationRequest]:
    """One MedicationRequest per Medication Activity entry in the section -
    a section can (and commonly does) have multiple entries."""
    requests = []
    for entry in find_all(section, "entry"):
        substance_administration = find_child(entry, "substanceAdministration")
        if substance_administration is None or not has_template_id(
            substance_administration, MEDICATION_ACTIVITY_TEMPLATE_ID
        ):
            continue
        request = _build_medication_request(substance_administration, patient_id)
        if request is not None:
            requests.append(request)
    return requests
