"""Unit tests for app/provenance/edi_field_names.py - the EDI sibling of
tests/test_hl7_field_names.py - the X12 source_location -> human-readable
"source field name" label behind the Data Specification page's crosswalk
table and hover tooltip column."""

from app.provenance.edi_locator import parse_edi_location
from app.provenance.edi_field_names import resolve_edi_field_label


def _label(source_location: str) -> str | None:
    parsed = parse_edi_location(source_location)
    assert parsed is not None
    return resolve_edi_field_label(parsed)


def test_element_component_label_wins_over_whole_element_name():
    # SV1-1 itself is "Composite Medical Procedure Identifier" - a
    # component asks for the more specific procedure-code sub-part.
    assert _label("SV1-1.2") == "Procedure Code"
    assert _label("SV1-1") == "Composite Medical Procedure Identifier"


def test_whole_element_label_used_when_no_component_present():
    assert _label("EQ-1") == "Service Type Code"
    assert _label("BHT-4") == "Date"
    assert _label("BHT-5") == "Time"
    assert _label("CL1-3") == "Patient Status Code"


def test_stc_composite_component_labels():
    # STC01's own two sub-components mean different things at the exact
    # same element number - both must resolve, not just one.
    assert _label("STC-1.1") == "Health Care Claim Status Category Code"
    assert _label("STC-1.2") == "Claim Status Code"


def test_hi_diagnosis_code_label_is_independent_of_element_number():
    # HI's own diagnosis-code sub-element repeats at HI01/HI02/.../HI0N -
    # one composite per diagnosis - and means the identical thing
    # regardless of which element number carried it.
    assert _label("HI-1.2") == "Diagnosis Code"
    assert _label("HI-2.2") == "Diagnosis Code"
    assert _label("HI-5.2") == "Diagnosis Code"
    # segment_repetition (institutional/dental claims' own multiple HI
    # segments) must not change the resolved label either.
    assert _label("HI[1]-1.2") == "Diagnosis Code"


def test_too_surface_label_regardless_of_which_surface_position():
    assert _label("TOO-3.1") == "Tooth Surface"
    assert _label("TOO-3.5") == "Tooth Surface"


def test_unrecognized_segment_returns_none():
    assert _label("ZZZ-1") is None


def test_unrecognized_element_on_recognized_segment_returns_none():
    assert _label("NM1-99") is None


def test_component_without_a_recognized_composite_falls_back_to_whole_element_name():
    # NM1 is never recorded with a component in this app - a location
    # string carrying one anyway should still degrade to the whole-
    # element name rather than returning nothing.
    assert _label("NM1-3.1") == "Name Last or Organization Name"


def test_in_network_indicator_and_reject_reason_labels():
    assert _label("EB-12") == "In-Network Indicator"
    assert _label("AAA-3") == "Reject Reason Code"
