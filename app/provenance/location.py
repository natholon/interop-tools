"""Source-location string formatters, one per input format, so every
mapper call site that records a fact composes its `source_location`
string the same consistent way rather than each hand-writing its own
f-string."""


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


def edi_location(
    segment_id: str, element_num: int, *, component: int | None = None, segment_repetition: int | None = None
) -> str:
    """`edi_location("NM1", 9)` -> `"NM1-9"`; `edi_location("HI", 1,
    component=2)` -> `"HI-1.2"` - the X12 equivalent of `hl7_location()`,
    intentionally the same `SEGMENT-element[.component]` shape since X12's
    own element numbering (`NM109`, i.e. "element 9 of segment NM1") is
    already positional like HL7v2's fields. `component` mirrors
    `app/edi/parser.py::component()`'s own 1-based sub-element numbering
    into a composite element value (e.g. `HI01`'s own qualifier:code pair).

    `segment_repetition` (0-based, mirroring `hl7_location()`'s own
    `repetition` convention) disambiguates X12's own repetition shape -
    unlike HL7v2, X12 handles a repeating field by repeating the whole
    *segment* rather than a value within one segment, so a caller walking
    several same-type segments (e.g. institutional/dental claims' own
    multiple `HI` segments, one per diagnosis code-list "type") needs a way
    to say *which* segment occurrence a value came from - `edi_location("HI",
    1, component=2, segment_repetition=1)` -> `"HI[1]-1.2"`. Omitted (the
    common case - most segments this app reads occur at most once per
    loop) for a location identical to the no-repetition form, so every
    pre-existing call site's own recorded location string is unaffected."""
    location = segment_id
    if segment_repetition is not None:
        location += f"[{segment_repetition}]"
    location += f"-{element_num}"
    if component is not None:
        location += f".{component}"
    return location
