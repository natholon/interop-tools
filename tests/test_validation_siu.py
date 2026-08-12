from pathlib import Path

from app.hl7.pipeline import validate_hl7

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_sch_missing_is_error():
    report = validate_hl7(read_fixture("validation_siu_sch_missing.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "siu.sch-missing")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_appointment_end_before_start_is_error():
    # Uses the mapper's own resolve_appointment_timing(), so this reflects
    # exactly what the real converter would resolve, not a re-derived path.
    report = validate_hl7(read_fixture("validation_siu_end_before_start.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "siu.appointment-end-before-start")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_participant_core_field_empty_is_info():
    report = validate_hl7(read_fixture("validation_siu_participant_empty.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "siu.participant-core-field-empty")
    assert finding.severity == "info"
    assert finding.segment == "AIP"
    assert finding.field == 3
