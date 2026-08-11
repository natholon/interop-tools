from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA08Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _encounter(fixture_name: str):
    message = parse_message(read_fixture(fixture_name))
    bundle = AdtA08Mapper().to_bundle(message)
    return [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter"][0]


def test_update_with_discharge_time_infers_finished():
    encounter = _encounter("adt_a08_finished.hl7")
    assert encounter.status == "finished"
    assert encounter.period.end.isoformat() == "2026-08-11T15:00:00+00:00"


def test_update_without_discharge_time_infers_in_progress():
    encounter = _encounter("adt_a08_in_progress.hl7")
    assert encounter.status == "in-progress"
    assert encounter.period.end is None
