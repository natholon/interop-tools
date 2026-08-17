"""Problems section (templateId 2.16.840.1.113883.10.20.22.2.5.1) ->
Condition, per the official "C-CDA on FHIR" IG's CF-problems.html
guidance (build.fhir.org/ig/HL7/ccda-on-fhir/)."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, effective_time_location, iter_nested_observations, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds
from app.provenance.location import xpath_location

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


# Locations for _resolve_clinical_status's own two-vocabulary priority
# order - which branch actually fired matters for the crosswalk the same
# way app/mappings/adt.py's PV1-44-vs-EVN-2 period.start resolution does,
# so this function returns which one alongside the resolved CodeableConcept
# rather than leaving the caller to guess.
_STATUS_OBSERVATION_LOCATION = xpath_location(
    "act", "entryRelationship[SUBJ]", "observation", "entryRelationship[REFR]", "observation", "value", "@code"
)
_ACT_STATUS_LOCATION = xpath_location("act", "statusCode", "@code")


def _resolve_clinical_status(act, problem_observation) -> tuple[CodeableConcept | None, str | None]:
    for status_observation in iter_nested_observations(problem_observation, "REFR"):
        code_element = find_child(status_observation, "code")
        if code_element is None or code_element.get("code") != STATUS_OBSERVATION_CODE:
            continue
        value_element = find_child(status_observation, "value")
        value_code = value_element.get("code") if value_element is not None else None
        mapped = STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS.get(value_code) if value_code else None
        if mapped:
            return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code=mapped)]), _STATUS_OBSERVATION_LOCATION

    status_code_element = find_child(act, "statusCode")
    act_status = status_code_element.get("code") if status_code_element is not None else None
    mapped = _ACT_STATUS_TO_CLINICAL_STATUS.get(act_status) if act_status else None
    if mapped:
        return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code=mapped)]), _ACT_STATUS_LOCATION
    return None, None


# Public (not module-private) - app/cda/hospital_discharge_diagnosis.py
# became a second real consumer once that section was confirmed (against a
# real official HL7 example) to wrap the byte-for-byte identical Problem
# Observation template inside a different Act wrapper - only the Act
# template differs, so the per-entry builder itself is reused as-is,
# including its own recorder instrumentation, since the recorded location
# strings ("act/entryRelationship[...]/...") are accurate regardless of
# which outer Act template wraps this identical inner entry shape (see
# hospital_discharge_diagnosis.py's own docstring).
def build_condition(act, problem_observation, patient_id: str, recorder=None) -> Condition | None:
    if problem_observation.get("negationInd") == "true":
        # "No known problem" pattern - disclosed limitation, not modeled as
        # its own resource this slice (see CLAUDE.md).
        return None

    value_element = find_child(problem_observation, "value")
    code = build_codeable_concept_from_cd(value_element)
    if code is None:
        return None

    condition_id = str(uuid.uuid4())
    condition = Condition(
        id=condition_id,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        code=code,
    )
    if recorder:
        value_location = xpath_location("act", "entryRelationship[SUBJ]", "observation", "value")
        code_value = value_element.get("code")
        display_value = value_element.get("displayName")
        if code_value:
            recorder.record(condition_id, "code.coding[0].code", f"{value_location}/@code", code_value)
        if display_value:
            recorder.record(condition_id, "code.coding[0].display", f"{value_location}/@displayName", display_value)

    clinical_status, status_location = _resolve_clinical_status(act, problem_observation)
    if clinical_status:
        condition.clinicalStatus = clinical_status
        if recorder:
            recorder.record(condition_id, "clinicalStatus.coding[0].code", status_location, clinical_status.coding[0].code)

    effective_time = find_child(problem_observation, "effectiveTime")
    low, high = ivl_ts_bounds(effective_time)
    effective_time_base = "act/entryRelationship[SUBJ]/observation/effectiveTime"
    onset = parse_partial_ts(low)
    if onset:
        condition.onsetDateTime = onset
        if recorder:
            recorder.record(
                condition_id, "onsetDateTime", effective_time_location(effective_time_base, effective_time, "low"), onset
            )
    abatement = parse_partial_ts(high)
    if abatement:
        condition.abatementDateTime = abatement
        if recorder:
            recorder.record(
                condition_id,
                "abatementDateTime",
                effective_time_location(effective_time_base, effective_time, "high"),
                abatement,
            )

    return condition


def build_conditions(section, patient_id: str, recorder=None) -> list[Condition]:
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
            condition = build_condition(act, observation, patient_id, recorder=recorder)
            if condition is not None:
                conditions.append(condition)
    return conditions
