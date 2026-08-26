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
    # Patient + Encounter + one Location per populated PV1-3 component
    # (the PL chain the v2-to-FHIR PL[Location] map specifies) + the PV1-7
    # attending Practitioner, which the IG maps to
    # participant.individual(Practitioner).
    assert len(bundle.entry) == 7

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
    assert encounter.location[0].location.display == "HOSP, W123, 456, A"
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


def test_pv1_doctor_fields_map_to_participants_with_their_own_type_codes():
    # PV1-7/8/9/17 are each 0..-1 in the v2-to-FHIR PV1[Encounter] map,
    # with their own ParticipationType code. Only the attending doctor was
    # read, and only its first repetition.
    pv1 = ["PV1"] + [""] * 50
    pv1[1], pv1[2], pv1[3] = "1", "I", "W^101^A"
    pv1[7] = "D1^Attend^Ann~D2^Attend^Bob"
    pv1[8] = "R1^Refer^Rae"
    pv1[9] = "C1^Consult^Cal~C2^Consult^Cam"
    pv1[17] = "A1^Admit^Amy"
    pv1[19], pv1[44] = "V1", "20260101120000"
    message = "\r".join([
        r"MSH|^~\&|A|B|C|D|20260101120000||ADT^A01|M1|P|2.5",
        "EVN|A01|20260101120000",
        "PID|1||MRN1^^^H^MR||Doe^Jane||19800101|F",
        "|".join(pv1),
    ])
    bundle = AdtA01Mapper().to_bundle(parse_message(message))
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    assert [p.type[0].coding[0].code for p in encounter.participant] == [
        "ATND", "ATND", "REF", "CON", "CON", "ADM",
    ]
    # Every repetition materialises its own Practitioner, not just the first.
    assert len([e for e in bundle.entry if e.resource.get_resource_type() == "Practitioner"]) == 6
    assert [p.individual.display for p in encounter.participant] == [
        "Attend, Ann", "Attend, Bob", "Refer, Rae", "Consult, Cal", "Consult, Cam", "Admit, Amy",
    ]
