from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.siu import SiuS14Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_modify_produces_booked_appointment_with_trigger_marker():
    message = parse_message(read_fixture("siu_s14_basic.hl7"))
    bundle = SiuS14Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.status == "booked"
    assert appointment.start.isoformat() == "2026-09-06T13:00:00+00:00"
    assert appointment.end.isoformat() == "2026-09-06T13:30:00+00:00"
    assert appointment.extension[0].valueCode == "S14"
