"""Source-location string formatters, one per input format, so every
mapper call site that records a fact composes its `source_location`
string the same consistent way rather than each hand-writing its own
f-string. X12 EDI's own equivalent (`edi_location()`) is added when that
format's own provenance slice is actually implemented, not speculatively
now."""


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


def xpath_location(*segments: str) -> str:
    """`xpath_location("recordTarget", "patientRole", "patient", "name[0]",
    "family")` -> `"recordTarget/patientRole/patient/name[0]/family"` - the
    C-CDA equivalent of `hl7_location()`.

    Composed *forward* at each call site from the same relative-path
    segments the caller is already walking via `app/cda/parser.py`'s
    `find_child`/`find_all` - stdlib `xml.etree.ElementTree` has no parent
    pointers, so there's no way to reconstruct a path *backward* from a
    bare `Element` after the fact the way `resolve_bundle_paths` does for
    a FHIR resource's own `Bundle.entry[N]` index. Each segment may itself
    already carry its own 0-based repetition index (e.g. `"name[0]"`),
    since - unlike HL7v2's fixed field/component shape - a repeating
    element can appear at any depth along a CDA path, not just the last
    one. An attribute-derived value (a coded element's own `@code`, a
    `@value` on a TS/PQ-shaped element, ...) is named with a trailing
    `"/@attr"` segment, matching real XPath's own attribute-axis syntax,
    e.g. `xpath_location("administrativeGenderCode", "@code")`."""
    return "/".join(segments)
