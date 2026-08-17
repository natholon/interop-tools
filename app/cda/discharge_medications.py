"""Discharge Medications Section (templateId
2.16.840.1.113883.10.20.22.2.11.1) -> MedicationRequest. Previously
disclosed as deferred (see app/cda/discharge_summary.py's original
scope-limit note) on the grounds that this section wraps its medication
entry in a genuinely different Act template than a bare
substanceAdministration - confirmed true, but the wrapped medication entry
itself turns out to be the byte-for-byte identical Medication Activity
template (2.16.840.1.113883.10.20.22.4.16) Medications already parses,
verified by fetching the real official HL7 C-CDA-Examples guide example for
this Act (Guide Examples/Discharge Medication
(V3)_2.16.840.1.113883.10.20.22.4.35) and quoting it verbatim:
`act[templateId=...4.35]/entryRelationship[typeCode=SUBJ]/
substanceAdministration[templateId=...4.16]`. This means the entry-level
parsing this app already built for Medications
(app.cda.medications.build_medication_request, including its dosing/
free-text-SIG handling) is directly reusable, not a case needing new
entry-shape logic - only the outer Act templateId differs, and only this
module's own outer walk is new. The Discharge Medication Act's own `code`
(fixed LOINC 10183-2 "Hospital discharge medication") carries no
information build_medication_request doesn't already get from the nested
substanceAdministration itself, so it isn't read here."""

from fhir.resources.R4B.medicationrequest import MedicationRequest

from app.cda.medications import MEDICATION_ACTIVITY_TEMPLATE_ID, build_medication_request
from app.cda.parser import find_all, find_child, has_template_id

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.11.1"
DISCHARGE_MEDICATION_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.35"


def build_discharge_medication_requests(section, patient_id: str, recorder=None) -> list[MedicationRequest]:
    """One MedicationRequest per Discharge Medication Act entry in the
    section - a section can (and commonly does) have multiple entries.

    `recorder` is accepted (see app/provenance/) but not yet acted on -
    build_medication_request itself isn't instrumented yet (see
    app/cda/medications.py::build_medication_requests' own docstring)."""
    requests = []
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, DISCHARGE_MEDICATION_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            substance_administration = find_child(relationship, "substanceAdministration")
            if substance_administration is None or not has_template_id(
                substance_administration, MEDICATION_ACTIVITY_TEMPLATE_ID
            ):
                continue
            request = build_medication_request(substance_administration, patient_id)
            if request is not None:
                requests.append(request)
    return requests
