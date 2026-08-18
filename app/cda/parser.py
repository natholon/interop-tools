"""C-CDA XML parsing primitives - the app/hl7/parser.py equivalent for the
XML input format. Built entirely on stdlib xml.etree.ElementTree (verified
sufficient for this project's scope: template-id-driven section/entry
lookup, fixed nesting depths, no need for XPath 1.0 axes/contains() or XSD
validation) - no new dependency.

Two real ElementTree gotchas shape every function here:
- A multi-segment relative path missing the namespace prefix on ANY segment
  fails silently (returns [], not an error) rather than raising - find_all
  auto-prefixes every '/'-separated segment so a caller cannot hit this by
  forgetting one.
- Every C-CDA template is emitted TWICE per element (a bare templateId with
  just @root, and a versioned sibling with @root + @extension) as sibling
  elements in no guaranteed order - has_template_id checks direct children
  for a matching @root rather than assuming find()'s first hit is the bare
  one.
"""

import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element

from app.cda.errors import CdaParseError

CDA_NS = "urn:hl7-org:v3"
_NS = {"hl7": CDA_NS}
_XSI_TYPE_ATTR = "{http://www.w3.org/2001/XMLSchema-instance}type"


def parse_document(raw_xml: str) -> Element:
    """Parse raw XML text into a ClinicalDocument root Element, or raise
    CdaParseError."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise CdaParseError(str(exc)) from exc
    if root.tag != f"{{{CDA_NS}}}ClinicalDocument":
        raise CdaParseError(f"Root element is {root.tag!r}, not ClinicalDocument")
    return root


def has_template_id(element: Element, root_oid: str) -> bool:
    """Whether `element` itself (not a descendant) declares the given
    templateId root OID - checking DIRECT CHILDREN only. A recursive
    search (.//) would false-match a nested descendant's templateId many
    levels down (e.g. checking a section for a template ID would
    incorrectly also match one of its entries' observations)."""
    return any(child.get("root") == root_oid for child in element.findall("hl7:templateId", _NS))


def find_child(element: Element, tag: str) -> Element | None:
    """Return the first direct child matching `tag` (a single element name,
    no path), or None."""
    return element.find(f"hl7:{tag}", _NS)


def find_all(element: Element, path: str) -> list[Element]:
    """Return every element matching `path`, a '/'-separated relative path
    (e.g. "component/structuredBody/component/section") - every segment is
    auto-prefixed with the CDA namespace, so an under-prefixed path (which
    ElementTree would otherwise silently resolve to an empty list) isn't
    possible to write here."""
    prefixed = "/".join(f"hl7:{segment}" for segment in path.split("/"))
    return element.findall(prefixed, _NS)


def xsi_type(element: Element) -> str | None:
    """The xsi:type attribute value (e.g. "CD" on a <value> element) - the
    one attribute that genuinely needs Clark notation, since xsi is a
    different namespace from the element itself. Plain attributes like
    @root/@code/@value never need this."""
    return element.get(_XSI_TYPE_ATTR)


def coded_value(element: Element | None) -> tuple[str, str, str] | None:
    """Extract (code, displayName, codeSystem) from a CDA coded element
    (either a <code .../> child or a <value xsi:type="CD" .../> - both use
    the same @code/@codeSystem/@displayName attribute shape). Returns None
    when the element is absent or has no @code - matching this project's
    established "no code component -> None" convention (see
    app/fhir_models/builders.py::build_codeable_concept_from_cwe). Does NOT
    attempt to resolve originalText/reference back into narrative <text> -
    displayName is used as-is when present, omitted otherwise."""
    if element is None:
        return None
    code = element.get("code")
    if not code:
        return None
    return code, element.get("displayName", ""), element.get("codeSystem", "")


def ts_value(element: Element | None) -> str | None:
    """A single TS (point-in-time) element's raw @value, or None when the
    element is absent, has no @value, or is explicitly nullFlavor (unknown/
    not-applicable) - all three cases mean "no usable time here", not an
    error."""
    if element is None:
        return None
    return element.get("value") or None


def ivl_ts_bounds(element: Element | None) -> tuple[str | None, str | None]:
    """An IVL_TS (interval of time) element's (low, high) raw @value
    strings. IVL_TS has four legal shapes in real C-CDA: a bare @value on
    the element itself (single point, both bounds equal), <low>/<high>
    children (a real interval, either bound optionally nullFlavor/absent),
    <center> (an approximate single point - confirmed against a real
    fetched HL7 C-CDA-Examples Plan of Care Activity Observation, whose own
    effectiveTime uses exactly this shape rather than a bare @value or
    <low>/<high> pair - app/cda/plan_of_treatment.py's own first real
    consumer of this fourth branch, treated the same as a bare @value:
    both bounds equal), or nullFlavor on the element itself (fully
    unknown). Returns (None, None) for the fully-unknown case rather than
    raising."""
    if element is None:
        return None, None
    bare_value = element.get("value")
    if bare_value:
        return bare_value, bare_value
    low = ts_value(find_child(element, "low"))
    high = ts_value(find_child(element, "high"))
    if low or high:
        return low, high
    center = ts_value(find_child(element, "center"))
    if center:
        return center, center
    return None, None
