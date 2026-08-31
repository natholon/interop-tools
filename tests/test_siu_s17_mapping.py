from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.siu import SiuS17Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_delete_sets_entered_in_error_status_without_requiring_timing():
    # S17 removes an appointment entered in error - distinct from S15's
    # cancellation of a valid request. The mapper still does not *require*
    # timing (see siu_s17_missing_timing.hl7 and the test below), but the
    # representative fixture carries it: R4's app-3 excuses a missing
    # start/end only for proposed, cancelled and waitlist, so an untimed
    # S17 converts to an invalid Appointment.
    message = parse_message(read_fixture("siu_s17_basic.hl7"))
    bundle = SiuS17Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.status == "entered-in-error"
    assert appointment.start is not None
    assert appointment.end is not None
    assert appointment.extension[0].valueCode == "S17"
