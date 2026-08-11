from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.siu import SiuS13Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_reschedule_produces_booked_appointment_with_trigger_marker():
    message = parse_message(read_fixture("siu_s13_basic.hl7"))
    bundle = SiuS13Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.status == "booked"
    assert appointment.start.isoformat() == "2026-09-05T11:00:00+00:00"
    assert appointment.end.isoformat() == "2026-09-05T11:45:00+00:00"
    assert appointment.minutesDuration == 45
    assert appointment.extension[0].valueCode == "S13"


def test_reschedule_prefers_tq1_duration_over_stale_sch9():
    # SCH-9/10 carry a stale 30-minute duration; TQ1 carries the actual new
    # 60-minute slot (consistent with TQ1-7/8). minutesDuration must match
    # the TQ1-derived start/end, not silently contradict them.
    message = parse_message(read_fixture("siu_s13_stale_duration.hl7"))
    bundle = SiuS13Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.start.isoformat() == "2026-09-11T10:00:00+00:00"
    assert appointment.end.isoformat() == "2026-09-11T11:00:00+00:00"
    assert appointment.minutesDuration == 60
