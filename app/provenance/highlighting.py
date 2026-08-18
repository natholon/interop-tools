"""Orchestrates the Data Specification page's correlated highlighting: for
an already-built `Bundle`/`CrosswalkReport` pair, resolves every direct
`ProvenanceEntry` to a character span in both the raw source text and the
pretty-printed FHIR JSON text, so the frontend can wrap matching spans in
same-colored `<mark>` tags.

Deliberately a post-processing step over an already-resolved
`CrosswalkReport` - `app/provenance/{models,recorder,resolver,dispatch}.py`
and every existing test of them stay completely untouched. Called only
from `app/routes/data_specification.py`.

**The occurrence-claiming problem**: a `source_location` string alone
(e.g. `"OBX-5"`, `"SV1-1.2"`, `"act/entryRelationship[SUBJ]/observation/
value/@code"`) doesn't say *which* physical occurrence of a repeating
segment/element it came from when a message has more than one (several
OBX segments across several DiagnosticReports, several LX/SV1 line items
within one Claim, several Concern Act entries in a Problems section) -
harmless when the crosswalk was just a table of strings, but now needs to
resolve to one specific span. Fixed via a **claim key** = `(root_key,
scope_hint, index_tuple)`, where `root_key` is the segment id (HL7v2/EDI)
or first path segment (CDA), `scope_hint` disambiguates same-named C-CDA
sections (see `app/provenance/cda_locator.py`'s own docstring - `None` for
HL7v2/EDI, which have no equivalent collision), and `index_tuple` is the
`Bundle.entry[N]` index plus - for EDI only - a `.item[N]` index when
present (see `_index_tuple`'s own docstring for why only these two, not
every bracketed integer in `fhir_path`). Entries are processed in the
order `CrosswalkReport.entries` already lists them (confirmed, by reading
`app/provenance/recorder.py`/`resolver.py` directly, to preserve the exact
order `.record()` was called during mapping - every mapper in this
codebase walks its own source segments/elements in document order); the
first entry with a given claim key claims the next unclaimed physical
occurrence of `root_key`, and every later entry sharing that same claim
key reuses it.

**The shared-physical-segment problem, and how it's resolved**: some
fields belong to a genuinely *different* resource than the one whose own
segment they're physically embedded in - ORU's OBX-16 (Responsible
Observer) builds a separate `Practitioner` resource, but OBX-16 lives
*inside* the identical physical `OBX` segment the referencing
`Observation`'s own OBX-2/3/5/6/7/8/11/14 fields do. Both resources share
`root_key="OBX"`, but as *different* resources they'd naturally claim
*different* occurrences under the scheme above - reproduced directly
during this module's own development: a `Practitioner`'s own OBX-16 claim
consumed an occurrence slot, silently shifting every *later* Observation's
own claim off by one and resolving it against the wrong physical OBX
segment entirely (not just the Practitioner's own highlight being
approximate - genuinely wrong data for unrelated, later resources). Fixed
by `_build_reference_map()`: before claiming a *new* occurrence for a
resource, check whether that resource is referenced (via *any* `Reference`
field, found the same generic recursive way `app/dedup.py::
_rewrite_references` walks a resource's own fields, reused here for the
identical "a Reference can be at any depth on any resource" reason) by
some *other* resource that already holds a claim for the same
`(root_key, scope_hint)` - if so, the referenced resource borrows that
same occurrence instead of claiming its own. Checked in both directions
(referenced-by and references-to) so processing order doesn't matter."""

import re

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.reference import Reference
from pydantic import BaseModel

from app.provenance.cda_locator import CdaLocator
from app.provenance.edi_locator import EdiLocator
from app.provenance.hl7_locator import Hl7Locator
from app.provenance.json_locator import locate_json_paths
from app.provenance.models import CrosswalkReport, ProvenanceEntry

_PALETTE_SIZE = 10

_ENTRY_INDEX_RE = re.compile(r"^Bundle\.entry\[(\d+)\]")
_ITEM_INDEX_RE = re.compile(r"\.item\[(\d+)\]")


class HighlightMatch(BaseModel):
    fhir_path: str
    color_index: int | None = None
    source_span: tuple[int, int] | None = None
    fhir_span: tuple[int, int] | None = None
    fhir_token_type: str | None = None  # "string" | "number" | "literal" - see json_locator.JsonSpan


class HighlightingPayload(BaseModel):
    display_source_text: str
    fhir_json_text: str
    matches: list[HighlightMatch]


def _item_index(fhir_path: str) -> int | None:
    """A `.item[N]` index in `fhir_path`, when present - the one bracket
    (beyond `Bundle.entry[N]` itself, handled separately via the real
    resource identity) that genuinely indicates a *different* physical
    source occurrence *within* one resource. Deliberately *not* every
    bracketed integer in the path: a field like `identifier[0]`/
    `name[0].given[1]` is a perfectly ordinary FHIR array on one resource,
    still sourced from the *same* physical segment as every other field on
    it - treating it as a distinct occurrence would be wrong (confirmed as
    a real bug during this module's own development: an EDI Organization's
    `identifier[0].value` and `name` fact were claiming *different*
    physical NM1 segments purely because `identifier[0]` added a bracket
    `name` didn't have). `.item[N]` (`Claim.item[]`/
    `CoverageEligibilityRequest.item[]`/`ClaimResponse.item[]`) is
    different - every item shares the *identical* location string across
    items (e.g. `SV1-1.2` for every line) with no other disambiguator
    baked into the string itself, unlike C-CDA's own repeating
    sub-structures (`reaction[N]`, `referenceRange[N]`), which already
    carry their own disambiguating index *inside* the location string's
    own bracket grammar (see `app/provenance/cda_locator.py`), and EDI's
    own `diagnosis[N]`, whose location string already varies by HI
    composite position/segment_repetition per diagnosis - neither needs
    help here."""
    match = _ITEM_INDEX_RE.search(fhir_path)
    return int(match.group(1)) if match else None


def _entry_index(fhir_path: str) -> int | None:
    match = _ENTRY_INDEX_RE.match(fhir_path)
    return int(match.group(1)) if match else None


def _resource_for_entry(fhir_path: str, bundle: Bundle):
    index = _entry_index(fhir_path)
    if index is None or bundle.entry is None or index >= len(bundle.entry):
        return None
    return bundle.entry[index].resource


class _CdaScopeResolver:
    """Precomputes, once per Bundle, the two Bundle-graph signals
    `_resolve_scope_hint` needs to tell apart an Observation built by
    Vitals from one built by Results (both are plain `Observation`
    resources - the resource type alone can't distinguish them) - see
    `app/provenance/cda_locator.py`'s own docstring for the full
    cross-section collision this exists to resolve."""

    def __init__(self, bundle: Bundle) -> None:
        self._member_of_panel: set[str] = set()
        self._member_of_report: set[str] = set()
        for entry in bundle.entry or []:
            resource = entry.resource
            has_member = getattr(resource, "hasMember", None)
            if has_member:
                for ref in has_member:
                    if ref.reference:
                        self._member_of_panel.add(ref.reference.removeprefix("urn:uuid:"))
            result = getattr(resource, "result", None)
            if result:
                for ref in result:
                    if ref.reference:
                        self._member_of_report.add(ref.reference.removeprefix("urn:uuid:"))

    def resolve(self, resource) -> str | None:
        resource_type = resource.get_resource_type()
        if resource_type == "Condition":
            return "hospital_discharge_diagnosis" if resource.category else "problems"
        if resource_type == "AllergyIntolerance":
            return "allergies"
        if resource_type == "MedicationRequest":
            return "medications"
        if resource_type == "Immunization":
            return "immunizations"
        if resource_type == "Procedure":
            return "procedures"
        if resource_type == "DiagnosticReport":
            return "results"
        if resource_type == "Observation":
            if resource.hasMember:
                return "vitals"  # a panel Observation - only Vitals' own panel is an Observation (Results' is a DiagnosticReport)
            if resource.id in self._member_of_panel:
                return "vitals"
            if resource.id in self._member_of_report:
                return "results"
            return None
        return None


def _resolve_scope_hint(entry: ProvenanceEntry, bundle: Bundle, scope_resolver: "_CdaScopeResolver | None") -> str | None:
    if scope_resolver is None:
        return None
    resource = _resource_for_entry(entry.fhir_path, bundle)
    if resource is None:
        return None
    return scope_resolver.resolve(resource)


def _walk_references(node, resource_id: str, out: dict[str, set[str]]) -> None:
    if isinstance(node, Reference):
        if node.reference and node.reference.startswith("urn:uuid:"):
            target_id = node.reference.removeprefix("urn:uuid:")
            if target_id != resource_id:
                out.setdefault(resource_id, set()).add(target_id)
                out.setdefault(target_id, set()).add(resource_id)
        return
    if isinstance(node, BaseModel):
        for field_name in type(node).model_fields:
            _walk_references(getattr(node, field_name), resource_id, out)
        return
    if isinstance(node, list):
        for item in node:
            _walk_references(item, resource_id, out)


def _build_reference_map(bundle: Bundle) -> dict[str, set[str]]:
    """`resource_id -> {every resource id directly connected to it by a
    Reference, in either direction}` - see this module's own docstring for
    why this exists (the "shared physical segment" problem, e.g. ORU's
    OBX-16 Practitioner). Direction doesn't matter for the borrowing check
    this feeds - only "are these two resources linked at all.\""""
    related: dict[str, set[str]] = {}
    for entry in bundle.entry or []:
        if entry.resource is not None and entry.resource.id:
            _walk_references(entry.resource, entry.resource.id, related)
    return related


def build_highlighting_payload(bundle: Bundle, report: CrosswalkReport, raw_text: str, source_format: str) -> HighlightingPayload:
    """The one entry point `app/routes/data_specification.py` calls. Never
    raises - a locator failing to build at all (e.g. `raw_text` somehow
    isn't the text that actually produced `bundle`, defensive, not
    currently reachable) degrades to "no source-side highlighting," and
    each individual entry's own resolution is independently guarded so one
    malformed location string can't take down the rest."""
    fhir_json_text = bundle.model_dump_json(indent=2, exclude_none=True)
    json_locations = locate_json_paths(fhir_json_text)

    source_locator = None
    scope_resolver = None
    try:
        if source_format == "HL7v2":
            source_locator = Hl7Locator(raw_text)
        elif source_format == "EDI":
            source_locator = EdiLocator(raw_text)
        elif source_format == "CDA":
            source_locator = CdaLocator(raw_text)
            scope_resolver = _CdaScopeResolver(bundle)
    except Exception:
        source_locator = None

    display_source_text = source_locator.display_text if source_locator is not None else raw_text
    reference_map = _build_reference_map(bundle) if source_locator is not None else {}

    claimed_occurrence: dict[tuple, int] = {}
    resource_claims: dict[str, dict[tuple, tuple[int, bool]]] = {}
    next_occurrence: dict[tuple, int] = {}
    matches: list[HighlightMatch] = []
    color_counter = 0

    for entry in report.entries:
        fhir_span = None
        token_type = None
        json_span = json_locations.get(entry.fhir_path)
        if json_span is not None:
            fhir_span = (json_span.start, json_span.end)
            token_type = json_span.token_type

        source_span = None
        if source_locator is not None and entry.derivation == "direct" and entry.source_location:
            try:
                source_span = _resolve_source_span(
                    entry, source_locator, scope_resolver, bundle, claimed_occurrence, resource_claims, next_occurrence, reference_map
                )
            except Exception:
                source_span = None

        color_index = None
        if entry.derivation == "direct" and (source_span is not None or fhir_span is not None):
            color_index = color_counter % _PALETTE_SIZE
            color_counter += 1

        matches.append(
            HighlightMatch(
                fhir_path=entry.fhir_path,
                color_index=color_index,
                source_span=source_span,
                fhir_span=fhir_span,
                fhir_token_type=token_type,
            )
        )

    return HighlightingPayload(display_source_text=display_source_text, fhir_json_text=fhir_json_text, matches=matches)


def _resolve_source_span(
    entry: ProvenanceEntry,
    source_locator,
    scope_resolver: "_CdaScopeResolver | None",
    bundle: Bundle,
    claimed_occurrence: dict[tuple, int],
    resource_claims: dict[str, dict[tuple, tuple[int, bool]]],
    next_occurrence: dict[tuple, int],
    reference_map: dict[str, set[str]],
) -> tuple[int, int] | None:
    root_key = source_locator.root_key(entry.source_location)
    if root_key is None:
        return None

    scope_hint = _resolve_scope_hint(entry, bundle, scope_resolver) if scope_resolver is not None else None
    resource = _resource_for_entry(entry.fhir_path, bundle)
    resource_id = resource.id if resource is not None else None
    item_index = _item_index(entry.fhir_path)

    count_key = (root_key, scope_hint)
    # A Bundle-level fact (no entry[N] at all, e.g. Bundle.identifier from
    # MSH-10, Bundle.timestamp from MSH-7) has no resource to key off -
    # `bundle.id` itself is the right scope, not `entry.fhir_path`: two
    # different Bundle-level facts sharing the same root_key (both MSH,
    # here) still come from the *one* physical segment every Bundle-level
    # field is read from, and must resolve to the *same* occurrence -
    # confirmed as a real bug during this module's own development,
    # identical in kind to the OBX-16 one above: MSH-10 claimed occurrence
    # 0 for "MSH" first, so MSH-7 (a different fhir_path, "Bundle.
    # timestamp") wrongly claimed a fresh occurrence 1 instead of reusing
    # the only real MSH segment.
    scope_id = resource_id if resource_id is not None else bundle.id
    claim_key = (root_key, scope_hint, scope_id, item_index)

    if claim_key not in claimed_occurrence:
        borrowed = None
        # Borrowing only applies at the whole-resource level, never within
        # one resource's own item[] array - nothing in this app ever
        # references a single Claim.item[] entry's own quasi-identity, so
        # an item-indexed fact always claims its own occurrence directly.
        if item_index is None and resource_id is not None:
            borrowed = _borrow_occurrence(resource_id, count_key, resource_claims, reference_map)
        if borrowed is not None:
            occurrence, is_original = borrowed, False
        else:
            occurrence = next_occurrence.get(count_key, 0)
            next_occurrence[count_key] = occurrence + 1
            is_original = True
        claimed_occurrence[claim_key] = occurrence
        if item_index is None and resource_id is not None:
            resource_claims.setdefault(resource_id, {})[count_key] = (occurrence, is_original)
    occurrence = claimed_occurrence[claim_key]

    if isinstance(source_locator, CdaLocator):
        return source_locator.locate(entry.source_location, occurrence, scope_hint)
    return source_locator.locate(entry.source_location, occurrence)


def _borrow_occurrence(
    resource_id: str, count_key: tuple, resource_claims: dict[str, dict[tuple, tuple[int, bool]]], reference_map: dict[str, set[str]]
) -> int | None:
    """If some other resource directly connected to `resource_id` by a
    Reference (in either direction) already holds an *original* (counter-
    assigned, not itself borrowed) claim for `count_key`, reuse it - see
    this module's own docstring for the shared-physical-segment problem
    this exists to fix (ORU's OBX-16 Practitioner).

    Deliberately restricted to *original* claims only - a real, reproduced
    bug during this module's own development: a Practitioner shared by two
    different Observations' own OBX-16 (see `app/dedup.py`'s own
    performer-caching precedent) first borrowed the *first* Observation's
    occurrence correctly, but the *second* Observation - a genuinely
    independent, later physical OBX segment - then wrongly borrowed that
    *same* occurrence too, via the shared Practitioner as an unintended
    bridge between two otherwise-unrelated leader resources. Restricting
    donors to resources whose own claim came directly from the counter
    (never itself borrowed) means a shared "follower" resource like the
    Practitioner can be borrowed *from* by its first referencer, but can't
    relay that borrowed occurrence on to a second, independent referencer -
    which then correctly falls through to claiming its own, fresh
    occurrence instead."""
    for related_id in reference_map.get(resource_id, ()):
        claims = resource_claims.get(related_id)
        if claims and count_key in claims:
            occurrence, is_original = claims[count_key]
            if is_original:
                return occurrence
    return None
