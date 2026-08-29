from pathlib import Path


from app.hl7.errors import MappingError
from app.hl7.parser import parse_message
from app.hl7.pipeline import validate_hl7
from app.validation import engine
from app.validation.engine import validate_message

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_message_type_and_trigger_event_are_echoed():
    report = validate_hl7(read_fixture("adt_a01_basic.hl7"))
    assert report.message_type == "ADT"
    assert report.trigger_event == "A01"


def test_is_valid_false_when_any_error_present():
    report = validate_hl7(read_fixture("validation_adt_pv1_missing.hl7"))
    assert any(f.severity == "error" for f in report.findings)
    assert report.is_valid is False


def test_is_valid_true_when_only_warnings_and_info_present():
    report = validate_hl7(read_fixture("validation_generic_pid8_unrecognized.hl7"))
    assert not any(f.severity == "error" for f in report.findings)
    assert report.is_valid is True


def test_unsupported_message_type_produces_info_not_error():
    report = validate_hl7(read_fixture("validation_unsupported_message_type.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "engine.unsupported-message-type")
    assert finding.severity == "info"
    assert "only generic checks were run" in finding.message
    # A message type this app can't convert isn't itself a defect in the
    # message - is_valid should still be true if nothing else is wrong.
    assert report.is_valid is True


def test_unsupported_trigger_with_registered_type_reports_type_specific_checks_ran():
    # Regression test: ADT^A12 has a registered TYPE (ADT) but no mapped
    # TRIGGER, so adt.py's rules already ran and found a real issue before
    # the convertibility check ever runs - the "unsupported message type"
    # finding's message used to always say "only generic checks were run"
    # regardless, which was factually wrong here. (Originally used ADT^A38
    # for this - A38 became a real mapped trigger once it shipped, so this
    # now uses A12, a still-unmapped ADT trigger, to keep exercising the
    # same registered-type/unmapped-trigger scenario.)
    report = validate_hl7(read_fixture("validation_adt_unmapped_trigger_with_type_specific_issue.hl7"))
    assert "adt.patient-class-unrecognized" in [f.rule_id for f in report.findings]
    finding = next(f for f in report.findings if f.rule_id == "engine.unsupported-message-type")
    assert "generic and type-specific checks were run" in finding.message


def test_would_not_convert_is_error_with_narrowed_exception_handling():
    # AdtA03Mapper raises MappingError (not MissingSegmentError) for a
    # missing discharge time - confirms the convertibility check's except
    # tuple actually includes MappingError raised from inside to_bundle,
    # not just from get_mapper() itself.
    report = validate_hl7(read_fixture("adt_a03_missing_discharge.hl7"))
    finding = next(f for f in report.findings if f.rule_id == "engine.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_convertibility_check_never_raises_on_unexpected_mapper_error(monkeypatch):
    # A validator must never 500 on the exact malformed input it exists to
    # flag - simulate a mapper that crashes with something other than the
    # three expected exception types and confirm it's absorbed into a
    # finding, not propagated.
    class _ExplodingMapper:
        def to_bundle(self, message):
            raise RuntimeError("boom")

    def _fake_get_mapper(message_type, trigger_event):
        return _ExplodingMapper()

    monkeypatch.setattr(engine, "get_mapper", _fake_get_mapper)

    message = parse_message(read_fixture("adt_a01_basic.hl7"))
    report = validate_message(message, "ADT", "A01")

    finding = next(f for f in report.findings if f.rule_id == "engine.convertibility-check-failed")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_get_mapper_mapping_error_still_produces_unsupported_type_info(monkeypatch):
    def _raise_mapping_error(message_type, trigger_event):
        raise MappingError("no mapper")

    monkeypatch.setattr(engine, "get_mapper", _raise_mapping_error)

    message = parse_message(read_fixture("adt_a01_basic.hl7"))
    report = validate_message(message, "ADT", "A01")

    finding = next(f for f in report.findings if f.rule_id == "engine.unsupported-message-type")
    assert finding.severity == "info"
