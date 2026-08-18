"""Unit tests for app/provenance/hl7_field_names.py - the HL7v2 source_
location -> human-readable "source field name" label behind the Data
Specification page's crosswalk table and hover tooltip column."""

from app.provenance.hl7_locator import parse_hl7_location
from app.provenance.hl7_field_names import resolve_hl7_field_label


def _label(source_location: str) -> str | None:
    parsed = parse_hl7_location(source_location)
    assert parsed is not None
    return resolve_hl7_field_label(parsed)


def test_component_level_label_wins_over_whole_field_name():
    # PID-3 itself is "Patient Identifier List" - a component asks for the
    # more specific CX sub-part name instead.
    assert _label("PID-3[0].1") == "ID"
    assert _label("PID-3[0].4") == "Assigning Authority"


def test_whole_field_label_used_when_no_component_present():
    assert _label("PID-5[0]") == "Patient Name"
    assert _label("PID-7") == "Date/Time of Birth"
    assert _label("PV1-45") == "Discharge Date/Time"


def test_xpn_and_xad_component_labels():
    assert _label("PID-5[0].1") == "Family Name"
    assert _label("PID-5[0].2") == "Given Name"
    assert _label("PID-11[0].3") == "City"
    assert _label("PID-11[0].4") == "State or Province"


def test_xcn_component_labels_for_practitioner_fields():
    assert _label("AIP-3.1") == "ID"
    assert _label("AIP-3.2") == "Family Name"
    assert _label("OBX-16.2") == "Family Name"
    assert _label("TXA-9.2") == "Family Name"


def test_unrecognized_segment_returns_none():
    assert _label("ZZZ-1") is None


def test_unrecognized_field_on_recognized_segment_returns_none():
    assert _label("PID-99") is None


def test_component_without_a_recognized_datatype_falls_back_to_whole_field_name():
    # PV1-44 (Admit Date/Time) is never recorded with a component in this
    # app, but a location string carrying one anyway should still degrade
    # to the whole-field name rather than returning nothing.
    assert _label("PV1-44.1") == "Admit Date/Time"


def test_unrecognized_component_number_falls_back_to_whole_field_name():
    # PID-3 is CX, which only defines components up to 6 here - a
    # (hypothetical) 9th component should fall back to the field name.
    assert _label("PID-3[0].9") == "Patient Identifier List"
