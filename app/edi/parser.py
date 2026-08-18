"""X12 EDI parsing primitives - the app/hl7/parser.py and app/cda/parser.py
equivalent for X12 delimited text. Unlike HL7v2 (fixed |^~\\& delimiters) or
XML (self-describing via angle brackets), X12's own delimiters are defined
BY the file itself, at fixed byte positions within the ISA segment - a
parser must read ISA before it can even split the rest of the file into
segments. Two real X12 gotchas these primitives are built to make
structurally impossible to hit by accident: (1) ISA is the ONLY
fixed-width segment (106 characters, spec-guaranteed) - every other
segment, including its own trailer IEA, is delimiter-based and
variable-width, so the byte-position trick must never be generalized past
ISA itself; (2) trailing omitted elements (`SVC*HC:99213**100*95~`) are
normal, not malformed - `element()`'s bounds-guard is what makes indexing
into a short segment safe rather than raising.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.edi.errors import EdiParseError

_BOM = "﻿"
_ISA_LENGTH = 106


@dataclass(frozen=True)
class Delimiters:
    element: str
    component: str
    repetition: str
    segment_terminator: str


# A segment is its own positional field list - segment[0] is the segment ID
# (e.g. "ISA", "NM1"), segment[1] is the first data element (X12 calls this
# "NM101" etc. - 1-based, matching element()'s own indexing below).
Segment = list[str]


@dataclass
class TransactionSet:
    st01: str
    st02: str
    # ST03 (Implementation Convention Reference) - situational (empty
    # string when absent, common for 4010-era or ST03-omitting senders),
    # carries the same TR3 identifier as GS08 (e.g. "005010X222A1" for
    # 837P, "005010X223A2" for 837I) but is local to the ST segment itself,
    # confirmed as the officially authoritative field for this ("ST03 will
    # always take precedence over GS08" per multiple companion guides) -
    # used to disambiguate transaction-set families that share one ST01
    # but multiple TR3s, e.g. 837P vs 837I vs 837D (see
    # app/edi/registry.py's own dispatch for the one real consumer so far).
    st03: str = ""
    segments: list[Segment] = field(default_factory=list)
    se: Segment | None = None  # SE itself - SE01 (declared segment count) is checked against it in validation


@dataclass
class FunctionalGroup:
    transaction_sets: list[TransactionSet] = field(default_factory=list)
    ge: Segment | None = None  # GE itself - GE01 (declared transaction-set count) is checked against it in validation


@dataclass
class Interchange:
    delimiters: Delimiters
    isa: Segment
    iea: Segment
    functional_groups: list[FunctionalGroup] = field(default_factory=list)


@dataclass
class HlLoop:
    hl01: str
    hl02: str
    hl03: str
    has_children: bool
    member_segments: list[Segment] = field(default_factory=list)
    children: list["HlLoop"] = field(default_factory=list)


def strip_bom_and_whitespace(raw_text: str) -> str:
    """Same BOM-before-whitespace ordering app/pipeline.py::is_xml already
    documents: the BOM character isn't itself whitespace, so it must be
    stripped first or a leading BOM stops a whitespace-only lstrip() before
    it ever reaches the whitespace that follows it. Idempotent - safe to
    call on already-stripped text.

    Public (not module-private) - app/provenance/edi_locator.py became a
    second real consumer, needing the exact same stripped text
    read_isa_delimiters()/split_segments() themselves parse against
    (re-deriving it independently would risk silent drift)."""
    return raw_text.lstrip(_BOM).lstrip()


def read_isa_delimiters(raw_text: str) -> Delimiters:
    """Read the four delimiters an X12 interchange defines for itself, by
    fixed byte position within the ISA segment (verified against the
    published 5010 ISA layout: 3-byte literal "ISA" + 16 elements each
    preceded by a 1-byte separator, totalling exactly 106 bytes with the
    trailing segment terminator):
      - element separator:      byte 3  (immediately after the literal "ISA")
      - repetition separator:   byte 82 (ISA11 - 5010 only; captured, not
                                  interpreted this slice, since no field this
                                  app maps needs it yet)
      - component separator:    byte 104 (ISA16)
      - segment terminator:     byte 105
    Raises EdiParseError if the text (after BOM/whitespace stripping) is
    under 106 bytes or doesn't start with "ISA"."""
    text = strip_bom_and_whitespace(raw_text)
    if len(text) < _ISA_LENGTH or not text.startswith("ISA"):
        raise EdiParseError("Input does not start with a valid 106-character ISA segment")
    return Delimiters(
        element=text[3],
        component=text[104],
        repetition=text[82],
        segment_terminator=text[105],
    )


def split_segments(raw_text: str, delimiters: Delimiters) -> list[Segment]:
    """Split raw text into segments using the given delimiters. Strips
    surrounding whitespace from each segment before splitting into
    elements - real-world files commonly emit a line break after the
    segment terminator despite the spec not calling for one; without this,
    the stray leading newline glues onto the next segment's first element
    (e.g. "\\nGS" instead of "GS"), silently breaking every subsequent
    segment-ID match. Empty segments (a trailing terminator with nothing
    after it) are dropped."""
    text = strip_bom_and_whitespace(raw_text)
    segments: list[Segment] = []
    for raw_segment in text.split(delimiters.segment_terminator):
        cleaned = raw_segment.strip()
        if not cleaned:
            continue
        segments.append(cleaned.split(delimiters.element))
    return segments


def element(segment: Segment, index: int) -> str:
    """1-based element access (X12 convention - element 1 is segment[1],
    since segment[0] is the segment ID itself). Out-of-range or missing
    returns "" rather than raising, the same "trailing omitted elements are
    normal" guarantee app.hl7.parser::field_str provides for HL7 fields."""
    if index < 1 or index >= len(segment):
        return ""
    return segment[index]


def component(value: str, delimiters: Delimiters, index: int) -> str:
    """1-based access into a composite element value's sub-elements (e.g.
    NM103 in a composite NM1 name field). Bounds-guarded the same way
    element() is - out-of-range or an empty value returns ""."""
    if not value:
        return ""
    parts = value.split(delimiters.component)
    if index < 1 or index > len(parts):
        return ""
    return parts[index - 1]


def parse_decimal(raw: str) -> Decimal | None:
    """Defensively parse an X12 monetary/quantity element (e.g. BPR02,
    CLP03/04, EQ/EB amounts) as a Decimal, returning None on any failure -
    the same bounds-guard contract element()/component() already provide,
    extended to numeric parsing. Deliberately rejects the IEEE-754 special
    values ("NaN"/"sNaN"/"Infinity"/"-Infinity"), which `Decimal()` itself
    parses successfully (no InvalidOperation raised) - a bare `except
    InvalidOperation` around `Decimal(raw)` therefore does NOT guard
    against a malformed "NaN" value slipping through as a "valid" amount,
    which then raises InvalidOperation later and uncaught the moment it's
    used in an ordered comparison (e.g. `paid > charge`) - the exact
    "validator crashes uncaught on a fat-fingered input it exists to flag"
    failure class already disclosed twice elsewhere in this app (PID-7's
    Feb-31 calendar date, the doubled-minus-sign trailer count `_int_or_
    none()` was written to fix). Shared by app.edi.remittance_835 (for
    Money construction) and app.edi.validation (for the BPR02/CLP03/CLP04
    plausibility comparisons) - a single implementation rather than two,
    after a follow-up code review caught both modules independently
    re-deriving the same try/except Decimal(...) guard."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return value


def parse_interchange(raw_text: str) -> Interchange:
    """Parse raw X12 text into an Interchange tree. Envelope boundaries
    (ISA/IEA, GS/GE, ST/SE) are found by walking segments in order and
    matching on segment ID - NOT by trusting the trailer segments' own
    declared counts (IEA02/GE01/SE01), since a wrong count is a plausible
    real-world sender bug that shouldn't block parsing (see
    app/edi/validation.py for where count mismatches are surfaced instead,
    as warning-severity findings - the EDI analogue of "never raise for
    something a real sender could plausibly produce").

    Raises EdiParseError only when the envelope's own nesting is genuinely
    broken (a boundary segment encountered with no matching opener, or an
    opener never closed before its own parent closes) - NOT when the
    interchange is simply empty (well-formed but containing zero
    transaction sets). That distinction mirrors app.hl7.parser::
    require_segment / app.cda.common::build_patient_from_header: structural
    absence of *content* is MissingSegmentError, raised later by whatever
    actually needed that content - see first_transaction_set() below."""
    delimiters = read_isa_delimiters(raw_text)
    segments = split_segments(raw_text, delimiters)
    if not segments or segments[0][0] != "ISA":
        raise EdiParseError("Interchange does not begin with an ISA segment")
    isa = segments[0]

    iea: Segment | None = None
    functional_groups: list[FunctionalGroup] = []
    current_gs: FunctionalGroup | None = None
    current_st: TransactionSet | None = None

    for seg in segments[1:]:
        seg_id = seg[0]
        if seg_id == "GS":
            current_gs = FunctionalGroup()
        elif seg_id == "ST":
            if current_gs is None:
                raise EdiParseError("ST segment encountered outside of a GS/GE functional group")
            current_st = TransactionSet(st01=element(seg, 1), st02=element(seg, 2), st03=element(seg, 3))
        elif seg_id == "SE":
            if current_st is None:
                raise EdiParseError("SE segment encountered with no matching ST")
            current_st.se = seg
            current_gs.transaction_sets.append(current_st)
            current_st = None
        elif seg_id == "GE":
            if current_gs is None:
                raise EdiParseError("GE segment encountered with no matching GS")
            if current_st is not None:
                raise EdiParseError("GS/GE functional group closed with an unterminated ST (missing SE)")
            current_gs.ge = seg
            functional_groups.append(current_gs)
            current_gs = None
        elif seg_id == "IEA":
            if current_gs is not None:
                raise EdiParseError("Interchange closed with an unterminated GS (missing GE)")
            iea = seg
        elif current_st is not None:
            current_st.segments.append(seg)
        # Segments outside any ST (e.g. between GS and its first ST) are not
        # expected in real 270/271 files and are silently ignored - there is
        # nothing meaningful to attach them to.

    if iea is None:
        raise EdiParseError("Interchange is missing its IEA trailer segment")

    return Interchange(delimiters=delimiters, isa=isa, iea=iea, functional_groups=functional_groups)


def first_transaction_set(interchange: Interchange) -> TransactionSet | None:
    """The first ST...SE transaction set in the interchange, or None if the
    interchange (though structurally well-formed) contains zero. Every
    pipeline in this app has a strict one-input-to-one-Bundle contract, and
    real 270/271 files are frequently batched (multiple ST per GS, multiple
    GS per ISA) - Phase 1 processes only the first, a disclosed scope limit
    (see app/edi/pipeline.py), not a parsing gap."""
    for functional_group in interchange.functional_groups:
        for transaction_set in functional_group.transaction_sets:
            return transaction_set
    return None


def find_segment(segments: list[Segment], segment_id: str) -> Segment | None:
    """Return the first segment with the given id in `segments`, or None -
    the flat-list equivalent of app.cda.parser::find_child for a loop's
    member_segments (e.g. finding the one NM1 or DMG segment within an
    HlLoop's members)."""
    for seg in segments:
        if seg[0] == segment_id:
            return seg
    return None


def group_by_leader(
    segments: list[Segment], leader_id: str, member_ids
) -> list[tuple[Segment, list[Segment]]]:
    """Walk `segments` in order and group them by a repeating
    leader-then-members structure (e.g. 270/271's EQ/EB leader followed by
    its REF/DTP/MSG members) - the X12 port of app.hl7.parser::
    group_segments_by_leader's exact algorithm: every occurrence of
    `leader_id` starts a new group, each subsequent segment whose id is in
    `member_ids` is appended to that group until the next leader. Segments
    before the first leader, or whose id is neither the leader nor a
    member, are skipped. Returns (leader_segment, [member_segments])
    tuples, one per leader occurrence, in order."""
    member_ids = set(member_ids)
    groups: list[tuple[Segment, list[Segment]]] = []
    current_members: list[Segment] | None = None
    for seg in segments:
        seg_id = seg[0]
        if seg_id == leader_id:
            current_members = []
            groups.append((seg, current_members))
        elif seg_id in member_ids and current_members is not None:
            current_members.append(seg)
    return groups


def group_by_hl_hierarchy(segments: list[Segment]) -> list[HlLoop]:
    """Reconstruct the HL-segment hierarchy (2000A/2000B/2000C/... loops in
    270/271, and other X12 transaction sets that use the same pattern) from
    its flat, sibling-pointer-based encoding: HL01 (this loop's own id),
    HL02 (parent's id, empty for roots), HL03 (level code - e.g.
    "20"=Information Source, "22"=Subscriber for 270/271), HL04
    (has-children flag "1"/"0"). Nothing in HL7v2 or C-CDA needs this shape
    (HL7's groups are leader/member only; C-CDA's nesting is positional/
    XML-native) - this is the one genuinely new grouping primitive X12
    requires.

    Two passes: first builds a flat HlLoop per HL segment (every following
    non-HL segment up to the next HL is that loop's member_segments - the
    same leader/member idea as group_by_leader, specialized to HL); second
    links .children by matching hl02 -> some other loop's hl01. A loop
    whose hl02 doesn't resolve to any hl01 in this transaction set
    (including a genuinely root loop, whose hl02 is simply absent) is
    treated as a root rather than dropped or raised on - a safe, disclosed
    default for a malformed sibling pointer."""
    flat: list[HlLoop] = []
    current: HlLoop | None = None
    for seg in segments:
        if seg[0] == "HL":
            current = HlLoop(
                hl01=element(seg, 1),
                hl02=element(seg, 2),
                hl03=element(seg, 3),
                has_children=element(seg, 4) == "1",
            )
            flat.append(current)
        elif current is not None:
            current.member_segments.append(seg)

    by_id = {loop.hl01: loop for loop in flat if loop.hl01}
    roots: list[HlLoop] = []
    for loop in flat:
        parent = by_id.get(loop.hl02) if loop.hl02 else None
        if parent is not None:
            parent.children.append(loop)
        else:
            roots.append(loop)
    return roots
