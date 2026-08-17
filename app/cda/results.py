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

**Disclosed scope limits, decided up front**: `category` (both organizer
and observation) needs a live LOINC CLASSTYPE terminology-server lookup
per the IG's own guidance (`CodeSystem/$lookup?system=http://loinc.org...`)
- this app has no such server integration anywhere, so category is left
unmapped rather than guessed at, the same "no verified crosswalk, don't
guess" discipline as Allergies' CF_AllergyIntoleranceAbsentCode gap.
`specimen` (on both organizer and observation) and `author`->Provenance
are deferred - this app has never built either a CDA-side Specimen or
Provenance resource. Of the six possible `/value` xsi:types the IG
documents, PQ/CD-family/INT/REAL/ST are mapped; IVL_PQ (a value *range*,
not a fixed value) and ED (embedded binary/attachment data) are deferred -
both genuinely rarer in practice than a fixed scalar or coded value, the
same "map the dominant shape now" judgment Medications' own IVL_PQ dosing
range already established. referenceRange.text (narrative-referenced, not
resolvable without this app's disclosed narrative-<text> gap) is deferred;
referenceRange's IVL_PQ low/high is mapped."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, build_quantity_from_pq, effective_time_location, parse_partial_ts
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


def _build_observation_value(
    observation_element, resource_id: str | None = None, member_base: str | None = None, recorder=None
) -> dict:
    """Return the kwarg for whichever Observation.value[x] choice fits the
    /value element's own xsi:type - the CDA-side mirror of app/mappings/
    oru.py::_build_observation_value's identical per-type dispatch for
    HL7v2's OBX-2/OBX-5. An unrecognized/unsupported xsi:type (IVL_PQ, ED,
    or absent) is left unmapped rather than guessed at, matching that
    function's own precedent. Records against the real chosen field
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
    if value_type == "ST":
        text = (value_element.text or "").strip()
        if not text:
            return {}
        if recorder and resource_id and value_base:
            recorder.record(resource_id, "valueString", f"{value_base}/text()", text)
        return {"valueString": text}
    return {}


def _build_result_observation(observation_element, patient_id: str, index: int, recorder=None) -> Observation | None:
    code_element = find_child(observation_element, "code")
    code = build_codeable_concept_from_cd(code_element)
    if code is None:
        return None

    observation_id = str(uuid.uuid4())
    status = _resolve_status(observation_element)
    observation = Observation(
        id=observation_id,
        status=status,
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    member_base = _member_base(index)
    if recorder:
        _record_status(recorder, observation_id, observation_element, status, member_base)
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

    return observation


def build_diagnostic_reports(section, patient_id: str, recorder=None) -> list[Observation | DiagnosticReport]:
    """One DiagnosticReport per Result Organizer entry (its own .result
    referencing one Observation per child Result Observation), plus each
    of those Observations - all returned as a flat list of separate,
    top-level resources. An organizer whose every child observation lacks
    a resolvable code produces no report either, matching vitals.py's
    identical "nothing to group" treatment."""
    resources: list[Observation | DiagnosticReport] = []
    for entry in find_all(section, "entry"):
        organizer = find_child(entry, "organizer")
        if organizer is None or not has_template_id(organizer, ORGANIZER_TEMPLATE_ID):
            continue

        result_observations = []
        for index, component in enumerate(find_all(organizer, "component")):
            observation_element = find_child(component, "observation")
            if observation_element is None or not has_template_id(observation_element, OBSERVATION_TEMPLATE_ID):
                continue
            observation = _build_result_observation(observation_element, patient_id, index, recorder=recorder)
            if observation is not None:
                result_observations.append(observation)

        if not result_observations:
            continue

        organizer_code_element = find_child(organizer, "code")
        organizer_code = build_codeable_concept_from_cd(organizer_code_element)
        report_id = str(uuid.uuid4())
        report_status = _resolve_status(organizer)
        report = DiagnosticReport(
            id=report_id,
            status=report_status,
            code=organizer_code or CodeableConcept(text="Unspecified result panel"),
            subject=Reference(reference=f"urn:uuid:{patient_id}"),
            result=[Reference(reference=f"urn:uuid:{obs.id}") for obs in result_observations],
        )

        if recorder:
            _record_status(recorder, report_id, organizer, report_status, "organizer")
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

        resources.append(report)
        resources.extend(result_observations)

    return resources
