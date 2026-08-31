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
    # Patient + Appointment + materialized Practitioner (AIP) + Location (AIL) + Device (AIG),
    # plus the extra Locations in AIL-3's own PL chain and one Practitioner
    # each for SCH-12/-16/-20, which the IG maps to participant[1..3].
    assert len(bundle.entry) == 11

    entries = _entries_by_type(bundle)
    patient = entries["Patient"].resource
    appointment = entries["Appointment"].resource
    practitioner = entries["Practitioner"].resource
    location = entries["Location"].resource
    device = entries["Device"].resource

    assert appointment.status == "booked"
    assert appointment.start.isoformat() == "2026-09-01T09:00:00+00:00"
    assert appointment.end.isoformat() == "2026-09-01T09:30:00+00:00"
    assert appointment.minutesDuration == 30
    assert appointment.comment == "Patient prefers morning slots"
    assert appointment.appointmentType.coding[0].code == "ROUTINE"
    assert appointment.reasonCode[0].coding[0].code == "CHECKUP"
    assert appointment.serviceType[0].coding[0].code == "MRI"
    assert [i.system for i in appointment.identifier] == [
        "urn:interop-tools:placer-appointment-id",
        "urn:interop-tools:filler-appointment-id",
    ]
    assert appointment.extension[0].url == "urn:interop-tools:siu-trigger-event"
    assert appointment.extension[0].valueCode == "S12"

    # The materialized Practitioner/Location/Device resources themselves
    assert practitioner.name[0].family == "Smith"
    assert practitioner.name[0].given == ["John"]
    # A PL-derived Location carries its component value as .identifier,
    # not .name - .name is only set for the AIG (CWE-shaped) case.
    assert location.identifier[0].value == "B"
    assert location.physicalType.coding[0].code == "bd"
    assert device.deviceName[0].name == "Portable X-Ray"
    assert device.identifier[0].value == "EQ001"
    assert device.type.coding[0].code == "EQUIPMENT"  # AIG-4 category, preserved not just used to pick the branch

    # patient participant references the real Patient resource with a display;
    # AIP/AIL/AIG participants reference the real materialized resources above
    # (not display-only text) but still carry a human-readable `display` too,
    # per FHIR's own guidance that Reference.display SHOULD be set even when
    # `reference` is present.
    # The patient, SCH-12/-16/-20's three contact people, then one each for
    # AIP/AIL/AIG - the order the IG's own participant[1..3] rows imply.
    assert len(appointment.participant) == 7
    (
        patient_participant,
        placer_participant,
        filler_participant,
        enterer_participant,
        practitioner_participant,
        location_participant,
        device_participant,
    ) = appointment.participant
    assert patient_participant.actor.reference == f"urn:uuid:{patient.id}"
    assert patient_participant.type is None
    # SCH-12 and SCH-16 get an actor but no type: the IG's own cells there
    # read "#placer contact#"/"#filler contact#", its placeholder notation.
    assert placer_participant.actor.display == "Placer, Contact"
    assert placer_participant.type is None
    assert filler_participant.actor.display == "Filler, Contact"
    assert filler_participant.type is None
    # SCH-20 is the one the IG gives a real code and system for.
    assert enterer_participant.actor.display == "Enterer, Contact"
    assert enterer_participant.type[0].coding[0].code == "enterer"
    assert (
        enterer_participant.type[0].coding[0].system
        == "http://terminology.hl7.org/CodeSystem/provenance-participant-type"
    )
    assert practitioner_participant.actor.reference == f"urn:uuid:{practitioner.id}"
    assert practitioner_participant.actor.display == "Smith, John"
    # AIP-4 is the source's own coded role, which the IG maps to
    # participant.type - preferred over the fixed ATND used before it.
    assert practitioner_participant.type[0].coding[0].code == "PROVIDER"
    assert location_participant.actor.reference == f"urn:uuid:{location.id}"
    assert location_participant.actor.display == "HOSP, W456, 101, B"
    assert location_participant.type is None
    assert device_participant.actor.reference == f"urn:uuid:{device.id}"
    assert device_participant.actor.display == "Portable X-Ray"
    assert device_participant.type is None
    assert all(p.status == "accepted" for p in appointment.participant)


def test_minimal_fixture_omits_optional_fields():
    message = parse_message(read_fixture("siu_s12_minimal.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)

    entries = _entries_by_type(bundle)
    appointment = entries["Appointment"].resource

    assert appointment.status == "booked"
    assert appointment.start is not None
    # The patient, plus SCH-12/-16/-20's contact people - all three are
    # 1..1 in the standard, so even a minimal conformant SCH carries them.
    assert len(appointment.participant) == 4
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


def test_aig_with_location_type_code_materializes_as_location_not_device():
    # AIG has no single fixed FHIR target - it depends on AIG-4 (Resource Type).
    # A location-typed AIG-4 must produce a Location, not the default Device.
    message = parse_message(read_fixture("siu_s12_aig_location.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)
    entries = _entries_by_type(bundle)

    assert "Device" not in entries
    location = entries["Location"].resource
    assert location.name == "Operating Room 3"
    assert location.identifier[0].value == "OR3"
    appointment = entries["Appointment"].resource
    aig_participant = appointment.participant[-1]
    assert aig_participant.actor.reference == f"urn:uuid:{location.id}"
    assert aig_participant.actor.display == "Operating Room 3"


def test_aip_with_id_only_materializes_with_id_as_display():
    # An XCN field with only an id component (no family/given) must still
    # produce a valid, non-empty Reference.display - person_display falls
    # back to the id when there's no name to show. Regression test for a bug
    # caught by code review: build_practitioner_from_xcn materializes a
    # Practitioner whenever id/family/given is present, but person_display
    # used to only look at family/given, producing Reference(display="")
    # (which FHIR rejects) whenever a segment supplied an id with no name.
    message = parse_message(read_fixture("siu_s12_aip_id_only.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)
    entries = _entries_by_type(bundle)

    practitioner = entries["Practitioner"].resource
    assert practitioner.identifier[0].value == "5678"
    assert practitioner.name is None

    appointment = entries["Appointment"].resource
    aip_participant = appointment.participant[-1]
    assert aip_participant.actor.reference == f"urn:uuid:{practitioner.id}"
    assert aip_participant.actor.display == "5678"


def test_multiple_nte_segments_are_concatenated_into_comment():
    # Regression test: Appointment.comment used to take only the *first* NTE
    # found anywhere in the message (nte_segments[0]), regardless of which
    # segment it actually trailed. This fixture has one NTE right after SCH
    # (appointment-level) and one right after an AIP occurrence
    # (personnel-level) - both should now be folded into one comment, since
    # there's no separate field for per-participant notes.
    message = parse_message(read_fixture("siu_s12_multiple_nte.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.comment == "Patient prefers morning slots\nDr. Smith requested a 15 min buffer before this slot"


def test_partial_tq1_falls_back_to_sch11_per_field():
    # TQ1 supplies only a start time; SCH-11 has a full start+end. The missing
    # TQ1 end must fall back to SCH-11 rather than being left blank just
    # because TQ1 supplied *something*.
    message = parse_message(read_fixture("siu_s12_partial_tq1.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)
    appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.start.isoformat() == "2026-09-10T08:30:00+00:00"  # from TQ1-7
    assert appointment.end.isoformat() == "2026-09-10T09:00:00+00:00"  # from SCH-11 fallback


def test_aig_identifier_system_comes_from_the_named_coding_system():
    # v2-to-FHIR maps AIG-3 through CWE[Identifier], where CWE.3 (Name of
    # Coding System) is Identifier.system. We hard-coded a local
    # placeholder and ignored CWE.3, so a sender naming its own coding
    # system had that name silently dropped.
    message = parse_message(read_fixture("siu_s12_aig_location.hl7"))
    bundle = SiuS12Mapper().to_bundle(message)
    location = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "Location"
    )
    assert location.identifier[0].system == "LOCAL"
    assert location.identifier[0].value == "OR3"
