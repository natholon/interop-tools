from pathlib import Path

from app.hl7.pipeline import validate_hl7

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_pv1_missing_is_error():
    report = validate_hl7(read_fixture("validation_adt_pv1_missing.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "adt.pv1-missing")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_discharge_before_admit_is_error():
    report = validate_hl7(read_fixture("validation_adt_discharge_before_admit.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "adt.discharge-before-admit")
    assert finding.severity == "error"
    assert finding.segment == "PV1"
    assert finding.field == 45
    assert report.is_valid is False


def test_admit_in_future_is_warning():
    report = validate_hl7(read_fixture("validation_adt_admit_in_future.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "adt.admit-in-future")
    assert finding.severity == "warning"


def test_admit_before_birth_is_error():
    report = validate_hl7(read_fixture("validation_adt_admit_before_birth.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "adt.admit-before-birth")
    assert finding.severity == "error"


def test_patient_class_unrecognized_is_warning():
    report = validate_hl7(read_fixture("validation_adt_patient_class_unrecognized.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "adt.patient-class-unrecognized")
    assert finding.severity == "warning"
    assert finding.segment == "PV1"
    assert finding.field == 2


def test_a02_missing_prior_location_is_info():
    report = validate_hl7(read_fixture("validation_adt_a02_missing_prior_location.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "adt.a02-missing-prior-location")
    assert finding.severity == "info"


def test_a01_missing_prior_location_does_not_trigger_a02_rule():
    # The A02-only rule must not fire for other triggers just because PV1-6
    # happens to be empty - PV1-6 is meaningless outside a transfer.
    report = validate_hl7(read_fixture("validation_generic_clean.hl7"))
    assert "adt.a02-missing-prior-location" not in [f.rule_id for f in report.findings]


def test_a02_lowercase_trigger_still_triggers_the_rule():
    # Regression test: MSH-9's trigger component reaches app/validation/
    # exactly as the sender wrote it - a02.py's rule used to compare it
    # case-sensitively against the literal "A02", so a lowercase trigger
    # (valid HL7, just unusual) silently skipped the check entirely.
    report = validate_hl7(read_fixture("validation_adt_a02_lowercase_trigger.hl7"))
    assert "adt.a02-missing-prior-location" in [f.rule_id for f in report.findings]


def test_same_day_date_only_discharge_does_not_falsely_error():
    # Regression test: PV1-45 date-only ("20240115") used to compare as UTC
    # midnight against a same-day timestamped PV1-44 ("...083000"),
    # producing a false "discharge before admit" - a date-only value on the
    # same calendar day as a timestamped one is indeterminate, not
    # unambiguously earlier.
    report = validate_hl7(read_fixture("validation_adt_same_day_date_only_discharge.hl7"))
    assert "adt.discharge-before-admit" not in [f.rule_id for f in report.findings]


def test_same_day_date_only_birth_does_not_falsely_error():
    report = validate_hl7(read_fixture("validation_adt_same_day_date_only_birth.hl7"))
    assert "adt.admit-before-birth" not in [f.rule_id for f in report.findings]


def test_genuine_discharge_before_admit_is_still_caught():
    # The date-precision fix must not swallow real violations - a discharge
    # a full calendar day before admit is unambiguous regardless of
    # whether either value has a time component.
    from app.generators.base import segment
    from app.hl7.parser import parse_message

    raw = (
        "\r".join(
            [
                "MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20260812120000||ADT^A03|VALREV007|P|2.5",
                "EVN|A03|20260812120000",
                segment("PID", {1: "1", 3: "678901^^^HOSP^MR", 5: "Patel^Priya", 7: "19880715", 8: "F"}, 8),
                segment("PV1", {1: "1", 2: "I", 3: "W123^456^A^HOSP", 44: "20240116083000", 45: "20240115"}, 45),
            ]
        )
        + "\r"
    )
    parse_message(raw)  # self-check
    report = validate_hl7(raw)
    finding = next(f for f in report.findings if f.rule_id == "adt.discharge-before-admit")
    assert finding.severity == "error"
