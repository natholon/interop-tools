from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA02Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_transfer_adds_prior_location_history():
    message = parse_message(read_fixture("adt_a02_basic.hl7"))
    bundle = AdtA02Mapper().to_bundle(message)
    encounter = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter"][0]

    assert encounter.status == "in-progress"
    assert len(encounter.location) == 2
    prior, current = encounter.location
    assert prior.status == "completed"
    assert prior.location.display == "W123 456"
    assert current.status == "active"
    assert current.location.display == "W456 101"


def test_transfer_without_prior_location_keeps_single_current_entry():
    # A message with no PV1-6 should behave like A01: just the current location, no history.
    raw = read_fixture("adt_a02_basic.hl7").replace("W123^456^A^HOSP", "")
    message = parse_message(raw)
    bundle = AdtA02Mapper().to_bundle(message)
    encounter = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter"][0]

    assert len(encounter.location) == 1
    assert encounter.location[0].location.display == "W456 101"
