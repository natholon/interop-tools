"""Discharge Medications Section (templateId
2.16.840.1.113883.10.20.22.2.11.1) -> MedicationRequest.

The outer Act is this section's own (...4.35), but what it wraps is the
byte-for-byte identical Medication Activity (...4.16) that Medications
already parses - verified against the official HL7 C-CDA-Examples guide
example for this Act:

    act[templateId=...4.35]/entryRelationship[typeCode=SUBJ]/
      substanceAdministration[templateId=...4.16]

So `app.cda.medications.build_medication_request` is reused wholesale,
dosing and free-text-SIG handling included; only the outer walk is new.
The Act's own fixed code (LOINC 10183-2) carries nothing the nested
substanceAdministration does not already give, so it is not read.

**`MedicationRequest.category` is set to `"discharge"`** - the one genuine
difference from a plain-Medications entry, which never populates
`.category` at all, mirroring how `hospital_discharge_diagnosis.py` marks
its Conditions. `discharge` is a real code in R4's own
`medicationrequest-category` CodeSystem, defined as "requests for
medications created when the patient is being released from a facility" -
an exact match for this section, not a locally-invented marker. It is also
what lets the reverse direction split this section back out; see
`app/transform/cda_ccd.py`."""

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
            built = build_medication_request(substance_administration, patient_id, recorder=recorder)
            if built is not None:
                request, extra = built
                requests.extend(extra)
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
