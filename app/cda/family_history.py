"""Family History section (templateId 2.16.840.1.113883.10.20.22.2.15) ->
DocumentReference+Binary (the narrative, see `narrative_sections.py`) plus
one `FamilyMemberHistory` per Family History Organizer. Both
representations coexist; neither replaces the other.

**No C-CDA on FHIR page covers Family History** (no `CF-familyhistory.md`
exists, unlike Social History's `CF-social.md`), so the field mapping below
is this app's own disclosed choice - the same situation as several EDI
families. `FamilyMemberHistory` is the purpose-built target for this shape,
not a judgement call between candidates the way Plan of Treatment's was.

Source shape, confirmed against three real HL7 C-CDA-Examples:

    organizer (...4.45, classCode=CLUSTER)
      subject/relatedSubject/code            -> .relationship (v3 RoleCode,
                                                OID 2.16.840.1.113883.5.111)
      subject/relatedSubject/subject/
        administrativeGenderCode             -> .sex
        birthTime                            -> .bornDate
        sdtc:deceasedInd / sdtc:deceasedTime -> .deceased[x]
      component/observation (...4.46)        -> .condition[]

**Gotcha**: the Family History Observation's `/code` is the fixed LOINC
`75323-6` "Condition" naming the *template*, not the diagnosis - the real
diagnosis is in `/value[xsi:type=CD]`. That is the opposite of every other
CD-valued section here, where `/code` carries the concept.

Two optional nested entryRelationships:
- `SUBJ` + `inversionInd="true"` wraps an Age Observation (...4.31,
  `/value[xsi:type=PQ]`) -> `.condition[].onsetAge`.
- `CAUS` wraps a Family History Death Observation (...4.47) whose `/value`
  is always the fixed SNOMED "Dead" - so only its *presence* is the signal,
  mapping to `.condition[].contributedToDeath`.

`deceased[x]` is a choice type: `.deceasedDate` wins when a real date
resolves (strictly more informative), `.deceasedBoolean` otherwise.
Setting both raises.

This is the only module that reads an sdtc-namespaced element directly
(`{urn:hl7-org:sdtc}...`, Clark notation) rather than via `find_child`/
`find_all`, which are CDA-namespace-only by design. A narrow exception, not
a pattern to copy without the same justification.

`.status` is fixed to `"completed"` - the organizer's statusCode is fixed
by the spec, so there is no mapping ambiguity."""

import uuid
from xml.etree.ElementTree import Element

from fhir.resources.R4B.age import Age
from fhir.resources.R4B.familymemberhistory import FamilyMemberHistory, FamilyMemberHistoryCondition
from fhir.resources.R4B.reference import Reference

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_quantity_from_pq,
    record_coding,
)
from app.cda.narrative_sections import FAMILY_HISTORY_TEMPLATE_ID, build_narrative_document_reference
from app.cda.parser import find_all, find_child, has_template_id
from app.provenance.location import xpath_location

SECTION_TEMPLATE_ID = FAMILY_HISTORY_TEMPLATE_ID
ORGANIZER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.45"
OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.46"
AGE_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.31"
DEATH_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.47"

_SDTC_NS = "urn:hl7-org:sdtc"
_UCUM_SYSTEM = "http://unitsofmeasure.org"


def _sdtc_child(element: Element | None, tag: str) -> Element | None:
    if element is None:
        return None
    return element.find(f"{{{_SDTC_NS}}}{tag}")


def _format_partial_date(raw: str | None) -> str | None:
    """CDA TS @value and FHIR `date` both legitimately support partial
    precision (year, year-month, or a full date) - unlike
    app.cda.common.parse_partial_ts (built for the narrower "full
    timestamp, or a specific 8-digit date, nothing shorter" choice this
    app's other effectiveTime/onset fields need), a relative's own
    deceasedTime is commonly just a bare year (e.g. "1967", confirmed
    against a real fetched example's own commented-out sdtc:deceasedTime
    usage) - this reformats whatever precision the source value actually
    carries, inserting the dashes FHIR's own date syntax requires, rather
    than discarding a shorter-than-8-digit value as unparseable."""
    if not raw:
        return None
    digits = raw[:8]
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(digits) >= 6:
        return f"{digits[0:4]}-{digits[4:6]}"
    if len(digits) >= 4:
        return digits[0:4]
    return None


def _find_entry_relationship(observation_element, type_code: str, *, inversion_ind: bool = False):
    for relationship in find_all(observation_element, "entryRelationship"):
        if relationship.get("typeCode") != type_code:
            continue
        if inversion_ind and (relationship.get("inversionInd") or "").lower() != "true":
            continue
        yield relationship


def _build_condition(
    observation_element, resource_id: str | None = None, source_index: int = 0,
    fhir_index: int = 0, recorder=None,
):
    """`source_index` is the component's position in the organizer;
    `fhir_index` is its position in `.condition[]`. They diverge once a
    component produces no condition, and using one for both recorded facts
    against a `condition[N]` that does not exist."""
    value_element = find_child(observation_element, "value")
    code = build_codeable_concept_from_cd(value_element)
    if code is None:
        return None

    condition = FamilyMemberHistoryCondition(code=code)
    condition_base = xpath_location(f"component[{source_index}]", "observation", "value")
    if recorder and resource_id:
        recorder.record(resource_id, f"condition[{fhir_index}].code.coding[0].code", f"{condition_base}/@code", code.coding[0].code)
        if code.coding[0].display:
            recorder.record(
                resource_id, f"condition[{fhir_index}].code.coding[0].display", f"{condition_base}/@displayName", code.coding[0].display
            )

    for age_relationship in _find_entry_relationship(observation_element, "SUBJ", inversion_ind=True):
        age_observation = find_child(age_relationship, "observation")
        if age_observation is None or not has_template_id(age_observation, AGE_OBSERVATION_TEMPLATE_ID):
            continue
        age_value_element = find_child(age_observation, "value")
        age_quantity = build_quantity_from_pq(age_value_element)
        if age_quantity is None:
            continue
        condition.onsetAge = Age(
            value=age_quantity.value, unit=age_quantity.unit, system=_UCUM_SYSTEM, code=age_quantity.unit
        )
        if recorder and resource_id:
            age_base = xpath_location(f"component[{source_index}]", "observation", "entryRelationship[SUBJ]", "observation", "value")
            recorder.record(resource_id, f"condition[{fhir_index}].onsetAge.value", f"{age_base}/@value", str(age_quantity.value))
            if age_quantity.unit:
                recorder.record(resource_id, f"condition[{fhir_index}].onsetAge.unit", f"{age_base}/@unit", age_quantity.unit)
        break

    # next(..., None) is not None rather than any(...) - ElementTree's own
    # Element.__bool__ is based on child *count*, not identity, and is
    # deprecated for exactly this kind of presence check (a real
    # DeprecationWarning caught by the test suite, not a hypothetical one).
    contributed_to_death = next(_find_entry_relationship(observation_element, "CAUS"), None) is not None
    if contributed_to_death:
        condition.contributedToDeath = True
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"condition[{fhir_index}].contributedToDeath",
                xpath_location(f"component[{source_index}]", "observation", "entryRelationship[CAUS]"),
                "true",
            )

    return condition


def _build_family_member_history(organizer_element, patient_id: str, recorder=None) -> FamilyMemberHistory | None:
    subject = find_child(organizer_element, "subject")
    related_subject = find_child(subject, "relatedSubject") if subject is not None else None
    if related_subject is None:
        return None

    relationship_code_element = find_child(related_subject, "code")
    relationship = build_codeable_concept_from_cd(relationship_code_element)
    if relationship is None:
        return None

    relative_subject = find_child(related_subject, "subject")
    # Generated up front (unlike Results' own report/member ordering,
    # which genuinely needs each member's own id before the report can
    # reference it) purely so _build_condition can record its own facts in
    # a single pass rather than a build-then-rebuild one - a resource that
    # ends up discarded below (no conditions resolved at all) leaves its
    # already-recorded facts safely orphaned, the same defensive skip
    # resolve_bundle_paths already provides for exactly this case.
    history_id = str(uuid.uuid4())
    conditions = []
    for index, component in enumerate(find_all(organizer_element, "component")):
        observation_element = find_child(component, "observation")
        if observation_element is None or not has_template_id(observation_element, OBSERVATION_TEMPLATE_ID):
            continue
        condition = _build_condition(
            observation_element, resource_id=history_id, source_index=index,
            fhir_index=len(conditions), recorder=recorder,
        )
        if condition is not None:
            conditions.append(condition)
    if not conditions:
        return None

    history = FamilyMemberHistory(
        id=history_id,
        status="completed",
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        relationship=relationship,
        condition=conditions,
    )

    if recorder:
        relationship_code = relationship_code_element.get("code")
        relationship_display = relationship_code_element.get("displayName")
        if relationship_code:
            recorder.record(
                history_id, "relationship.coding[0].code", xpath_location("subject", "relatedSubject", "code", "@code"), relationship_code
            )
        if relationship_display:
            recorder.record(
                history_id,
                "relationship.coding[0].display",
                xpath_location("subject", "relatedSubject", "code", "@displayName"),
                relationship_display,
            )
        recorder.record_inferred(
            history_id,
            "status",
            'The Family History Organizer template fixes statusCode to "completed" per the spec itself - never read from a variable source field.',
            "completed",
        )

    if relative_subject is not None:
        gender_element = find_child(relative_subject, "administrativeGenderCode")
        sex = build_codeable_concept_from_cd(gender_element)
        if sex:
            history.sex = sex
            record_coding(
                recorder,
                history_id,
                "sex",
                xpath_location("subject", "relatedSubject", "subject", "administrativeGenderCode"),
                sex,
            )

        birth_time = find_child(relative_subject, "birthTime")
        born_date = _format_partial_date(birth_time.get("value")) if birth_time is not None else None
        if born_date:
            history.bornDate = born_date
            if recorder:
                recorder.record(
                    history_id,
                    "bornDate",
                    xpath_location("subject", "relatedSubject", "subject", "birthTime", "@value"),
                    born_date,
                    source_value=birth_time.get("value"),
                )

        deceased_ind = _sdtc_child(relative_subject, "deceasedInd")
        if deceased_ind is not None:
            deceased = (deceased_ind.get("value") or "").strip().lower() == "true"
            # deceased[x] is a FHIR choice type - deceasedBoolean and
            # deceasedDate can't both be set at once (confirmed by direct
            # construction, a real pydantic ValidationError otherwise).
            # When a real deceasedTime also resolves, it's strictly more
            # informative than the bare boolean (a date implies deceased),
            # so it wins; deceasedBoolean is the fallback for
            # deceasedInd-with-no-date or explicitly-not-deceased.
            deceased_time = _sdtc_child(relative_subject, "deceasedTime")
            deceased_date = _format_partial_date(deceased_time.get("value")) if deceased_time is not None else None
            if deceased and deceased_date:
                history.deceasedDate = deceased_date
                if recorder:
                    # Direct, not inferred: this is read from a real source
                    # attribute. Recording it as inferred said no source
                    # field produced it, which then made sdtc:deceasedTime
                    # itself look like dropped data.
                    recorder.record(
                        history_id,
                        "deceasedDate",
                        xpath_location("subject", "relatedSubject", "subject", "deceasedTime", "@value"),
                        deceased_date,
                        source_value=deceased_time.get("value"),
                    )
            else:
                history.deceasedBoolean = deceased
                if recorder:
                    recorder.record(
                        history_id,
                        "deceasedBoolean",
                        xpath_location("subject", "relatedSubject", "subject", "deceasedInd", "@value"),
                        str(deceased),
                        source_value=deceased_ind.get("value"),
                    )

    return history


def build_family_history_resources(section, patient_id: str, recorder=None) -> list:
    """The narrative DocumentReference+Binary (always built) plus one
    FamilyMemberHistory per Family History Organizer entry whose own
    relationship and at least one condition both resolve - matching
    narrative_sections.py's own disclosed "coexist, don't replace" design."""
    resources = list(build_narrative_document_reference(section, patient_id, recorder=recorder))
    for entry in find_all(section, "entry"):
        organizer_element = find_child(entry, "organizer")
        if organizer_element is None or not has_template_id(organizer_element, ORGANIZER_TEMPLATE_ID):
            continue
        history = _build_family_member_history(organizer_element, patient_id, recorder=recorder)
        if history is not None:
            resources.append(history)
    return resources
