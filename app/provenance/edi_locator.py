"""Resolves an X12 EDI `source_location` string - produced by
`app/provenance/location.py::edi_location()`, e.g. `"NM1-9"` or
`"HI[1]-1.2"` (SEGMENT[segment_repetition]-element[.component]) - to its
exact character span in the raw interchange text, for the Data
Specification page's correlated highlighting.

Re-splits the raw text itself (segment terminator -> element separator ->
component separator, using the interchange's own discovered `Delimiters` -
`app/edi/parser.py::read_isa_delimiters`), tracking offsets as it goes -
independent of `app/edi/parser.py::element`/`component` (which operate on
already-split lists with no character-offset information). Mirrors `app/
edi/parser.py::split_segments`'s own per-segment `.strip()` exactly (real-
world files commonly emit a stray newline after the segment terminator),
so offsets land on each segment's real content, not surrounding
whitespace - re-deriving that stripping behavior independently would risk
silent drift between the two."""

import re
from dataclasses import dataclass

from app.edi.common import DTP_SERVICE_DATE
from app.edi.parser import Delimiters, read_isa_delimiters, strip_bom_and_whitespace

_LOCATION_RE = re.compile(r"^([A-Z0-9]{2,3})(?:\[(\d+)\])?-(\d+)(?:\.(\d+))?$")

Span = tuple[int, int]


def _split_with_offsets(text: str, separator: str) -> list[tuple[str, int, int]]:
    parts = []
    pos = 0
    for part in text.split(separator):
        parts.append((part, pos, pos + len(part)))
        pos += len(part) + len(separator)
    return parts


@dataclass
class ParsedEdiLocation:
    segment_id: str
    segment_repetition: int | None
    element_num: int
    component: int | None


def parse_edi_location(source_location: str) -> ParsedEdiLocation | None:
    """Parses an `edi_location()`-shaped string. Returns `None` for
    anything that doesn't match the expected grammar - defensive, not
    currently reachable in practice, but the caller must degrade
    gracefully (skip highlighting for that one fact) rather than crash the
    whole page over a single malformed location string."""
    match = _LOCATION_RE.match(source_location)
    if match is None:
        return None
    segment_id, segment_repetition_str, element_str, component_str = match.groups()
    return ParsedEdiLocation(
        segment_id=segment_id,
        segment_repetition=int(segment_repetition_str) if segment_repetition_str is not None else None,
        element_num=int(element_str),
        component=int(component_str) if component_str is not None else None,
    )


class EdiLocator:
    """Built once per interchange. `display_text` is the exact text every
    resolved span is relative to - the raw text after the identical BOM/
    leading-whitespace strip `app/edi/parser.py::split_segments` itself
    applies (`strip_bom_and_whitespace`), so it can be shown to the user
    unchanged (X12 has no `\\r`-vs-`\\n` normalization concern the way
    HL7v2 does)."""

    def __init__(self, raw_text: str) -> None:
        self.delimiters: Delimiters = read_isa_delimiters(raw_text)
        self.display_text = strip_bom_and_whitespace(raw_text)
        # (segment_id, start, end) per physical segment occurrence, in
        # document order - start/end relative to display_text, scoped to
        # the segment's own stripped content (not surrounding whitespace).
        self._segments: list[tuple[str, int, int]] = []
        pos = 0
        terminator = self.delimiters.segment_terminator
        for raw_segment in self.display_text.split(terminator):
            stripped = raw_segment.strip()
            if stripped:
                lstrip_amount = len(raw_segment) - len(raw_segment.lstrip())
                seg_start = pos + lstrip_amount
                seg_end = seg_start + len(stripped)
                segment_id = stripped.split(self.delimiters.element, 1)[0]
                self._segments.append((segment_id, seg_start, seg_end))
            pos += len(raw_segment) + len(terminator)

    def root_key(self, source_location: str) -> str | None:
        """The segment id a location string addresses - the "which
        repeating structural unit" key app/provenance/highlighting.py's
        own occurrence-claiming algorithm groups by. A location string
        carrying an explicit `segment_repetition` never needs claiming (it
        already names its own occurrence), so highlighting.py skips the
        occurrence-claiming step for those - see that module's own docstring."""
        parsed = parse_edi_location(source_location)
        return parsed.segment_id if parsed else None

    def _matching_segments(self, segment_id: str) -> list[Span]:
        """Every physical occurrence of `segment_id`, filtered for the one
        segment type in this app's own field set that legitimately carries
        more than one *semantically distinct* use within a single
        transaction: `DTP` (Date/Time Reference) - a claim can carry both
        a `DTP*472` (service date, the only qualifier any `edi_location(
        "DTP", ...)` call in this app ever targets - confirmed by grepping
        every real call site) *and* a `DTP*434` (statement period, never
        read into any FHIR field). Without this filter, the unrelated
        `DTP*434` segment - which this app's own mappers already skip
        entirely - would still get counted as an "occurrence" by this
        locator, silently shifting every real DTP*472 occurrence's own
        index off by one (reproduced directly during this module's own
        development against a real 837I fixture, whose own claim-level
        `DTP*434` sits before its two per-line `DTP*472` segments)."""
        matching = [(start, end) for sid, start, end in self._segments if sid == segment_id]
        if segment_id != "DTP":
            return matching
        filtered = []
        for start, end in matching:
            qualifier_span = self._resolve_element(self.display_text[start:end], 1)
            if qualifier_span is None:
                continue
            q_start, q_end = qualifier_span
            if self.display_text[start + q_start : start + q_end] == DTP_SERVICE_DATE:
                filtered.append((start, end))
        return filtered

    def occurrence_count(self, segment_id: str) -> int:
        return len(self._matching_segments(segment_id))

    def locate(self, source_location: str, occurrence: int) -> Span | None:
        """Resolves `source_location` against the `occurrence`-th (0-based)
        physical segment with the matching segment id, in document order -
        unless the location string carries its own explicit
        `segment_repetition`, which is used directly instead (it already
        disambiguates which physical segment, so the caller-supplied
        `occurrence` is ignored in that case). Returns `None` for anything
        unresolvable rather than raising."""
        parsed = parse_edi_location(source_location)
        if parsed is None:
            return None

        matching = self._matching_segments(parsed.segment_id)
        target_occurrence = parsed.segment_repetition if parsed.segment_repetition is not None else occurrence
        if target_occurrence >= len(matching):
            return None
        seg_start, seg_end = matching[target_occurrence]
        segment_text = self.display_text[seg_start:seg_end]

        element_span = self._resolve_element(segment_text, parsed.element_num)
        if element_span is None:
            return None
        element_start, element_end = element_span

        if parsed.component is not None:
            element_text = segment_text[element_start:element_end]
            comp_span = self._resolve_sub(element_text, self.delimiters.component, parsed.component)
            if comp_span is None:
                return None
            comp_start, comp_end = comp_span
            element_start, element_end = element_start + comp_start, element_start + comp_end

        return (seg_start + element_start, seg_start + element_end)

    def _resolve_element(self, segment_text: str, element_num: int) -> Span | None:
        # 1-based, matching app/edi/parser.py::element()'s own convention
        # (segment[0] is the segment id itself, segment[1] is element 1).
        parts = _split_with_offsets(segment_text, self.delimiters.element)
        if element_num < 1 or element_num >= len(parts):
            return None
        _, start, end = parts[element_num]
        return (start, end)

    @staticmethod
    def _resolve_sub(text: str, separator: str, index: int) -> Span | None:
        # 1-based, matching app/edi/parser.py::component()'s own convention.
        parts = _split_with_offsets(text, separator)
        real_index = index - 1
        if real_index < 0 or real_index >= len(parts):
            return None
        _, start, end = parts[real_index]
        return (start, end)
