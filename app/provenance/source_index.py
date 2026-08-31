"""What field is at each offset of a source message, for the editor's own
hover hints.

The crosswalk's caret readout answers the same question, but only for a
message that has already been converted - its index is a by-product of
`build_highlighting_payload`. Editing a message is exactly when knowing
that `VISIT0001` is `PV1-19` helps most, and at that point there may be no
conversion at all: the text can be half-typed, or not convertible.

So this builds the index from the text alone. `build_source_position_index`
is already a pure function of the display text and its format, and the
three field-name tables are already keyed by location string, so nothing
here re-derives either - it only puts them together and sniffs the format
the way `app/pipeline.py` does.

**Offsets are relative to `display_text`, not the raw input.** Each
locator normalises (HL7v2 rewrites `\r` to `\n` and truncates to the
first message; X12 strips a BOM), so the caller has to check its own text
still matches before trusting an offset - see `app/static/app.js`.
"""

from dataclasses import dataclass

from app.pipeline import is_x12, is_xml
from app.provenance.cda_locator import CdaLocator
from app.provenance.edi_locator import EdiLocator
from app.provenance.hl7_locator import Hl7Locator
from app.provenance.position_index import build_source_position_index
from app.provenance.resolver import resolve_field_label


@dataclass(frozen=True)
class SourceIndex:
    source_format: str
    display_text: str
    # (start, end, path, label) - label is None where the format's own
    # scoped table has no name for that location, never a guess.
    entries: list[tuple[int, int, str, str | None]]


def build_source_index(raw_text: str) -> SourceIndex:
    """The index for whatever format `raw_text` looks like.

    Never raises: unparseable text simply yields no entries, which is the
    right answer while a message is half-typed.
    """
    if is_x12(raw_text):
        source_format, locator_type = "EDI", EdiLocator
    elif is_xml(raw_text):
        source_format, locator_type = "CDA", CdaLocator
    else:
        source_format, locator_type = "HL7v2", Hl7Locator

    try:
        locator = locator_type(raw_text)
        display_text = locator.display_text
    except Exception:
        return SourceIndex(source_format=source_format, display_text=raw_text, entries=[])

    try:
        positions = build_source_position_index(display_text, source_format)
    except Exception:
        return SourceIndex(source_format=source_format, display_text=display_text, entries=[])

    return SourceIndex(
        source_format=source_format,
        display_text=display_text,
        entries=[
            (e.start, e.end, e.path, resolve_field_label(source_format, e.path))
            for e in positions
        ],
    )
