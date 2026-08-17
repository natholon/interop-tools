"""Hospital Discharge Diagnosis Section (templateId
2.16.840.1.113883.10.20.22.2.24) -> Condition. Previously disclosed as
deferred (see app/cda/discharge_summary.py's original scope-limit note) on
the grounds that this section wraps its diagnosis in a genuinely different
Act template than Problems' own Concern Act - confirmed true, but the
wrapped *diagnosis itself* turns out to be the byte-for-byte identical
Problem Observation template (2.16.840.1.113883.10.20.22.4.4) Problems
already parses, verified by fetching the real official HL7 C-CDA-Examples
guide example for this Act (Guide Examples/Hospital Discharge Diagnosis
(V3)_2.16.840.1.113883.10.20.22.4.33) and quoting it verbatim:
`act[templateId=...4.33]/entryRelationship[typeCode=SUBJ]/
observation[templateId=...4.4]` - the same entryRelationship[SUBJ] wrapper
shape Problems' own Concern Act uses, just with a different outer Act
template. This means the entry-level parsing this app already built for
Problems (app.cda.problems.build_condition, including its two-vocabulary
clinicalStatus resolution) is directly reusable, not a case needing new
entry-shape logic - only the outer Act templateId differs, and only this
module's own outer walk is new.

Condition.category is set to "encounter-diagnosis" (a real code from
FHIR's own condition-category CodeSystem, terminology.hl7.org/CodeSystem/
condition-category - a diagnosis made in the context of one encounter, as
opposed to Problems' own general problem-list entries) - a field Problems
never populates, since Problems has no comparable signal to distinguish a
category. This is the one genuine difference between a Condition sourced
from this section and one sourced from Problems."""

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.condition import Condition

from app.cda.parser import find_all, find_child, has_template_id
from app.cda.problems import PROBLEM_OBSERVATION_TEMPLATE_ID, build_condition

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.24"
HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.33"

# Public (not module-private) - promoted once
# app/transform/cda_discharge_summary.py became a real reverse-direction
# consumer: Condition.category == "encounter-diagnosis" is the one reliable,
# real signal distinguishing a Condition sourced from this section from one
# sourced from a plain Problems section (which never populates .category at
# all), so the reverse builder checks for this exact (system, code) pair
# rather than a re-derived copy.
CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/condition-category"
CATEGORY_CODE = "encounter-diagnosis"


def build_hospital_discharge_diagnoses(section, patient_id: str, recorder=None) -> list[Condition]:
    """One Condition per Hospital Discharge Diagnosis Act entry in the
    section - a section can (and commonly does) have multiple entries.

    `recorder` is threaded straight into the reused build_condition (see
    module docstring - the wrapped Problem Observation entry shape is
    byte-for-byte identical to Problems' own) rather than accepted-and-
    ignored the way every other not-yet-instrumented SECTION_BUILDERS entry
    is this slice - Problems' own instrumentation work covers this section
    "for free" the same way build_patient/assemble_bundle already
    instrument every HL7v2 message type's shared fields for free, since the
    recorded location strings ("act/entryRelationship/observation/...")
    are accurate regardless of which outer Act template wraps the identical
    inner entry shape."""
    conditions = []
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            observation = find_child(relationship, "observation")
            if observation is None or not has_template_id(observation, PROBLEM_OBSERVATION_TEMPLATE_ID):
                continue
            condition = build_condition(act, observation, patient_id, recorder=recorder)
            if condition is not None:
                condition.category = [CodeableConcept(coding=[Coding(system=CATEGORY_SYSTEM, code=CATEGORY_CODE)])]
                conditions.append(condition)
    return conditions
