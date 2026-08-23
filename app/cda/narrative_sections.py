"""Narrative-only C-CDA sections -> DocumentReference + Binary.

**Why not a FHIR-Document Bundle(type="document") + Composition**:
`Composition.author` is required (1..1+) and this app never parses
`ClinicalDocument/author`; the redesign would also ripple into
app/dedup.py, app/provenance/, app/transform/cda_*.py and every test
asserting `Bundle.type == "collection"`. Instead each section becomes a
DocumentReference (LOINC `.type` read live from the section's own
`<code>`) referencing a Binary holding the extracted plain text - the
same pattern app/mappings/mdm.py already uses for TXA. Purely additive.

**templateId gotcha**: four of these do NOT live in the
`2.16.840.1.113883.10.20.22.2.x` namespace every other section uses.
Hospital Course/History of Present Illness/Review of Systems are legacy
IHE PCC (`1.3.6.1.4.1.19376.1.5.3.1.3.x`); Physical Exam/General Status
are legacy HITSP/C32 (`2.16.840.1.113883.10.20.2.x` - one OID segment
shorter than the native ones, easy to transcribe wrong). Both confirmed
against the real HL7 C-CDA-Examples documents, which also pinned the
narrative shapes `extract_narrative_text` handles (`<paragraph>`,
`<list>`/`<item>`, `<table>`, plain mixed text).

**Plan of Treatment and "Plan of Care" are one template**, not two
(`2.16.840.1.113883.10.20.22.2.10`, LOINC 18776-5) - real documents just
title it differently - so registering it once covers both document types.

Plan of Treatment, Social History, and Family History can also carry real
structured entries. Those are parsed by app/cda/social_history.py/
family_history.py/plan_of_treatment.py, which override this module's
SECTION_BUILDERS registration with a builder that still calls
`build_narrative_document_reference` and adds structured resources
alongside it. Validation (`cda.narrative-section-missing-text`),
provenance, and reverse transform (app/transform/cda_ccd.py::
_build_narrative_section, keyed off `DocumentReference.type.coding[0]`)
all cover these sections too."""

import base64
import uuid
from xml.etree.ElementTree import Element

from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.binary import Binary
from fhir.resources.R4B.documentreference import DocumentReference, DocumentReferenceContent
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.cda.common import build_codeable_concept_from_cd
from app.cda.parser import find_child
from app.provenance.location import xpath_location

# Discharge Summary only.
HOSPITAL_COURSE_TEMPLATE_ID = "1.3.6.1.4.1.19376.1.5.3.1.3.5"  # LOINC 8648-8, legacy IHE PCC OID

# Discharge Summary + History and Physical (titled "Plan of Care" on H&P).
PLAN_OF_TREATMENT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.10"  # LOINC 18776-5

# History and Physical only, from here down. The IG requires a Reason for
# Visit/Chief Complaint SHOWING as any *one* of these three shapes (combined,
# or either standalone alternative) - all three registered against the
# identical builder, since whichever one a real sender used still carries the
# same kind of narrative content.
REASON_FOR_VISIT_CHIEF_COMPLAINT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.13"  # LOINC 46239-0 (combined)
REASON_FOR_VISIT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.12"  # LOINC 29299-5 (standalone alt)
CHIEF_COMPLAINT_TEMPLATE_ID = "1.3.6.1.4.1.19376.1.5.3.1.1.13.2.1"  # LOINC 10154-3 (standalone alt, legacy IHE PCC OID)

HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID = "1.3.6.1.4.1.19376.1.5.3.1.3.4"  # LOINC 10164-2, legacy IHE PCC OID
PHYSICAL_EXAM_TEMPLATE_ID = "2.16.840.1.113883.10.20.2.10"  # LOINC 29545-1, legacy HITSP/C32 OID
ASSESSMENT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.8"  # LOINC 51848-0
REVIEW_OF_SYSTEMS_TEMPLATE_ID = "1.3.6.1.4.1.19376.1.5.3.1.3.18"  # LOINC 10187-3, legacy IHE PCC OID
SOCIAL_HISTORY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.17"  # LOINC 29762-2
FAMILY_HISTORY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.15"  # LOINC 10157-6
GENERAL_STATUS_TEMPLATE_ID = "2.16.840.1.113883.10.20.2.5"  # LOINC 10210-3, legacy HITSP/C32 OID

# Every templateId this module registers a builder for - the single
# canonical list `app/cda/registry.py`'s own SECTION_BUILDERS entries are
# built from and `app/cda/validation.py` walks a document for, so the two
# can never independently drift apart about which twelve templateIds count
# as "a narrative section" (the same "one real implementation, not two
# maintained lists" discipline this app applies everywhere - e.g.
# app.edi.common.resolve_837_variant).
ALL_TEMPLATE_IDS = [
    HOSPITAL_COURSE_TEMPLATE_ID,
    PLAN_OF_TREATMENT_TEMPLATE_ID,
    REASON_FOR_VISIT_CHIEF_COMPLAINT_TEMPLATE_ID,
    REASON_FOR_VISIT_TEMPLATE_ID,
    CHIEF_COMPLAINT_TEMPLATE_ID,
    HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
    PHYSICAL_EXAM_TEMPLATE_ID,
    ASSESSMENT_TEMPLATE_ID,
    REVIEW_OF_SYSTEMS_TEMPLATE_ID,
    SOCIAL_HISTORY_TEMPLATE_ID,
    FAMILY_HISTORY_TEMPLATE_ID,
    GENERAL_STATUS_TEMPLATE_ID,
]

# Reverse of the templateId->LOINC pairs above - the one real
# implementation app/transform/cda_ccd.py's own narrative-section
# regeneration uses to decide which of these twelve templateIds a
# recovered DocumentReference.type.coding[0].code corresponds to, not a
# second, independently-drifting copy (the same "one real implementation,
# not two maintained lists" discipline ALL_TEMPLATE_IDS's own docstring
# above already established for the forward direction). Every LOINC code
# here is genuinely distinct, even across the three Reason for Visit/Chief
# Complaint shapes, so this reverse lookup is unambiguous.
LOINC_TO_TEMPLATE_ID: dict[str, str] = {
    "8648-8": HOSPITAL_COURSE_TEMPLATE_ID,
    "18776-5": PLAN_OF_TREATMENT_TEMPLATE_ID,
    "46239-0": REASON_FOR_VISIT_CHIEF_COMPLAINT_TEMPLATE_ID,
    "29299-5": REASON_FOR_VISIT_TEMPLATE_ID,
    "10154-3": CHIEF_COMPLAINT_TEMPLATE_ID,
    "10164-2": HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
    "29545-1": PHYSICAL_EXAM_TEMPLATE_ID,
    "51848-0": ASSESSMENT_TEMPLATE_ID,
    "10187-3": REVIEW_OF_SYSTEMS_TEMPLATE_ID,
    "29762-2": SOCIAL_HISTORY_TEMPLATE_ID,
    "10157-6": FAMILY_HISTORY_TEMPLATE_ID,
    "10210-3": GENERAL_STATUS_TEMPLATE_ID,
}

# A disclosed fallback title/displayName per templateId, used by the
# reverse direction only when DocumentReference.description/
# .type.coding[0].display themselves don't resolve (both are genuinely
# optional on DocumentReference - build_narrative_document_reference only
# sets them when <title>/<code displayName> are actually present, unlike
# the FHIR-required fields every other reverse builder in this app can
# lean on) - not necessarily the exact original wording, but a real,
# readable section name rather than a blank one.
CANONICAL_TITLES: dict[str, str] = {
    HOSPITAL_COURSE_TEMPLATE_ID: "Hospital Course",
    PLAN_OF_TREATMENT_TEMPLATE_ID: "Plan of Treatment",
    REASON_FOR_VISIT_CHIEF_COMPLAINT_TEMPLATE_ID: "Reason for Visit",
    REASON_FOR_VISIT_TEMPLATE_ID: "Reason for Visit",
    CHIEF_COMPLAINT_TEMPLATE_ID: "Chief Complaint",
    HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID: "History of Present Illness",
    PHYSICAL_EXAM_TEMPLATE_ID: "Physical Exam",
    ASSESSMENT_TEMPLATE_ID: "Assessment",
    REVIEW_OF_SYSTEMS_TEMPLATE_ID: "Review of Systems",
    SOCIAL_HISTORY_TEMPLATE_ID: "Social History",
    FAMILY_HISTORY_TEMPLATE_ID: "Family History",
    GENERAL_STATUS_TEMPLATE_ID: "General Status",
}

_TABLE_ROW_TAG = "tr"
_TABLE_CELL_TAGS = ("th", "td")
_BLOCK_TEXT_TAGS = ("paragraph", "content", "caption", "item")


def _local_tag(element: Element) -> str:
    """Strips the CDA namespace ElementTree keeps on every real tag (Clark
    notation, `{urn:hl7-org:v3}paragraph`) - needed here because, unlike
    `app.cda.parser`'s own `find_child`/`find_all` (which only resolve
    known, named paths), this module has to walk *whatever* block-level
    children a real `<text>` narrative happens to contain."""
    return element.tag.rsplit("}", 1)[-1]


def _cell_text(cell: Element) -> str:
    return "".join(cell.itertext()).strip()


def _table_lines(table: Element) -> list[str]:
    """One line per `<tr>`, cells `" | "`-joined - preserves the real
    row/column association a naive whole-table `itertext()` join would
    destroy (confirmed necessary: Social History/Family History/Plan of
    Treatment all routinely narrate via `<table>` in real C-CDA documents,
    e.g. a Social History row like "Tobacco smoking status | Former smoker |
    20050501 to 20090227" - flattened without the `" | "` separators, the
    three column values run together into one unlabeled, misleading
    string). Walks every `<tr>` anywhere under `table` via `Element.iter()`
    rather than resolving `thead`/`tbody` explicitly first, since a `<tr>`
    can legally sit directly under `<table>` too, with no row-group wrapper
    at all."""
    lines = []
    for row in table.iter():
        if _local_tag(row) != _TABLE_ROW_TAG:
            continue
        cells = [_cell_text(cell) for cell in row if _local_tag(cell) in _TABLE_CELL_TAGS]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append(" | ".join(cells))
    return lines


def _walk_block(element: Element, lines: list[str]) -> None:
    tag = _local_tag(element)
    if tag == "table":
        lines.extend(_table_lines(element))
        return
    if tag in _BLOCK_TEXT_TAGS:
        # itertext() already flattens any nested inline markup (sub/sup/br/
        # linkHtml/footnote/...) into this one block's own readable text -
        # no need to recurse into paragraph/item/content/caption's own
        # children separately.
        text = "".join(element.itertext()).strip()
        if text:
            lines.append(text)
        return
    if tag == "list":
        for child in element:
            _walk_block(child, lines)
        return
    # An unrecognized wrapper - recurse looking for further block-level
    # content, since C-CDA's own narrative block schema allows nesting in
    # practice beyond the handful of tags this app's own real fixtures
    # exercise. A genuinely childless, unrecognized element falls back to
    # its own direct text.
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if text:
            lines.append(text)
        return
    for child in children:
        _walk_block(child, lines)


def _extract_narrative_lines(text_element: Element | None) -> list[str]:
    """The real work behind `extract_narrative_text` (below), exposed as
    its own list-returning step - `build_narrative_document_reference`
    needs the line *count* too (to decide between a precise vs. a
    disclosed-marker provenance location, see its own docstring), not just
    the final joined string."""
    if text_element is None:
        return []
    lines: list[str] = []
    # <text>'s own direct leading text (before its first child, if any) -
    # Hospital Course's own real shape has no <paragraph> wrapper at all,
    # just raw mixed text as the section's entire narrative.
    leading = (text_element.text or "").strip()
    if leading:
        lines.append(leading)
    for child in text_element:
        _walk_block(child, lines)
        tail = (child.tail or "").strip()
        if tail:
            lines.append(tail)
    return lines


def extract_narrative_text(text_element: Element | None) -> str:
    """Renders a CDA `<section>/<text>` narrative block - `paragraph`/
    `list`+`item`/`table`/plain mixed text, confirmed against the real
    shapes this app's own fixtures and the fetched HL7 C-CDA-Examples
    documents actually use - to readable plain text, one line per
    paragraph/list-item/table-row. Returns `""` for a missing or fully
    empty `<text>` element - the caller's own "nothing to build" signal,
    matching this app's established "no resolvable content -> skip"
    convention rather than fabricating a DocumentReference with an empty
    body."""
    return "\n".join(_extract_narrative_lines(text_element))


def build_narrative_document_reference(section: Element, patient_id: str, recorder=None) -> list[Resource]:
    """The one shared builder registered under every templateId this module
    exports (see module docstring) - reads `<code>` (via the already-
    established `build_codeable_concept_from_cd`, the same CD-shaped
    extraction every other section builder's own coded fields already use)
    for `.type` and `<title>` for `.description`, extracts `<text>` via
    `extract_narrative_text`. Returns `[]` when there's no usable narrative
    text at all, rather than a DocumentReference with an empty attachment.

    **`Binary.data`'s own provenance location is precise only for the one
    shape that genuinely allows it**: when `<text>` has no child elements at
    all (Hospital Course's own real shape - plain mixed text, no `<paragraph>`
    wrapper), the whole narrative literally *is* `<text>`'s own direct text
    content, so `xpath_location("text")` (bare tag - "descend into this
    child, take its own text") points at it exactly. Every other real shape
    (one or more `<paragraph>`/`<list>`/`<table>` children) has no single
    element whose own text span covers the *joined*, multi-line value
    `Binary.data` actually holds - the identical "N source elements joined
    into one FHIR value with no per-element boundary in the output" case
    `app/mappings/mdm.py::_build_binary_from_obx`'s own OBX-5 join and
    `app/mappings/siu.py`'s own NTE-3 join already disclose, so this
    follows their exact precedent: a disclosed marker naming the block
    count (`"text (×N blocks)"`) rather than guessing at one of them."""
    text_element = find_child(section, "text")
    narrative_lines = _extract_narrative_lines(text_element)
    if not narrative_lines:
        return []
    narrative_text = "\n".join(narrative_lines)

    binary_id = str(uuid.uuid4())
    binary = Binary(
        id=binary_id,
        contentType="text/plain",
        data=base64.b64encode(narrative_text.encode("utf-8")).decode("ascii"),
    )
    if recorder:
        has_children = len(list(text_element)) > 0
        # xpath_location() joins segments with "/", meant for a real path
        # chain - a disclosed marker isn't one, so it's built directly here
        # instead, matching MDM's own identical f-string marker precedent.
        location = f"text (×{len(narrative_lines)} blocks)" if has_children else xpath_location("text")
        recorder.record(binary_id, "data", location, narrative_text)

    document_reference_id = str(uuid.uuid4())
    attachment = Attachment(contentType="text/plain", url=f"urn:uuid:{binary_id}")
    document_reference = DocumentReference(
        id=document_reference_id,
        status="current",
        content=[DocumentReferenceContent(attachment=attachment)],
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    code_element = find_child(section, "code")
    section_type = build_codeable_concept_from_cd(code_element)
    if section_type:
        document_reference.type = section_type
        if recorder:
            recorder.record(
                document_reference_id, "type.coding[0].code", xpath_location("code", "@code"), section_type.coding[0].code
            )
            if section_type.coding[0].display:
                recorder.record(
                    document_reference_id,
                    "type.coding[0].display",
                    xpath_location("code", "@displayName"),
                    section_type.coding[0].display,
                )

    title_element = find_child(section, "title")
    if title_element is not None and title_element.text and title_element.text.strip():
        title = title_element.text.strip()
        document_reference.description = title
        if recorder:
            recorder.record(document_reference_id, "description", xpath_location("title"), title)

    return [document_reference, binary]
