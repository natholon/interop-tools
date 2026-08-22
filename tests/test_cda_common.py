"""Direct unit tests for app/cda/common.py's own shared helpers, built
against small inline XML strings rather than fixture files - the same
style test_cda_parser.py already established for this package's own
primitive-level tests, deliberately not reusing generator output for the
same reason test_validation_generic.py doesn't.

Scoped to originalText resolution (the narrative-anchor pre-pass and the
CodeableConcept.text mapping it feeds); the rest of this module's helpers
are exercised end-to-end through tests/test_ccd_mapping.py's own real
fixtures."""

import xml.etree.ElementTree as ET

from app.cda.common import (
    build_codeable_concept_from_cd,
    narrative_anchor_text,
    resolve_narrative_references,
)

_SNOMED = "2.16.840.1.113883.6.96"


def _section(narrative: str, entry_code: str) -> ET.Element:
    return ET.fromstring(
        f'<section xmlns="urn:hl7-org:v3"><text>{narrative}</text>'
        f"<entry><procedure>{entry_code}</procedure></entry></section>"
    )


def _code_of(section: ET.Element) -> ET.Element:
    return section.find(".//{urn:hl7-org:v3}code")


def test_inline_original_text_maps_to_codeable_concept_text():
    # The inline shape needs no narrative context at all - the text is a
    # direct child of the CD element already being read.
    element = ET.fromstring(
        f'<code xmlns="urn:hl7-org:v3" code="1" codeSystem="{_SNOMED}">'
        "<originalText>Colonic polypectomy</originalText></code>"
    )
    concept = build_codeable_concept_from_cd(element)
    assert concept.text == "Colonic polypectomy"
    assert concept.coding[0].code == "1"


def test_inline_original_text_whitespace_is_normalized():
    element = ET.fromstring(
        f'<code xmlns="urn:hl7-org:v3" code="1" codeSystem="{_SNOMED}">'
        "<originalText>\n   Inline  free   text\n  </originalText></code>"
    )
    assert build_codeable_concept_from_cd(element).text == "Inline free text"


def test_no_original_text_leaves_codeable_concept_text_unset():
    element = ET.fromstring(f'<code xmlns="urn:hl7-org:v3" code="1" codeSystem="{_SNOMED}"/>')
    assert build_codeable_concept_from_cd(element).text is None


def test_narrative_anchor_text_collects_id_carrying_elements():
    # CDA's own spelling is capital-ID, not xml:id or lowercase id.
    section = _section(
        '<table><tr><td ID="P1"> Colonic  polypectomy </td><td>no anchor</td></tr></table>',
        '<code code="1" codeSystem="{}"/>'.format(_SNOMED),
    )
    assert narrative_anchor_text(section) == {"P1": "Colonic polypectomy"}


def test_narrative_anchor_text_flattens_nested_inline_markup():
    # The IG requires markup be removed and the result stored as plain
    # text - <content> anchors inside a cell are the real C-CDA "entries
    # derive from narrative" pattern.
    section = _section(
        '<paragraph ID="P1">Resolved <content styleCode="Bold">narrative</content> text</paragraph>',
        '<code code="1" codeSystem="{}"/>'.format(_SNOMED),
    )
    assert narrative_anchor_text(section) == {"P1": "Resolved narrative text"}


def test_narrative_reference_resolves_to_codeable_concept_text():
    section = _section(
        '<table><tr><td ID="P1"> Colonic polypectomy </td></tr></table>',
        f'<code code="1" codeSystem="{_SNOMED}"><originalText><reference value="#P1"/></originalText></code>',
    )
    # Before the pre-pass the reference shape carries no inline text at all.
    assert build_codeable_concept_from_cd(_code_of(section)).text is None

    resolve_narrative_references(section)
    assert build_codeable_concept_from_cd(_code_of(section)).text == "Colonic polypectomy"


def test_narrative_reference_resolution_is_additive_not_destructive():
    # The <reference> child stays exactly where it was - only
    # originalText's own (previously empty) .text is filled in, so a
    # provenance source_location pointing at code/originalText stays
    # accurate and the raw reference is still readable.
    section = _section(
        '<paragraph ID="P1">Resolved text</paragraph>',
        f'<code code="1" codeSystem="{_SNOMED}"><originalText><reference value="#P1"/></originalText></code>',
    )
    resolve_narrative_references(section)
    reference = section.find(".//{urn:hl7-org:v3}reference")
    assert reference is not None
    assert reference.get("value") == "#P1"


def test_dangling_narrative_reference_leaves_text_unset():
    # An anchor that doesn't resolve must not crash or fabricate text.
    section = _section(
        '<paragraph ID="other">Some text</paragraph>',
        f'<code code="1" codeSystem="{_SNOMED}"><originalText><reference value="#missing"/></originalText></code>',
    )
    resolve_narrative_references(section)
    assert build_codeable_concept_from_cd(_code_of(section)).text is None


def test_inline_original_text_is_not_overwritten_by_resolution():
    # An entry carrying BOTH inline text and a reference keeps its own
    # inline text - the pre-pass only fills in an empty originalText.
    section = _section(
        '<paragraph ID="P1">Narrative wording</paragraph>',
        f'<code code="1" codeSystem="{_SNOMED}">'
        '<originalText>Entry wording<reference value="#P1"/></originalText></code>',
    )
    resolve_narrative_references(section)
    assert build_codeable_concept_from_cd(_code_of(section)).text == "Entry wording"


def test_section_with_no_narrative_text_is_a_no_op():
    section = ET.fromstring(
        '<section xmlns="urn:hl7-org:v3"><entry><procedure>'
        f'<code code="1" codeSystem="{_SNOMED}"><originalText><reference value="#P1"/></originalText></code>'
        "</procedure></entry></section>"
    )
    resolve_narrative_references(section)
    assert build_codeable_concept_from_cd(_code_of(section)).text is None
