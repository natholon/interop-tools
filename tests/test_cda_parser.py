import pytest

from app.cda.errors import CdaParseError
from app.cda.parser import (
    coded_value,
    find_all,
    find_child,
    has_template_id,
    ivl_ts_bounds,
    parse_document,
    ts_value,
    xsi_type,
)

_XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def _doc(body: str) -> str:
    return f'<ClinicalDocument xmlns="urn:hl7-org:v3" {_XSI}>{body}</ClinicalDocument>'


def test_parse_document_returns_root_element():
    root = parse_document(_doc("<id root='1.2.3'/>"))
    assert root.tag == "{urn:hl7-org:v3}ClinicalDocument"


def test_parse_document_raises_on_malformed_xml():
    with pytest.raises(CdaParseError):
        parse_document("<ClinicalDocument><unclosed></ClinicalDocument>")


def test_parse_document_raises_when_root_is_not_clinical_document():
    with pytest.raises(CdaParseError):
        parse_document('<NotAClinicalDocument xmlns="urn:hl7-org:v3"/>')


def test_has_template_id_matches_when_bare_templateid_comes_first():
    section = parse_document(
        _doc(
            "<component><section>"
            "<templateId root='1.2.3'/>"
            "<templateId root='1.2.3' extension='2015-08-01'/>"
            "</section></component>"
        )
    )
    section_el = find_child(find_child(section, "component"), "section")
    assert has_template_id(section_el, "1.2.3")


def test_has_template_id_matches_when_versioned_templateid_comes_first():
    section = parse_document(
        _doc(
            "<component><section>"
            "<templateId root='1.2.3' extension='2015-08-01'/>"
            "<templateId root='1.2.3'/>"
            "</section></component>"
        )
    )
    section_el = find_child(find_child(section, "component"), "section")
    assert has_template_id(section_el, "1.2.3")


def test_has_template_id_does_not_recurse_into_descendants():
    # A section whose own templateId is "1.2.3" but which nests an entry
    # carrying an unrelated templateId "9.9.9" - has_template_id("9.9.9")
    # must NOT false-match via the nested descendant.
    section = parse_document(
        _doc(
            "<component><section>"
            "<templateId root='1.2.3'/>"
            "<entry><act><templateId root='9.9.9'/></act></entry>"
            "</section></component>"
        )
    )
    section_el = find_child(find_child(section, "component"), "section")
    assert has_template_id(section_el, "1.2.3")
    assert not has_template_id(section_el, "9.9.9")


def test_has_template_id_returns_false_when_absent():
    root = parse_document(_doc("<id root='1.2.3'/>"))
    assert not has_template_id(root, "1.2.3")


def test_find_all_auto_prefixes_every_path_segment():
    root = parse_document(
        _doc(
            "<component><structuredBody>"
            "<component><section><title>A</title></section></component>"
            "<component><section><title>B</title></section></component>"
            "</structuredBody></component>"
        )
    )
    sections = find_all(root, "component/structuredBody/component/section")
    assert [find_child(s, "title").text for s in sections] == ["A", "B"]


def test_find_all_returns_empty_list_when_path_does_not_match():
    root = parse_document(_doc("<id root='1.2.3'/>"))
    assert find_all(root, "component/structuredBody/component/section") == []


def test_xsi_type_reads_clark_notation_attribute():
    root = parse_document(_doc("<value xsi:type='CD' code='1' codeSystem='2.16'/>"))
    value_el = find_child(root, "value")
    assert xsi_type(value_el) == "CD"


def test_xsi_type_returns_none_when_absent():
    root = parse_document(_doc("<value code='1' codeSystem='2.16'/>"))
    value_el = find_child(root, "value")
    assert xsi_type(value_el) is None


def test_coded_value_extracts_code_display_and_system():
    root = parse_document(_doc("<code code='38341003' codeSystem='2.16.840.1.113883.6.96' displayName='Hypertension'/>"))
    code_el = find_child(root, "code")
    assert coded_value(code_el) == ("38341003", "Hypertension", "2.16.840.1.113883.6.96")


def test_coded_value_returns_none_when_code_attribute_absent():
    root = parse_document(_doc("<code nullFlavor='UNK'/>"))
    code_el = find_child(root, "code")
    assert coded_value(code_el) is None


def test_coded_value_returns_none_for_none_element():
    assert coded_value(None) is None


def test_ts_value_returns_bare_value():
    root = parse_document(_doc("<birthTime value='19750501'/>"))
    assert ts_value(find_child(root, "birthTime")) == "19750501"


def test_ts_value_returns_none_when_element_absent():
    assert ts_value(None) is None


def test_ivl_ts_bounds_point_in_time_returns_equal_bounds():
    root = parse_document(_doc("<effectiveTime value='20220301'/>"))
    assert ivl_ts_bounds(find_child(root, "effectiveTime")) == ("20220301", "20220301")


def test_ivl_ts_bounds_low_only():
    root = parse_document(_doc("<effectiveTime><low value='20220615'/></effectiveTime>"))
    assert ivl_ts_bounds(find_child(root, "effectiveTime")) == ("20220615", None)


def test_ivl_ts_bounds_low_and_high():
    root = parse_document(_doc("<effectiveTime><low value='20210110'/><high value='20210209'/></effectiveTime>"))
    assert ivl_ts_bounds(find_child(root, "effectiveTime")) == ("20210110", "20210209")


def test_ivl_ts_bounds_null_flavor_returns_none_none():
    root = parse_document(_doc("<effectiveTime nullFlavor='UNK'/>"))
    assert ivl_ts_bounds(find_child(root, "effectiveTime")) == (None, None)


def test_ivl_ts_bounds_returns_none_none_for_none_element():
    assert ivl_ts_bounds(None) == (None, None)
