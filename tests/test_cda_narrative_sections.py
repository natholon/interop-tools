import xml.etree.ElementTree as ET

from app.cda.narrative_sections import build_narrative_document_reference, extract_narrative_text
from app.provenance.recorder import ProvenanceRecorder

_NS = "urn:hl7-org:v3"


def _text(body: str) -> ET.Element:
    return ET.fromstring(f'<text xmlns="{_NS}">{body}</text>')


def _section(body: str) -> ET.Element:
    return ET.fromstring(f'<section xmlns="{_NS}">{body}</section>')


# --- extract_narrative_text ---


def test_extract_narrative_text_returns_empty_string_for_none():
    assert extract_narrative_text(None) == ""


def test_extract_narrative_text_returns_empty_string_for_empty_element():
    assert extract_narrative_text(_text("")) == ""


def test_extract_narrative_text_handles_plain_mixed_text_with_no_children():
    # Hospital Course's own real shape - no <paragraph> wrapper at all.
    element = _text(" Patient presented with dark stools. ")
    assert extract_narrative_text(element) == "Patient presented with dark stools."


def test_extract_narrative_text_joins_multiple_paragraphs_with_newlines():
    element = _text("<paragraph>First line.</paragraph><paragraph>Second line.</paragraph>")
    assert extract_narrative_text(element) == "First line.\nSecond line."


def test_extract_narrative_text_joins_list_items_with_newlines():
    element = _text('<list listType="ordered"><item>One.</item><item>Two.</item></list>')
    assert extract_narrative_text(element) == "One.\nTwo."


def test_extract_narrative_text_renders_table_rows_pipe_joined_preserving_columns():
    element = _text(
        "<table><thead><tr><th>Element</th><th>Description</th></tr></thead>"
        "<tbody><tr><td>Tobacco smoking status</td><td>Former smoker</td></tr></tbody></table>"
    )
    assert extract_narrative_text(element) == "Element | Description\nTobacco smoking status | Former smoker"


def test_extract_narrative_text_table_with_content_anchor_inside_a_cell():
    # The real Social History shape: a <content ID="..."/> anchor sits
    # inside the same <td> as the cell's own real text - both must merge
    # into one cell value, not two separate table cells.
    element = _text(
        '<table><tbody><tr><td><content ID="soc1"/>Tobacco smoking status</td>'
        "<td>Former smoker</td></tr></tbody></table>"
    )
    assert extract_narrative_text(element) == "Tobacco smoking status | Former smoker"


def test_extract_narrative_text_table_with_no_thead_tbody_wrapper():
    # <tr> can legally sit directly under <table> with no row-group
    # wrapper at all - must still be found.
    element = _text("<table><tr><td>A</td><td>B</td></tr></table>")
    assert extract_narrative_text(element) == "A | B"


def test_extract_narrative_text_combines_paragraph_and_table():
    element = _text(
        "<paragraph>Father (deceased)</paragraph>"
        "<table><thead><tr><th>Diagnosis</th><th>Age</th></tr></thead>"
        "<tbody><tr><td>Myocardial infarction</td><td>57</td></tr></tbody></table>"
    )
    assert extract_narrative_text(element) == "Father (deceased)\nDiagnosis | Age\nMyocardial infarction | 57"


def test_extract_narrative_text_captures_tail_text_after_an_inline_child():
    # Mixed content directly under <text>: leading text, an inline
    # <content> child, then more direct text after it (the child's own
    # "tail") - all three pieces belong to the narrative.
    element = _text('Some intro <content ID="x">highlighted</content> continues here.')
    text = extract_narrative_text(element)
    assert "Some intro" in text
    assert "highlighted" in text
    assert "continues here." in text


def test_extract_narrative_text_ignores_empty_cells():
    element = _text("<table><tbody><tr><td></td><td>Only this</td></tr></tbody></table>")
    assert extract_narrative_text(element) == "Only this"


# --- build_narrative_document_reference ---


def _hospital_course_section() -> ET.Element:
    return _section(
        '<templateId root="1.3.6.1.4.1.19376.1.5.3.1.3.5" extension="2014-06-09"/>'
        '<code code="8648-8" displayName="HOSPITAL COURSE" codeSystem="2.16.840.1.113883.6.1" codeSystemName="LOINC"/>'
        "<title>HOSPITAL COURSE</title>"
        "<text> Patient presented with dark stools. </text>"
    )


def test_build_narrative_document_reference_basic_construction():
    resources = build_narrative_document_reference(_hospital_course_section(), "patient-1")
    assert len(resources) == 2
    document_reference, binary = resources
    assert document_reference.get_resource_type() == "DocumentReference"
    assert binary.get_resource_type() == "Binary"

    assert document_reference.status == "current"
    assert document_reference.subject.reference == "urn:uuid:patient-1"
    assert document_reference.type.coding[0].system == "http://loinc.org"
    assert document_reference.type.coding[0].code == "8648-8"
    assert document_reference.type.coding[0].display == "HOSPITAL COURSE"
    assert document_reference.description == "HOSPITAL COURSE"
    assert document_reference.content[0].attachment.contentType == "text/plain"
    assert document_reference.content[0].attachment.url == f"urn:uuid:{binary.id}"

    assert binary.contentType == "text/plain"
    assert binary.data.decode("utf-8") == "Patient presented with dark stools."


def test_build_narrative_document_reference_returns_empty_list_for_empty_text():
    section = _section(
        '<templateId root="1.3.6.1.4.1.19376.1.5.3.1.3.5"/>'
        '<code code="8648-8" displayName="HOSPITAL COURSE" codeSystem="2.16.840.1.113883.6.1"/>'
        "<title>HOSPITAL COURSE</title>"
        "<text></text>"
    )
    assert build_narrative_document_reference(section, "patient-1") == []


def test_build_narrative_document_reference_returns_empty_list_for_missing_text_element():
    section = _section(
        '<templateId root="1.3.6.1.4.1.19376.1.5.3.1.3.5"/>'
        '<code code="8648-8" displayName="HOSPITAL COURSE" codeSystem="2.16.840.1.113883.6.1"/>'
        "<title>HOSPITAL COURSE</title>"
    )
    assert build_narrative_document_reference(section, "patient-1") == []


def test_build_narrative_document_reference_without_code_still_builds_with_no_type():
    section = _section("<title>HOSPITAL COURSE</title><text>Some narrative.</text>")
    resources = build_narrative_document_reference(section, "patient-1")
    assert len(resources) == 2
    document_reference, _binary = resources
    assert document_reference.type is None
    assert document_reference.description == "HOSPITAL COURSE"


def test_build_narrative_document_reference_without_title_leaves_description_unset():
    section = _section(
        '<code code="8648-8" displayName="HOSPITAL COURSE" codeSystem="2.16.840.1.113883.6.1"/>'
        "<text>Some narrative.</text>"
    )
    resources = build_narrative_document_reference(section, "patient-1")
    document_reference, _binary = resources
    assert document_reference.description is None
    assert document_reference.type.coding[0].code == "8648-8"


def test_build_narrative_document_reference_records_type_description_and_data():
    recorder = ProvenanceRecorder(source_format="CDA")
    document_reference, binary = build_narrative_document_reference(
        _hospital_course_section(), "patient-1", recorder=recorder
    )
    facts = {(f.resource_id, f.relative_path): f for f in recorder.facts}

    code_fact = facts[(document_reference.id, "type.coding[0].code")]
    assert code_fact.derivation == "direct"
    assert code_fact.source_location == "code/@code"
    assert code_fact.value == "8648-8"

    display_fact = facts[(document_reference.id, "type.coding[0].display")]
    assert display_fact.source_location == "code/@displayName"
    assert display_fact.value == "HOSPITAL COURSE"

    description_fact = facts[(document_reference.id, "description")]
    assert description_fact.source_location == "title"
    assert description_fact.value == "HOSPITAL COURSE"

    # Hospital Course's own real shape (no <paragraph> wrapper at all) is
    # the one case with a genuinely precise location - see the function's
    # own docstring for why every other shape gets a disclosed marker
    # instead.
    data_fact = facts[(binary.id, "data")]
    assert data_fact.source_location == "text"
    assert data_fact.value == "Patient presented with dark stools."


def test_build_narrative_document_reference_uses_disclosed_marker_for_multi_block_text():
    section = _section(
        '<templateId root="1.3.6.1.4.1.19376.1.5.3.1.3.5"/>'
        '<code code="8648-8" displayName="HOSPITAL COURSE" codeSystem="2.16.840.1.113883.6.1"/>'
        "<title>HOSPITAL COURSE</title>"
        "<text><paragraph>First line.</paragraph><paragraph>Second line.</paragraph></text>"
    )
    recorder = ProvenanceRecorder(source_format="CDA")
    _document_reference, binary = build_narrative_document_reference(section, "patient-1", recorder=recorder)
    data_fact = next(f for f in recorder.facts if f.resource_id == binary.id and f.relative_path == "data")
    assert data_fact.source_location == "text (×2 blocks)"
    assert data_fact.value == "First line.\nSecond line."


def test_build_narrative_document_reference_without_recorder_still_works():
    # recorder is optional - every SECTION_BUILDERS entry must tolerate
    # being called with no recorder at all (the normal, non-provenance
    # conversion path).
    resources = build_narrative_document_reference(_hospital_course_section(), "patient-1")
    assert len(resources) == 2
