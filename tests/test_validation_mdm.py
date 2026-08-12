from pathlib import Path

from app.hl7.pipeline import validate_hl7

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_txa_missing_is_error():
    # Reuses the existing mdm_t02_missing_txa.hl7 fixture (built for the
    # mapper's own MissingSegmentError test) - the same input is a
    # structural validation error too.
    report = validate_hl7(read_fixture("mdm_t02_missing_txa.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "mdm.txa-missing")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_origination_date_far_future_is_warning():
    # Built with a year-2099 date deliberately, not relying on any fixture
    # whose "future-ness" could drift as real time passes.
    report = validate_hl7(read_fixture("validation_mdm_origination_far_future.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "mdm.origination-date-future")
    assert finding.severity == "warning"
    assert finding.segment == "TXA"
    assert finding.field == 6


def test_availability_status_unverified_is_info():
    report = validate_hl7(read_fixture("validation_mdm_availability_unverified.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "mdm.availability-status-unverified")
    assert finding.severity == "info"


def test_availability_status_av_produces_no_finding():
    # mdm_t02_basic.hl7's TXA-19 is explicitly "AV" - the one verified mapping.
    report = validate_hl7(read_fixture("mdm_t02_basic.hl7"))
    assert "mdm.availability-status-unverified" not in [f.rule_id for f in report.findings]
