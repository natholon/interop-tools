"""Narrative-only C-CDA sections -> DocumentReference + Binary - the
Discharge Summary/History and Physical follow-up that closes their own
long-disclosed gap (Hospital Course, Plan of Treatment, and H&P's own nine
required narrative sections were all previously silently skipped, see
CLAUDE.md's own prior disclosure).

**Why DocumentReference+Binary, not a full FHIR-Document Bundle(type=
"document")+Composition** - confirmed with the user directly, not assumed:
`Composition.author` is FHIR-required (1..1+) and this app has never parsed
`ClinicalDocument/author` at all; a real Bundle(type="document") redesign
would also ripple into app/dedup.py, app/provenance/, app/transform/
cda_*.py, and every existing test asserting Bundle.type=="collection" - a
much larger, cross-cutting undertaking than this slice. Instead, each
present narrative section becomes its own DocumentReference (LOINC-coded
`.type`, read live from the section's own real `<code>`, not hardcoded)
referencing a Binary (the extracted plain-text narrative) - mirroring this
app's own already-established MDM/TXA -> DocumentReference+Binary pattern
(app/mappings/mdm.py::build_document_reference/_build_binary_from_obx)
exactly. Purely additive: Bundle.type stays "collection", nothing about any
other pillar changes.

**Verified against the real HL7 C-CDA-Examples GitHub documents** (`Documents/
Discharge Summary/Discharge_Summary.xml`, `Documents/History and Physical/
History_and_Physical.xml`), not guessed - both the templateId/LOINC pairs
below and the real narrative shapes (`<paragraph>`, `<list>`/`<item>`,
`<table>`, plain mixed text) `extract_narrative_text` handles. **A genuine
gotcha found during that research**: four of these sections do NOT live in
the `2.16.840.1.113883.10.20.22.2.x` namespace every other section in this
app uses - Hospital Course/History of Present Illness/Review of Systems are
legacy IHE PCC templates (`1.3.6.1.4.1.19376.1.5.3.1.3.x`), Physical Exam/
General Status are legacy HITSP/C32 templates (`2.16.840.1.113883.10.20.2.x`
- one OID segment shorter than the native C-CDA ones, easy to transcribe
wrong) - both root trees confirmed directly against the real example
documents' own `<templateId root="...">` values, not assumed from the C-CDA
2.1 IG's own naming conventions alone.

**Plan of Treatment and "Plan of Care" are the same template, not two**:
`2.16.840.1.113883.10.20.22.2.10` (LOINC 18776-5) is required by both
Discharge Summary and History and Physical - real documents just title it
differently ("PLAN OF CARE" on H&P, confirmed directly in the real fetched
example) - so registering it once in SECTION_BUILDERS covers both document
types for free, the same "one shared templateId, multiple document types"
shape Problems/Medications/etc. already established.

**Three of these eleven registered templateIds - Plan of Treatment, Social
History, Family History - can carry real structured entries in the wild**
(a Plan of Care Activity Observation, Social History/Smoking Status
Observations, a Family History Organizer with member Observations,
confirmed by inspecting the real fetched examples' own entry content) -
this slice deliberately does not parse any of them; the narrative
DocumentReference is the only representation added here. A future slice
could add real structured resources (Observation/FamilyMemberHistory/etc.)
alongside this narrative one without conflict - disclosed as a natural
next slice, not attempted here, the same "map the general case now,
disclose the special case as a later slice" precedent this app's Vitals/
Results sections already established for their own deferred grouping
special cases.

**Provenance instrumentation, bidirectional transform, and new validation
rules for these sections are all likewise disclosed as deferred, not
attempted this slice** - every other C-CDA section this app has ever added
got its own follow-up slice for each of those three pillars, sometimes long
after the section itself first shipped (see CLAUDE.md's own Data
Specification section for the C-CDA instrumentation timeline)."""

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
    if text_element is None:
        return ""
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
    return "\n".join(lines)


def build_narrative_document_reference(section: Element, patient_id: str, recorder=None) -> list[Resource]:
    """The one shared builder registered under every templateId this module
    exports (see module docstring) - reads `<code>` (via the already-
    established `build_codeable_concept_from_cd`, the same CD-shaped
    extraction every other section builder's own coded fields already use)
    for `.type` and `<title>` for `.description`, extracts `<text>` via
    `extract_narrative_text`. Returns `[]` when there's no usable narrative
    text at all, rather than a DocumentReference with an empty attachment.

    `recorder` is accepted but not yet acted on - every section builder
    SECTION_BUILDERS dispatches through accepts it uniformly, even before
    its own slice records anything (see app.cda.common.build_sectioned_
    bundle's own docstring); provenance instrumentation for these sections
    is a disclosed, deliberate follow-up, not attempted here."""
    text_element = find_child(section, "text")
    narrative_text = extract_narrative_text(text_element)
    if not narrative_text:
        return []

    binary_id = str(uuid.uuid4())
    binary = Binary(
        id=binary_id,
        contentType="text/plain",
        data=base64.b64encode(narrative_text.encode("utf-8")).decode("ascii"),
    )

    document_reference_id = str(uuid.uuid4())
    attachment = Attachment(contentType="text/plain", url=f"urn:uuid:{binary_id}")
    document_reference = DocumentReference(
        id=document_reference_id,
        status="current",
        content=[DocumentReferenceContent(attachment=attachment)],
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    section_type = build_codeable_concept_from_cd(find_child(section, "code"))
    if section_type:
        document_reference.type = section_type

    title_element = find_child(section, "title")
    if title_element is not None and title_element.text and title_element.text.strip():
        document_reference.description = title_element.text.strip()

    return [document_reference, binary]
