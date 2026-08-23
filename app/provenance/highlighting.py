"""Resolves every direct `ProvenanceEntry` to a character span in both the
raw source text and the pretty-printed FHIR JSON, so the Data
Specification page can wrap matching spans in same-colored `<mark>` tags.

A post-processing step over an already-resolved `CrosswalkReport`, so
`app/provenance/{models,recorder,resolver,dispatch}.py` stay untouched.
Called only from `app/routes/data_specification.py`.

Three correctness problems drive the design, each found by a real failure:

**Which occurrence?** A `source_location` like "OBX-5" doesn't say which
physical OBX it came from when a message has several. Resolved by a claim
key of `(root_key, scope_hint, index_tuple)` - segment id or first path
segment, a C-CDA section disambiguator (see `cda_locator.py`), and the
`Bundle.entry[N]` index plus an EDI `.item[N]` index. Entries arrive in
mapping order (recorders walk their source in document order), so the
first entry with a key claims the next unclaimed occurrence and later
entries with that key reuse it.

**Shared physical segments.** ORU's OBX-16 builds a separate Practitioner
but lives inside the same physical OBX as the Observation's own fields.
As different resources they'd claim different occurrences, which shifted
every later Observation's claim off by one - wrong data for unrelated
resources, not just an approximate highlight. `_build_reference_map()`
lets a resource borrow the occurrence held by a resource it's connected
to by a Reference (checked both directions, so processing order doesn't
matter). Only *original* claims can be borrowed, or a shared Practitioner
would relay one referencer's occurrence to an unrelated second one.

**Order can't be trusted.** 837P/837I/837D carry untracked leading NM1
loops and build resources in an order that doesn't match document order,
so sequential claiming resolved every NM1 fact to the wrong segment.
`_claim_fresh_occurrence()` first tries to match the fact's own recorded
text against each unclaimed occurrence, falling back to sequential order
when nothing matches - a derived value (a display string, a mapped code)
never matches literal source text, so that fallback is the proven-correct
path for HL7v2/CDA."""

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
# Every FHIR array field this app has ever recorded facts against where a
# *single* location string is shared identically across every repetition,
# with no other disambiguator baked into the location string itself (see
# _item_index's own docstring for the full reasoning, and why other
# repeating arrays - reaction[N], referenceRange[N], diagnosis[N] - are
# deliberately NOT in this set). `detail[N]` (PaymentReconciliation.
# detail[], one per 835 CLP claim) was found missing here via the exact
# same real-fixture bug edi_837p_basic.x12's own NM1 occurrence-resolution
# bug was found through - reproduced directly against edi_835_multi_claim
# .x12, whose own second claim's facts (CLP-1/CLP-2/CLP-4) were silently
# resolving to the *first* claim's physical CLP segment instead, since both
# claims live on the one PaymentReconciliation resource with no item[]-
# style bracket in their shared "CLP-1"/"CLP-2"/"CLP-4" location strings.
_REPEATING_FIELD_INDEX_RE = re.compile(r"\.(?:item|detail)\[(\d+)\]")


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
    """A `.item[N]`/`.detail[N]` index in `fhir_path`, when present - the
    one kind of bracket (beyond `Bundle.entry[N]` itself, handled
    separately via the real resource identity) that genuinely indicates a
    *different* physical source occurrence *within* one resource.
    Deliberately *not* every bracketed integer in the path: a field like
    `identifier[0]`/`name[0].given[1]` is a perfectly ordinary FHIR array
    on one resource, still sourced from the *same* physical segment as
    every other field on it - treating it as a distinct occurrence would be
    wrong (confirmed as a real bug during this module's own development: an
    EDI Organization's `identifier[0].value` and `name` fact were claiming
    *different* physical NM1 segments purely because `identifier[0]` added
    a bracket `name` didn't have). `.item[N]` (`Claim.item[]`/
    `CoverageEligibilityRequest.item[]`/`ClaimResponse.item[]`) and
    `.detail[N]` (`PaymentReconciliation.detail[]`) are different - every
    repetition shares the *identical* location string (e.g. `SV1-1.2` for
    every claim line, `CLP-1` for every remittance detail) with no other
    disambiguator baked into the string itself, unlike C-CDA's own
    repeating sub-structures (`reaction[N]`, `referenceRange[N]`), which
    already carry their own disambiguating index *inside* the location
    string's own bracket grammar (see `app/provenance/cda_locator.py`), and
    EDI's own `diagnosis[N]`, whose location string already varies by HI
    composite position/segment_repetition per diagnosis - neither needs
    help here."""
    match = _REPEATING_FIELD_INDEX_RE.search(fhir_path)
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
    used_occurrences: dict[tuple, set[int]] = {}
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
                    entry, source_locator, scope_resolver, bundle, claimed_occurrence, resource_claims, used_occurrences, reference_map
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


def _occurrence_count(source_locator, root_key: str, scope_hint: str | None) -> int:
    if isinstance(source_locator, CdaLocator):
        return source_locator.occurrence_count(root_key, scope_hint)
    return source_locator.occurrence_count(root_key)


def _locate(source_locator, source_location: str, occurrence: int, scope_hint: str | None) -> tuple[int, int] | None:
    if isinstance(source_locator, CdaLocator):
        return source_locator.locate(source_location, occurrence, scope_hint)
    return source_locator.locate(source_location, occurrence)


def _claim_fresh_occurrence(
    source_locator,
    entry: ProvenanceEntry,
    root_key: str,
    scope_hint: str | None,
    used: set[int],
) -> int:
    """Picks the physical occurrence a brand-new (never borrowed) claim
    should use - content-verified first, falling back to the original
    "next unclaimed, in ascending order" scheme only when content
    verification can't apply. See this module's own docstring for the real
    837P/837I/837D bug this exists to fix, and why the fallback is safe for
    every case the original scheme already got right."""
    expected = entry.source_value if entry.source_value is not None else entry.value
    if expected is not None:
        total = _occurrence_count(source_locator, root_key, scope_hint)
        for candidate in range(total):
            if candidate in used:
                continue
            span = _locate(source_locator, entry.source_location, candidate, scope_hint)
            if span is None:
                continue
            start, end = span
            if source_locator.display_text[start:end] == expected:
                return candidate
    candidate = 0
    while candidate in used:
        candidate += 1
    return candidate


def _resolve_source_span(
    entry: ProvenanceEntry,
    source_locator,
    scope_resolver: "_CdaScopeResolver | None",
    bundle: Bundle,
    claimed_occurrence: dict[tuple, int],
    resource_claims: dict[str, dict[tuple, tuple[int, bool]]],
    used_occurrences: dict[tuple, set[int]],
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
            used = used_occurrences.setdefault(count_key, set())
            occurrence = _claim_fresh_occurrence(source_locator, entry, root_key, scope_hint, used)
            is_original = True
        claimed_occurrence[claim_key] = occurrence
        # Recorded for *both* branches, not just the fresh one - a borrowed
        # occurrence is just as "spoken for" as a freshly-claimed one, so a
        # later content-matching search (see _claim_fresh_occurrence) must
        # never be able to pick it back up for an unrelated resource.
        used_occurrences.setdefault(count_key, set()).add(occurrence)
        if item_index is None and resource_id is not None:
            resource_claims.setdefault(resource_id, {})[count_key] = (occurrence, is_original)
    occurrence = claimed_occurrence[claim_key]

    return _locate(source_locator, entry.source_location, occurrence, scope_hint)


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
