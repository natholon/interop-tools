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
substanceAdministration itself, so it isn't read here.

**`MedicationRequest.category` is set to "discharge"** - the one genuine
difference between a MedicationRequest sourced from this section and one
sourced from a plain Medications section (which never populates
`.category` at all), exactly mirroring how
app/cda/hospital_discharge_diagnosis.py marks its own Conditions with
`category="encounter-diagnosis"`. `discharge` is a real code from FHIR
R4's own medicationrequest-category CodeSystem (hl7.org/fhir/R4/
codesystem-medicationrequest-category.html), defined verbatim as
"Includes requests for medications created when the patient is being
released from a facility" - an exact semantic match for this section, not
a locally-invented marker.

**This closes what was previously disclosed as a permanent limitation,
and that disclosure was simply wrong**: earlier notes claimed a
Discharge-Medications-sourced MedicationRequest was structurally
indistinguishable from a plain-Medications one on the FHIR side, so the
reverse direction could never split it back out. That described this
module's own original implementation choice (reusing
build_medication_request with zero modification) as though it were a
standards constraint. `MedicationRequest.category` exists, a standard
code for precisely this case exists, and the sibling Hospital Discharge
Diagnosis section had already proven the pattern - see
app/transform/cda_ccd.py for the matching reverse-direction split."""

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.medicationrequest import MedicationRequest

from app.cda.medications import MEDICATION_ACTIVITY_TEMPLATE_ID, build_medication_request
from app.cda.parser import find_all, find_child, has_template_id

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.11.1"
DISCHARGE_MEDICATION_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.35"

# Public for the same reason hospital_discharge_diagnosis.py's own
# CATEGORY_SYSTEM/CATEGORY_CODE are: app/transform/cda_ccd.py became a
# real reverse-direction consumer, needing the identical pair to tell a
# Discharge-Medications-sourced MedicationRequest apart from a
# plain-Medications one when deciding which section to regenerate it into.
CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/medicationrequest-category"
CATEGORY_CODE = "discharge"


def build_discharge_medication_requests(section, patient_id: str, recorder=None) -> list[MedicationRequest]:
    """One MedicationRequest per Discharge Medication Act entry in the
    section - a section can (and commonly does) have multiple entries.

    Reuses build_medication_request's own recorder instrumentation as-is
    (see that function's own docstring and app/cda/medications.py's
    _ENTRY_BASE for the one disclosed location-string simplification this
    reuse carries: the recorded paths describe the substanceAdministration
    element's own relative shape, not this module's own outer
    act/entryRelationship[SUBJ]/ wrapper)."""
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
            request = build_medication_request(substance_administration, patient_id, recorder=recorder)
            if request is not None:
                request.category = [CodeableConcept(coding=[Coding(system=CATEGORY_SYSTEM, code=CATEGORY_CODE)])]
                if recorder:
                    # Inferred, not direct: no source element carries this -
                    # it's implied by which section the entry was found in,
                    # the identical reasoning hospital_discharge_diagnosis.py's
                    # own category marker records under.
                    recorder.record_inferred(
                        request.id,
                        "category[0].coding[0].code",
                        "Implied by the entry appearing in a Discharge Medications section - no source element carries it.",
                        CATEGORY_CODE,
                    )
                requests.append(request)
    return requests
