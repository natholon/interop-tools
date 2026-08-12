from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA13Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _encounter(fixture_name: str):
    message = parse_message(read_fixture(fixture_name))
    bundle = AdtA13Mapper().to_bundle(message)
    return next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")


def test_cancel_discharge_with_discharge_fields_populates_period_and_disposition():
    encounter = _encounter("adt_a13_with_discharge.hl7")
    assert encounter.status == "entered-in-error"
    assert encounter.period.end.isoformat() == "2026-08-12T08:30:00+00:00"
    assert encounter.hospitalization.dischargeDisposition.coding[0].code == "01"


def test_cancel_discharge_without_discharge_fields_does_not_raise():
    # Unlike A03, A13 does not require a discharge date/time - asserting
    # entered-in-error doesn't depend on having one.
    encounter = _encounter("adt_a13_no_discharge.hl7")
    assert encounter.status == "entered-in-error"
    assert encounter.hospitalization is None


def test_cancel_discharge_without_pv1_44_does_not_leak_evn2_as_period_start():
    # Regression test: build_encounter_core falls back to EVN-2 for
    # period.start when PV1-44 is absent - correct for admission-lifecycle
    # triggers (A01/A02/A04/A05/A08), where EVN-2 genuinely is the
    # encounter's own event time, but wrong for A13: this fixture's EVN-2
    # (20260812094500) is when the *cancel* notification was recorded, not
    # any real admission start - period must end up empty, not silently
    # populated with the cancel-event's own timestamp.
    encounter = _encounter("adt_a13_no_discharge.hl7")
    assert encounter.period is None
