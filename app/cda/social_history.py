"""Social History section (templateId 2.16.840.1.113883.10.20.22.2.17) ->
DocumentReference+Binary (the narrative, see `narrative_sections.py`) plus
one `Observation` per recognized structured entry. Both representations
coexist; neither replaces the other.

Per the IG's published CF-social guidance: target is `Observation`,
`.category` fixed to `social-history`, `.status` fixed to `final` (C-CDA's
statusCode is always `completed` for this template, so there is no mapping
ambiguity), and `.value[x]` dispatches on the entry's `xsi:type` - PQ, CD,
INT and ST are mapped, IVL_PQ is deferred. Both the base Social History
Observation (...4.38) and the Smoking-Status specialisation (...4.78) are
recognized against one builder, being field-for-field identical.

**Some observations map to a Patient extension instead**, and the IG is
explicit that "a FHIR Observation should not be created; instead, the
corresponding extension should be used". `apply_patient_extensions` handles
those, and they are excluded from the Observation walk:

    Birth Sex (...22.4.200)          -> us-core-birthsex        valueCode
    Sex (...22.4.507)                -> us-core-sex             valueCode
    Gender Identity (...34.3.45)     -> us-core-genderIdentity  valueCodeableConcept

**Gender Identity specialises Social History Observation and so declares
BOTH templateIds** (its own SD has `templateId min:2` over base
SocialHistoryObservation), which is why excluding it has to be explicit -
matching the generic templateId alone converted it into exactly the plain
Observation the IG forbids.

It takes the Patient resource rather than `patient.id`, which is why it is
a separate entry point called from `build_sectioned_bundle` rather than
part of the registered section builder: SECTION_BUILDERS hands builders
only the id, and these entries have no resource of their own to return.

Scope limits:
- **Tribal Affiliation (...22.4.506)** is excluded from the Observation
  walk but not mapped. Its US Core extension is a complex one - a
  `tribalAffiliation` CodeableConcept plus an `isEnrolled` boolean - and
  the C-CDA template publishes no element to read the enrolled flag from,
  so it is left to the drop register rather than guessed at. Excluding it
  is still right: it also specialises Social History Observation, so
  without that it becomes the Observation the IG says not to create.
- Pregnancy Status and Pregnancy Intent have their own US Core profiles
  and their own moodCode-driven split; not attempted here."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.reference import Reference

from fhir.resources.R4B.extension import Extension

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_quantity_from_pq,
    effective_time_location,
    parse_partial_ts,
    record_coding,
)

from app.cda.narrative_sections import SOCIAL_HISTORY_TEMPLATE_ID, build_narrative_document_reference
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, xsi_type
from app.provenance.location import xpath_location

# Public (not module-private) - matches this app's established pattern of
# exposing every section's own templateId(s) for registry/validation reuse.
SECTION_TEMPLATE_ID = SOCIAL_HISTORY_TEMPLATE_ID
OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.38"
SMOKING_STATUS_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.78"

# Observations the IG sends to a Patient extension instead of a FHIR
# Observation: "in these cases, a FHIR Observation should not be created;
# instead, the corresponding extension should be used."
#
# Gender Identity and Tribal Affiliation specialise Social History
# Observation and so declare OBSERVATION_TEMPLATE_ID as well as their own
# (templateId min:2, base SocialHistoryObservation) - which is why they
# must be excluded from the generic walk explicitly. Birth Sex and Sex
# derive from plain Observation and were simply never picked up.
BIRTH_SEX_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.200"
GENDER_IDENTITY_TEMPLATE_ID = "2.16.840.1.113883.10.20.34.3.45"
SEX_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.507"
TRIBAL_AFFILIATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.506"

US_CORE_BIRTHSEX_EXTENSION = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-birthsex"
US_CORE_GENDER_IDENTITY_EXTENSION = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-genderIdentity"
US_CORE_SEX_EXTENSION = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-sex"

# Excluded from the generic Observation walk. Tribal Affiliation is in
# this set but has no builder below: its US Core extension is a complex
# one (a tribalAffiliation CodeableConcept plus an isEnrolled boolean),
# and the C-CDA template publishes no element this app could read the
# enrolled flag from - so it is left unmapped and shows up honestly in the
# drop register, rather than being emitted as the plain social-history
# Observation the IG says not to create.
_PATIENT_EXTENSION_TEMPLATE_IDS = (
    BIRTH_SEX_TEMPLATE_ID,
    GENDER_IDENTITY_TEMPLATE_ID,
    SEX_TEMPLATE_ID,
    TRIBAL_AFFILIATION_TEMPLATE_ID,
)

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


def apply_patient_extensions(section, patient, recorder=None) -> None:
    """Attach the US Core Patient extensions this section carries.

    Called with the Patient resource itself rather than its id, which is
    why this is a separate entry point instead of part of the registered
    section builder - SECTION_BUILDERS hands builders only `patient.id`,
    and these observations have no resource of their own to return.

    Mutates `patient` in place and returns nothing; an unrecognised or
    unresolvable observation is left alone rather than guessed at.
    """
    for entry in find_all(section, "entry"):
        observation_element = find_child(entry, "observation")
        if observation_element is None:
            continue
        value_element = find_child(observation_element, "value")
        if value_element is None:
            continue

        if has_template_id(observation_element, BIRTH_SEX_TEMPLATE_ID):
            _add_code_extension(
                patient, US_CORE_BIRTHSEX_EXTENSION, value_element, "birthsex", recorder
            )
        elif has_template_id(observation_element, SEX_TEMPLATE_ID):
            _add_code_extension(patient, US_CORE_SEX_EXTENSION, value_element, "sex", recorder)
        elif has_template_id(observation_element, GENDER_IDENTITY_TEMPLATE_ID):
            concept = build_codeable_concept_from_cd(value_element)
            if concept is None:
                continue
            index = len(patient.extension or [])
            patient.extension = (patient.extension or []) + [
                Extension(url=US_CORE_GENDER_IDENTITY_EXTENSION, valueCodeableConcept=concept)
            ]
            if recorder:
                record_coding(
                    recorder,
                    patient.id,
                    f"extension[{index}].valueCodeableConcept",
                    xpath_location("entry", "observation", "value"),
                    concept,
                )


def _add_code_extension(patient, url: str, value_element, label: str, recorder) -> None:
    """A US Core extension whose value[x] is a bare `code` (birthsex, sex),
    taken from the source `<value>`'s own @code."""
    code = (value_element.get("code") or "").strip()
    if not code:
        return
    index = len(patient.extension or [])
    patient.extension = (patient.extension or []) + [Extension(url=url, valueCode=code)]
    if recorder:
        recorder.record(
            patient.id,
            f"extension[{index}].valueCode",
            xpath_location("entry", "observation", "value", "@code"),
            code,
        )


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
        if any(
            has_template_id(observation_element, template_id)
            for template_id in _PATIENT_EXTENSION_TEMPLATE_IDS
        ):
            # Belongs on Patient as an extension - see apply_patient_extensions.
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
