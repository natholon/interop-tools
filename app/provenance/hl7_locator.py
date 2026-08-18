"""Resolves an HL7v2 `source_location` string - produced by
`app/provenance/location.py::hl7_location()`, e.g. `"PID-5[0].1"`
(SEGMENT-field[repetition].component) - to its exact character span in
the raw message text, for the Data Specification page's correlated
highlighting.

Deliberately independent of the `hl7` library (whose object model exposes
no character-offset information at all) and of `app/hl7/parser.py`'s own
field/component helpers (`field_str`/`component_str`, built on top of the
`hl7` library's own indexing) - this module re-splits the raw text itself,
tracking offsets as it goes. It does reuse `app/hl7/parser.py::
normalize_segment_separators`/`truncate_to_first_message` directly (not
reimplemented) so the text these offsets are relative to is *exactly* the
text the real parser also sees - re-deriving that normalization
independently would risk silent drift between the two.

**The MSH field-numbering quirk, replicated exactly**: MSH-1 is the field
separator character itself (not a `|`-split token - there's nothing before
it to split on), and MSH-2 is the encoding-characters field: the `hl7`
library's own object model already accounts for this by synthesizing
MSH-1 back in, which shifts every subsequent field's `|`-split index by
one relative to every other segment. Confirmed by direct construction: for
`MSH|^~\\&|SENDER|...`, `msh[7]` (the `hl7` library's own 1-based access)
equals the *seventh* real field, which is the *sixth* `|`-split token of
the raw text (`"MSH|^~\\&|SENDER|...".split("|")[6]`), since the very
first split token is `"MSH"` itself, not a real field. This module
replicates that exact shift rather than re-deriving it from scratch."""

import re
from dataclasses import dataclass

from app.hl7.parser import normalize_segment_separators, truncate_to_first_message

_LOCATION_RE = re.compile(r"^([A-Z0-9]{3})-(\d+)(?:\[(\d+)\])?(?:\.(\d+))?$")

Span = tuple[int, int]


def _split_with_offsets(text: str, separator: str) -> list[tuple[str, int, int]]:
    """`text.split(separator)`, but each part paired with its own
    `(start, end)` character offset relative to `text`'s own start (0)."""
    parts = []
    pos = 0
    for part in text.split(separator):
        parts.append((part, pos, pos + len(part)))
        pos += len(part) + len(separator)
    return parts


@dataclass
class ParsedHl7Location:
    segment_id: str
    field: int
    repetition: int | None
    component: int | None


def parse_hl7_location(source_location: str) -> ParsedHl7Location | None:
    """Parses a `hl7_location()`-shaped string. Returns `None` for
    anything that doesn't match the expected grammar - defensive, not
    currently reachable in practice, but the caller must degrade
    gracefully (skip highlighting for that one fact) rather than crash the
    whole page over a single malformed location string."""
    match = _LOCATION_RE.match(source_location)
    if match is None:
        return None
    segment_id, field_str, repetition_str, component_str = match.groups()
    return ParsedHl7Location(
        segment_id=segment_id,
        field=int(field_str),
        repetition=int(repetition_str) if repetition_str is not None else None,
        component=int(component_str) if component_str is not None else None,
    )


class Hl7Locator:
    """Built once per message. `display_text` is the exact text every
    resolved span is relative to - `\\r`-joined segments substituted to
    `\\n` for display (a 1-for-1 character replacement, so this never
    shifts any offset), matching this app's own established "\\r
    substituted with \\n for display only" precedent from the FHIR ->
    Message page."""

    def __init__(self, raw_text: str) -> None:
        normalized = truncate_to_first_message(normalize_segment_separators(raw_text))
        self.display_text = normalized.replace("\r", "\n")
        # (segment_id, start, end) per physical segment occurrence, in
        # document order - start/end relative to display_text.
        self._segments: list[tuple[str, int, int]] = []
        pos = 0
        for part in self.display_text.split("\n"):
            self._segments.append((part[:3], pos, pos + len(part)))
            pos += len(part) + 1  # +1 for the \n separator just split on

    def root_key(self, source_location: str) -> str | None:
        """The segment id a location string addresses - the "which
        repeating structural unit" key app/provenance/highlighting.py's
        own occurrence-claiming algorithm groups by."""
        parsed = parse_hl7_location(source_location)
        return parsed.segment_id if parsed else None

    def occurrence_count(self, segment_id: str) -> int:
        return sum(1 for sid, _, _ in self._segments if sid == segment_id)

    def locate(self, source_location: str, occurrence: int) -> Span | None:
        """Resolves `source_location` against the `occurrence`-th (0-based)
        physical segment with the matching segment id, in document order.
        Returns `None` for anything unresolvable (malformed location
        string, an out-of-range field/repetition/component, an occurrence
        index beyond how many segments actually exist) rather than raising
        - degrades to "no highlight for this one fact," never crashes the
        page."""
        parsed = parse_hl7_location(source_location)
        if parsed is None:
            return None

        matching = [(start, end) for sid, start, end in self._segments if sid == parsed.segment_id]
        if occurrence >= len(matching):
            return None
        seg_start, seg_end = matching[occurrence]
        segment_text = self.display_text[seg_start:seg_end]

        field_span = self._resolve_field(segment_text, parsed.segment_id, parsed.field)
        if field_span is None:
            return None
        field_start, field_end = field_span
        field_text = segment_text[field_start:field_end]

        if parsed.repetition is not None:
            rep_span = self._resolve_sub(field_text, "~", parsed.repetition, one_based=False)
            if rep_span is None:
                return None
            rep_start, rep_end = rep_span
            field_start, field_end = field_start + rep_start, field_start + rep_end
            field_text = segment_text[field_start:field_end]

        if parsed.component is not None:
            comp_span = self._resolve_sub(field_text, "^", parsed.component, one_based=True)
            if comp_span is None:
                return None
            comp_start, comp_end = comp_span
            field_start, field_end = field_start + comp_start, field_start + comp_end

        return (seg_start + field_start, seg_start + field_end)

    @staticmethod
    def _resolve_field(segment_text: str, segment_id: str, field_num: int) -> Span | None:
        if segment_id == "MSH" and field_num == 1:
            # MSH-1 is the field separator character itself, immediately
            # after the literal "MSH" - there's no `|` before it to split
            # on, so this is the one genuinely special case.
            return (3, 4) if len(segment_text) > 3 else None
        parts = _split_with_offsets(segment_text, "|")
        # MSH's own field numbering is shifted by one relative to every
        # other segment - see this module's own docstring for why.
        index = field_num - 1 if segment_id == "MSH" else field_num
        if index < 0 or index >= len(parts):
            return None
        _, start, end = parts[index]
        return (start, end)

    @staticmethod
    def _resolve_sub(text: str, separator: str, index: int, *, one_based: bool) -> Span | None:
        parts = _split_with_offsets(text, separator)
        real_index = index - 1 if one_based else index
        if real_index < 0 or real_index >= len(parts):
            return None
        _, start, end = parts[real_index]
        return (start, end)
