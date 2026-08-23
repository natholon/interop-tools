"""Computes the mapping decisions a conversion actually made, so a
reviewer can accept or reject each one for the message at hand.

**Decisions are computed, never hand-declared.** A register a reviewer is
meant to trust has to be complete, and a hand-maintained list is complete
only until someone forgets to add to it. Both kinds are derived from data
the pillar already produces:

- **Inferred mappings** - every `ProvenanceEntry` with
  `derivation="inferred"` is, by definition, a value this app produced
  without a source field to point at. Those already carry a `reason`.
- **Dropped source data** - source values that were present but never
  recorded as mapped. Derived by diffing what the raw message populates
  against what the recorder saw. This half is per format, since each
  splits its raw text differently: HL7v2 on `|`/`~`/`^`, X12 on the ISA's
  own self-declared delimiters. C-CDA has no positional coordinate at all -
  a recorded XML location is a relative path composed while walking, with
  no inverse (see `cda_locator.py`) - so it enumerates leaf values and
  matches them against the source *spans* the correlated-highlighting view
  already resolves. That distinction matters: most values are transformed
  on the way through (a date reformatted, an OID rewritten as a URI), so
  comparing values alone called nearly half a CCD lost.

**The join problem, and why it needs an explicit allowance.** Some mappers
read several components and collapse them into one value (PV1-3's point of
care + room become a single display string), recording the fact at *field*
level with no component. A pure diff cannot tell that apart from a genuine
drop, and would report every component of such a field as lost. `JOINED_
FIELDS` names those fields and the components they genuinely consume, so
the diff stays accurate. That table is the one hand-maintained piece here -
kept deliberately tiny, and a field listed there still reports any
component *outside* its consumed set as dropped.
"""

import re
from typing import NamedTuple

from pydantic import BaseModel

from app.edi.parser import read_isa_delimiters, split_segments, strip_bom_and_whitespace
from app.hl7.parser import normalize_segment_separators, truncate_to_first_message
from app.provenance.citations import (
    Citation,
    DEFAULT_BY_FORMAT,
    DROP_NOT_YET_CHECKED,
    X12_NO_OFFICIAL_CROSSWALK,
)
from app.provenance.hl7_field_names import SEGMENT_FIELD_NAMES, component_names_for_field
from app.provenance.cda_field_names import resolve_cda_field_label
from app.provenance.cda_ig_verdicts import GAP, verdict_for
from app.provenance.cda_locator import _resolve_attribute_span, parse_with_positions
from app.provenance.edi_field_names import resolve_edi_field_label
from app.provenance.edi_locator import ParsedEdiLocation, parse_edi_location
from app.provenance.hl7_locator import parse_hl7_location
from app.provenance.models import CrosswalkReport

DecisionKind = str  # "inferred" | "dropped"

# Leading "SEG-N" of a recorded location, for the disclosed multi-segment
# markers that are not otherwise parseable (see _mapped_components).
_MARKER_PREFIX_RE = re.compile(r"^([A-Z][A-Z0-9]{2})-(\d+)\b")
# Sentinel in a consumed-component set: the whole field was mapped, so no
# component of it can be a drop. Never a real component number.
_WHOLE_FIELD_MAPPED = 0

# Fields whose mapper reads several components and collapses them into one
# recorded value. Maps (segment, field) -> the components genuinely
# consumed. Any populated component NOT listed is still reported dropped.
#
# Hand-maintained by necessity (nothing in the recorded data distinguishes
# "joined" from "dropped"), so it is kept as small as possible and each
# entry names the function responsible.
JOINED_FIELDS: dict[tuple[str, int], set[int]] = {
    # app.mappings.common.location_display - joins point of care + room.
    ("PV1", 3): {1, 2},
    ("PV1", 6): {1, 2},
    ("AIL", 3): {1, 2},
    # app.mappings.common.person_display - family + given, id as fallback.
    ("PV1", 7): {1, 2, 3},
}
# AIG-3 is deliberately absent: it is CWE-shaped, not PL, and
# app.mappings.siu._build_aig_resource records the component it actually
# used (2, falling back to 1), so the plain diff is already accurate.

# Fields carrying no mappable content. Excluded so the register reports
# real data loss rather than structural noise - the same hand-maintained-
# but-tiny discipline as JOINED_FIELDS, each entry stating why it is not a
# loss. Anything not listed here is reported, so the default stays
# "disclose it".
UNMAPPABLE_FIELDS: dict[tuple[str, int], str] = {
    # Set ID - a segment's sequence number within the message, not data
    # about the patient or the event.
    ("PID", 1): "Set ID",
    ("PV1", 1): "Set ID",
    ("OBR", 1): "Set ID",
    ("OBX", 1): "Set ID",
    ("NTE", 1): "Set ID",
    ("TQ1", 1): "Set ID",
    ("TXA", 1): "Set ID",
    ("AIS", 1): "Set ID",
    ("AIG", 1): "Set ID",
    ("AIL", 1): "Set ID",
    ("AIP", 1): "Set ID",
    ("RGS", 1): "Set ID",
    # The trigger event is read from MSH-9, which selects the mapper - EVN-1
    # restates it and is not a second, unmapped value.
    ("EVN", 1): "Read from MSH-9",
}

# Qualifier fields, which are **conditional** the same way X12's are (see
# EDI_QUALIFIER_OF): consumed only when the field they qualify was itself
# mapped. Maps (segment, field) -> the field it qualifies.
HL7_QUALIFIER_OF: dict[tuple[str, int], int] = {
    # app.mappings.oru._build_observation_value branches on OBX-2 to pick
    # which Observation.value[x] OBX-5 populates.
    ("OBX", 2): 5,
}


class MappingDecision(BaseModel):
    """One reviewable decision. `id` is stable for a given
    (kind, location, target) so a reviewer's accept/reject survives a
    re-run of the same message - it deliberately does NOT include the
    field's *value*, or changing the message would silently discard the
    review."""

    id: str
    kind: DecisionKind
    summary: str
    detail: str | None = None
    source_location: str | None = None
    field_label: str | None = None
    fhir_path: str | None = None
    lost_value: str | None = None
    citation: Citation


def _decision_id(kind: str, *parts: str | None) -> str:
    joined = "|".join(p for p in parts if p)
    return f"{kind}:{joined}"


def _repetition_suffix(repetition: int) -> str:
    """`[n]` for a second or later repetition, empty for the first.

    A repeating field (PID-3 carrying several identifiers is routine)
    otherwise collapses every repetition onto one location string, and so
    onto one decision id - `apply_rejections` builds a dict keyed by id, so
    one of them silently becomes unreachable and the UI shares a single
    review state across both rows. Repetition 0 stays unsuffixed so ids for
    the ordinary non-repeating case are unchanged.
    """
    return f"[{repetition}]" if repetition else ""


def _inferred_decisions(report: CrosswalkReport) -> list[MappingDecision]:
    """Every inferred entry is a mapping this app made without a source
    field - exactly the class of decision a reviewer needs to sign off."""
    citation = DEFAULT_BY_FORMAT.get(report.source_format or "", None)
    decisions = []
    for entry in report.entries:
        if entry.derivation != "inferred":
            continue
        decisions.append(
            MappingDecision(
                id=_decision_id("inferred", entry.fhir_path),
                kind="inferred",
                summary=f"{entry.fhir_path} was inferred, not read from the source.",
                detail=entry.reason,
                fhir_path=entry.fhir_path,
                lost_value=entry.value,
                citation=citation or DEFAULT_BY_FORMAT["HL7v2"],
            )
        )
    return decisions


def _mapped_components(report: CrosswalkReport) -> dict[tuple[str, int, int], set[int]]:
    """(segment, field, repetition) -> the components the recorder saw.
    A field-level record (no component) counts as component 1, which is
    what `field_str`'s own default reads."""
    seen: dict[tuple[str, int, int], set[int]] = {}
    for entry in report.entries:
        if not entry.source_location:
            continue
        parsed = parse_hl7_location(entry.source_location)
        if parsed is not None:
            key = (parsed.segment_id, parsed.field, parsed.repetition or 0)
            seen.setdefault(key, set()).add(parsed.component or 1)
            continue
        # Not every recorded location is a parseable SEG-N: a value joined
        # from several segments is recorded against a disclosed marker
        # ("OBX-5 (x2 segments)" for an MDM document body, "NTE-3 (xN
        # segments)" for an appointment comment). Reading only the leading
        # SEG-N off those still identifies the field as mapped - without
        # this the marker parses as nothing and the register reports the
        # document body itself as dropped data.
        prefix = _MARKER_PREFIX_RE.match(entry.source_location)
        if prefix:
            key = (prefix.group(1), int(prefix.group(2)), 0)
            seen.setdefault(key, set()).add(_WHOLE_FIELD_MAPPED)
    return seen


def _dropped_decisions(
    report: CrosswalkReport, populated: dict[tuple[str, int, int], dict[int, str]]
) -> list[MappingDecision]:
    """A component present in the source but never recorded as mapped."""
    mapped = _mapped_components(report)
    decisions = []
    for key, components in sorted(populated.items()):
        segment_id, occurrence, field, repetition = key
        if (segment_id, field) in UNMAPPABLE_FIELDS:
            continue
        # A qualifier is consumed only when the field it qualifies was
        # itself mapped - see EDI_QUALIFIER_OF for the same rule and the
        # bug that motivated it.
        qualified = HL7_QUALIFIER_OF.get((segment_id, field))
        if qualified is not None and any(
            sid == segment_id and num == qualified for sid, num, _ in mapped
        ):
            continue

        # Consumption is looked up without the segment occurrence, because
        # no hl7_location() string carries one - "OBX-5" cannot say which
        # of an ORU's OBX segments it came from. Same aggregation, and same
        # disclosed trade, as the X12 half: rows are per occurrence, but an
        # element read on any occurrence counts as read on all of them.
        recorded = mapped.get((segment_id, field, repetition), set())
        # The joined-components allowance is conditional too: it exists so a
        # mapper that collapses several components into one value does not
        # look like it dropped the rest. Where *nothing* read the field, it
        # collapsed nothing - MDM never reads PV1-3, so suppressing its
        # point-of-care and room hid real drops.
        joined = JOINED_FIELDS.get((segment_id, field), set()) if recorded else set()
        consumed = set(recorded) | joined
        if _WHOLE_FIELD_MAPPED in consumed:
            continue

        # A field nothing touched at all is one decision ("this field is
        # not mapped"), not one per component - a reviewer reading five
        # rows for an entirely-unmapped PV1-8 learns nothing the single
        # row doesn't already say, and the noise buries the fields where
        # only *part* was dropped.
        rep = _repetition_suffix(repetition)
        seg_rep = _repetition_suffix(occurrence)
        if not consumed:
            whole = SEGMENT_FIELD_NAMES.get(segment_id, {}).get(field)
            raw = "^".join(components.get(i, "") for i in range(1, max(components) + 1))
            field_location = f"{segment_id}{seg_rep}-{field}{rep}"
            decisions.append(
                MappingDecision(
                    id=_decision_id("dropped", field_location),
                    kind="dropped",
                    summary=f"{field_location} is present in the source but not mapped to any FHIR field.",
                    detail=f"{whole} carried {raw!r}." if whole else f"The field carried {raw!r}.",
                    source_location=field_location,
                    field_label=whole,
                    lost_value=raw,
                    citation=DROP_NOT_YET_CHECKED,
                )
            )
            continue

        for component, value in sorted(components.items()):
            if component in consumed:
                continue
            location = f"{segment_id}{seg_rep}-{field}{rep}.{component}"
            names = component_names_for_field(segment_id, field)
            label = names.get(component) if names else None
            decisions.append(
                MappingDecision(
                    id=_decision_id("dropped", location),
                    kind="dropped",
                    summary=f"{location} was present in the source but is not mapped to any FHIR field.",
                    detail=(
                        f"{label} carried {value!r}." if label else f"Component {component} carried {value!r}."
                    ),
                    source_location=location,
                    field_label=label,
                    lost_value=value,
                    citation=DROP_NOT_YET_CHECKED,
                )
            )
    return decisions


def compute_decisions(
    report: CrosswalkReport,
    raw_text: str | None = None,
    source_spans: set[tuple[int, int]] | None = None,
) -> list[MappingDecision]:
    """Inferred mappings first, then dropped source data - the order a
    reviewer reads them in (what the app added, then what it discarded).

    The dropped half needs the raw source to diff against, and each format
    splits it differently, so the branch lives here rather than in every
    caller. C-CDA has no scan yet: an XML location cannot be parsed
    backward into a fixed grammar the way a positional HL7v2 field or X12
    element can (see `app/provenance/cda_locator.py`), so it reports
    inferred decisions only - stated rather than silently returning a
    shorter list.
    """
    decisions = _inferred_decisions(report)
    if not raw_text:
        return decisions
    if report.source_format == "HL7v2":
        decisions.extend(_dropped_decisions(report, scan_populated_components(raw_text)))
    elif report.source_format == "EDI":
        decisions.extend(_dropped_edi_decisions(report, scan_populated_edi_elements(raw_text)))
    elif report.source_format == "CDA":
        # Matched by value rather than coordinate - see
        # _dropped_cda_decisions for why a C-CDA location has no inverse.
        mapped_values = {e.value for e in report.entries if e.source_location and e.value}
        mapped_values |= {e.source_value for e in report.entries if e.source_value}
        decisions.extend(
            _dropped_cda_decisions(
                report, scan_populated_cda_values(raw_text), source_spans or set(), mapped_values
            )
        )
    return decisions


def scan_populated_components(raw_text: str) -> dict[tuple[str, int, int, int], dict[int, str]]:
    """(segment, segment_occurrence, field, repetition) -> {component:
    value} for every non-empty component in the message.

    **HL7v2 repeats whole segments as well as fields**, and the two are
    different axes: an ORU carries one OBX per result, a SIU one AIP per
    participant, and any segment can be trailed by several NTEs. Keying
    only by (segment, field, repetition) let the third OBX overwrite the
    first two, so a component dropped by an earlier segment was invisible -
    the same completeness hole as skipping non-composite fields, one axis
    over.

    Re-splits the raw text rather than walking the parsed `hl7.Message`,
    for the same reason `app/provenance/hl7_locator.py` does: the library
    collapses a field with no `^` to a bare string, so component identity
    is only reliable when read off the original text. Reuses the parser's
    own normalize/truncate helpers so this sees exactly the text the real
    parse saw (one message, `\r`-separated).

    MSH is skipped entirely: its field numbering is offset by one (MSH-1
    is the field separator itself, not a `|`-split token), and none of its
    fields are composites this app decomposes - including it would emit
    noise, not decisions.
    """
    normalized = truncate_to_first_message(normalize_segment_separators(raw_text))
    populated: dict[tuple[str, int, int, int], dict[int, str]] = {}
    seen_count: dict[str, int] = {}
    for line in normalized.split("\r"):
        if not line.strip():
            continue
        fields = line.split("|")
        segment_id = fields[0]
        if segment_id == "MSH":
            continue
        occurrence = seen_count.get(segment_id, 0)
        seen_count[segment_id] = occurrence + 1
        for field_index, field_text in enumerate(fields[1:], start=1):
            if not field_text:
                continue
            for repetition, repetition_text in enumerate(field_text.split("~")):
                if not repetition_text:
                    continue
                # A field with no "^" is a single component, not "nothing to
                # drop" - a wholly unmapped simple field (PV1-10 Hospital
                # Service, say) is real data loss, and skipping it here made
                # every non-composite field structurally invisible to the
                # register regardless of what it carried.
                components = repetition_text.split("^")
                values = {i: v for i, v in enumerate(components, start=1) if v}
                if values:
                    populated[(segment_id, occurrence, field_index, repetition)] = values
    return populated


# --- C-CDA -------------------------------------------------------------

# XML and CDA plumbing: structure that says how to read the document, not
# content a mapper could lose. Same discipline as the other two formats -
# each entry states what it is, and anything unlisted is reported.
CDA_STRUCTURAL_ATTRS: dict[str, str] = {
    "classCode": "RIM class code, structural",
    "moodCode": "RIM mood code, structural",
    "typeCode": "Relationship type, consumed walking entryRelationship/participant",
    "contextControlCode": "RIM context propagation, structural",
    "inversionInd": "Relationship direction, consumed walking entryRelationship",
    "determinerCode": "RIM determiner, structural",
    "xmlns": "Namespace declaration",
    "xsi": "Namespace declaration",
    "type": "xsi:type, selects which datatype shape to read",
    "nullFlavor": "Says a value is absent - there is no value to lose",
    "mediaType": "Encoding of the narrative beside it",
    "representation": "Encoding of the value beside it",
}

# Elements whose whole subtree is structure rather than content.
CDA_STRUCTURAL_ELEMENTS: dict[str, str] = {
    # Identifies which template an element conforms to; consumed by
    # has_template_id to dispatch, never mapped to a field.
    "templateId": "Template identity, consumed dispatching to a section builder",
    "realmCode": "Conformance realm, structural",
    # Fixed document-type identifier every CDA carries; not content.
    "typeId": "CDA document type id, structural",
}

# Conditional qualifiers, the C-CDA mirror of EDI_QUALIFIER_OF: an
# attribute that says how to read another attribute on the same element is
# consumed only when that attribute was actually mapped.
CDA_QUALIFIER_OF: dict[str, str] = {
    "codeSystem": "code",
    "codeSystemName": "code",
}

# The narrative block a structured section carries alongside its entries.
# C-CDA requires it to restate the same content for human display, so
# reporting every paragraph and table cell as lost would bury the real
# findings under a duplicate of them. Reported once per section instead.
CDA_NARRATIVE_TAG = "text"


class _CdaLeaf(NamedTuple):
    path: str          # e.g. "section/entry/act/effectiveTime/@value"
    tag: str           # the element the value sits on
    name: str          # attribute name, or "" for element text
    value: str
    in_narrative: bool
    span: tuple[int, int] | None


def scan_populated_cda_values(raw_text: str) -> list[_CdaLeaf]:
    """Every leaf value in the document - each attribute, and the text of
    each childless element - in document order.

    Unlike HL7v2 fields and X12 elements, a C-CDA value has no positional
    coordinate: `cda_locator.py` composes locations forward while walking,
    and they cannot be parsed back into one. So this enumerates values
    rather than coordinates, and `_dropped_cda_decisions` matches them
    against what the recorder read by *span and value* instead.

    Returns a list, not a dict: the same value legitimately appears many
    times in one document (a repeated codeSystem OID, two identical dates),
    and collapsing them would lose the drop count.
    """
    root = parse_with_positions(raw_text)
    if root is None:
        return []
    leaves: list[_CdaLeaf] = []

    def walk(node, path: str, in_narrative: bool) -> None:
        if node.tag in CDA_STRUCTURAL_ELEMENTS:
            return
        narrative = in_narrative or node.tag == CDA_NARRATIVE_TAG
        for name, value in node.attrs.items():
            if value:
                span = _resolve_attribute_span(raw_text, node.start_tag_span, name)
                leaves.append(_CdaLeaf(f"{path}/@{name}", node.tag, name, value, narrative, span))
        text = node.text.strip()
        if text and not node.children:
            leaves.append(_CdaLeaf(f"{path}/text()", node.tag, "", text, narrative, node.text_span))
        # Repeated siblings need a positional index or they collapse onto
        # one path, and so one decision id - an Allergy entry nests several
        # observations under the same tag. Indexed from the second onward,
        # matching _repetition_suffix's convention for the other formats.
        seen_tags: dict[str, int] = {}
        for child in node.children:
            index = seen_tags.get(child.tag, 0)
            seen_tags[child.tag] = index + 1
            walk(child, f"{path}/{child.tag}{_repetition_suffix(index)}", narrative)

    walk(root, root.tag, False)
    return leaves


def _element_path(leaf_path: str) -> str:
    """The element a leaf sits on - its path without the trailing `@attr`
    or `text()`."""
    return leaf_path.rsplit("/", 1)[0]


def _leaf_name(leaf: _CdaLeaf) -> str:
    return f"@{leaf.name}" if leaf.name else "text()"


def _is_consumed_qualifier(leaf: _CdaLeaf, siblings: list[_CdaLeaf], is_mapped) -> bool:
    """A qualifier attribute is consumed only when the attribute it
    qualifies, on the same element, was itself read - the conditional rule
    all three formats share."""
    qualified = CDA_QUALIFIER_OF.get(leaf.name)
    if qualified is None:
        return False
    sibling = next((o for o in siblings if o.name == qualified), None)
    return sibling is not None and is_mapped(sibling)


def _shape_of(location: str) -> str:
    """A location with its positional indices stripped, so the same kind of
    drop in a repeating structure collapses onto one shape."""
    return "/".join(part.split("[")[0] for part in location.split("/"))


def _collapse_repeated_shapes(rows: list[tuple[str, str, str, str | None]]) -> list[MappingDecision]:
    """One decision per *shape*, carrying how many occurrences it covers.

    A Problems section with seven entries drops seven identical
    `entry/act/id/@root` facts. Seven rows say nothing the first does not,
    and they bury the findings that differ - so they collapse into one row
    that states the count and lists the values it covers.
    """
    grouped: dict[str, list[tuple[str, str, str, str | None]]] = {}
    for row in rows:
        grouped.setdefault(_shape_of(row[0]), []).append(row)

    decisions: list[MappingDecision] = []
    for shape, group in grouped.items():
        first_location, label_key, first_value, detail = group[0]
        count = len(group)
        if count == 1:
            summary = f"{first_location} is present in the source but not mapped to any FHIR field."
        else:
            summary = (
                f"{shape} is present {count} times in the source but not mapped to any FHIR field."
            )
            values = ", ".join(dict.fromkeys(repr(row[2]) for row in group))
            detail = f"{count} occurrences, carrying {values}."
        verdict, citation, ig_note = verdict_for(shape)
        if ig_note:
            detail = f"{detail} {ig_note}" if detail else ig_note
        if verdict == GAP:
            # A gap is the one verdict a reviewer has to act on, so it
            # leads the summary rather than sitting in the citation note.
            summary = f"GAP: {summary}"
        decisions.append(
            MappingDecision(
                id=_decision_id("dropped", shape),
                kind="dropped",
                summary=summary,
                detail=detail,
                source_location=first_location if count == 1 else shape,
                field_label=resolve_cda_field_label(label_key),
                lost_value=first_value,
                citation=citation,
            )
        )
    return decisions


def _dropped_cda_decisions(
    report: CrosswalkReport,
    leaves: list[_CdaLeaf],
    mapped_spans: set[tuple[int, int]],
    mapped_values: set[str],
) -> list[MappingDecision]:
    """A leaf the recorder never read.

    **Matched by source span, not by coordinate or value.** A recorded
    C-CDA location is a relative path composed while walking, so it has no
    inverse the way `PID-5.1` or `NM1-9` does - but `cda_locator.py`
    already resolves each recorded location to a character span in the raw
    XML for the correlated-highlighting view, and a span identifies the
    source text precisely regardless of what the mapper turned it into.
    That matters: most values *are* transformed (a date reformatted, an
    OID rewritten as a URI, `use="HP"` mapped to `home`), so comparing
    values alone reported nearly half the document as lost.

    Value comparison survives only as a fallback for the handful of
    locations the locator cannot resolve (a bare `<id>`, per its own
    disclosed simplification). That fallback is imprecise - two leaves
    carrying the same text are indistinguishable - so it errs toward
    under-reporting, the direction this register has to fail in.
    """
    narrative_sections_seen: set[str] = set()

    def is_mapped(leaf: _CdaLeaf) -> bool:
        if leaf.span is not None:
            start, end = leaf.span
            for mapped_start, mapped_end in mapped_spans:
                if start < mapped_end and mapped_start < end:
                    return True
        return leaf.value in mapped_values

    by_element: dict[str, list[_CdaLeaf]] = {}
    for leaf in leaves:
        by_element.setdefault(_element_path(leaf.path), []).append(leaf)

    rows: list[tuple[str, str, str, str | None]] = []  # (location, label_key, value, detail)
    for element_path, element_leaves in by_element.items():
        reportable = [
            leaf
            for leaf in element_leaves
            if not is_mapped(leaf)
            and leaf.name not in CDA_STRUCTURAL_ATTRS
            and not _is_consumed_qualifier(leaf, element_leaves, is_mapped)
        ]
        if not reportable:
            continue

        narrative = reportable[0].in_narrative
        if narrative:
            # One row per narrative block, not one per paragraph or cell.
            section = reportable[0].path.split(f"/{CDA_NARRATIVE_TAG}/", 1)[0]
            if section in narrative_sections_seen:
                continue
            narrative_sections_seen.add(section)
            rows.append(
                (
                    f"{section}/{CDA_NARRATIVE_TAG}()",
                    f"{CDA_NARRATIVE_TAG}/text()",
                    reportable[0].value,
                    "C-CDA requires a section's narrative to restate its entries for human display. "
                    "Only the entries are mapped; this block's own wording is not.",
                )
            )
            continue

        if len(reportable) == len(element_leaves):
            # Nothing on this element was read. One row saying the element
            # is unmapped tells a reviewer everything three rows for its
            # @code/@codeSystem/@displayName would - the same rule the
            # HL7v2 half already applies to a wholly unmapped field.
            tag = reportable[0].tag
            carried = ", ".join(f"{_leaf_name(leaf)}={leaf.value!r}" for leaf in reportable)
            rows.append((element_path, f"{tag}/text()" if not tag else tag, reportable[0].value, f"Carried {carried}."))
            continue

        # Partially read: name the specific parts that were not, since the
        # element as a whole *was* used for something.
        for leaf in reportable:
            rows.append(
                (
                    leaf.path,
                    f"{leaf.tag}/@{leaf.name}" if leaf.name else f"{leaf.tag}/text()",
                    leaf.value,
                    f"It carried {leaf.value!r}.",
                )
            )

    return _collapse_repeated_shapes(rows)


# --- X12 EDI -----------------------------------------------------------

# Envelope segments: pure interchange structure, carrying no clinical or
# administrative content a mapper could lose.
_EDI_ENVELOPE_SEGMENTS = frozenset({"ISA", "GS", "ST", "SE", "GE", "IEA"})

# Elements that are pure transaction structure - never payload, whatever
# the transaction set. Same discipline as UNMAPPABLE_FIELDS above: each
# entry states what consumes it, and anything unlisted is reported.
UNMAPPABLE_EDI_ELEMENTS: dict[tuple[str, int], str] = {
    # HL hierarchy: parsed into the loop tree by group_by_hl_hierarchy and
    # resolved by each family's own resolve_*_loops.
    ("HL", 1): "Loop id, consumed building the HL tree",
    ("HL", 2): "Parent loop pointer, consumed building the HL tree",
    ("HL", 3): "Level code, selects which loop this is",
    ("HL", 4): "Has-child flag, consumed building the HL tree",
    # BHT: transaction structure/purpose, not payload. BHT03/04/05 are
    # mapped (Bundle.identifier/timestamp) and so are not listed.
    ("BHT", 1): "Hierarchical structure code",
    ("BHT", 2): "Purpose code, selects request vs response for 278",
    ("BHT", 6): "Transaction type code",
    # Which loop/role this segment is - consumed by the loop resolvers to
    # decide what to build, not a value of its own.
    ("NM1", 1): "Entity identifier code, selects which loop this NM1 is",
    ("NM1", 2): "Entity type qualifier, selects Organization vs Practitioner",
    ("N1", 1): "Entity identifier code, selects which party this N1 is",
    # Line counter.
    ("LX", 1): "Service line counter",
}

# Qualifiers, which are **conditional**: an element that says how to read
# the element beside it is consumed only if that element was actually
# mapped. Listing them as unconditionally-unmappable hid real data - 837I
# never reads CLM05 at all, so its CLM05-2 qualifies nothing, yet it was
# suppressed while the CLM05-1 it supposedly qualified was reported as
# dropped. Maps (segment, element, component) -> the (element, component)
# it qualifies; `None` on either side of the key means "any", and a target
# element of `None` means "the same element this qualifier sits in".
EDI_QUALIFIER_OF: dict[tuple[str, int | None, int | None], tuple[int | None, int | None]] = {
    ("NM1", 8, None): (9, None),      # id qualifier -> the id it types
    ("N1", 3, None): (4, None),
    ("TRN", 1, None): (2, None),      # trace type -> the trace number
    ("DTP", 1, None): (3, None),      # which date / how formatted -> the date
    ("DTP", 2, None): (3, None),
    ("DMG", 1, None): (2, None),
    ("TOO", 1, None): (2, None),      # tooth numbering system -> tooth number
    ("CLM", 5, 2): (5, 1),            # facility code qualifier -> place of service
    ("SV1", 1, 1): (1, 2),            # procedure code qualifier -> the code
    ("SV2", 2, 1): (2, 2),
    ("SV3", 1, 1): (1, 2),
    # HI repeats one composite across HI01, HI02, ... - one per diagnosis -
    # so its qualifier cannot be pinned to an element position.
    ("HI", None, 1): (None, 2),
}


def _edi_qualifier_target(
    segment_id: str, element_num: int, component: int | None
) -> tuple[int, int | None] | None:
    """The (element, component) this one qualifies, or None if it is not a
    qualifier at all."""
    for key in ((segment_id, element_num, component), (segment_id, None, component)):
        target = EDI_QUALIFIER_OF.get(key)
        if target is None:
            continue
        target_element, target_component = target
        return (element_num if target_element is None else target_element, target_component)
    return None

def scan_populated_edi_elements(raw_text: str) -> dict[tuple[str, int, int, int | None], str]:
    """(segment_id, occurrence, element, component) -> value, for every
    non-empty element in the interchange.

    `occurrence` is the index among segments sharing that id, so each
    physical segment reports its own drops with its own value - X12 repeats
    whole *segments* where HL7v2 repeats fields, and an 837P carries six
    NM1 loops.

    Envelope segments are skipped: ISA/GS/ST/SE/GE/IEA are interchange
    structure, and reporting them would bury real findings under control
    numbers.
    """
    delimiters = read_isa_delimiters(raw_text)
    populated: dict[tuple[str, int, int, int | None], str] = {}
    seen_count: dict[str, int] = {}
    for segment in split_segments(strip_bom_and_whitespace(raw_text), delimiters):
        segment_id = segment[0]
        if segment_id in _EDI_ENVELOPE_SEGMENTS:
            continue
        occurrence = seen_count.get(segment_id, 0)
        seen_count[segment_id] = occurrence + 1
        for element_num, element_text in enumerate(segment[1:], start=1):
            if not element_text:
                continue
            parts = element_text.split(delimiters.component)
            if len(parts) == 1:
                populated[(segment_id, occurrence, element_num, None)] = element_text
                continue
            for component_num, value in enumerate(parts, start=1):
                if value:
                    populated[(segment_id, occurrence, element_num, component_num)] = value
    return populated


def _mapped_edi_elements(report: CrosswalkReport) -> set[tuple[str, int, int | None]]:
    """(segment_id, element, component) the recorder saw - deliberately
    **not** keyed by which physical segment it came from.

    **Disclosed limitation.** No `edi_location()` string carries a segment
    occurrence (only `HI` and 837D's claim-level DTP use
    `segment_repetition` at all), so "NM1-3" cannot be attributed to one of
    an 837P's six NM1 loops. Consumption is therefore aggregated by
    element: an element some mapper reads on *any* occurrence counts as
    read on *every* occurrence of that segment id.

    The trade is deliberate. Attributing per occurrence would need the
    occurrence-claiming resolver in `highlighting.py`, and getting it wrong
    reports five of six NM1 loops as wholly dropped - a register that cries
    wolf gets ignored, so this errs toward under-reporting. What it can
    miss: element E read on one loop and populated-but-unmapped on another.
    """
    seen: set[tuple[str, int, int | None]] = set()
    for entry in report.entries:
        if not entry.source_location:
            continue
        # A value built from more than one element is recorded against a
        # compound location ("BHT-4+BHT-5" for a date and time combined into
        # Bundle.timestamp). Each side is a real element that was read, so
        # both must count as consumed - parsing only the whole string sees
        # neither, and the register then reports the timestamp's own source
        # elements as dropped.
        for part in entry.source_location.split("+"):
            parsed = parse_edi_location(part.strip())
            if parsed is not None:
                seen.add((parsed.segment_id, parsed.element_num, parsed.component))
    return seen


def _dropped_edi_decisions(
    report: CrosswalkReport, populated: dict[tuple[str, int, int, int | None], str]
) -> list[MappingDecision]:
    mapped = _mapped_edi_elements(report)

    def is_mapped(element_num: int, component: int | None) -> bool:
        # A whole-element record covers every component of it; a
        # component-level record covers only its own.
        if (segment_id, element_num, None) in mapped:
            return True
        if component is not None:
            return (segment_id, element_num, component) in mapped
        return any(sid == segment_id and num == element_num for sid, num, _ in mapped)

    decisions = []
    for key, value in sorted(populated.items()):
        segment_id, occurrence, element_num, component = key
        if (segment_id, element_num) in UNMAPPABLE_EDI_ELEMENTS:
            continue
        if is_mapped(element_num, component):
            continue
        # A qualifier is consumed only when the element it qualifies was
        # actually mapped. Suppressing it unconditionally hid real drops
        # for any transaction set that never reads the target - 837I never
        # reads CLM05, so its CLM05-2 qualifies nothing.
        target = _edi_qualifier_target(segment_id, element_num, component)
        if target is not None and is_mapped(*target):
            continue
        rep = _repetition_suffix(occurrence)
        location = f"{segment_id}{rep}-{element_num}"
        if component is not None:
            location = f"{location}.{component}"
        label = resolve_edi_field_label(
            ParsedEdiLocation(
                segment_id=segment_id,
                segment_repetition=None,
                element_num=element_num,
                component=component,
            )
        )
        decisions.append(
            MappingDecision(
                id=_decision_id("dropped", location),
                kind="dropped",
                summary=f"{location} is present in the source but not mapped to any FHIR field.",
                detail=f"{label} carried {value!r}." if label else f"The element carried {value!r}.",
                source_location=location,
                field_label=label,
                lost_value=value,
                # Not "not yet checked": X12 publishes no FHIR crosswalk at
                # all, so there is nothing pending to check this against.
                # Saying "unchecked" would imply work that cannot be done.
                citation=X12_NO_OFFICIAL_CROSSWALK,
            )
        )
    return decisions


DATA_ABSENT_REASON_URL = "http://hl7.org/fhir/StructureDefinition/data-absent-reason"
# The code used when a reviewer rejects an inferred value. None of the 15
# DataAbsentReason codes means "a reviewer rejected our inference" - that
# concept isn't in the value set. "unknown" ("the value is expected to
# exist but is not known") is the accurate fit: after rejection the value
# certainly exists in reality, this app just has no approved basis to
# assert it. Confirmed with the product owner rather than assumed.
REJECTED_ABSENT_REASON = "unknown"

# How to express a rejected value conformantly, per (resourceType, field).
# "code" = the field's own value set has a null-flavour code, so emit it
# and the resource stays a fully valid model. "absent" = the value set has
# no such code AND the binding is Required, so the only conformant option
# is to drop the value and carry FHIR's data-absent-reason extension on
# the primitive instead.
#
# Every row was checked against that field's published R4 value set - the
# fhir.resources library does NOT validate value sets (it accepts
# intent="null"), so it cannot be used to derive this.
REJECTION_STRATEGY: dict[tuple[str, str], str] = {
    ("Encounter", "status"): "code",           # value set includes "unknown"
    ("Observation", "status"): "code",         # value set includes "unknown"
    ("DiagnosticReport", "status"): "code",    # value set includes "unknown"
    ("Appointment", "status"): "absent",       # no null code; Required binding
    ("DocumentReference", "status"): "absent",  # current|superseded|entered-in-error only
}
NULL_FLAVOUR_CODE = "unknown"


class RejectionOutcome(BaseModel):
    """What actually happened to one rejected decision. `applied=False`
    means the rejection could not be expressed conformantly and the
    original value was left in place - reported rather than silently
    dropped, since a reviewer who rejected something needs to know it
    did not take effect."""

    decision_id: str
    fhir_path: str
    applied: bool
    strategy: str | None = None
    note: str | None = None


def _split_entry_path(fhir_path: str) -> tuple[int, str] | None:
    """"Bundle.entry[3].resource.status" -> (3, "status"). Returns None
    for a Bundle-level fact, which has no entry to rewrite."""
    if not fhir_path.startswith("Bundle.entry["):
        return None
    head, _, tail = fhir_path.partition("].resource.")
    if not tail:
        return None
    try:
        return int(head[len("Bundle.entry[") :]), tail
    except ValueError:
        return None


def apply_rejections(
    bundle_dict: dict, decisions: list[MappingDecision], rejected_ids: set[str]
) -> tuple[dict, list[RejectionOutcome]]:
    """Apply a reviewer's rejections to an already-serialized Bundle.

    Operates on the serialized dict, not the model, because a rejected
    required value has to be expressed as value-absent-plus-extension -
    legal FHIR, but `fhir.resources` enforces required fields even on
    assignment and cannot represent it. Working at the JSON level keeps
    the output conformant rather than bending it to the library's stricter
    model.

    Only nested field paths one level under `.resource` are handled (all
    of HL7v2's inferred surface is `status`); anything deeper is reported
    as not applied rather than silently ignored.
    """
    entries = bundle_dict.get("entry") or []
    outcomes: list[RejectionOutcome] = []
    by_id = {d.id: d for d in decisions}

    for decision_id in sorted(rejected_ids):
        decision = by_id.get(decision_id)
        if decision is None:
            continue
        # NB: a dropped decision has no fhir_path (it never produced a
        # FHIR field), so this must not guard on fhir_path before the
        # kind check below - doing so silently swallowed every rejected
        # drop instead of reporting it.
        if decision.kind != "inferred":
            # Rejecting a *drop* means "this should have been mapped" -
            # a gap to fix in the mapper, not something conversion can
            # act on. Recorded so the reviewer sees it was registered.
            outcomes.append(
                RejectionOutcome(
                    decision_id=decision_id,
                    fhir_path=decision.fhir_path or decision.source_location or "",
                    applied=False,
                    note="Rejecting dropped data flags an unmapped field; conversion cannot supply a mapping.",
                )
            )
            continue

        split = _split_entry_path(decision.fhir_path)
        if split is None:
            outcomes.append(
                RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=False,
                                 note="Bundle-level values are not rejectable.")
            )
            continue
        index, field = split
        if index >= len(entries) or "." in field:
            outcomes.append(
                RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=False,
                                 note="Only top-level fields of a Bundle entry can be rejected.")
            )
            continue

        resource = entries[index].get("resource", {})
        strategy = REJECTION_STRATEGY.get((resource.get("resourceType", ""), field))
        if strategy == "code":
            resource[field] = NULL_FLAVOUR_CODE
        elif strategy == "absent":
            resource.pop(field, None)
            resource[f"_{field}"] = {
                "extension": [{"url": DATA_ABSENT_REASON_URL, "valueCode": REJECTED_ABSENT_REASON}]
            }
        else:
            # Not in the table means nobody has checked this field's own
            # value set. Removing it could silently produce a
            # non-conformant resource, so the value stays and the
            # reviewer is told the rejection did not take effect.
            outcomes.append(
                RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=False,
                                 note="No verified conformant representation for this field; value left unchanged.")
            )
            continue
        outcomes.append(
            RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=True, strategy=strategy)
        )
    return bundle_dict, outcomes
