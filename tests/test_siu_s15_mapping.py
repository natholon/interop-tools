from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.siu import SiuS15Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_cancel_succeeds_without_any_timing():
    # Unlike S12/S13/S14, S15 must not require a resolvable start time.
    message = parse_message(read_fixture("siu_s15_basic.hl7"))
    bundle = SiuS15Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.status == "cancelled"
    assert appointment.start is None
    assert appointment.end is None
    assert appointment.extension[0].valueCode == "S15"
