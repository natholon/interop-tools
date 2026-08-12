from pathlib import Path

from app.hl7.pipeline import validate_hl7

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_obr_missing_is_error():
    # Reuses the existing oru_r01_missing_obr.hl7 fixture (built for the
    # mapper's own MissingSegmentError test) - the same input is a
    # structural validation error too.
    report = validate_hl7(read_fixture("oru_r01_missing_obr.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "oru.obr-missing")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_value_outside_reference_range_is_info():
    report = validate_hl7(read_fixture("validation_oru_value_outside_range.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "oru.value-outside-reference-range")
    assert finding.severity == "info"
    assert finding.segment == "OBX"
    assert finding.field == 5


def test_abnormal_flag_contradicts_range_is_warning():
    report = validate_hl7(read_fixture("validation_oru_flag_contradicts_range.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "oru.abnormal-flag-contradicts-range")
    assert finding.severity == "warning"
    assert finding.segment == "OBX"
    assert finding.field == 8


def test_value_within_range_and_flag_normal_produces_no_findings():
    report = validate_hl7(read_fixture("oru_r01_basic.hl7"))
    rule_ids = [f.rule_id for f in report.findings]
    assert "oru.value-outside-reference-range" not in rule_ids
    assert "oru.abnormal-flag-contradicts-range" not in rule_ids


def test_reversed_reference_range_is_silently_skipped():
    # Regression test: OBX-7="100-70" (transposed low/high, e.g. a
    # sending-system field-order bug) used to parse successfully into
    # bounds=(100.0, 70.0), producing an inverted (and wrong) in/out-of-range
    # determination instead of being skipped like other malformed formats -
    # OBX-5=85 is genuinely within the intended 70-100 range.
    report = validate_hl7(read_fixture("validation_oru_reversed_reference_range.hl7"))
    rule_ids = [f.rule_id for f in report.findings]
    assert "oru.value-outside-reference-range" not in rule_ids
    assert "oru.abnormal-flag-contradicts-range" not in rule_ids
