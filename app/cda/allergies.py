"""Allergies and Intolerances section (templateId
2.16.840.1.113883.10.20.22.2.6.1) -> AllergyIntolerance, per the official
"C-CDA on FHIR" IG's CF-allergies.html guidance and its underlying
CCDA-FHIR Allergy.csv mapping table (build.fhir.org/ig/HL7/ccda-on-fhir/,
github.com/HL7/ccda-on-fhir)."""

import uuid

from fhir.resources.R4B.allergyintolerance import AllergyIntolerance, AllergyIntoleranceReaction
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, ts_value
from app.cda.problems import STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS as _PROBLEM_STATUS_MAP

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as app/cda/problems.py's constants.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.6.1"
# The "entries optional" sibling section (no @extension on this templateId,
# vs. "2.16.840.1.113883.10.20.22.2.6.1" which is the versioned "entries
# required" one) - a real-world Discharge Summary example (fetched while
# researching that document type, see app/cda/discharge_summary.py) used
# this exact variant for its Allergies section. Entry-level content is
# identical either way (the same Allergy Concern Act/Allergy-Intolerance
# Observation templates); only the section-level entry-cardinality
# constraint differs, so both templateIds are registered against the same
# build_allergy_intolerances in app/cda/registry.py.
SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.6"
ALLERGY_CONCERN_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.30"
ALLERGY_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.7"
ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.28"
CRITICALITY_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.145"
REACTION_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.9"
SEVERITY_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.8"

_CLINICAL_STATUS_SYSTEM = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"

# CF_AllergyIntoleranceType / CF_AllergyIntoleranceCategory ConceptMaps
# (build.fhir.org/ig/HL7/ccda-on-fhir) - both keyed off the SAME source
# value (the Allergy-Intolerance Observation's own <value>), but target
# genuinely different vocabularies, so two separate dicts rather than one
# merged table. The "propensity to adverse reaction" SNOMED codes
# (418038007 always, 419199007/420134006 for category only) have no row in
# the published ConceptMaps - disclosed, not guessed at.
_TYPE_MAP = {
    "235719002": "intolerance",  # Intolerance to food
    "414285001": "allergy",  # Allergy to food
    "416098002": "allergy",  # Allergy to drug
    "419199007": "allergy",  # Allergy to substance
    "59037007": "intolerance",  # Intolerance to drug
}
_CATEGORY_MAP = {
    "235719002": "food",
    "414285001": "food",
    "418471000": "food",  # Propensity to adverse reactions to food
    "416098002": "medication",
    "419511003": "medication",  # Propensity to adverse reactions to drug
    "59037007": "medication",
}

# CF_AllergyStatus ConceptMap - reuses the identical SNOMED CT values
# app.cda.problems.STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS already
# defines, since C-CDA has exactly one generic "Status Observation" shape
# (LOINC 33999-4) shared across section types, not a Problems-specific one.
# Still its own name/dict (not a re-export) since AllergyIntolerance.
# clinicalStatus binds to a DIFFERENT terminology system
# (allergyintolerance-clinical) than Condition.clinicalStatus
# (condition-clinical) - only the source->intermediate-value crosswalk is
# shared, not the target CodeableConcept's coding system.
CLINICAL_STATUS_MAP = dict(_PROBLEM_STATUS_MAP)

# CF_Criticality ConceptMap - local HL7ObservationValue table codes, not SNOMED.
CRITICALITY_MAP = {"CRITH": "high", "CRITL": "low", "CRITU": "unable-to-assess"}

# CF_Severity ConceptMap.
SEVERITY_MAP = {
    "255604002": "mild",
    "6736007": "moderate",
    "24484000": "severe",
}


def _find_nested_observation(parent, type_code: str, template_id: str):
    """Find a nested observation via entryRelationship[@typeCode=type_code]
    whose observation carries the given templateId - the same
    typeCode-then-templateId guard app.cda.problems uses (checking only the
    nested templateId, without the relationship typeCode, let a
    wrongly-typed relationship falsely match once already - see CLAUDE.md)."""
    for relationship in find_all(parent, "entryRelationship"):
        if relationship.get("typeCode") != type_code:
            continue
        observation = find_child(relationship, "observation")
        if observation is not None and has_template_id(observation, template_id):
            return observation
    return None


def _value_code(element) -> str | None:
    value_element = find_child(element, "value") if element is not None else None
    return value_element.get("code") if value_element is not None else None


def _resolve_clinical_status(allergy_observation) -> CodeableConcept:
    status_observation = _find_nested_observation(allergy_observation, "REFR", ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID)
    value_code = _value_code(status_observation)
    mapped = CLINICAL_STATUS_MAP.get(value_code) if value_code else None
    if mapped:
        return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code=mapped)])
    # Per the IG's own CCDA-FHIR Allergy.csv: "entryRelationship (Allergy
    # Status absent) -> clinicalStatus: fixed value 'active'". Unlike
    # Problems (which falls back to the Concern Act's own statusCode), the
    # IG marks the Allergy Concern Act's statusCode/effectiveTime "not
    # supported" entirely - there's no second vocabulary to fall back to
    # here, only this disclosed fixed default.
    return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code="active")])


def _resolve_criticality(allergy_observation) -> str | None:
    criticality_observation = _find_nested_observation(
        allergy_observation, "SUBJ", CRITICALITY_OBSERVATION_TEMPLATE_ID
    )
    value_code = _value_code(criticality_observation)
    return CRITICALITY_MAP.get(value_code) if value_code else None


def _build_reaction(reaction_observation) -> AllergyIntoleranceReaction | None:
    manifestation = build_codeable_concept_from_cd(find_child(reaction_observation, "value"))
    if manifestation is None:
        # manifestation is FHIR-required on AllergyIntoleranceReaction -
        # skip the reaction entirely rather than construct an invalid one,
        # matching Problems'/Medications' "no resolvable code -> skip"
        # convention.
        return None

    reaction = AllergyIntoleranceReaction(manifestation=[manifestation])

    onset, _ = ivl_ts_bounds(find_child(reaction_observation, "effectiveTime"))
    onset_dt = parse_partial_ts(onset)
    if onset_dt:
        reaction.onset = onset_dt

    severity_observation = _find_nested_observation(reaction_observation, "SUBJ", SEVERITY_OBSERVATION_TEMPLATE_ID)
    severity_code = _value_code(severity_observation)
    severity = SEVERITY_MAP.get(severity_code) if severity_code else None
    if severity:
        reaction.severity = severity

    return reaction


def _build_reactions(allergy_observation) -> list[AllergyIntoleranceReaction]:
    reactions = []
    for relationship in find_all(allergy_observation, "entryRelationship"):
        if relationship.get("typeCode") != "MFST":
            continue
        reaction_observation = find_child(relationship, "observation")
        if reaction_observation is None or not has_template_id(reaction_observation, REACTION_OBSERVATION_TEMPLATE_ID):
            continue
        reaction = _build_reaction(reaction_observation)
        if reaction is not None:
            reactions.append(reaction)
    return reactions


def _resolve_allergen_code(allergy_observation, negated: bool) -> CodeableConcept | None:
    """participant[@typeCode=CSM]/participantRole/playingEntity/code -> the
    allergen itself. When negationInd="true", this app does not attempt the
    IG's full source-specific "no known allergy to X" SNOMED crosswalk
    (CF_AllergyIntoleranceAbsentCode) - no verified published table for it
    was found (same "don't guess at an unverified crosswalk" philosophy as
    MDM's TXA-17/TXA-19) - and instead falls back to the IG's own disclosed
    text-only patterns: a still-resolvable allergen text-describes what's
    NOT allergic ("No known allergy to <display>"), an unresolvable/absent
    one uses the IG's exact "No known allergies" wording."""
    for participant in find_all(allergy_observation, "participant"):
        if participant.get("typeCode") != "CSM":
            continue
        participant_role = find_child(participant, "participantRole")
        playing_entity = find_child(participant_role, "playingEntity") if participant_role is not None else None
        code_element = find_child(playing_entity, "code") if playing_entity is not None else None
        allergen = build_codeable_concept_from_cd(code_element)
        if allergen is None:
            continue
        if not negated:
            return allergen
        display = allergen.coding[0].display if allergen.coding and allergen.coding[0].display else None
        text = f"No known allergy to {display}" if display else "No known allergies"
        return CodeableConcept(text=text)
    return CodeableConcept(text="No known allergies") if negated else None


def _build_allergy_intolerance(allergy_observation, patient_id: str) -> AllergyIntolerance | None:
    negated = allergy_observation.get("negationInd") == "true"
    code = _resolve_allergen_code(allergy_observation, negated)
    if code is None:
        # code is not FHIR-required on AllergyIntolerance, but with no
        # allergen and no negation there is nothing meaningful to report -
        # matching Problems'/Medications' "no resolvable code -> skip"
        # convention rather than emitting an empty resource.
        return None

    allergy = AllergyIntolerance(
        id=str(uuid.uuid4()),
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        code=code,
        clinicalStatus=_resolve_clinical_status(allergy_observation),
    )

    value_code = _value_code(allergy_observation)
    if value_code:
        allergy_type = _TYPE_MAP.get(value_code)
        if allergy_type:
            allergy.type = allergy_type
        category = _CATEGORY_MAP.get(value_code)
        if category:
            allergy.category = [category]

    onset, _ = ivl_ts_bounds(find_child(allergy_observation, "effectiveTime"))
    onset_dt = parse_partial_ts(onset)
    if onset_dt:
        allergy.onsetDateTime = onset_dt

    author_element = find_child(allergy_observation, "author")
    recorded_time_element = find_child(author_element, "time") if author_element is not None else None
    recorded_dt = parse_partial_ts(ts_value(recorded_time_element)) if recorded_time_element is not None else None
    if recorded_dt:
        allergy.recordedDate = recorded_dt

    criticality = _resolve_criticality(allergy_observation)
    if criticality:
        allergy.criticality = criticality

    reactions = _build_reactions(allergy_observation)
    if reactions:
        allergy.reaction = reactions

    return allergy


def build_allergy_intolerances(section, patient_id: str) -> list[AllergyIntolerance]:
    """One AllergyIntolerance per Allergy-Intolerance Observation entry in
    the section - a section can (and commonly does) have multiple entries."""
    allergies = []
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, ALLERGY_CONCERN_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            observation = find_child(relationship, "observation")
            if observation is None or not has_template_id(observation, ALLERGY_OBSERVATION_TEMPLATE_ID):
                continue
            allergy = _build_allergy_intolerance(observation, patient_id)
            if allergy is not None:
                allergies.append(allergy)
    return allergies
