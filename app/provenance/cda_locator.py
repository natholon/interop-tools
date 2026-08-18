"""Resolves a C-CDA `source_location` string - produced by
`app/provenance/location.py::xpath_location()`, e.g. `"act/
entryRelationship[SUBJ]/observation/value/@code"` - to its exact character
span in the raw XML text, for the Data Specification page's correlated
highlighting.

**Deliberately independent of `app/cda/parser.py::parse_document`** (which
returns a plain `xml.etree.ElementTree.Element` with no position
information at all, and is the well-tested real conversion path this
module must not risk regressing). Re-parses the raw text a second time
using `xml.parsers.expat` directly, the only stdlib layer that exposes
character positions during parsing (`parser.CurrentByteIndex` - confirmed
by direct construction to be a Python *string* character offset, not a
UTF-8 *byte* offset, despite the attribute's name; verified against a
non-ASCII fixture where the two diverge).

**The location-string grammar**, confirmed by enumerating every real
`xpath_location(...)` call site across `app/cda/*.py`:
```
segment := TAG                # first (or only) child with this tag
         | TAG[N]              # N-th (0-based) child sharing this tag,
                                #   no filtering by attribute
         | TAG[LABEL]           # child with this tag whose own @typeCode
                                #   (or @classCode) == LABEL (non-numeric)
         | TAG[LABEL][N]        # LABEL-filtered, then the N-th such match
         | "@" ATTR              # an attribute of the *current* element
                                 #   (must be the last segment)
         | "text()"              # the current element's own direct text
                                 #   content (must be the last segment)
location := segment ("/" segment)*
```
A bare `TAG` and `TAG[0]` are equivalent (both mean "the first matching
child") - most call sites write the bare form when a section only ever
has one such child in practice.

**The cross-section root-tag collision, and how it's resolved**: several
different C-CDA sections reuse the identical entry-container tag name for
their own, entirely distinct entries - both Problems and Allergies use
`<entry><act>...`, both Vital Signs and Results use `<entry><organizer>
...`, and Discharge Summary's own Hospital Discharge Diagnosis section
reuses Problems' own `<act>` shape byte-for-byte (see `app/cda/
hospital_discharge_diagnosis.py`). Naively counting "the N-th `<act>` (or
`<organizer>`) anywhere in the document" would interleave two unrelated
sections' own entries and resolve to the wrong physical element whenever
a document carries more than one of these sections at once - a real
correctness bug, not a hypothetical one, since a typical CCD carries both
Vital Signs and Results together. Fixed via an optional `scope_hint`
parameter on `occurrence_count()`/`locate()`, resolved by `app/provenance/
highlighting.py` from the Bundle's own resource graph (never guessed at
here) and mapped, in this module only, to the real section-entry template
ID via `_SCOPE_TEMPLATE_IDS` - reusing each C-CDA section module's own
already-public `*_TEMPLATE_ID` constant, not a duplicated/guessed value.
Candidate root elements are then filtered to those carrying a matching
`<templateId root="...">` child before counting/selecting an occurrence."""

import re
import xml.parsers.expat
from dataclasses import dataclass, field

from app.cda.allergies import ALLERGY_CONCERN_ACT_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID
from app.cda.immunizations import IMMUNIZATION_ACTIVITY_TEMPLATE_ID
from app.cda.medications import MEDICATION_ACTIVITY_TEMPLATE_ID
from app.cda.problems import CONCERN_ACT_TEMPLATE_ID
from app.cda.procedures import PROCEDURE_TEMPLATE_ID
from app.cda.results import ORGANIZER_TEMPLATE_ID as RESULTS_ORGANIZER_TEMPLATE_ID
from app.cda.vitals import ORGANIZER_TEMPLATE_ID as VITALS_ORGANIZER_TEMPLATE_ID

Span = tuple[int, int]

# scope_hint -> the real section-entry template ID a candidate root
# element's own <templateId> child must match. See module docstring for
# why this exists and why it's scoped to this module only.
_SCOPE_TEMPLATE_IDS = {
    "problems": CONCERN_ACT_TEMPLATE_ID,
    "hospital_discharge_diagnosis": HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID,
    "allergies": ALLERGY_CONCERN_ACT_TEMPLATE_ID,
    "medications": MEDICATION_ACTIVITY_TEMPLATE_ID,
    "immunizations": IMMUNIZATION_ACTIVITY_TEMPLATE_ID,
    "procedures": PROCEDURE_TEMPLATE_ID,
    "results": RESULTS_ORGANIZER_TEMPLATE_ID,
    "vitals": VITALS_ORGANIZER_TEMPLATE_ID,
}


@dataclass
class _ElementNode:
    tag: str
    attrs: dict[str, str]
    start_tag_span: Span
    children: list["_ElementNode"] = field(default_factory=list)
    text: str = ""
    text_span: Span | None = None


def _strip_prefix(name: str) -> str:
    """expat, used here without namespace processing, hands back a raw
    tag/attribute name including any literal namespace prefix (e.g.
    "cda:code") - this app's own fixtures use a bare default namespace
    (no prefix) exclusively, but stripping defensively costs nothing and
    protects against a differently-authored document."""
    return name.rsplit(":", 1)[-1]


def _find_start_tag_end(text: str, start: int) -> int:
    """Given `start` = the index of the `<` beginning a start tag, returns
    the index just past its own closing `>` (whether `...>` or `.../>`).
    Scans character-by-character tracking quote state, since an attribute
    value may legitimately contain an unescaped `>` but never an unescaped
    copy of its own opening quote character - this is intentionally
    independent of expat's own `EndElementHandler` byte-offset semantics
    (which don't cleanly distinguish "end of this start tag" from "end of
    this element" for a self-closing element), and is the only place this
    module needs the true end of one specific start tag's own text."""
    i = start + 1
    quote: str | None = None
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == ">":
            return i + 1
        i += 1
    return len(text)  # malformed/truncated input - degrade gracefully rather than looping forever


def parse_with_positions(raw_xml: str) -> _ElementNode | None:
    """Re-parses `raw_xml` (already known-parseable - conversion already
    succeeded via the real `app/cda/parser.py::parse_document`) into a
    position-aware tree. Returns `None` on any parse failure - defensive,
    not currently reachable given the input already parsed once, but the
    Data Specification page's own highlighting feature must never crash
    over a rendering concern unrelated to the conversion that already
    succeeded."""
    parser = xml.parsers.expat.ParserCreate()
    root: _ElementNode | None = None
    stack: list[_ElementNode] = []

    def start_element(name: str, attrs: dict) -> None:
        nonlocal root
        start = parser.CurrentByteIndex
        end = _find_start_tag_end(raw_xml, start)
        node = _ElementNode(
            tag=_strip_prefix(name),
            attrs={_strip_prefix(k): v for k, v in attrs.items()},
            start_tag_span=(start, end),
        )
        if stack:
            stack[-1].children.append(node)
        else:
            root = node
        stack.append(node)

    def end_element(name: str) -> None:
        if stack:
            stack.pop()

    def char_data(data: str) -> None:
        if not stack:
            return
        node = stack[-1]
        start = parser.CurrentByteIndex
        if node.text_span is None:
            node.text_span = (start, start + len(data))
        else:
            node.text_span = (node.text_span[0], start + len(data))
        node.text += data

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = char_data
    try:
        parser.Parse(raw_xml, True)
    except xml.parsers.expat.ExpatError:
        return None
    return root


_SEGMENT_RE = re.compile(r"^([A-Za-z_][\w.-]*)(?:\[([^\]]*)\])?(?:\[(\d+)\])?$")


@dataclass(frozen=True)
class ParsedSegment:
    tag: str
    label: str | None  # a non-numeric bracket (typeCode/classCode filter), or None
    index: int  # 0-based; defaults to 0 when no numeric bracket is given


def parse_segment(segment: str) -> ParsedSegment:
    """Parses one `/`-separated piece of an `xpath_location()`-shaped
    string per the grammar in this module's own docstring. A malformed
    segment (not currently reachable - every real call site produces one
    of the documented shapes) degrades to `ParsedSegment(segment, None,
    0)`, matching a literal one-time tag lookup rather than raising."""
    match = _SEGMENT_RE.match(segment)
    if match is None:
        return ParsedSegment(segment, None, 0)
    tag, bracket1, bracket2 = match.groups()
    if bracket1 is None:
        return ParsedSegment(tag, None, 0)
    if bracket2 is not None:
        return ParsedSegment(tag, bracket1, int(bracket2))
    if bracket1.isdigit():
        return ParsedSegment(tag, None, int(bracket1))
    return ParsedSegment(tag, bracket1, 0)


def _select_child(node: _ElementNode, parsed: ParsedSegment) -> _ElementNode | None:
    matches = [
        child
        for child in node.children
        if child.tag == parsed.tag
        and (parsed.label is None or child.attrs.get("typeCode") == parsed.label or child.attrs.get("classCode") == parsed.label)
    ]
    if parsed.index < 0 or parsed.index >= len(matches):
        return None
    return matches[parsed.index]


_ATTR_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _attr_pattern(attr_name: str) -> re.Pattern:
    if attr_name not in _ATTR_PATTERN_CACHE:
        # A leading whitespace requirement is what correctly distinguishes
        # e.g. "code" from "codeSystem"/"mycode" - a real attribute name
        # is always preceded by whitespace (the previous attribute's own
        # closing quote is never immediately adjacent without one, and XML
        # requires whitespace between the tag name and its first
        # attribute too). The optional `(?:[\\w.-]+:)?` tolerates a
        # namespace-prefixed attribute (e.g. "xsi:type") even though this
        # app's own fixtures never use one.
        _ATTR_PATTERN_CACHE[attr_name] = re.compile(rf'\s(?:[\w.-]+:)?{re.escape(attr_name)}\s*=\s*(["\'])')
    return _ATTR_PATTERN_CACHE[attr_name]


def _resolve_attribute_span(raw_xml: str, start_tag_span: Span, attr_name: str) -> Span | None:
    """Finds `attr_name`'s own value span *within* the element's own start
    tag text (scoped to `start_tag_span` first, so there's no risk of
    matching a same-named attribute on a completely different element).
    Deliberately searches the *raw* text for the value rather than
    comparing against the already-unescaped value expat handed back in
    `node.attrs` - sidesteps needing to re-escape entities to find the
    span at all, since the raw quoted text between the matched quotes
    *is* the span, whatever it literally contains."""
    tag_start, tag_end = start_tag_span
    tag_text = raw_xml[tag_start:tag_end]
    match = _attr_pattern(attr_name).search(tag_text)
    if match is None:
        return None
    quote_char = match.group(1)
    value_start = match.end()
    value_end = tag_text.find(quote_char, value_start)
    if value_end == -1:
        return None
    return (tag_start + value_start, tag_start + value_end)


def _has_template_id(node: _ElementNode, template_id: str) -> bool:
    """Mirrors `app/cda/parser.py::has_template_id`'s own "direct children
    only" discipline - a nested descendant's own templateId several levels
    down must never false-match a shallower ancestor's own check."""
    return any(child.tag == "templateId" and child.attrs.get("root") == template_id for child in node.children)


def _resolve_from(node: _ElementNode, segments: list[str], raw_xml: str) -> Span | None:
    """Walks every segment except the last as a normal child descent, then
    resolves the final segment - one of three shapes, confirmed by
    enumerating every real *final* location string this app actually
    records (not the many intermediate, further-extended base paths that
    `xpath_location()` is also used to build):
      - `@attr` - an attribute of the *current* element (e.g. coded
        values, `@code`/`@displayName`/`@value`).
      - `text()` - the current element's own direct text content, used
        when a caller already descended to the target element itself
        (e.g. a free-text lab result value).
      - a bare tag (e.g. `family`, `given[0]`, `city`, or the `<text>`
        element in a free-text medication SIG) - the common shape for a
        simple, single-purpose text-bearing field: descend into *that*
        child, then take *its own* text content. Confirmed against every
        real call site ending this way in `app/cda/common.py`/
        `medications.py` - a genuine, previously-uncaught bug during this
        module's own development mistakenly resolved this shape to the
        child's own *start tag* span instead of its text, which highlit
        `<family>` instead of the name itself.
    An entirely empty `segments` list (the location string was just its
    own root segment, e.g. the document-level `<id>` this app's Bundle-
    level identifier reads) resolves to the *current* element's own start
    tag span - a disclosed, deliberate simplification: that one field's
    real value is derived from *either* the id's own `@extension` *or*
    (when absent) its `@root` attribute, an ambiguity this module doesn't
    try to resolve exactly, the same "no fitting single value, don't
    guess" precedent as an out-of-range field elsewhere in this app."""
    for segment in segments[:-1]:
        child = _select_child(node, parse_segment(segment))
        if child is None:
            return None
        node = child

    if not segments:
        return node.start_tag_span

    final = segments[-1]
    if final == "text()":
        return node.text_span
    if final.startswith("@"):
        return _resolve_attribute_span(raw_xml, node.start_tag_span, final[1:])
    child = _select_child(node, parse_segment(final))
    return child.text_span if child else None


class CdaLocator:
    """Built once per document. `display_text` is the raw XML text
    unchanged - C-CDA has no `\\r`-vs-`\\n`/BOM normalization concern the
    way HL7v2/EDI do (`app/cda/parser.py::parse_document` parses the given
    text as-is)."""

    def __init__(self, raw_xml: str) -> None:
        self.display_text = raw_xml
        self._root = parse_with_positions(raw_xml)

    def root_key(self, source_location: str) -> str | None:
        """The first segment's own bare tag name - the "which repeating
        structural unit" key `app/provenance/highlighting.py`'s own
        occurrence-claiming algorithm groups by."""
        if not source_location:
            return None
        return source_location.split("/", 1)[0].split("[", 1)[0]

    def _candidates(self, root_tag: str, scope_hint: str | None) -> list[_ElementNode]:
        if self._root is None:
            return []
        template_id = _SCOPE_TEMPLATE_IDS.get(scope_hint) if scope_hint else None
        found: list[_ElementNode] = []

        def walk(node: _ElementNode) -> None:
            for child in node.children:
                if child.tag == root_tag and (template_id is None or _has_template_id(child, template_id)):
                    found.append(child)
                walk(child)

        walk(self._root)
        return found

    def occurrence_count(self, root_tag: str, scope_hint: str | None = None) -> int:
        return len(self._candidates(root_tag, scope_hint))

    def locate(self, source_location: str, occurrence: int, scope_hint: str | None = None) -> Span | None:
        """Resolves `source_location` against the `occurrence`-th (0-based)
        candidate root element - see this module's own docstring for what
        `scope_hint` disambiguates and why it's needed. Returns `None` for
        anything unresolvable rather than raising."""
        if self._root is None or not source_location:
            return None
        segments = source_location.split("/")
        root_tag = parse_segment(segments[0]).tag
        candidates = self._candidates(root_tag, scope_hint)
        if occurrence < 0 or occurrence >= len(candidates):
            return None
        return _resolve_from(candidates[occurrence], segments[1:], self.display_text)
