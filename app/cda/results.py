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

from app.cda.common import build_codeable_concept_from_cd, build_quantity_from_pq, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, xsi_type

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.3.1"
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


def _build_reference_range(observation_element) -> list[ObservationReferenceRange]:
    ranges = []
    for reference_range in find_all(observation_element, "referenceRange"):
        observation_range = find_child(reference_range, "observationRange")
        if observation_range is None:
            continue
        value_element = find_child(observation_range, "value")
        if value_element is None or xsi_type(value_element) != "IVL_PQ":
            continue
        low = build_quantity_from_pq(find_child(value_element, "low"))
        high = build_quantity_from_pq(find_child(value_element, "high"))
        if low is None and high is None:
            continue
        observation_reference_range = ObservationReferenceRange()
        if low is not None:
            observation_reference_range.low = low
        if high is not None:
            observation_reference_range.high = high
        ranges.append(observation_reference_range)
    return ranges


def _build_observation_value(observation_element) -> dict:
    """Return the kwarg for whichever Observation.value[x] choice fits the
    /value element's own xsi:type - the CDA-side mirror of app/mappings/
    oru.py::_build_observation_value's identical per-type dispatch for
    HL7v2's OBX-2/OBX-5. An unrecognized/unsupported xsi:type (IVL_PQ, ED,
    or absent) is left unmapped rather than guessed at, matching that
    function's own precedent."""
    value_element = find_child(observation_element, "value")
    if value_element is None:
        return {}
    value_type = xsi_type(value_element)
    if value_type == "PQ":
        quantity = build_quantity_from_pq(value_element)
        return {"valueQuantity": quantity} if quantity else {}
    if value_type in ("CD", "CE", "CV", "CO", "CS"):
        concept = build_codeable_concept_from_cd(value_element)
        return {"valueCodeableConcept": concept} if concept else {}
    if value_type == "INT":
        raw = value_element.get("value")
        if raw is None:
            return {}
        try:
            return {"valueInteger": int(raw)}
        except ValueError:
            return {}
    if value_type == "REAL":
        # REAL elements never carry a `unit` attribute, so
        # build_quantity_from_pq naturally produces a unit-less Quantity -
        # matching the IG's own "leave unit fields empty" instruction
        # without needing a separate code path.
        quantity = build_quantity_from_pq(value_element)
        return {"valueQuantity": quantity} if quantity else {}
    if value_type == "ST":
        text = (value_element.text or "").strip()
        return {"valueString": text} if text else {}
    return {}


def _build_result_observation(observation_element, patient_id: str) -> Observation | None:
    code = build_codeable_concept_from_cd(find_child(observation_element, "code"))
    if code is None:
        return None

    observation = Observation(
        id=str(uuid.uuid4()),
        status=_resolve_status(observation_element),
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    effective, _ = ivl_ts_bounds(find_child(observation_element, "effectiveTime"))
    effective_dt = parse_partial_ts(effective)
    if effective_dt:
        observation.effectiveDateTime = effective_dt

    for key, value in _build_observation_value(observation_element).items():
        setattr(observation, key, value)

    interpretation = build_codeable_concept_from_cd(find_child(observation_element, "interpretationCode"))
    if interpretation:
        observation.interpretation = [interpretation]

    method = build_codeable_concept_from_cd(find_child(observation_element, "methodCode"))
    if method:
        observation.method = method

    body_site = build_codeable_concept_from_cd(find_child(observation_element, "targetSiteCode"))
    if body_site:
        observation.bodySite = body_site

    reference_ranges = _build_reference_range(observation_element)
    if reference_ranges:
        observation.referenceRange = reference_ranges

    return observation


def build_diagnostic_reports(section, patient_id: str) -> list[Observation | DiagnosticReport]:
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
        for component in find_all(organizer, "component"):
            observation_element = find_child(component, "observation")
            if observation_element is None or not has_template_id(observation_element, OBSERVATION_TEMPLATE_ID):
                continue
            observation = _build_result_observation(observation_element, patient_id)
            if observation is not None:
                result_observations.append(observation)

        if not result_observations:
            continue

        organizer_code = build_codeable_concept_from_cd(find_child(organizer, "code"))
        report = DiagnosticReport(
            id=str(uuid.uuid4()),
            status=_resolve_status(organizer),
            code=organizer_code or CodeableConcept(text="Unspecified result panel"),
            subject=Reference(reference=f"urn:uuid:{patient_id}"),
            result=[Reference(reference=f"urn:uuid:{obs.id}") for obs in result_observations],
        )

        organizer_effective, _ = ivl_ts_bounds(find_child(organizer, "effectiveTime"))
        report_effective_dt = parse_partial_ts(organizer_effective)
        if report_effective_dt:
            report.effectiveDateTime = report_effective_dt

        resources.append(report)
        resources.extend(result_observations)

    return resources
