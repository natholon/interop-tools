from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA38Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_cancel_pre_admit_sets_entered_in_error_status():
    # Same rationale as A11/A13: this stateless converter has no persisted
    # A05 Encounter to actually cancel, so entered-in-error is the only
    # signal in the output that this represents a backed-out pre-admission.
    message = parse_message(read_fixture("adt_a38_basic.hl7"))
    bundle = AdtA38Mapper().to_bundle(message)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    assert encounter.status == "entered-in-error"
    assert encounter.class_fhir.code == "IMP"


def test_cancel_pre_admit_without_pv1_44_does_not_leak_evn2_as_period_start():
    # Regression test: same EVN-2 mislabeling hazard as A11/A13 - this
    # fixture's EVN-2 (20260812091500) is when the A38 cancel notification
    # was recorded, not any real pre-admission start, so period must end up
    # empty rather than silently populated with the cancel-event's own
    # timestamp.
    message = parse_message(read_fixture("adt_a38_basic.hl7"))
    bundle = AdtA38Mapper().to_bundle(message)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.period is None
