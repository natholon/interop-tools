from app.pipeline import is_x12, is_xml


def test_is_x12_true_for_literal_isa_prefix():
    assert is_x12("ISA*00*          *00*          *ZZ*SENDER*...") is True


def test_is_x12_false_for_lowercase_isa_prefix():
    # is_x12() must stay case-sensitive to match app/edi/parser.py::
    # read_isa_delimiters' own case-sensitive "ISA" check downstream - an
    # earlier case-insensitive version here misrouted ordinary non-X12 text
    # that merely started with "isa" (e.g. a name beginning "Isabella")
    # into the EDI pipeline, where it then failed the case-sensitive check
    # anyway and surfaced a confusing EDI parse error instead of falling
    # through to the default HL7v2 pipeline.
    assert is_x12("isabella is a patient name, not an X12 interchange") is False


def test_is_x12_strips_bom_before_whitespace():
    assert is_x12("﻿  ISA*00*") is True


def test_is_xml_true_for_leading_angle_bracket():
    assert is_xml("<ClinicalDocument/>") is True


def test_is_xml_false_for_x12():
    assert is_xml("ISA*00*") is False
