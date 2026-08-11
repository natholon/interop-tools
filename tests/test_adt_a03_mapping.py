from pathlib import Path

import pytest

from app.hl7.errors import MappingError
from app.hl7.parser import parse_message
from app.mappings.adt import AdtA03Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_discharge_sets_finished_status_and_period_end():
    message = parse_message(read_fixture("adt_a03_basic.hl7"))
    bundle = AdtA03Mapper().to_bundle(message)
    encounter = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter"][0]

    assert encounter.status == "finished"
    assert encounter.period.start.isoformat() == "2026-08-10T09:00:00+00:00"
    assert encounter.period.end.isoformat() == "2026-08-11T10:00:00+00:00"
    assert encounter.hospitalization.dischargeDisposition.coding[0].code == "01"


def test_discharge_without_discharge_time_raises_mapping_error():
    message = parse_message(read_fixture("adt_a03_missing_discharge.hl7"))
    with pytest.raises(MappingError):
        AdtA03Mapper().to_bundle(message)
