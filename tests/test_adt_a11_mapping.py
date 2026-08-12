from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA11Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_cancel_admit_sets_entered_in_error_status():
    # Stateless converter: there's no persisted A01 Encounter to actually
    # cancel, so entered-in-error is the only signal in the output that this
    # Encounter represents a backed-out admission rather than a real one.
    message = parse_message(read_fixture("adt_a11_basic.hl7"))
    bundle = AdtA11Mapper().to_bundle(message)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    assert encounter.status == "entered-in-error"
    assert encounter.class_fhir.code == "IMP"


def test_cancel_admit_without_pv1_44_does_not_leak_evn2_as_period_start():
    # Regression test: same EVN-2 mislabeling hazard as A13 (see
    # test_adt_a13_mapping.py) - this fixture's EVN-2 (20260812091500) is
    # when the A11 cancel notification was recorded, not any real admission
    # start, so period must end up empty rather than silently populated
    # with the cancel-event's own timestamp.
    message = parse_message(read_fixture("adt_a11_basic.hl7"))
    bundle = AdtA11Mapper().to_bundle(message)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.period is None
