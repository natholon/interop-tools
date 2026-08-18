"""Unit tests for app/provenance/edi_locator.py - the X12 EDI
source_location string -> character-span resolver behind the Data
Specification page's correlated highlighting. Verified against real
fixtures, including the explicit segment_repetition case (multiple
physical HI segments) and the multi-occurrence (repeating LX/SV1 line
item) resolution case."""

from app.provenance.edi_locator import EdiLocator, parse_edi_location

FIXTURES = "tests/fixtures"


def read_fixture(name: str) -> str:
    with open(f"{FIXTURES}/{name}", encoding="utf-8") as f:
        return f.read()


def test_parse_edi_location_full_grammar():
    parsed = parse_edi_location("HI[1]-1.2")
    assert parsed.segment_id == "HI"
    assert parsed.segment_repetition == 1
    assert parsed.element_num == 1
    assert parsed.component == 2

    parsed_plain = parse_edi_location("NM1-9")
    assert parsed_plain.segment_id == "NM1"
    assert parsed_plain.segment_repetition is None
    assert parsed_plain.component is None


def test_parse_edi_location_rejects_malformed_strings():
    assert parse_edi_location("not-a-location") is None
    assert parse_edi_location("") is None


def test_basic_element_and_component_resolve_correctly():
    loc = EdiLocator(read_fixture("edi_270_basic.x12"))
    payer_id_span = loc.locate("NM1-9", 0)
    assert loc.display_text[payer_id_span[0] : payer_id_span[1]] == "PAYERID001"

    bht_span = loc.locate("BHT-3", 0)
    assert loc.display_text[bht_span[0] : bht_span[1]] == "10001234"


def test_multi_occurrence_resolution_for_repeating_nm1_segments():
    # edi_270_basic.x12's own subscriber/dependent loops legitimately share
    # a last name ("DOE") - so this checks each occurrence resolves to its
    # own known-correct value in document order (payer, provider,
    # subscriber, dependent), not merely "all values differ."
    loc = EdiLocator(read_fixture("edi_270_basic.x12"))
    assert loc.occurrence_count("NM1") >= 3
    values = []
    for occurrence in range(loc.occurrence_count("NM1")):
        span = loc.locate("NM1-3", occurrence)
        assert span is not None
        values.append(loc.display_text[span[0] : span[1]])
    assert values[0] == "ACME HEALTH PLAN"
    assert values[1] == "GENERAL HOSPITAL"
    assert values[2] == "DOE"


def test_explicit_segment_repetition_disambiguates_multiple_hi_segments():
    # edi_837i_basic.x12's own claim splits diagnoses across a Principal HI
    # segment and an Other HI segment (see CLAUDE.md's own documented
    # shape) - both share intra-segment position 1, so only
    # segment_repetition tells them apart.
    loc = EdiLocator(read_fixture("edi_837i_basic.x12"))
    assert loc.occurrence_count("HI") == 2

    principal_span = loc.locate("HI[0]-1.2", 0)
    other_span = loc.locate("HI[1]-1.2", 0)
    other_second_span = loc.locate("HI[1]-2.2", 0)
    assert loc.display_text[principal_span[0] : principal_span[1]] == "3669"
    assert loc.display_text[other_span[0] : other_span[1]] == "4019"
    assert loc.display_text[other_second_span[0] : other_second_span[1]] == "79431"
    # Genuinely distinct spans, not the same HI-1.2 location collapsed.
    assert principal_span != other_span


def test_segment_repetition_ignores_the_caller_supplied_occurrence():
    # An explicit segment_repetition in the location string always wins -
    # confirmed by passing a deliberately wrong `occurrence` argument.
    loc = EdiLocator(read_fixture("edi_837i_basic.x12"))
    span_via_repetition = loc.locate("HI[1]-1.2", 0)
    span_with_wrong_occurrence_arg = loc.locate("HI[1]-1.2", 99)
    assert span_via_repetition == span_with_wrong_occurrence_arg


def test_multi_occurrence_resolution_for_repeating_sv1_line_items():
    # edi_837p_basic.x12's own claim has two service lines, each its own
    # LX/SV1 group - "SV1-1.2" is identical text for both, only the
    # occurrence-claiming (by which item[] the fact belongs to) tells them
    # apart at the highlighting.py orchestration layer; this locator's own
    # job is just to correctly resolve each occurrence independently.
    loc = EdiLocator(read_fixture("edi_837p_basic.x12"))
    assert loc.occurrence_count("SV1") == 2
    proc0 = loc.locate("SV1-1.2", 0)
    proc1 = loc.locate("SV1-1.2", 1)
    assert loc.display_text[proc0[0] : proc0[1]] != loc.display_text[proc1[0] : proc1[1]]
    assert loc.locate("SV1-1.2", 2) is None  # one past the last real occurrence


def test_out_of_range_element_returns_none_not_a_crash():
    loc = EdiLocator(read_fixture("edi_270_basic.x12"))
    assert loc.locate("NM1-999", 0) is None
    assert loc.locate("NM1-9.999", 0) is None
    assert loc.locate("ZZZ-1", 0) is None
    assert loc.locate("not-a-real-location", 0) is None


def test_root_key_returns_the_segment_id():
    loc = EdiLocator(read_fixture("edi_270_basic.x12"))
    assert loc.root_key("NM1-9") == "NM1"
    assert loc.root_key("not-a-location") is None


def test_display_text_matches_the_real_parser_delimiters():
    from app.edi.parser import read_isa_delimiters

    raw = read_fixture("edi_270_basic.x12")
    loc = EdiLocator(raw)
    assert loc.delimiters == read_isa_delimiters(raw)
    assert loc.display_text.startswith("ISA")
