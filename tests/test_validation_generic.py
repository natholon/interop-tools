from pathlib import Path

from app.hl7.pipeline import validate_hl7

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _rule_ids(fixture_name: str) -> list[str]:
    report = validate_hl7(read_fixture(fixture_name))
    return [f.rule_id for f in report.findings]


def test_clean_message_has_no_findings():
    report = validate_hl7(read_fixture("validation_generic_clean.hl7"))
    assert report.findings == []
    assert report.is_valid is True


def test_pid_missing_is_error():
    report = validate_hl7(read_fixture("validation_generic_pid_missing.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "generic.pid-missing")
    assert finding.severity == "error"
    assert finding.segment == "PID"
    assert report.is_valid is False


def test_pid_3_and_5_missing_are_warnings():
    report = validate_hl7(read_fixture("validation_generic_pid_3_5_missing.hl7"))
    findings_by_rule = {f.rule_id: f for f in report.findings}
    assert findings_by_rule["generic.pid-3-missing"].severity == "warning"
    assert findings_by_rule["generic.pid-3-missing"].field == 3
    assert findings_by_rule["generic.pid-5-missing"].severity == "warning"
    assert findings_by_rule["generic.pid-5-missing"].field == 5


def test_pid_7_unparseable_is_warning():
    assert "generic.pid-7-unparseable" in _rule_ids("validation_generic_pid7_unparseable.hl7")


def test_pid_7_in_future_is_error():
    # Built directly rather than from a fixture so this stays robust to
    # wall-clock drift over time (a hardcoded fixture date will eventually
    # stop being "in the future").
    from app.generators.base import segment
    from app.hl7.parser import parse_message

    raw = "\r".join(
        [
            "MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20260812120000||ADT^A01|VALFUT001|P|2.5",
            "EVN|A01|20260812120000",
            segment("PID", {1: "1", 3: "678901^^^HOSP^MR", 5: "Patel^Priya", 7: "20990101", 8: "F"}, 8),
            segment("PV1", {1: "1", 2: "I", 3: "W123^456^A^HOSP"}, 19),
        ]
    ) + "\r"
    parse_message(raw)  # self-check
    report = validate_hl7(raw)
    finding = next(f for f in report.findings if f.rule_id == "generic.pid-7-in-future")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_pid_7_implausibly_old_is_warning():
    report = validate_hl7(read_fixture("validation_generic_pid7_implausibly_old.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "generic.pid-7-implausibly-old")
    assert finding.severity == "warning"


def test_pid_8_unrecognized_is_warning():
    report = validate_hl7(read_fixture("validation_generic_pid8_unrecognized.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "generic.pid-8-unrecognized")
    assert finding.severity == "warning"
    assert finding.segment == "PID"
    assert finding.field == 8


def test_msh_9_incomplete_is_error():
    report = validate_hl7(read_fixture("validation_generic_msh9_incomplete.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "generic.msh-9-incomplete")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_msh_7_missing_is_warning():
    assert "generic.msh-7-missing" in _rule_ids("validation_generic_msh7_missing.hl7")


def test_msh_10_missing_is_info():
    report = validate_hl7(read_fixture("validation_generic_msh10_missing.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "generic.msh-10-missing")
    assert finding.severity == "info"


def test_msh_encoding_characters_unusual_is_warning():
    assert "generic.msh-encoding-characters-unusual" in _rule_ids("validation_generic_msh2_unusual.hl7")


def test_calendar_invalid_date_does_not_crash():
    # Regression test: PID-7="20260231" (Feb 31 - a common fat-finger error)
    # is numerically well-formed (8 digits) but not a real calendar date.
    # parse_hl7_date only checks digit shape, not calendar validity, so
    # this used to raise an uncaught ValueError deep inside
    # parse_comparable_datetime instead of producing a finding - a
    # validator must never 500 on the exact malformed input it exists to
    # flag.
    report = validate_hl7(read_fixture("validation_generic_pid7_invalid_calendar_date.hl7"))
    assert "generic.pid-7-unparseable" in [f.rule_id for f in report.findings]
