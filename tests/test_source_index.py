"""The editor's own field-hint index."""

from pathlib import Path

import pytest

from app.provenance.source_index import build_source_index

FIXTURES = Path(__file__).parent / "fixtures"


def _by_path(index):
    return {e[2]: e for e in index.entries}


@pytest.mark.parametrize(
    "fixture,expected_format,path,label,text",
    [
        ("adt_a01_basic.hl7", "HL7v2", "MSH-10", "Message Control ID", "MSG00001"),
        ("adt_a01_basic.hl7", "HL7v2", "PID-5.1", "Family Name", "Doe"),
        ("adt_a01_basic.hl7", "HL7v2", "PV1-2", "Patient Class", "I"),
        ("edi_270_basic.x12", "EDI", "BHT-3", "Reference Identification", "10001234"),
        ("ccd_basic.xml", "CDA", "ClinicalDocument/id/@extension", "ID", "TT988"),
    ],
)
def test_a_known_field_resolves_to_its_span_and_name(fixture, expected_format, path, label, text):
    index = build_source_index((FIXTURES / fixture).read_text(encoding="utf-8"))
    assert index.source_format == expected_format
    start, end, _, resolved_label = _by_path(index)[path]
    # The span has to cover the value itself, or a hover lands elsewhere.
    assert index.display_text[start:end] == text
    assert resolved_label == label


def test_half_typed_text_yields_an_empty_index_rather_than_raising():
    # Editing is exactly when a message is incomplete, so this must be an
    # ordinary answer rather than an error.
    for text in ("MSH|^~", "<ClinicalDocument", "ISA*00*", ""):
        index = build_source_index(text)
        assert isinstance(index.entries, list)


def test_offsets_are_relative_to_display_text_not_the_raw_input():
    # HL7v2 rewrites \r to \n, so an index built against the raw upload
    # would be right only for a message that happened to use \n already.
    raw = "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|CTRL1|P|2.5\rPID|1||X||Doe^Jane\r"
    index = build_source_index(raw)
    assert "\r" not in index.display_text
    start, end, _, _ = _by_path(index)["MSH-10"]
    assert index.display_text[start:end] == "CTRL1"


def test_an_unlabelled_location_reports_no_label_rather_than_a_guess():
    index = build_source_index((FIXTURES / "adt_a01_basic.hl7").read_text(encoding="utf-8"))
    # MSH-9 is indexed (it has a span) but the HL7v2 field-name table is
    # scoped to what this app's mappers record, so it has no name here.
    assert _by_path(index)["MSH-9"][3] is None
