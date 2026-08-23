"""Character offset -> location, for the crosswalk's caret position
readout.

Every other locator in this package answers the opposite question - given
a location string, where is it in the text. This answers it in reverse:
given where the caret is, what is the reader looking at. Both panes use
it, so clicking in the source says `PID-5.1` and clicking in the FHIR JSON
says `Bundle.entry[1].resource.name`.

Built as a flat list of `(start, end, path)` spans rather than a tree: the
client only needs "the most specific span containing this offset", which
is a scan for the shortest containing range. Sorting is left to the
consumer, since the walks emit in document order already.

**Offsets are relative to the text the pane actually displays**, which for
HL7v2 and X12 is the normalised text each locator exposes as
`display_text`, not the raw upload - the two differ by segment-separator
normalisation and, for HL7v2, truncation to the first message. Building
the index anywhere else would put every span out by the difference.

Containers are included, not just leaves. Clicking a `{` or a key in the
FHIR pane should still resolve to the object it opens, and clicking a
segment id in the source pane should resolve to the segment.
"""

from dataclasses import dataclass

from app.provenance.cda_locator import parse_with_positions
from app.provenance.json_locator import locate_json_paths_with_containers


@dataclass(frozen=True)
class PositionEntry:
    start: int
    end: int
    path: str


def build_fhir_position_index(json_text: str) -> list[PositionEntry]:
    """Every value *and* container in the serialized Bundle."""
    return [
        PositionEntry(span.start, span.end, path)
        for path, span in locate_json_paths_with_containers(json_text)
    ]


def build_source_position_index(display_text: str, source_format: str | None) -> list[PositionEntry]:
    if source_format == "HL7v2":
        return _hl7_index(display_text)
    if source_format == "EDI":
        return _edi_index(display_text)
    if source_format == "CDA":
        return _cda_index(display_text)
    return []


def _hl7_index(text: str) -> list[PositionEntry]:
    """Segment, field, repetition and component spans.

    MSH's field numbering is offset by one - MSH-1 is the field separator
    itself rather than a `|`-split token - so it is numbered separately,
    exactly as `hl7_locator.py` does.
    """
    entries: list[PositionEntry] = []
    seen: dict[str, int] = {}
    line_start = 0
    for line in text.split("\n"):
        if line.strip():
            segment_id = line.split("|", 1)[0]
            occurrence = seen.get(segment_id, 0)
            seen[segment_id] = occurrence + 1
            suffix = f"[{occurrence}]" if occurrence else ""
            entries.append(PositionEntry(line_start, line_start + len(line), f"{segment_id}{suffix}"))
            _hl7_fields(line, line_start, segment_id, suffix, entries)
        line_start += len(line) + 1
    return entries


def _hl7_fields(line: str, line_start: int, segment_id: str, suffix: str, entries: list[PositionEntry]) -> None:
    offset = line_start
    for token_index, token in enumerate(line.split("|")):
        if token_index == 0:
            offset += len(token) + 1
            continue
        # Every MSH field N>=2 is the (N-1)th `|`-split token; every other
        # segment's field N is token N.
        field_number = token_index + 1 if segment_id == "MSH" else token_index
        if token:
            _hl7_repetitions(token, offset, f"{segment_id}{suffix}-{field_number}", entries)
        offset += len(token) + 1


def _hl7_repetitions(token: str, offset: int, base: str, entries: list[PositionEntry]) -> None:
    repetitions = token.split("~")
    for repetition_index, repetition in enumerate(repetitions):
        path = base if len(repetitions) == 1 else f"{base}[{repetition_index}]"
        if repetition:
            entries.append(PositionEntry(offset, offset + len(repetition), path))
            component_offset = offset
            components = repetition.split("^")
            if len(components) > 1:
                for component_index, component in enumerate(components, start=1):
                    if component:
                        entries.append(
                            PositionEntry(
                                component_offset, component_offset + len(component), f"{path}.{component_index}"
                            )
                        )
                    component_offset += len(component) + 1
        offset += len(repetition) + 1


def _edi_index(text: str) -> list[PositionEntry]:
    """Segment, element and component spans, using the interchange's own
    self-declared delimiters."""
    from app.edi.parser import read_isa_delimiters

    try:
        delimiters = read_isa_delimiters(text)
    except Exception:
        return []

    entries: list[PositionEntry] = []
    seen: dict[str, int] = {}
    offset = 0
    for raw_segment in text.split(delimiters.segment_terminator):
        stripped = raw_segment.strip()
        if stripped:
            lead = len(raw_segment) - len(raw_segment.lstrip())
            start = offset + lead
            segment_id = stripped.split(delimiters.element, 1)[0]
            occurrence = seen.get(segment_id, 0)
            seen[segment_id] = occurrence + 1
            suffix = f"[{occurrence}]" if occurrence else ""
            entries.append(PositionEntry(start, start + len(stripped), f"{segment_id}{suffix}"))

            element_offset = start
            for element_index, element_text in enumerate(stripped.split(delimiters.element)):
                if element_index and element_text:
                    base = f"{segment_id}{suffix}-{element_index}"
                    entries.append(PositionEntry(element_offset, element_offset + len(element_text), base))
                    components = element_text.split(delimiters.component)
                    if len(components) > 1:
                        component_offset = element_offset
                        for component_index, component in enumerate(components, start=1):
                            if component:
                                entries.append(
                                    PositionEntry(
                                        component_offset,
                                        component_offset + len(component),
                                        f"{base}.{component_index}",
                                    )
                                )
                            component_offset += len(component) + 1
                element_offset += len(element_text) + 1
        offset += len(raw_segment) + len(delimiters.segment_terminator)
    return entries


def _cda_index(text: str) -> list[PositionEntry]:
    """Element and attribute spans, from the same position-aware parse
    `cda_locator.py` builds for span resolution."""
    root = parse_with_positions(text)
    if root is None:
        return []

    entries: list[PositionEntry] = []

    def walk(node, path: str) -> None:
        # The start tag and the element's own text are two places a reader
        # can click and mean the same element, so both map to its path.
        if node.start_tag_span:
            entries.append(PositionEntry(node.start_tag_span[0], node.start_tag_span[1], path))
        if node.text_span and node.text.strip():
            entries.append(PositionEntry(node.text_span[0], node.text_span[1], path))
        for name, value in node.attrs.items():
            if not value:
                continue
            attribute_span = _attribute_span(text, node.start_tag_span, name)
            if attribute_span:
                entries.append(PositionEntry(attribute_span[0], attribute_span[1], f"{path}/@{name}"))
        seen: dict[str, int] = {}
        for child in node.children:
            index = seen.get(child.tag, 0)
            seen[child.tag] = index + 1
            walk(child, f"{path}/{child.tag}" + (f"[{index}]" if index else ""))

    walk(root, root.tag)
    return entries


def _attribute_span(text: str, start_tag_span, name: str):
    """The value span of `name=` inside a start tag - the same shape
    `cda_locator._resolve_attribute_span` resolves, reimplemented here
    rather than imported because that one is private to span resolution
    and takes a different argument shape."""
    if not start_tag_span:
        return None
    start, end = start_tag_span
    tag_text = text[start:end]
    needle = f"{name}="
    at = tag_text.find(needle)
    if at == -1:
        return None
    quote_at = at + len(needle)
    if quote_at >= len(tag_text) or tag_text[quote_at] not in "\"'":
        return None
    quote = tag_text[quote_at]
    closing = tag_text.find(quote, quote_at + 1)
    if closing == -1:
        return None
    return (start + quote_at + 1, start + closing)
