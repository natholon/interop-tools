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
from app.cda.discharge_medications import CATEGORY_CODE as DISCHARGE_CATEGORY_CODE
from app.cda.discharge_medications import CATEGORY_SYSTEM as DISCHARGE_CATEGORY_SYSTEM
from app.cda.common import OID_TO_FHIR_SYSTEM
from app.provenance.models import CrosswalkReport, ProvenanceEntry
from app.provenance.position_index import build_fhir_position_index, build_source_position_index

_PALETTE_SIZE = 10

_ENTRY_INDEX_RE = re.compile(r"^Bundle\.entry\[(\d+)\]")
# Array fields where one location string is shared identically across every
# repetition, with no other disambiguator in the string itself - so the
# bracket index is the only thing that can tell two occurrences apart. See
# _item_index for why reaction[N]/referenceRange[N]/diagnosis[N] are
# deliberately NOT here. `detail[N]` is PaymentReconciliation.detail[], one
# per 835 CLP claim: all of them live on one resource and share "CLP-1"/
# "CLP-2"/"CLP-4", so without this the second claim resolved to the first
# claim's segment.
_REPEATING_FIELD_INDEX_RE = re.compile(r"\.(?:item|detail)\[(\d+)\]")


class HighlightMatch(BaseModel):
    fhir_path: str
    color_index: int | None = None
    source_span: tuple[int, int] | None = None
    fhir_span: tuple[int, int] | None = None
    fhir_token_type: str | None = None  # "string" | "number" | "literal" - see json_locator.JsonSpan


class PositionSpan(BaseModel):
    """One clickable region and what it is - see position_index.py."""

    start: int
    end: int
    path: str


class HighlightingPayload(BaseModel):
    display_source_text: str
    fhir_json_text: str
    matches: list[HighlightMatch]
    # Offset -> location for the caret readout under each pane. Carried
    # here rather than computed separately because the spans have to be
    # relative to the two display texts above, and this is where both are
    # already resolved.
    source_positions: list[PositionSpan] = []
    fhir_positions: list[PositionSpan] = []


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


CVX_SYSTEM = OID_TO_FHIR_SYSTEM["2.16.840.1.113883.12.292"]


def _is_cvx_coded(resource) -> bool:
    """A vaccine, not a medication - the Immunizations section's own
    INT-mood entries build a MedicationRequest too, and CVX is the only
    thing on the resource that tells them apart."""
    concept = getattr(resource, "medicationCodeableConcept", None)
    for coding in (concept.coding if concept else None) or []:
        if coding.system == CVX_SYSTEM:
            return True
    return False


def _is_discharge_medication(resource) -> bool:
    for concept in resource.category or []:
        for coding in concept.coding or []:
            if coding.system == DISCHARGE_CATEGORY_SYSTEM and coding.code == DISCHARGE_CATEGORY_CODE:
                return True
    return False


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
            # Three sections build a MedicationRequest from a
            # <substanceAdministration>, so the resource type alone says
            # nothing about which one. Discharge Medications carries the
            # same .category marker the reverse builder splits on (a plain
            # Medications entry never populates .category at all); an
            # INT-mood Immunization is CVX-coded, which no medication is.
            # Anything else is Medications, which is what every
            # MedicationRequest resolved to before.
            if _is_discharge_medication(resource):
                return "discharge_medications"
            if _is_cvx_coded(resource):
                return "immunizations"
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


def build_highlighting_payload(
    bundle: Bundle,
    report: CrosswalkReport,
    raw_text: str,
    source_format: str,
    fhir_json_text: str | None = None,
) -> HighlightingPayload:
    """The one entry point `app/routes/data_specification.py` calls. Never
    raises - a locator failing to build at all (e.g. `raw_text` somehow
    isn't the text that actually produced `bundle`, defensive, not
    currently reachable) degrades to "no source-side highlighting," and
    each individual entry's own resolution is independently guarded so one
    malformed location string can't take down the rest.

    `fhir_json_text` overrides the serialization taken from `bundle`, and
    exists for one real case: a reviewer's rejections are applied to the
    serialized dict, not the model (a rejected required value has to be
    expressed as value-absent-plus-extension, which fhir.resources cannot
    represent). Without this the displayed Bundle was always the
    *pre*-rejection one, so rejecting a value changed the returned bundle
    but not the pane the reviewer was looking at - it read as doing
    nothing. Spans are computed against whatever text is passed, so they
    stay aligned with what is actually shown."""
    if fhir_json_text is None:
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

    return HighlightingPayload(
        display_source_text=display_source_text,
        fhir_json_text=fhir_json_text,
        matches=matches,
        source_positions=[
            PositionSpan(start=e.start, end=e.end, path=e.path)
            for e in build_source_position_index(display_source_text, source_format)
        ],
        fhir_positions=[
            PositionSpan(start=e.start, end=e.end, path=e.path)
            for e in build_fhir_position_index(fhir_json_text)
        ],
    )


def _occurrence_count(source_locator, root_key: str, scope_hint: str | None) -> int:
    if isinstance(source_locator, CdaLocator):
        return source_locator.occurrence_count(root_key, scope_hint)
    return source_locator.occurrence_count(root_key)


def _locate(source_locator, source_location: str, occurrence: int, scope_hint: str | None) -> tuple[int, int] | None:
    if isinstance(source_locator, CdaLocator):
        return source_locator.locate(source_location, occurrence, scope_hint)
    return source_locator.locate(source_location, occurrence)


def _occurrence_carries_value(source_locator, entry, root_key, scope_hint, occurrence) -> bool | None:
    """Whether `occurrence` really holds this fact's own value.

    None when the question does not apply - the fact has no verbatim value
    to check (the mapper transformed it), or the location does not resolve
    there at all.
    """
    expected = entry.source_value if entry.source_value is not None else entry.value
    if expected is None:
        return None
    span = _locate(source_locator, entry.source_location, occurrence, scope_hint)
    if span is None:
        return None
    return source_locator.display_text[span[0]: span[1]] == expected


def _content_matched_occurrence(
    source_locator,
    entry: ProvenanceEntry,
    root_key: str,
    scope_hint: str | None,
    skip: set[int] | None = None,
) -> int | None:
    """The occurrence whose own text at this location equals the fact's
    recorded value, or None when nothing matches.

    `skip` excludes already-claimed occurrences, which is what a fresh
    claim wants. A resource that has to *share* an occurrence passes
    nothing, since every occurrence is already spoken for by then and the
    question is which one to share, not whether one is free.
    """
    expected = entry.source_value if entry.source_value is not None else entry.value
    if expected is None:
        return None
    total = _occurrence_count(source_locator, root_key, scope_hint)
    for candidate in range(total):
        if skip is not None and candidate in skip:
            continue
        span = _locate(source_locator, entry.source_location, candidate, scope_hint)
        if span is None:
            continue
        start, end = span
        if source_locator.display_text[start:end] == expected:
            return candidate
    return None


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
    matched = _content_matched_occurrence(source_locator, entry, root_key, scope_hint, skip=used)
    if matched is not None:
        return matched
    # Nothing unclaimed carries this value. A fact whose value *does* sit in
    # an already-claimed occurrence belongs to that one, shared with
    # whatever claimed it - several Observations built from one Results or
    # Vitals organizer are exactly that, and their own location strings
    # (component[N]) already say which member they are. Without this the
    # second member skipped its real organizer because a sibling had
    # claimed it, and resolved component[N] against the *next* organizer:
    # a Hemoglobin row pointing at the Leukocytes text.
    shared = _content_matched_occurrence(source_locator, entry, root_key, scope_hint)
    if shared is not None:
        return shared
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
            # Borrowing is a heuristic about relationships; the text is
            # direct evidence. A Composition references nearly every
            # resource in the Bundle, so it borrowed a narrative section's
            # <code> and reported the document's own type against a
            # section's code. Where the borrowed occurrence demonstrably
            # does not carry this fact's value and another one does, the
            # text wins.
            if borrowed is not None and _occurrence_carries_value(
                source_locator, entry, root_key, scope_hint, borrowed
            ) is False:
                matched = _content_matched_occurrence(source_locator, entry, root_key, scope_hint)
                if matched is not None:
                    borrowed = matched
        used = used_occurrences.setdefault(count_key, set())
        if borrowed is None and item_index is None and resource_id is not None:
            # Once every physical occurrence is claimed, a further resource
            # cannot have one of its own - several resources routinely come
            # from one source element (a C-CDA Vital Signs organizer builds
            # a panel plus one Observation per reading). Sharing a
            # connected resource's occurrence resolves; claiming an index
            # past the end resolves to nothing at all, which is what left
            # Vitals/Results/Procedures highlighting mostly blank.
            total = _occurrence_count(source_locator, root_key, scope_hint)
            if total and len(used) >= total:
                borrowed = _borrow_occurrence(
                    resource_id, count_key, resource_claims, reference_map, allow_relayed=True
                )
                if borrowed is None:
                    # Every occurrence is claimed, so this resource must
                    # share one - but which. An 835 builds a ClaimResponse
                    # per CLP, and each is a *different* claim, so
                    # collapsing them all onto the lowest occurrence made
                    # every claim past the first highlight the first CLP's
                    # text. Content matching picks the right one to share;
                    # min(used) stays the fallback for a value that was
                    # transformed on the way through and so matches no
                    # physical text (the C-CDA panel case this branch was
                    # originally written for).
                    borrowed = _content_matched_occurrence(source_locator, entry, root_key, scope_hint)
                    if borrowed is None:
                        borrowed = min(used)
        if borrowed is not None:
            occurrence, is_original = borrowed, False
        else:
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
    resource_id: str,
    count_key: tuple,
    resource_claims: dict[str, dict[tuple, tuple[int, bool]]],
    reference_map: dict[str, set[str]],
    allow_relayed: bool = False,
) -> int | None:
    """If another resource connected to `resource_id` by a Reference (either
    direction) already holds an *original* claim for `count_key` - one
    assigned by the counter, not itself borrowed - reuse it. See the module
    docstring for the shared-physical-segment problem this solves (ORU's
    OBX-16 Practitioner).

    **Restricted to original claims** because of a real bug: a Practitioner
    shared by two Observations' OBX-16 correctly lent its occurrence to the
    first, then wrongly lent the *same* one to the second - a genuinely later
    physical OBX - by acting as a bridge between two unrelated leaders. A
    shared follower can be borrowed from, but cannot relay a borrowed
    occurrence onward; the second referencer then claims its own fresh one.

    `allow_relayed` lifts that restriction, and only when every physical
    occurrence is already claimed. The bug depended on a fresh occurrence
    still being available; when none is, relaying beats inventing an index
    past the end that resolves to nothing. That is the ordinary case wherever
    one source element builds several resources - a Vital Signs organizer
    produces a panel plus one Observation per reading, all from one
    `<organizer>`."""
    relayed: int | None = None
    for related_id in reference_map.get(resource_id, ()):
        claims = resource_claims.get(related_id)
        if claims and count_key in claims:
            occurrence, is_original = claims[count_key]
            if is_original:
                return occurrence
            if relayed is None:
                relayed = occurrence
    return relayed if allow_relayed else None
