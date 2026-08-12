from pathlib import Path

import pytest

from app.hl7.errors import MappingError
from app.hl7.parser import parse_message
from app.mappings.siu import SiuS26Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_no_show_sets_noshow_status():
    message = parse_message(read_fixture("siu_s26_basic.hl7"))
    bundle = SiuS26Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.status == "noshow"
    assert appointment.start.isoformat() == "2026-09-01T09:00:00+00:00"


def test_no_show_without_timing_raises_mapping_error():
    # S26 refers to a specific already-scheduled appointment, same as a
    # booking - a resolvable start time is required, same as S12/S13/S14.
    message = parse_message(read_fixture("siu_s26_missing_timing.hl7"))
    with pytest.raises(MappingError):
        SiuS26Mapper().to_bundle(message)
