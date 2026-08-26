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


def _pid_message(fields: dict[int, str]) -> str:
    pid = {1: "1", 3: "MRN1^^^HOSP^MR", 5: "Doe^Jane", 7: "19800101", 8: "F", **fields}
    parts = ["PID"] + [""] * 30
    for index, value in pid.items():
        parts[index] = value
    return "\r".join(
        [
            "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|M1|P|2.5",
            "EVN|A01|20260101120000",
            "|".join(parts),
            "PV1|1|I|W^101^A|||||||||||||||||V1|||||||||||||||||||||||20260101120000",
        ]
    )


def _demographic_rule_ids(fields: dict[int, str]) -> set[str]:
    return {f.rule_id for f in validate_hl7(_pid_message(fields)).findings}


def test_death_date_before_birth_date_is_an_error():
    findings = validate_hl7(_pid_message({29: "19700101120000"})).findings
    finding = next(f for f in findings if f.rule_id == "generic.pid-29-before-birth")
    assert finding.severity == "error"


def test_death_date_with_a_not_deceased_indicator_is_flagged():
    # The mapper resolves the pair by precedence, so without this the
    # contradiction converts silently into a deceasedDateTime.
    assert "generic.pid-29-contradicts-pid-30" in _demographic_rule_ids({29: "20200101120000", 30: "N"})


def test_death_date_in_the_future_is_a_warning():
    assert "generic.pid-29-in-future" in _demographic_rule_ids({29: "20990101120000"})


def test_unparseable_death_date_is_a_warning_not_a_crash():
    assert "generic.pid-29-unparseable" in _demographic_rule_ids({29: "not-a-date"})


def test_birth_order_with_a_not_multiple_birth_indicator_is_flagged():
    assert "generic.pid-25-contradicts-pid-24" in _demographic_rule_ids({24: "N", 25: "2"})


def test_non_numeric_birth_order_is_a_warning():
    rule_ids = _demographic_rule_ids({25: "second"})
    assert "generic.pid-25-not-numeric" in rule_ids
    # A value that isn't a number can't contradict the indicator either.
    assert "generic.pid-25-contradicts-pid-24" not in rule_ids


def test_unrecognized_yes_no_indicators_are_warnings():
    rule_ids = _demographic_rule_ids({24: "X", 30: "Z"})
    assert "generic.pid-24-unrecognized" in rule_ids
    assert "generic.pid-30-unrecognized" in rule_ids


def test_consistent_demographics_produce_no_findings():
    rule_ids = _demographic_rule_ids({24: "Y", 25: "2", 29: "20200101120000"})
    assert not any(rule_id.startswith(("generic.pid-24", "generic.pid-25", "generic.pid-29", "generic.pid-30")) for rule_id in rule_ids)

