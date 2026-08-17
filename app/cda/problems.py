"""Problems section (templateId 2.16.840.1.113883.10.20.22.2.5.1) ->
Condition, per the official "C-CDA on FHIR" IG's CF-problems.html
guidance (build.fhir.org/ig/HL7/ccda-on-fhir/)."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, iter_nested_observations, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds

# Public (not module-private) - reused by app/cda/generator.py (to build
# realistic synthetic Problems entries) and app/cda/validation.py (to walk
# a document's Problems section for its own rules), not just this module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.5.1"
CONCERN_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.3"
PROBLEM_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.4"
STATUS_OBSERVATION_CODE = "33999-4"  # LOINC "Status"

_CLINICAL_STATUS_SYSTEM = "http://terminology.hl7.org/CodeSystem/condition-clinical"
# Two SEPARATE vocabularies feed clinicalStatus, checked in priority order -
# they must not be merged into one dict. The Concern Act's own statusCode
# uses the act-lifecycle vocabulary (active/suspended/aborted/completed);
# the nested Status Observation's value uses SNOMED CT concepts. The nested
# observation, when present, takes priority per the IG.
_ACT_STATUS_TO_CLINICAL_STATUS = {
    "active": "active",
    "suspended": "inactive",
    "aborted": "resolved",
    "completed": "resolved",
}
# Disclosed, extensible - not IG-published as an exhaustive table; covers
# the common SNOMED CT problem-status concepts.
STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS = {
    "55561003": "active",
    "73425007": "inactive",
    "413322009": "resolved",
}


def _resolve_clinical_status(act, problem_observation) -> CodeableConcept | None:
    for status_observation in iter_nested_observations(problem_observation, "REFR"):
        code_element = find_child(status_observation, "code")
        if code_element is None or code_element.get("code") != STATUS_OBSERVATION_CODE:
            continue
        value_element = find_child(status_observation, "value")
        value_code = value_element.get("code") if value_element is not None else None
        mapped = STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS.get(value_code) if value_code else None
        if mapped:
            return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code=mapped)])

    status_code_element = find_child(act, "statusCode")
    act_status = status_code_element.get("code") if status_code_element is not None else None
    mapped = _ACT_STATUS_TO_CLINICAL_STATUS.get(act_status) if act_status else None
    return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code=mapped)]) if mapped else None


def _build_condition(act, problem_observation, patient_id: str) -> Condition | None:
    if problem_observation.get("negationInd") == "true":
        # "No known problem" pattern - disclosed limitation, not modeled as
        # its own resource this slice (see CLAUDE.md).
        return None

    value_element = find_child(problem_observation, "value")
    code = build_codeable_concept_from_cd(value_element)
    if code is None:
        return None

    condition = Condition(
        id=str(uuid.uuid4()),
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        code=code,
    )

    clinical_status = _resolve_clinical_status(act, problem_observation)
    if clinical_status:
        condition.clinicalStatus = clinical_status

    low, high = ivl_ts_bounds(find_child(problem_observation, "effectiveTime"))
    onset = parse_partial_ts(low)
    if onset:
        condition.onsetDateTime = onset
    abatement = parse_partial_ts(high)
    if abatement:
        condition.abatementDateTime = abatement

    return condition


def build_conditions(section, patient_id: str) -> list[Condition]:
    """One Condition per Problem Observation entry in the section - a
    section can (and commonly does) have multiple entries."""
    conditions = []
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, CONCERN_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            observation = find_child(relationship, "observation")
            if observation is None or not has_template_id(observation, PROBLEM_OBSERVATION_TEMPLATE_ID):
                continue
            condition = _build_condition(act, observation, patient_id)
            if condition is not None:
                conditions.append(condition)
    return conditions
