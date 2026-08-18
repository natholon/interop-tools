"""Unit tests for app/provenance/hl7_locator.py - the HL7v2 source_location
string -> character-span resolver behind the Data Specification page's
correlated highlighting. Verified against real fixtures (not just
synthetic strings), including the MSH field-numbering quirk and the
multi-occurrence (repeating segment) resolution case."""

from app.provenance.hl7_locator import Hl7Locator, parse_hl7_location

FIXTURES = "tests/fixtures"


def read_fixture(name: str) -> str:
    with open(f"{FIXTURES}/{name}", encoding="utf-8") as f:
        return f.read()


def test_parse_hl7_location_full_grammar():
    parsed = parse_hl7_location("PID-5[0].1")
    assert parsed.segment_id == "PID"
    assert parsed.field == 5
    assert parsed.repetition == 0
    assert parsed.component == 1

    parsed_no_component = parse_hl7_location("MSH-10")
    assert parsed_no_component.field == 10
    assert parsed_no_component.repetition is None
    assert parsed_no_component.component is None


def test_parse_hl7_location_rejects_malformed_strings():
    assert parse_hl7_location("not-a-location") is None
    assert parse_hl7_location("") is None
    assert parse_hl7_location("PID") is None


def test_msh_field_numbering_quirk_msh1_is_the_separator_char():
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    span = loc.locate("MSH-1", 0)
    assert span is not None
    assert loc.display_text[span[0] : span[1]] == "|"


def test_msh_fields_resolve_correctly_despite_the_shift():
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    # MSH-9 (message type) is a composite CWE-like field - MSH-9.1/.2 must
    # resolve to its own components, not the wrong shifted field.
    type_span = loc.locate("MSH-9.1", 0)
    trigger_span = loc.locate("MSH-9.2", 0)
    assert loc.display_text[type_span[0] : type_span[1]] == "ADT"
    assert loc.display_text[trigger_span[0] : trigger_span[1]] == "A01"

    control_id_span = loc.locate("MSH-10", 0)
    assert loc.display_text[control_id_span[0] : control_id_span[1]] == "MSG00001"


def test_pid_field_component_and_repetition_resolve_correctly():
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    family_span = loc.locate("PID-5[0].1", 0)
    given_span = loc.locate("PID-5[0].2", 0)
    assert loc.display_text[family_span[0] : family_span[1]] == "Doe"
    assert loc.display_text[given_span[0] : given_span[1]] == "Jane"

    dob_span = loc.locate("PID-7", 0)
    assert loc.display_text[dob_span[0] : dob_span[1]] == "19620305"


def test_resolved_span_falls_within_the_correct_segment_line():
    # A field value that happens to also appear as a substring elsewhere in
    # the message must not accidentally resolve outside its own segment's
    # own line - checked via the public display_text alone (splitting on
    # the newline this locator itself substitutes \r into).
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    span = loc.locate("PID-5[0].1", 0)
    line_start = loc.display_text.rfind("\n", 0, span[0]) + 1
    line_end = loc.display_text.find("\n", span[1])
    line = loc.display_text[line_start : line_end if line_end != -1 else None]
    assert line.startswith("PID|")


def test_out_of_range_field_returns_none_not_a_crash():
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    assert loc.locate("PID-999", 0) is None
    assert loc.locate("PID-5[0].999", 0) is None
    assert loc.locate("ZZZ-1", 0) is None  # a segment that doesn't exist at all
    assert loc.locate("not-a-real-location", 0) is None


def test_occurrence_count_and_multi_occurrence_resolution_for_repeating_segments():
    # oru_r01_basic.hl7 has 2 OBR-led reports with 3 OBX results total
    # (CLAUDE.md's own documented shape) - each occurrence must resolve to
    # a genuinely distinct, correct value, not all collapsing to the first.
    loc = Hl7Locator(read_fixture("oru_r01_basic.hl7"))
    assert loc.occurrence_count("OBX") == 3
    assert loc.occurrence_count("OBR") == 2

    values = []
    for occurrence in range(loc.occurrence_count("OBX")):
        span = loc.locate("OBX-5", occurrence)
        assert span is not None
        values.append(loc.display_text[span[0] : span[1]])

    assert len(set(values)) == 3  # all three genuinely distinct
    assert loc.locate("OBX-5", 3) is None  # one past the last real occurrence


def test_root_key_returns_the_segment_id():
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    assert loc.root_key("PID-5[0].1") == "PID"
    assert loc.root_key("MSH-10") == "MSH"
    assert loc.root_key("not-a-location") is None


def test_display_text_uses_newlines_not_carriage_returns():
    loc = Hl7Locator(read_fixture("adt_a01_basic.hl7"))
    assert "\r" not in loc.display_text
    assert "\n" in loc.display_text
    assert loc.display_text.startswith("MSH|")


def test_batched_message_only_resolves_against_the_first_message():
    # Mirrors app/hl7/parser.py's own truncate_to_first_message contract -
    # a second MSH-led message concatenated after the first must not be
    # searchable at all (matching what actually gets converted).
    first = read_fixture("adt_a01_basic.hl7").rstrip("\n").rstrip("\r")
    second = read_fixture("adt_a01_basic.hl7")
    batched = first + "\r" + second
    loc = Hl7Locator(batched)
    assert loc.occurrence_count("MSH") == 1
