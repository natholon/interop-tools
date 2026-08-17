"""Vital Signs section (templateId 2.16.840.1.113883.10.20.22.2.4.1) ->
Observation, per the official "C-CDA on FHIR" IG's CF-vitals.html guidance
(github.com/HL7/ccda-on-fhir/blob/master/input/pagecontent/CF-vitals.md) -
no CSV mapping table is published for this section the way Problems/
Medications/Allergies/Immunizations/Procedures each have one; this
module's field mapping is drawn directly from that markdown page's own
tables and worked examples, fetched and read directly rather than
paraphrased from a secondary source. The section templateId itself was
confirmed against a real HL7 C-CDA-Examples CCD document (not assumed from
the IG page alone, which only gives the section's LOINC code via an
abbreviated XPath, not its templateId OID).

C-CDA groups vitals into a Vital Signs Organizer (one per reading session)
wrapping one or more individual Vital Sign Observations. The IG maps this
to a FHIR Observation "panel" (code=85353-1, the fixed LOINC "Vital Signs
Panel" code - the organizer's own /code, e.g. 74728-7 "Vital signs" or
SNOMED 46680005, is narrative-only in the source and not carried into any
FHIR field) whose .hasMember references one Observation per individual
vital sign - mirrored here exactly, with every resource returned as its
own separate, top-level Bundle entry (this app's established convention,
never FHIR .contained - the same shape app/mappings/oru.py already uses
for DiagnosticReport + its own Observation results on the HL7v2 side).

**Disclosed scope limit, decided up front**: the IG's own Blood Pressure
(systolic 8480-6 + diastolic 8462-4 grouped as .component under one
85354-9 "Blood Pressure Panel" Observation) and Pulse Oximetry (O2
saturation + flow rate + concentration similarly grouped) special cases
are NOT implemented this slice - correlating specific sibling LOINC codes
within one organizer into a single grouped Observation is real, additional
complexity distinct from the general 1:1 mapping every other vital sign
uses. Every Vital Sign Observation instead maps 1:1 to its own Observation
- still valid, useful, correctly-coded data (just not US-Core-profile-
conformant for those two specific vital types, which real consumers
expecting the grouped panel shape would need to reconstruct themselves).
A future slice can add the grouping without changing anything shipped
here, the same "map the general case now, disclose the special case as a
later slice" precedent this app already applied to Medications' IVL_PQ
dosing ranges and Immunizations' INT-mood entries."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, build_quantity_from_pq, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.4.1"
ORGANIZER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.26"
OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.27"

_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
_CATEGORY_CODE = "vital-signs"
# Vital Signs Panel - fixed per the IG's own "C-CDA Vital Signs Organizer
# to FHIR Observation Panel" table ("Set to 85353-1"), not derived from
# the source organizer's own /code.
_PANEL_CODE_SYSTEM = "http://loinc.org"
_PANEL_CODE = "85353-1"

# C-CDA's statusCode is fixed to "completed" for both the Vital Signs
# Organizer and each Vital Sign Observation per the C-CDA spec itself (not
# just this IG's own disclosed default) - the IG's own tables state this
# plainly ("`final` (C-CDA is fixed to `completed`)"), so there's no
# ConceptMap to build, unlike Results'/Procedures' own genuinely-variable
# statusCode.
_FIXED_STATUS = "final"


def _category() -> CodeableConcept:
    return CodeableConcept(coding=[Coding(system=_CATEGORY_SYSTEM, code=_CATEGORY_CODE)])


def _build_vital_sign_observation(observation_element, patient_id: str) -> Observation | None:
    code = build_codeable_concept_from_cd(find_child(observation_element, "code"))
    if code is None:
        # No resolvable coded value - skip the entry, matching every other
        # section's own "no resolvable code -> skip" convention (Problems,
        # Medications, Allergies, Immunizations).
        return None

    observation = Observation(
        id=str(uuid.uuid4()),
        status=_FIXED_STATUS,
        category=[_category()],
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    effective, _ = ivl_ts_bounds(find_child(observation_element, "effectiveTime"))
    effective_dt = parse_partial_ts(effective)
    if effective_dt:
        observation.effectiveDateTime = effective_dt

    value = build_quantity_from_pq(find_child(observation_element, "value"))
    if value:
        observation.valueQuantity = value

    interpretation = build_codeable_concept_from_cd(find_child(observation_element, "interpretationCode"))
    if interpretation:
        observation.interpretation = [interpretation]

    method = build_codeable_concept_from_cd(find_child(observation_element, "methodCode"))
    if method:
        observation.method = method

    body_site = build_codeable_concept_from_cd(find_child(observation_element, "targetSiteCode"))
    if body_site:
        observation.bodySite = body_site

    return observation


def build_vital_signs(section, patient_id: str) -> list[Observation]:
    """One panel Observation per Vital Signs Organizer entry (its own
    .hasMember referencing one Observation per individual Vital Sign
    Observation), plus each of those individual Observations - all
    returned as a flat list of separate, top-level resources. An organizer
    whose every child observation lacks a resolvable code produces no
    panel either (nothing to group), matching the "no resolvable code ->
    skip" convention at the organizer level too, not just the leaf level."""
    observations: list[Observation] = []
    for entry in find_all(section, "entry"):
        organizer = find_child(entry, "organizer")
        if organizer is None or not has_template_id(organizer, ORGANIZER_TEMPLATE_ID):
            continue

        member_observations = []
        for component in find_all(organizer, "component"):
            observation_element = find_child(component, "observation")
            if observation_element is None or not has_template_id(observation_element, OBSERVATION_TEMPLATE_ID):
                continue
            observation = _build_vital_sign_observation(observation_element, patient_id)
            if observation is not None:
                member_observations.append(observation)

        if not member_observations:
            continue

        panel = Observation(
            id=str(uuid.uuid4()),
            status=_FIXED_STATUS,
            category=[_category()],
            code=CodeableConcept(coding=[Coding(system=_PANEL_CODE_SYSTEM, code=_PANEL_CODE)]),
            subject=Reference(reference=f"urn:uuid:{patient_id}"),
            hasMember=[Reference(reference=f"urn:uuid:{member.id}") for member in member_observations],
        )

        # If organizer/effectiveTime is missing, the IG's own guidance says
        # to fall back to the earliest/latest observation effectiveTime -
        # disclosed and deferred (every real example fetched while
        # verifying this module carried its own organizer-level
        # effectiveTime, making this the rare case, not the common one).
        organizer_effective, _ = ivl_ts_bounds(find_child(organizer, "effectiveTime"))
        panel_effective_dt = parse_partial_ts(organizer_effective)
        if panel_effective_dt:
            panel.effectiveDateTime = panel_effective_dt

        observations.append(panel)
        observations.extend(member_observations)

    return observations
