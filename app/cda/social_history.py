"""Social History section (templateId 2.16.840.1.113883.10.20.22.2.17) ->
DocumentReference+Binary (the narrative, see app/cda/narrative_sections.py)
*plus* one Observation per structured Social History Observation entry
(e.g. Smoking Status) - the first of narrative_sections.py's own three
disclosed "can carry real structured entries" sections to actually gain
one, following that module's own precedent ("a future slice could add real
structured resources alongside this narrative one without conflict").

**Per the real, published "C-CDA on FHIR" Social History mapping**
(github.com/HL7/ccda-on-fhir/blob/master/input/pagecontent/CF-social.md -
confirmed by fetching it directly, not assumed to not exist just because
no CF-socialhistory.html/CF-familyhistory.html page exists the way CF-
vitals.html/CF-results.html do): target resource is Observation,
`.category` is fixed to `social-history` (the FHIR observation-category
CodeSystem), `.status` is fixed to `final` (C-CDA's own statusCode is
always `completed` for this template - confirmed against a real fetched
example, no ConceptMap needed, the same "no status-mapping ambiguity at
all" precedent app/cda/vitals.py already established), and `.value[x]`
dispatches on the entry's own /value xsi:type.

**Source shape confirmed against a real fetched HL7 C-CDA-Examples Social
History Observation** (`code="160573003"` Alcohol intake, SNOMED,
`value[xsi:type=PQ] value="12" unit="/d"`) and a real fetched Smoking
Status - Meaningful Use (V2) example (`code="72166-2"` Tobacco smoking
status NHIS, LOINC, `value[xsi:type=CD] code="8517006"` Ex-smoker, SNOMED)
- both wrap the identical Social History Observation template
(2.16.840.1.113883.10.20.22.4.38), Smoking Status being a same-shape
specialization of it (a distinct templateId, `...4.78`, but a field-for-
field identical entry otherwise), registered against the same builder here
rather than duplicated.

**Disclosed scope limit, matching the mapping guide's own "Observations to
Extensions" special-case list**: Birth Sex/Gender Identity/Sex/Tribal
Affiliation Observations are meant to become US Core *extensions* on
Patient, not their own Observation resources - this app has never built a
Patient extension anywhere, so those four are deliberately left unparsed
here (an entry with one of their own templateIds, if ever present, is
simply not recognized by OBSERVATION_TEMPLATE_ID and silently skipped,
the same "unrecognized entry shape, don't guess" precedent every other
section builder in this app already follows). Pregnancy Observation/
Pregnancy Intention (also covered by the same real IG page) are likewise
deferred - this app has no Pregnancy-specific FHIR shape built anywhere
else either.

**Of the value xsi:types the mapping guide documents, PQ/CD/INT/ST are
mapped; IVL_PQ (a value *range*, not a fixed value) is deferred** - the
same "map the dominant scalar/coded shape now, disclose the range as a
later slice" judgment app/cda/results.py's own identical IVL_PQ gap
already established for Results' own /value dispatch."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, build_quantity_from_pq, effective_time_location, parse_partial_ts
from app.cda.narrative_sections import SOCIAL_HISTORY_TEMPLATE_ID, build_narrative_document_reference
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, xsi_type
from app.provenance.location import xpath_location

# Public (not module-private) - matches this app's established pattern of
# exposing every section's own templateId(s) for registry/validation reuse.
SECTION_TEMPLATE_ID = SOCIAL_HISTORY_TEMPLATE_ID
OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.38"
SMOKING_STATUS_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.78"

CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
CATEGORY_CODE = "social-history"
_ENTRY_BASE = "observation"


def _build_observation_value(observation_element, resource_id: str, recorder=None) -> dict:
    """The Social History mirror of app/cda/results.py's own identically-
    named function - same per-xsi:type dispatch, same "record against the
    real chosen field" discipline, reused rather than imported since
    Results' own version is parameterized around a member_base/index shape
    this section doesn't have (a flat entry list, no organizer/component
    nesting)."""
    value_element = find_child(observation_element, "value")
    if value_element is None:
        return {}
    value_type = xsi_type(value_element)
    value_base = xpath_location(_ENTRY_BASE, "value")
    if value_type == "PQ":
        quantity = build_quantity_from_pq(value_element)
        if not quantity:
            return {}
        if recorder:
            recorder.record(resource_id, "valueQuantity.value", f"{value_base}/@value", value_element.get("value"))
            if quantity.unit:
                recorder.record(resource_id, "valueQuantity.unit", f"{value_base}/@unit", quantity.unit)
        return {"valueQuantity": quantity}
    if value_type == "CD":
        concept = build_codeable_concept_from_cd(value_element)
        if not concept:
            return {}
        if recorder:
            recorder.record(resource_id, "valueCodeableConcept.coding[0].code", f"{value_base}/@code", concept.coding[0].code)
            if concept.coding[0].display:
                recorder.record(
                    resource_id,
                    "valueCodeableConcept.coding[0].display",
                    f"{value_base}/@displayName",
                    concept.coding[0].display,
                )
        return {"valueCodeableConcept": concept}
    if value_type == "INT":
        raw = value_element.get("value")
        if raw is None:
            return {}
        try:
            parsed = int(raw)
        except ValueError:
            return {}
        if recorder:
            recorder.record(resource_id, "valueInteger", f"{value_base}/@value", raw)
        return {"valueInteger": parsed}
    if value_type == "ST":
        text = (value_element.text or "").strip()
        if not text:
            return {}
        if recorder:
            recorder.record(resource_id, "valueString", f"{value_base}/text()", text)
        return {"valueString": text}
    return {}


def _build_social_history_observation(observation_element, patient_id: str, recorder=None) -> Observation | None:
    code_element = find_child(observation_element, "code")
    code = build_codeable_concept_from_cd(code_element)
    if code is None:
        return None

    observation_id = str(uuid.uuid4())
    category = CodeableConcept(coding=[Coding(system=CATEGORY_SYSTEM, code=CATEGORY_CODE)])
    observation = Observation(
        id=observation_id,
        status="final",
        category=[category],
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    if recorder:
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(observation_id, "code.coding[0].code", xpath_location(_ENTRY_BASE, "code", "@code"), code_value)
        if display_value:
            recorder.record(
                observation_id, "code.coding[0].display", xpath_location(_ENTRY_BASE, "code", "@displayName"), display_value
            )
        # C-CDA's own statusCode is fixed to "completed" for this template
        # (confirmed, not just this app's own default) - the same "no
        # status-mapping ambiguity at all" case app/cda/vitals.py's own
        # organizer/observation statusCode already established.
        recorder.record_inferred(
            observation_id,
            "status",
            'The Social History Observation template fixes statusCode to "completed" per the spec itself, mapping to the fixed FHIR value "final" - never read from a variable source field.',
            "final",
        )
        recorder.record_inferred(
            observation_id,
            "category[0].coding[0].code",
            'Fixed per the "C-CDA on FHIR" Social History mapping guide - every Social History Observation gets the identical category, never read from a source field.',
            CATEGORY_CODE,
        )

    effective_time = find_child(observation_element, "effectiveTime")
    effective, _ = ivl_ts_bounds(effective_time)
    effective_dt = parse_partial_ts(effective)
    if effective_dt:
        observation.effectiveDateTime = effective_dt
        if recorder:
            recorder.record(
                observation_id,
                "effectiveDateTime",
                effective_time_location(_ENTRY_BASE + "/effectiveTime", effective_time, "low"),
                effective_dt,
            )

    for key, value in _build_observation_value(observation_element, observation_id, recorder=recorder).items():
        setattr(observation, key, value)

    return observation


def build_social_history_resources(section, patient_id: str, recorder=None) -> list:
    """The narrative DocumentReference+Binary (always built, matching every
    other narrative-only section's own unconditional behavior) plus one
    Observation per recognized structured entry - both representations
    coexist for this section, neither replacing the other, per narrative_
    sections.py's own disclosed design."""
    resources = list(build_narrative_document_reference(section, patient_id, recorder=recorder))
    for entry in find_all(section, "entry"):
        observation_element = find_child(entry, "observation")
        if observation_element is None:
            continue
        if not (
            has_template_id(observation_element, OBSERVATION_TEMPLATE_ID)
            or has_template_id(observation_element, SMOKING_STATUS_TEMPLATE_ID)
        ):
            continue
        observation = _build_social_history_observation(observation_element, patient_id, recorder=recorder)
        if observation is not None:
            resources.append(observation)
    return resources
