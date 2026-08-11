from pathlib import Path

import pytest

from app.hl7.errors import MappingError
from app.hl7.parser import parse_message
from app.mappings.siu import SiuS12Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _entries_by_type(bundle):
    return {entry.resource.get_resource_type(): entry for entry in bundle.entry}


def test_basic_fixture_maps_every_field():
    message = parse_message(read_fixture("siu_s12_basic.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)

    assert bundle.type == "collection"
    assert len(bundle.entry) == 2

    entries = _entries_by_type(bundle)
    patient = entries["Patient"].resource
    appointment = entries["Appointment"].resource

    assert appointment.status == "booked"
    assert appointment.start.isoformat() == "2026-09-01T09:00:00+00:00"
    assert appointment.end.isoformat() == "2026-09-01T09:30:00+00:00"
    assert appointment.minutesDuration == 30
    assert appointment.comment == "Patient prefers morning slots"
    assert appointment.appointmentType.coding[0].code == "ROUTINE"
    assert appointment.reasonCode[0].coding[0].code == "CHECKUP"
    assert appointment.serviceType[0].coding[0].code == "MRI"
    assert [i.system for i in appointment.identifier] == [
        "urn:hl7-tools:placer-appointment-id",
        "urn:hl7-tools:filler-appointment-id",
    ]
    assert appointment.extension[0].url == "urn:hl7-tools:siu-trigger-event"
    assert appointment.extension[0].valueCode == "S12"

    # patient participant references the real Patient resource; the rest are display-only
    assert len(appointment.participant) == 4
    patient_participant, practitioner, location, equipment = appointment.participant
    assert patient_participant.actor.reference == f"urn:uuid:{patient.id}"
    assert patient_participant.type is None
    assert practitioner.actor.display == "Smith, John"
    assert practitioner.type[0].coding[0].code == "ATND"
    assert location.actor.display == "W456 101"
    assert location.type is None
    assert equipment.actor.display == "Portable X-Ray (Equipment)"
    assert all(p.status == "accepted" for p in appointment.participant)


def test_minimal_fixture_omits_optional_fields():
    message = parse_message(read_fixture("siu_s12_minimal.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)

    entries = _entries_by_type(bundle)
    appointment = entries["Appointment"].resource

    assert appointment.status == "booked"
    assert appointment.start is not None
    assert len(appointment.participant) == 1
    assert appointment.serviceType is None
    assert appointment.comment is None
    assert appointment.appointmentType is None


def test_sch11_fallback_used_when_tq1_absent():
    message = parse_message(read_fixture("siu_s12_sch11_fallback.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)

    entries = _entries_by_type(bundle)
    appointment = entries["Appointment"].resource

    assert appointment.status == "booked"
    assert appointment.start.isoformat() == "2026-09-03T14:00:00+00:00"
    assert appointment.end.isoformat() == "2026-09-03T15:00:00+00:00"


def test_missing_timing_raises_mapping_error():
    message = parse_message(read_fixture("siu_s12_missing_timing.hl7"))
    with pytest.raises(MappingError):
        SiuS12Mapper().to_bundle(message)


def test_partial_tq1_falls_back_to_sch11_per_field():
    # TQ1 supplies only a start time; SCH-11 has a full start+end. The missing
    # TQ1 end must fall back to SCH-11 rather than being left blank just
    # because TQ1 supplied *something*.
    message = parse_message(read_fixture("siu_s12_partial_tq1.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.start.isoformat() == "2026-09-10T08:30:00+00:00"  # from TQ1-7
    assert appointment.end.isoformat() == "2026-09-10T09:00:00+00:00"  # from SCH-11 fallback
