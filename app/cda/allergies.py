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

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_identifiers,
    effective_time_location,
    find_nested_observation,
    parse_partial_ts,
)
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, ts_value
from app.cda.problems import STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS as _PROBLEM_STATUS_MAP
from app.provenance.location import xpath_location

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

# CF_AllergyIntoleranceType / CF_AllergyIntoleranceCategory - both keyed
# off the SAME source value (the Allergy-Intolerance Observation's
# <value>) but targeting different vocabularies, hence two dicts rather
# than one merged table. The "propensity to adverse reaction" SNOMED codes
# (418038007, and 419199007/420134006 for category only) have no row in
# either published ConceptMap. Public: the reverse direction searches both
# for a source code whose (type, category) pair matches.
TYPE_MAP = {
    "235719002": "intolerance",  # Intolerance to food
    "414285001": "allergy",  # Allergy to food
    "416098002": "allergy",  # Allergy to drug
    "419199007": "allergy",  # Allergy to substance
    "59037007": "intolerance",  # Intolerance to drug
}
CATEGORY_MAP = {
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

# The Allergy-Intolerance Observation itself - reached via the Concern
# Act's own entryRelationship[SUBJ], the identical outer-Act shape
# Problems' own build_condition already established (unlike Medications'
# plain substanceAdministration, which has no such wrapper).
_ENTRY_BASE = "act/entryRelationship[SUBJ]/observation"
_ALLERGEN_LOCATION = xpath_location(_ENTRY_BASE, "participant[CSM]", "participantRole", "playingEntity", "code")
_STATUS_OBSERVATION_LOCATION = xpath_location(_ENTRY_BASE, "entryRelationship[REFR]", "observation", "value", "@code")
_VALUE_LOCATION = xpath_location(_ENTRY_BASE, "value", "@code")
_CRITICALITY_LOCATION = xpath_location(_ENTRY_BASE, "entryRelationship[SUBJ]", "observation", "value", "@code")
_RECORDED_DATE_LOCATION = xpath_location(_ENTRY_BASE, "author", "time", "@value")
_EFFECTIVE_TIME_BASE = xpath_location(_ENTRY_BASE, "effectiveTime")


def _reaction_base(index: int) -> str:
    """The nested Reaction Observation's own relative path, for the i-th
    (0-based) entryRelationship[MFST] found - since an allergy entry can
    carry more than one reaction, a bare "entryRelationship[MFST]" alone
    would record the identical, ambiguous location for every reaction,
    the same collision risk 837I's own multi-HI-segment diagnoses already
    hit - disambiguated here proactively, the same "apply the fix
    everywhere the identical hazard structurally exists" precedent 837D's
    own diagnosis loop already established, even though neither real
    fixture currently carries more than one reaction to exercise it."""
    return xpath_location(_ENTRY_BASE, f"entryRelationship[MFST][{index}]", "observation")


def _value_code(element) -> str | None:
    value_element = find_child(element, "value") if element is not None else None
    return value_element.get("code") if value_element is not None else None


def _resolve_clinical_status(allergy_observation) -> tuple[CodeableConcept, str | None]:
    """Returns the resolved CodeableConcept alongside the source location
    that produced it - None for the disclosed fixed-default branch, the
    same "report which branch really fired" discipline
    app/mappings/adt.py's PV1-44-vs-EVN-2 resolution and
    app/cda/problems.py's own two-vocabulary clinicalStatus resolution
    already established."""
    status_observation = find_nested_observation(allergy_observation, "REFR", ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID)
    value_code = _value_code(status_observation)
    mapped = CLINICAL_STATUS_MAP.get(value_code) if value_code else None
    if mapped:
        return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code=mapped)]), _STATUS_OBSERVATION_LOCATION
    # Per the IG's own CCDA-FHIR Allergy.csv: "entryRelationship (Allergy
    # Status absent) -> clinicalStatus: fixed value 'active'". Unlike
    # Problems (which falls back to the Concern Act's own statusCode), the
    # IG marks the Allergy Concern Act's statusCode/effectiveTime "not
    # supported" entirely - there's no second vocabulary to fall back to
    # here, only this disclosed fixed default.
    return CodeableConcept(coding=[Coding(system=_CLINICAL_STATUS_SYSTEM, code="active")]), None


def _resolve_criticality(allergy_observation) -> str | None:
    criticality_observation = find_nested_observation(
        allergy_observation, "SUBJ", CRITICALITY_OBSERVATION_TEMPLATE_ID
    )
    value_code = _value_code(criticality_observation)
    return CRITICALITY_MAP.get(value_code) if value_code else None


def _build_reaction(
    reaction_observation,
    index: int,
    resource_id: str | None = None,
    relative_path: str | None = None,
    recorder=None,
) -> AllergyIntoleranceReaction | None:
    manifestation = build_codeable_concept_from_cd(find_child(reaction_observation, "value"))
    if manifestation is None:
        # manifestation is FHIR-required on AllergyIntoleranceReaction -
        # skip the reaction entirely rather than construct an invalid one,
        # matching Problems'/Medications' "no resolvable code -> skip"
        # convention.
        return None

    reaction = AllergyIntoleranceReaction(manifestation=[manifestation])
    reaction_base = _reaction_base(index)
    if recorder and resource_id and relative_path:
        coding = manifestation.coding[0]
        recorder.record(resource_id, f"{relative_path}.manifestation[0].coding[0].code", f"{reaction_base}/value/@code", coding.code)
        if coding.display:
            recorder.record(
                resource_id, f"{relative_path}.manifestation[0].coding[0].display", f"{reaction_base}/value/@displayName", coding.display
            )

    effective_time = find_child(reaction_observation, "effectiveTime")
    onset, _ = ivl_ts_bounds(effective_time)
    onset_dt = parse_partial_ts(onset)
    if onset_dt:
        reaction.onset = onset_dt
        if recorder and resource_id and relative_path:
            recorder.record(
                resource_id,
                f"{relative_path}.onset",
                effective_time_location(f"{reaction_base}/effectiveTime", effective_time, "low"),
                onset_dt,
            )

    severity_observation = find_nested_observation(reaction_observation, "SUBJ", SEVERITY_OBSERVATION_TEMPLATE_ID)
    severity_code = _value_code(severity_observation)
    severity = SEVERITY_MAP.get(severity_code) if severity_code else None
    if severity:
        reaction.severity = severity
        if recorder and resource_id and relative_path:
            recorder.record(
                resource_id,
                f"{relative_path}.severity",
                xpath_location(reaction_base, "entryRelationship[SUBJ]", "observation", "value", "@code"),
                severity,
            )

    return reaction


def _build_reactions(
    allergy_observation, resource_id: str | None = None, recorder=None
) -> list[AllergyIntoleranceReaction]:
    reactions = []
    index = 0
    for relationship in find_all(allergy_observation, "entryRelationship"):
        if relationship.get("typeCode") != "MFST":
            continue
        reaction_observation = find_child(relationship, "observation")
        if reaction_observation is None or not has_template_id(reaction_observation, REACTION_OBSERVATION_TEMPLATE_ID):
            continue
        reaction = _build_reaction(
            reaction_observation,
            index,
            resource_id=resource_id,
            relative_path=f"reaction[{len(reactions)}]",
            recorder=recorder,
        )
        index += 1
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


def _build_allergy_intolerance(allergy_observation, patient_id: str, recorder=None) -> AllergyIntolerance | None:
    negated = allergy_observation.get("negationInd") == "true"
    code = _resolve_allergen_code(allergy_observation, negated)
    if code is None:
        # code is not FHIR-required on AllergyIntolerance, but with no
        # allergen and no negation there is nothing meaningful to report -
        # matching Problems'/Medications' "no resolvable code -> skip"
        # convention rather than emitting an empty resource.
        return None

    allergy_id = str(uuid.uuid4())
    clinical_status, status_location = _resolve_clinical_status(allergy_observation)
    allergy = AllergyIntolerance(
        id=allergy_id,
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        code=code,
        clinicalStatus=clinical_status,
    )


    # The IG maps the Allergy - Intolerance Observation's own <id> as a
    # source value to AllergyIntolerance.identifier. Procedure already
    # built its identifier; this one did not, and the drop register
    # flagged it as a gap against the IG.
    identifiers = build_identifiers(
        find_all(allergy_observation, "id"),
        "urn:interop-tools:cda-allergy-id",
        resource_id=allergy_id,
        location_prefix=xpath_location(_ENTRY_BASE, "id"),
        recorder=recorder,
    )
    if identifiers:
        allergy.identifier = identifiers

    if recorder:
        if code.coding:
            # The asserted, non-negated case - a real coded allergen.
            recorder.record(allergy_id, "code.coding[0].code", f"{_ALLERGEN_LOCATION}/@code", code.coding[0].code)
            if code.coding[0].display:
                recorder.record(allergy_id, "code.coding[0].display", f"{_ALLERGEN_LOCATION}/@displayName", code.coding[0].display)
        else:
            # negated=True - a locally-constructed text pattern per the
            # IG's own disclosed "No known allergy to X"/"No known
            # allergies" wording, not read from a single source field.
            recorder.record_inferred(
                allergy_id,
                "code.text",
                "negationInd=\"true\" - no coded allergen was asserted, so code.text is built from the IG's own disclosed fixed text pattern rather than copied from a single source field.",
                code.text,
            )
        if status_location:
            recorder.record(allergy_id, "clinicalStatus.coding[0].code", status_location, clinical_status.coding[0].code)
        else:
            recorder.record_inferred(
                allergy_id,
                "clinicalStatus.coding[0].code",
                'No Status Observation was present or resolvable - the IG\'s own CCDA-FHIR Allergy.csv fixes clinicalStatus to "active" in this case, since the Allergy Concern Act\'s own statusCode/effectiveTime are marked "not supported" for this section.',
                clinical_status.coding[0].code,
            )

    value_code = _value_code(allergy_observation)
    if value_code:
        allergy_type = TYPE_MAP.get(value_code)
        if allergy_type:
            allergy.type = allergy_type
            if recorder:
                recorder.record(allergy_id, "type", _VALUE_LOCATION, allergy_type)
        category = CATEGORY_MAP.get(value_code)
        if category:
            allergy.category = [category]
            if recorder:
                recorder.record(allergy_id, "category[0]", _VALUE_LOCATION, category)

    effective_time = find_child(allergy_observation, "effectiveTime")
    onset, _ = ivl_ts_bounds(effective_time)
    onset_dt = parse_partial_ts(onset)
    if onset_dt:
        allergy.onsetDateTime = onset_dt
        if recorder:
            recorder.record(allergy_id, "onsetDateTime", effective_time_location(_EFFECTIVE_TIME_BASE, effective_time, "low"), onset_dt)

    author_element = find_child(allergy_observation, "author")
    recorded_time_element = find_child(author_element, "time") if author_element is not None else None
    recorded_dt = parse_partial_ts(ts_value(recorded_time_element)) if recorded_time_element is not None else None
    if recorded_dt:
        allergy.recordedDate = recorded_dt
        if recorder:
            recorder.record(allergy_id, "recordedDate", _RECORDED_DATE_LOCATION, recorded_dt)

    criticality = _resolve_criticality(allergy_observation)
    if criticality:
        allergy.criticality = criticality
        if recorder:
            recorder.record(allergy_id, "criticality", _CRITICALITY_LOCATION, criticality)

    reactions = _build_reactions(allergy_observation, resource_id=allergy_id, recorder=recorder)
    if reactions:
        allergy.reaction = reactions

    return allergy


def build_allergy_intolerances(section, patient_id: str, recorder=None) -> list[AllergyIntolerance]:
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
            allergy = _build_allergy_intolerance(observation, patient_id, recorder=recorder)
            if allergy is not None:
                allergies.append(allergy)
    return allergies
