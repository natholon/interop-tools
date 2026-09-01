"""Unit tests for app/provenance/cda_locator.py - the C-CDA source_location
string -> character-span resolver behind the Data Specification page's
correlated highlighting. Verified against real fixtures, including the
double-bracket `entryRelationship[MFST][0]` case and - the module's own
hardest, most consequential problem - the cross-section root-tag
collision (Vital Signs vs Results both use <organizer>, plain Problems vs
Hospital Discharge Diagnosis both use <act>) proven fixed by constructing
a real document that actually exercises it, not just asserted."""

import re

from app.provenance.cda_locator import CdaLocator, _resolve_attribute_span, parse_segment, parse_with_positions

FIXTURES = "tests/fixtures"


def read_fixture(name: str) -> str:
    with open(f"{FIXTURES}/{name}", encoding="utf-8") as f:
        return f.read()


def _extract_section(doc: str, template_id: str) -> str:
    match = re.search(r"<section>((?:(?!</section>).)*" + re.escape(template_id) + r"(?:(?!</section>).)*)</section>", doc, re.S)
    assert match is not None, f"template id {template_id} not found in fixture"
    return f"<section>{match.group(1)}</section>"


# --- parse_segment grammar ---


def test_parse_segment_plain_tag_defaults_to_index_zero():
    parsed = parse_segment("code")
    assert parsed.tag == "code"
    assert parsed.label is None
    assert parsed.index == 0


def test_parse_segment_numeric_bracket_is_positional_index():
    parsed = parse_segment("name[2]")
    assert parsed.tag == "name"
    assert parsed.label is None
    assert parsed.index == 2


def test_parse_segment_label_bracket_is_a_typecode_filter():
    parsed = parse_segment("entryRelationship[SUBJ]")
    assert parsed.tag == "entryRelationship"
    assert parsed.label == "SUBJ"
    assert parsed.index == 0


def test_parse_segment_label_and_index_together():
    parsed = parse_segment("entryRelationship[MFST][0]")
    assert parsed.tag == "entryRelationship"
    assert parsed.label == "MFST"
    assert parsed.index == 0

    parsed_second = parse_segment("entryRelationship[MFST][1]")
    assert parsed_second.index == 1


# --- position-aware parsing primitives ---


def test_parse_with_positions_resolves_attribute_and_text_spans():
    raw = '<root><code code="88" displayName="flu"/><name>Doe</name></root>'
    root = parse_with_positions(raw)
    code_elem = root.children[0]
    assert raw[code_elem.start_tag_span[0] : code_elem.start_tag_span[1]] == '<code code="88" displayName="flu"/>'
    name_elem = root.children[1]
    assert raw[name_elem.text_span[0] : name_elem.text_span[1]] == "Doe"


def test_parse_with_positions_non_ascii_content_offsets_are_character_based():
    # Confirms expat's CurrentByteIndex, as exposed through this module,
    # is usable directly as a Python string slice index - the exact
    # concern this module's own docstring discloses testing for.
    raw = '<root><name>José García</name></root>'
    root = parse_with_positions(raw)
    name_elem = root.children[0]
    assert raw[name_elem.text_span[0] : name_elem.text_span[1]] == "José García"


def test_attribute_resolution_does_not_false_match_a_prefixed_attribute_name():
    # "code" must not match inside "codeSystem" (a false prefix match) or
    # "mycode" (a false suffix match) - the leading-whitespace requirement
    # in _attr_pattern is what prevents both.
    raw = '<code mycode="wrong" code="88" codeSystem="2.16.840.1.113883.6.12"/>'
    root = parse_with_positions(raw)
    span = _resolve_attribute_span(raw, root.start_tag_span, "code")
    assert raw[span[0] : span[1]] == "88"


def test_attribute_resolution_returns_none_for_absent_attribute():
    raw = '<code code="88"/>'
    root = parse_with_positions(raw)
    assert _resolve_attribute_span(raw, root.start_tag_span, "displayName") is None


def test_malformed_xml_degrades_to_none_not_a_crash():
    loc = CdaLocator("<not valid xml")
    assert loc.locate("act/statusCode/@code", 0) is None
    assert loc.occurrence_count("act") == 0


# --- real fixtures: basic resolution ---


def test_allergen_and_double_bracket_reaction_resolve_correctly():
    loc = CdaLocator(read_fixture("ccd_allergies_basic.xml"))
    allergen_span = loc.locate(
        "act/entryRelationship[SUBJ]/observation/participant[CSM]/participantRole/playingEntity/code/@code", 0, "allergies"
    )
    assert loc.display_text[allergen_span[0] : allergen_span[1]] == "102263004"

    reaction_span = loc.locate(
        "act/entryRelationship[SUBJ]/observation/entryRelationship[MFST][0]/observation/value/@code", 0, "allergies"
    )
    assert loc.display_text[reaction_span[0] : reaction_span[1]] == "247472004"


def test_text_axis_resolves_free_text_value():
    loc = CdaLocator(read_fixture("ccd_results_basic.xml"))
    # The free-text ST-valued member's own text() location, per
    # app/cda/results.py's own f"{value_base}/text()" call site.
    span = loc.locate("organizer/component[1]/observation/value/text()", 0, "results")
    assert span is not None
    assert loc.display_text[span[0] : span[1]] == "No growth after 48 hours"


def test_out_of_range_occurrence_returns_none():
    loc = CdaLocator(read_fixture("ccd_allergies_basic.xml"))
    assert loc.locate("act/entryRelationship[SUBJ]/observation/value/@code", 5, "allergies") is None
    assert loc.locate("not/a/real/@path", 0) is None


def test_root_key_returns_the_bare_first_tag():
    loc = CdaLocator(read_fixture("ccd_allergies_basic.xml"))
    assert loc.root_key("act/entryRelationship[SUBJ]/observation/value/@code") == "act"
    assert loc.root_key("") is None


# --- the cross-section root-tag collision, proven fixed ---


def test_scope_hint_disambiguates_vitals_from_results_organizer_collision():
    # Neither real fixture alone has both sections - constructed by
    # combining two real fixtures' own sections into one document, the
    # only way to actually exercise this collision rather than merely
    # assert the fix exists.
    vitals_doc = read_fixture("ccd_vitals_basic.xml")
    results_doc = read_fixture("ccd_results_basic.xml")
    results_section = _extract_section(results_doc, "2.16.840.1.113883.10.20.22.2.3.1")
    combined = vitals_doc.replace("</structuredBody>", results_section + "</structuredBody>")

    loc = CdaLocator(combined)
    assert loc.occurrence_count("organizer") == 2  # unscoped: both sections' organizers counted together
    assert loc.occurrence_count("organizer", "vitals") == 1
    assert loc.occurrence_count("organizer", "results") == 1

    vitals_member_code = loc.locate("organizer/component[0]/observation/code/@code", 0, "vitals")
    results_member_code = loc.locate("organizer/component[0]/observation/code/@code", 0, "results")
    assert combined[vitals_member_code[0] : vitals_member_code[1]] == "8867-4"  # Heart Rate, the real Vitals member
    assert combined[results_member_code[0] : results_member_code[1]] == "6690-2"  # Leukocytes, the real Results member
    assert vitals_member_code != results_member_code


def test_scope_hint_disambiguates_problems_from_hospital_discharge_diagnosis_act_collision():
    # discharge_summary_basic.xml genuinely carries both a plain Problems
    # Concern Act and a Hospital Discharge Diagnosis Act (see docs/build-history.md's
    # own documented shape for this fixture) - a real, not constructed,
    # collision case.
    loc = CdaLocator(read_fixture("discharge_summary_basic.xml"))
    assert loc.occurrence_count("act", "problems") == 1
    assert loc.occurrence_count("act", "hospital_discharge_diagnosis") == 1

    problems_code = loc.locate("act/entryRelationship[SUBJ]/observation/value/@code", 0, "problems")
    hdd_code = loc.locate("act/entryRelationship[SUBJ]/observation/value/@code", 0, "hospital_discharge_diagnosis")
    assert problems_code is not None and hdd_code is not None
    assert loc.display_text[problems_code[0] : problems_code[1]] != loc.display_text[hdd_code[0] : hdd_code[1]]
