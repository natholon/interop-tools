"""Results section (templateId 2.16.840.1.113883.10.20.22.2.3.1) ->
DiagnosticReport + Observation, per the official "C-CDA on FHIR" IG's
CF-results.html guidance (github.com/HL7/ccda-on-fhir/blob/master/input/
pagecontent/CF-results.md) plus its two published ConceptMaps
(ConceptMap-CF-ResultReportStatus, ConceptMap-CF-ResultStatus, both under
github.com/HL7/ccda-on-fhir/blob/master/input/maps/) - no CSV mapping
table is published for Results the way Problems/Medications/Allergies/
Immunizations/Procedures each have one; this module's field mapping is
drawn directly from the markdown page's own tables and the two fetched
ConceptMaps, not paraphrased from a secondary source. Organizer/
Observation templateIds and the section templateId itself were confirmed
against a real HL7 C-CDA-Examples CCD document, not assumed from the IG
page's own abbreviated XPath (which gives only the section's LOINC code,
not its templateId OID).

C-CDA groups results into a Result Organizer (one per panel, e.g. a CBC)
wrapping one or more Result Observations. The IG maps the organizer to a
FHIR DiagnosticReport whose .result references one Observation per child
Result Observation - mirrored here with every resource returned as its
own separate, top-level Bundle entry (this app's established convention,
never FHIR .contained - the same shape app/mappings/oru.py already builds
for OBR->DiagnosticReport/OBX->Observation on the HL7v2 side, and
app/cda/vitals.py just established for this same document type).

**The two ConceptMaps are genuinely value-identical** (both map the same
six CDA ActStatus codes to registered/final/cancelled), confirmed by
fetching both directly rather than assumed from the fact they cover the
same source vocabulary - kept as one shared dict here since they really
are the same crosswalk, just feeding two different target fields
(DiagnosticReport.status and Observation.status both happen to accept the
exact codes this map produces).

**`category`** (both organizer and observation): the IG's own guidance
requires a live LOINC CLASSTYPE terminology-server lookup this app has no
integration for anywhere (confirmed - not assumed - by fetching CF-
results.md directly: "Query a FHIR server via CodeSystem/$lookup..."), and
bulk-embedding the full licensed LOINC database to do this per-code is a
genuinely different category of dependency than anything else in this
app (every other local judgment-call table here is a small, hand-curated
set scoped to what's actually used, not a bulk terminology import) -
confirmed with the user directly as out of scope. `category` is instead
recorded as an unconditional, disclosed `"laboratory"` default (CLASSTYPE
1, the dominant real-world case for a C-CDA Results section) via
`record_inferred`, the same "default to the most common real value when
no verified per-item signal exists" precedent this app's own
`DEFAULT_PURPOSE`/`DEFAULT_CLAIM_TYPE` (EDI 270/278) already established.

**`specimen`**: `app/cda/results.py::_build_specimen` (see below) builds a
real `Specimen` resource from CDA's own `Specimen`/`SpecimenRole`/
`SpecimenPlayingEntity` classes (confirmed via their own real, directly-
fetched CDA-core structuredefs, not assumed from field-name similarity to
anything else in this app) plus the sibling **Specimen Collection
Procedure** (`component/procedure[code/@code=17636008]`, the fixed
SNOMED code identifying it) for `.collection.bodySite`. Per the IG's own
explicit attachment rule: a `<specimen>` found on the **organizer**
attaches to `DiagnosticReport.specimen` *and* becomes the default
`Observation.specimen` for every child result Observation; a `<specimen>`
found on an individual **observation** builds its own separate `Specimen`
and overrides that default for that one Observation only.

**`/value` xsi:type coverage**: of the six the IG documents, PQ/CD-family/
INT/REAL/ST/ED are now all mapped - ED's own plain-text (narrative-
reference) case is structurally identical to ST once considered on its
own terms, so it shares that branch; a genuine binary/attachment ED value
(the IG's own literal target for that case is a formal FHIR R5-backport
extension this app could not independently verify resolves to a real,
live StructureDefinition on any path tried - confirmed with the user
directly as out of scope for that reason) still has no `.value[x]`
mapped. **`IVL_PQ`** (a value *range*, not a fixed value) is now mapped
too, per `mappingGuidance.md`'s own "Ranges of Physical Quantities"
section (fetched directly, with its own worked examples): both bounds
present -> `.valueRange`; only one bound present -> `.valueQuantity` with
a `comparator` (`<=`/`<` for a high-only bound, `>=`/`>` for a low-only
bound, based on that bound's own `@inclusive` attribute, default `true`).

**Disclosed scope limits that remain**: `author`->Provenance is still
deferred - this app has never built a CDA-side Provenance resource
anywhere. `referenceRange.text` (narrative-referenced, not resolvable
without this app's disclosed narrative-`<text>` gap) is deferred;
`referenceRange`'s own `IVL_PQ` low/high is mapped."""

import uuid

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.range import Range
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.specimen import Specimen, SpecimenCollection

from app.cda.common import build_codeable_concept_from_cd, build_identifiers, build_quantity_from_pq, effective_time_location, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, xsi_type
from app.provenance.location import xpath_location

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.3.1"
# The "entries optional" sibling section - see app/cda/vitals.py's own
# docstring for how this was found (a real official HL7 History and
# Physical example) and app/cda/procedures.py's for why it's registered
# defensively rather than only when directly observed standalone.
SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.3"
ORGANIZER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.1"
OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.2"

# CF_ResultReportStatus / CF_ResultStatus ConceptMaps - see module
# docstring for why these are genuinely identical, not just similar.
STATUS_MAP = {
    "aborted": "cancelled",
    "active": "registered",
    "cancelled": "cancelled",
    "completed": "final",
    "held": "registered",
    "suspended": "registered",
}
_DEFAULT_STATUS = "unknown"

# No live LOINC CLASSTYPE lookup exists anywhere in this app - see module
# docstring for the full reasoning behind this disclosed default.
_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
_CATEGORY_CODE = "laboratory"

_SPECIMEN_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-specimen-id"
# Specimen Collection Procedure's own fixed SNOMED CT code, per
# CF-results.md's own "C-CDA Specimen to FHIR Specimen" table. Public (not
# module-private) - app/transform/cda_ccd.py became a real reverse-
# direction consumer, reusing this same fixed code to regenerate the
# sibling Specimen Collection Procedure component rather than a second,
# independently-drifting copy.
SPECIMEN_COLLECTION_PROCEDURE_CODE = "17636008"
SPECIMEN_COLLECTION_PROCEDURE_CODE_SYSTEM = "2.16.840.1.113883.6.96"


def _resolve_status(element) -> str:
    status_element = find_child(element, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _record_status(recorder, resource_id: str, element, status: str, base: str) -> None:
    """Direct when a real, recognized statusCode drove the result, inferred
    with the disclosed "unknown" fallback otherwise - the same "direct
    when read, inferred when the same table's own fallback fired"
    distinction every other STATUS_MAP-driven field in this app already
    established (e.g. Medications' own status recording)."""
    if not recorder:
        return
    status_element = find_child(element, "statusCode")
    code = status_element.get("code") if status_element is not None else None
    if code and code.strip().lower() in STATUS_MAP:
        recorder.record(resource_id, "status", xpath_location(base, "statusCode", "@code"), status)
    else:
        recorder.record_inferred(
            resource_id,
            "status",
            f'statusCode was absent or not one of the six recognized CF_Result(Report)Status codes - defaults to the disclosed fallback "{_DEFAULT_STATUS}".',
            status,
        )


def _category() -> CodeableConcept:
    return CodeableConcept(coding=[Coding(system=_CATEGORY_SYSTEM, code=_CATEGORY_CODE)])


def _record_category(recorder, resource_id: str) -> None:
    if not recorder:
        return
    recorder.record_inferred(
        resource_id,
        "category[0].coding[0].code",
        'Results category requires a live LOINC CLASSTYPE terminology-server lookup this app has no integration for anywhere - defaults to "laboratory", the dominant real-world case for a C-CDA Results section.',
        _CATEGORY_CODE,
    )


def _member_base(index: int) -> str:
    """The i-th (0-based) component's own nested Observation - see
    app/cda/vitals.py's own _member_base for why this disambiguation is
    needed whenever an organizer wraps more than one member."""
    return xpath_location("organizer", f"component[{index}]", "observation")


def _build_reference_range(
    observation_element, resource_id: str | None = None, member_base: str | None = None, recorder=None
) -> list[ObservationReferenceRange]:
    ranges = []
    for range_index, reference_range in enumerate(find_all(observation_element, "referenceRange")):
        observation_range = find_child(reference_range, "observationRange")
        if observation_range is None:
            continue
        value_element = find_child(observation_range, "value")
        if value_element is None or xsi_type(value_element) != "IVL_PQ":
            continue
        low_element = find_child(value_element, "low")
        high_element = find_child(value_element, "high")
        low = build_quantity_from_pq(low_element)
        high = build_quantity_from_pq(high_element)
        if low is None and high is None:
            continue
        observation_reference_range = ObservationReferenceRange()
        range_path = f"referenceRange[{len(ranges)}]"
        range_base = xpath_location(member_base, f"referenceRange[{range_index}]", "observationRange", "value") if member_base else None
        if low is not None:
            observation_reference_range.low = low
            if recorder and resource_id and range_base:
                recorder.record(resource_id, f"{range_path}.low.value", f"{range_base}/low/@value", low_element.get("value"))
        if high is not None:
            observation_reference_range.high = high
            if recorder and resource_id and range_base:
                recorder.record(resource_id, f"{range_path}.high.value", f"{range_base}/high/@value", high_element.get("value"))
        ranges.append(observation_reference_range)
    return ranges


def _build_ivl_pq_value(value_element, resource_id: str | None = None, value_base: str | None = None, recorder=None) -> dict:
    """/value[xsi:type=IVL_PQ] -> .valueRange (both bounds present) or
    .valueQuantity+comparator (one bound only) - per mappingGuidance.md's
    own "Ranges of Physical Quantities" section, fetched directly with its
    own worked examples. A missing low, or a low of exactly the IVL_PQ's
    own default, is not specially detected here - only which of low/high
    actually resolves to a real value decides which shape is built,
    matching the guidance's own examples exactly."""
    low_element = find_child(value_element, "low")
    high_element = find_child(value_element, "high")
    low = build_quantity_from_pq(low_element)
    high = build_quantity_from_pq(high_element)
    if low is not None and high is not None:
        range_value = Range(low=low, high=high)
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueRange.low.value", f"{value_base}/low/@value", low_element.get("value"))
            recorder.record(resource_id, "valueRange.high.value", f"{value_base}/high/@value", high_element.get("value"))
        return {"valueRange": range_value}
    if high is not None:
        inclusive = high_element.get("inclusive", "true") != "false"
        quantity = Quantity(value=high.value, unit=high.unit, comparator="<=" if inclusive else "<")
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueQuantity.value", f"{value_base}/high/@value", high_element.get("value"))
        return {"valueQuantity": quantity}
    if low is not None:
        inclusive = low_element.get("inclusive", "true") != "false"
        quantity = Quantity(value=low.value, unit=low.unit, comparator=">=" if inclusive else ">")
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueQuantity.value", f"{value_base}/low/@value", low_element.get("value"))
        return {"valueQuantity": quantity}
    return {}


def _build_observation_value(
    observation_element, resource_id: str | None = None, member_base: str | None = None, recorder=None
) -> dict:
    """Return the kwarg for whichever Observation.value[x] choice fits the
    /value element's own xsi:type - the CDA-side mirror of app/mappings/
    oru.py::_build_observation_value's identical per-type dispatch for
    HL7v2's OBX-2/OBX-5. Records against the real chosen field
    (valueQuantity.value, not a vaguer "value") the same way ORU's own
    OBX-5 recording already does, since Observation.value[x] is a real
    FHIR choice type."""
    value_element = find_child(observation_element, "value")
    if value_element is None:
        return {}
    value_type = xsi_type(value_element)
    value_base = xpath_location(member_base, "value") if member_base else None
    if value_type in ("PQ", "REAL"):
        # REAL elements never carry a `unit` attribute, so
        # build_quantity_from_pq naturally produces a unit-less Quantity -
        # matching the IG's own "leave unit fields empty" instruction
        # without needing a separate code path.
        quantity = build_quantity_from_pq(value_element)
        if not quantity:
            return {}
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueQuantity.value", f"{value_base}/@value", value_element.get("value"))
            if quantity.unit:
                recorder.record(resource_id, "valueQuantity.unit", f"{value_base}/@unit", quantity.unit)
        return {"valueQuantity": quantity}
    if value_type in ("CD", "CE", "CV", "CO", "CS"):
        concept = build_codeable_concept_from_cd(value_element)
        if not concept:
            return {}
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueCodeableConcept.coding[0].code", f"{value_base}/@code", concept.coding[0].code)
        return {"valueCodeableConcept": concept}
    if value_type == "IVL_PQ":
        return _build_ivl_pq_value(value_element, resource_id=resource_id, value_base=value_base, recorder=recorder)
    if value_type == "INT":
        raw = value_element.get("value")
        if raw is None:
            return {}
        try:
            parsed = int(raw)
        except ValueError:
            return {}
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueInteger", f"{value_base}/@value", raw)
        return {"valueInteger": parsed}
    if value_type in ("ST", "ED"):
        # ED's own plain-text (narrative-reference) case is structurally
        # identical to ST once a genuine binary/attachment value is out of
        # scope (see module docstring) - a true attachment ED still has no
        # .value[x] mapped, same as an ED with no text content at all.
        text = (value_element.text or "").strip()
        if not text:
            return {}
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueString", f"{value_base}/text()", text)
        return {"valueString": text}
    return {}


def _build_specimen(specimen_element, location_base: str, recorder=None) -> Specimen | None:
    """specimen/specimenRole/... -> Specimen, per CF-results.md's own
    "C-CDA Specimen to FHIR Specimen" table (confirmed against the real
    CDA-core Specimen/SpecimenRole/SpecimenPlayingEntity structuredefs
    fetched directly, not assumed from field-name similarity to anything
    else in this app). `location_base` is the xpath prefix up to (not
    including) the <specimen> element itself - "organizer" or a result
    observation's own _member_base(index)."""
    specimen_role = find_child(specimen_element, "specimenRole")
    if specimen_role is None:
        return None

    base = xpath_location(location_base, "specimen", "specimenRole")
    specimen_id = str(uuid.uuid4())
    specimen = Specimen(id=specimen_id)

    identifiers = build_identifiers(
        find_all(specimen_role, "id"),
        _SPECIMEN_ID_FALLBACK_SYSTEM,
        resource_id=specimen_id,
        location_prefix=xpath_location(base, "id"),
        recorder=recorder,
    )
    if identifiers:
        specimen.identifier = identifiers

    playing_entity = find_child(specimen_role, "specimenPlayingEntity")
    if playing_entity is not None:
        entity_base = xpath_location(base, "specimenPlayingEntity")

        code_element = find_child(playing_entity, "code")
        specimen_type = build_codeable_concept_from_cd(code_element)
        name_element = find_child(playing_entity, "name")
        name = (name_element.text or "").strip() if name_element is not None else ""
        if specimen_type:
            specimen.type = specimen_type
            if recorder:
                if code_element.get("code"):
                    recorder.record(
                        specimen_id, "type.coding[0].code", xpath_location(entity_base, "code", "@code"), code_element.get("code")
                    )
                if code_element.get("displayName"):
                    recorder.record(
                        specimen_id,
                        "type.coding[0].display",
                        xpath_location(entity_base, "code", "@displayName"),
                        code_element.get("displayName"),
                    )
        elif name:
            # No resolvable code - the IG's own stated fallback is the
            # playingEntity's own name, as text only.
            specimen.type = CodeableConcept(text=name)
            if recorder:
                recorder.record(specimen_id, "type.text", xpath_location(entity_base, "name"), name)

        quantity_element = find_child(playing_entity, "quantity")
        quantity = build_quantity_from_pq(quantity_element)
        if quantity:
            specimen.collection = SpecimenCollection(quantity=quantity)
            if recorder:
                recorder.record(
                    specimen_id,
                    "collection.quantity.value",
                    xpath_location(entity_base, "quantity", "@value"),
                    quantity_element.get("value"),
                )

        desc_element = find_child(playing_entity, "desc")
        desc = (desc_element.text or "").strip() if desc_element is not None else ""
        if desc:
            specimen.note = [Annotation(text=desc)]
            if recorder:
                recorder.record(specimen_id, "note[0].text", xpath_location(entity_base, "desc"), desc)

    return specimen


def _apply_collection_body_site(specimen: Specimen, collection_procedure_element, location: str, recorder=None) -> None:
    """The Specimen Collection Procedure's own targetSiteCode ->
    Specimen.collection.bodySite - merged into whatever SpecimenCollection
    _build_specimen already built (e.g. from specimenPlayingEntity/
    quantity), or a fresh one when that side contributed nothing."""
    body_site_element = find_child(collection_procedure_element, "targetSiteCode")
    body_site = build_codeable_concept_from_cd(body_site_element)
    if not body_site:
        return
    if specimen.collection is None:
        specimen.collection = SpecimenCollection()
    specimen.collection.bodySite = body_site
    if recorder:
        recorder.record(
            specimen.id, "collection.bodySite.coding[0].code", xpath_location(location, "targetSiteCode", "@code"), body_site.coding[0].code
        )


def _find_specimen_collection_procedure(organizer):
    """(index, procedure_element) for the organizer's own Specimen
    Collection Procedure component - a component/procedure sibling of the
    result observations, identified by its own fixed SNOMED code
    (17636008) - or None when absent."""
    for index, component in enumerate(find_all(organizer, "component")):
        procedure_element = find_child(component, "procedure")
        if procedure_element is None:
            continue
        code_element = find_child(procedure_element, "code")
        if code_element is None:
            continue
        if (
            code_element.get("code") == SPECIMEN_COLLECTION_PROCEDURE_CODE
            and code_element.get("codeSystem") == SPECIMEN_COLLECTION_PROCEDURE_CODE_SYSTEM
        ):
            return index, procedure_element
    return None


def _build_result_observation(
    observation_element, patient_id: str, index: int, default_specimen_id: str | None = None, recorder=None
) -> tuple[Observation, Specimen | None] | None:
    code_element = find_child(observation_element, "code")
    code = build_codeable_concept_from_cd(code_element)
    if code is None:
        return None

    observation_id = str(uuid.uuid4())
    status = _resolve_status(observation_element)
    observation = Observation(
        id=observation_id,
        status=status,
        category=[_category()],
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    member_base = _member_base(index)
    if recorder:
        _record_status(recorder, observation_id, observation_element, status, member_base)
        _record_category(recorder, observation_id)
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(observation_id, "code.coding[0].code", f"{member_base}/code/@code", code_value)
        if display_value:
            recorder.record(observation_id, "code.coding[0].display", f"{member_base}/code/@displayName", display_value)

    effective_time = find_child(observation_element, "effectiveTime")
    effective, _ = ivl_ts_bounds(effective_time)
    effective_dt = parse_partial_ts(effective)
    if effective_dt:
        observation.effectiveDateTime = effective_dt
        if recorder:
            recorder.record(
                observation_id,
                "effectiveDateTime",
                effective_time_location(f"{member_base}/effectiveTime", effective_time, "low"),
                effective_dt,
            )

    for key, value in _build_observation_value(
        observation_element, resource_id=observation_id, member_base=member_base, recorder=recorder
    ).items():
        setattr(observation, key, value)

    interpretation_element = find_child(observation_element, "interpretationCode")
    interpretation = build_codeable_concept_from_cd(interpretation_element)
    if interpretation:
        observation.interpretation = [interpretation]
        if recorder:
            recorder.record(
                observation_id,
                "interpretation[0].coding[0].code",
                f"{member_base}/interpretationCode/@code",
                interpretation.coding[0].code,
            )

    method_element = find_child(observation_element, "methodCode")
    method = build_codeable_concept_from_cd(method_element)
    if method:
        observation.method = method
        if recorder:
            recorder.record(observation_id, "method.coding[0].code", f"{member_base}/methodCode/@code", method.coding[0].code)

    body_site_element = find_child(observation_element, "targetSiteCode")
    body_site = build_codeable_concept_from_cd(body_site_element)
    if body_site:
        observation.bodySite = body_site
        if recorder:
            recorder.record(observation_id, "bodySite.coding[0].code", f"{member_base}/targetSiteCode/@code", body_site.coding[0].code)

    reference_ranges = _build_reference_range(observation_element, resource_id=observation_id, member_base=member_base, recorder=recorder)
    if reference_ranges:
        observation.referenceRange = reference_ranges

    # specimen: this observation's own <specimen> child, when present,
    # overrides the organizer-level default for this one Observation only
    # - per CF-results.md's own explicit attachment rule (see module
    # docstring).
    own_specimen = None
    own_specimen_element = find_child(observation_element, "specimen")
    if own_specimen_element is not None:
        own_specimen = _build_specimen(own_specimen_element, member_base, recorder=recorder)
    specimen_id = own_specimen.id if own_specimen is not None else default_specimen_id
    if specimen_id:
        observation.specimen = Reference(reference=f"urn:uuid:{specimen_id}")

    return observation, own_specimen


def build_diagnostic_reports(section, patient_id: str, recorder=None) -> list[Observation | DiagnosticReport | Specimen]:
    """One DiagnosticReport per Result Organizer entry (its own .result
    referencing one Observation per child Result Observation), plus each
    of those Observations, plus any Specimen resources built along the
    way - all returned as a flat list of separate, top-level resources. An
    organizer whose every child observation lacks a resolvable code
    produces no report either, matching vitals.py's identical "nothing to
    group" treatment."""
    resources: list[Observation | DiagnosticReport | Specimen] = []
    for entry in find_all(section, "entry"):
        organizer = find_child(entry, "organizer")
        if organizer is None or not has_template_id(organizer, ORGANIZER_TEMPLATE_ID):
            continue

        # A <specimen> found directly on the organizer becomes the
        # default for every child result Observation below (an
        # individual observation's own <specimen>, if present, overrides
        # it just for that one Observation - see _build_result_observation).
        organizer_specimen = None
        organizer_specimen_element = find_child(organizer, "specimen")
        if organizer_specimen_element is not None:
            organizer_specimen = _build_specimen(organizer_specimen_element, "organizer", recorder=recorder)
            if organizer_specimen is not None:
                collection_procedure = _find_specimen_collection_procedure(organizer)
                if collection_procedure is not None:
                    proc_index, procedure_element = collection_procedure
                    _apply_collection_body_site(
                        organizer_specimen,
                        procedure_element,
                        xpath_location("organizer", f"component[{proc_index}]", "procedure"),
                        recorder=recorder,
                    )

        result_observations = []
        specimens = [organizer_specimen] if organizer_specimen is not None else []
        for index, component in enumerate(find_all(organizer, "component")):
            observation_element = find_child(component, "observation")
            if observation_element is None or not has_template_id(observation_element, OBSERVATION_TEMPLATE_ID):
                continue
            built = _build_result_observation(
                observation_element,
                patient_id,
                index,
                default_specimen_id=organizer_specimen.id if organizer_specimen is not None else None,
                recorder=recorder,
            )
            if built is None:
                continue
            observation, own_specimen = built
            result_observations.append(observation)
            if own_specimen is not None:
                specimens.append(own_specimen)

        if not result_observations:
            continue

        organizer_code_element = find_child(organizer, "code")
        organizer_code = build_codeable_concept_from_cd(organizer_code_element)
        report_id = str(uuid.uuid4())
        report_status = _resolve_status(organizer)
        report = DiagnosticReport(
            id=report_id,
            status=report_status,
            category=[_category()],
            code=organizer_code or CodeableConcept(text="Unspecified result panel"),
            subject=Reference(reference=f"urn:uuid:{patient_id}"),
            result=[Reference(reference=f"urn:uuid:{obs.id}") for obs in result_observations],
        )
        if organizer_specimen is not None:
            report.specimen = [Reference(reference=f"urn:uuid:{organizer_specimen.id}")]

        if recorder:
            _record_status(recorder, report_id, organizer, report_status, "organizer")
            _record_category(recorder, report_id)
            if organizer_code:
                code_value = organizer_code_element.get("code")
                display_value = organizer_code_element.get("displayName")
                if code_value:
                    recorder.record(report_id, "code.coding[0].code", xpath_location("organizer", "code", "@code"), code_value)
                if display_value:
                    recorder.record(
                        report_id, "code.coding[0].display", xpath_location("organizer", "code", "@displayName"), display_value
                    )
            else:
                recorder.record_inferred(
                    report_id,
                    "code.text",
                    "The organizer's own /code was absent or unresolvable - code defaults to a generic placeholder text rather than leaving this FHIR-required field unset.",
                    "Unspecified result panel",
                )

        organizer_effective_time = find_child(organizer, "effectiveTime")
        organizer_effective, _ = ivl_ts_bounds(organizer_effective_time)
        report_effective_dt = parse_partial_ts(organizer_effective)
        if report_effective_dt:
            report.effectiveDateTime = report_effective_dt
            if recorder:
                recorder.record(
                    report_id,
                    "effectiveDateTime",
                    effective_time_location("organizer/effectiveTime", organizer_effective_time, "low"),
                    report_effective_dt,
                )

        resources.extend(specimens)
        resources.append(report)
        resources.extend(result_observations)

    return resources
