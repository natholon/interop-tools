from pathlib import Path

from fhir.resources.R4B.bundle import Bundle

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA01Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _entries_by_type(bundle):
    return {entry.resource.get_resource_type(): entry for entry in bundle.entry}


def test_basic_fixture_maps_every_field():
    message = parse_message(read_fixture("adt_a01_basic.hl7"))
    bundle = AdtA01Mapper().to_bundle(message)

    assert bundle.type == "collection"
    assert len(bundle.entry) == 2

    entries = _entries_by_type(bundle)
    patient = entries["Patient"].resource
    encounter = entries["Encounter"].resource

    assert patient.identifier[0].value == "123456"
    assert patient.name[0].family == "Doe"
    assert patient.name[0].given == ["Jane", "Q"]
    assert patient.birthDate.isoformat() == "1962-03-05"
    assert patient.gender == "female"
    assert patient.address[0].city == "Springfield"
    assert patient.address[0].state == "IL"
    assert patient.telecom[0].value == "(555)555-1234"

    assert encounter.status == "in-progress"
    assert encounter.class_fhir.code == "IMP"
    assert encounter.identifier[0].value == "V0001"
    assert encounter.location[0].location.display == "W123 456"
    assert encounter.participant[0].individual.display == "Smith, John"
    assert encounter.period.start.isoformat() == "2026-08-11T12:00:00+00:00"

    assert encounter.subject.reference == f"urn:uuid:{patient.id}"
    assert entries["Patient"].fullUrl == f"urn:uuid:{patient.id}"
    assert entries["Encounter"].fullUrl == f"urn:uuid:{encounter.id}"


def test_minimal_fixture_omits_optional_fields():
    message = parse_message(read_fixture("adt_a01_minimal.hl7"))
    bundle = AdtA01Mapper().to_bundle(message)

    entries = _entries_by_type(bundle)
    patient = entries["Patient"].resource
    encounter = entries["Encounter"].resource

    assert patient.name[0].family == "Smith"
    assert patient.name[0].given == ["Alice"]
    assert patient.birthDate is None
    assert patient.address is None
    assert patient.telecom is None

    assert encounter.class_fhir.code == "AMB"
    assert encounter.period is None
    assert encounter.location is None


def test_bundle_round_trips_through_json():
    message = parse_message(read_fixture("adt_a01_basic.hl7"))
    bundle = AdtA01Mapper().to_bundle(message)
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
