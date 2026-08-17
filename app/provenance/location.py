"""Source-location string formatters, one per input format, so every
mapper call site that records a fact composes its `source_location`
string the same consistent way rather than each hand-writing its own
f-string. Only HL7v2's exists yet (Phase 0's own scope) - C-CDA's and
X12 EDI's own equivalents (`xpath_location()`/`edi_location()`) are added
when those formats' own provenance slices are actually implemented, not
speculatively now. C-CDA's in particular needs its own real design at
that point: stdlib `xml.etree.ElementTree` (this app's own parsing choice
for `app/cda/parser.py`) has no parent pointers, so an XPath-shaped
location has to be composed *forward* while walking the document tree -
each `find_child`/`find_all` call extending a path string it's handed -
not reconstructed backward from a bare `Element` after the fact."""


def hl7_location(segment_id: str, field: int, *, repetition: int | None = None, component: int | None = None) -> str:
    """`hl7_location("PID", 5, repetition=0, component=1)` -> `"PID-5[0].1"`.
    `repetition` is the 0-based index `field_repetitions()`/`enumerate()`
    already use throughout this app's own mapper code - included only when
    a field's repetition genuinely matters (a repeating field like PID-5/
    PID-11), omitted for a field this app only ever reads position 0 of.
    `component` mirrors `field_str()`'s own 1-based component numbering -
    omitted for a field with no component structure (e.g. PID-7, a bare
    TS value)."""
    location = f"{segment_id}-{field}"
    if repetition is not None:
        location += f"[{repetition}]"
    if component is not None:
        location += f".{component}"
    return location
