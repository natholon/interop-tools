from pathlib import Path

from fhir.resources.R4B.bundle import Bundle

from app.hl7.parser import parse_message
from app.hl7.pipeline import convert_hl7_to_bundle
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


def _segment(segment_id: str, fields: dict[int, str], width: int) -> str:
    """Build a segment from {field_number: value} rather than hand-counting
    pipes - the same discipline every fixture in this repo is built with."""
    parts = [segment_id] + [""] * width
    for index, value in fields.items():
        parts[index] = value
    return "|".join(parts)


def _message(pid_fields: dict[int, str], pv1_fields: dict[int, str]) -> str:
    pid = {1: "1", 3: "MRN1^^^HOSP^MR", 5: "Doe^Jane", 7: "19800101", 8: "F", **pid_fields}
    pv1 = {1: "1", 2: "I", 3: "W^101^A", 19: "V1", 44: "20260101120000", **pv1_fields}
    return "\r".join(
        [
            "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|M1|P|2.5",
            "EVN|A01|20260101120000",
            _segment("PID", pid, 30),
            _segment("PV1", pv1, 45),
        ]
    )


def _encounter(raw: str):
    return next(e.resource for e in convert_hl7_to_bundle(raw).entry if e.resource.get_resource_type() == "Encounter")


def _patient(raw: str):
    return next(e.resource for e in convert_hl7_to_bundle(raw).entry if e.resource.get_resource_type() == "Patient")


def test_pv1_hospitalization_cluster_maps_every_field():
    # PV1-4/5/10/13/14/15/16/38 all have a real target in the v2-to-FHIR
    # PV1[Encounter] map; every one of them was previously dropped.
    encounter = _encounter(
        _message(
            {},
            {
                4: "E^Emergency^HL70007",
                5: "PRE123^^^HOSP^PI",
                10: "SUR^Surgery^L",
                13: "R^Re-admission^HL70092",
                14: "7^Emergency room^HL70023",
                15: "A0^No functional limitations^HL70009",
                16: "Y^VIP^HL70099",
                38: "LF^Low fat^L",
            },
        )
    )
    assert encounter.type[0].coding[0].code == "E"
    assert encounter.serviceType.coding[0].code == "SUR"
    hospitalization = encounter.hospitalization
    assert hospitalization.preAdmissionIdentifier.value == "PRE123"
    # PV1-5 is CX, so it gets the same CX.4/CX.5 treatment PID-3 does.
    assert hospitalization.preAdmissionIdentifier.system == "HOSP"
    assert hospitalization.preAdmissionIdentifier.type.coding[0].code == "PI"
    assert hospitalization.reAdmission.coding[0].code == "R"
    assert hospitalization.admitSource.coding[0].code == "7"
    assert [c.coding[0].code for c in hospitalization.specialArrangement] == ["A0"]
    assert [c.coding[0].code for c in hospitalization.specialCourtesy] == ["Y"]
    assert [c.coding[0].code for c in hospitalization.dietPreference] == ["LF"]


def test_encounter_without_any_hospitalization_field_gets_no_empty_hospitalization():
    # An empty .hospitalization would be a resource claiming to say
    # something about the stay while saying nothing.
    assert _encounter(_message({}, {})).hospitalization is None


def test_pid_marital_status_and_language_map():
    patient = _patient(_message({15: "en^English^HL70296", 16: "M^Married^HL70002"}, {}))
    assert patient.maritalStatus.coding[0].code == "M"
    assert patient.communication[0].language.coding[0].code == "en"


def test_pid_25_birth_order_supersedes_pid_24_indicator():
    # multipleBirth[x] is a choice and the IG states the precedence itself:
    # PID-24 maps "IF PID-25 NOT VALUED".
    patient = _patient(_message({24: "Y", 25: "3"}, {}))
    assert patient.multipleBirthInteger == 3
    assert patient.multipleBirthBoolean is None


def test_pid_24_indicator_used_when_no_birth_order():
    patient = _patient(_message({24: "Y"}, {}))
    assert patient.multipleBirthBoolean is True
    assert patient.multipleBirthInteger is None


def test_pid_29_death_date_supersedes_pid_30_indicator():
    patient = _patient(_message({29: "20200501103000", 30: "Y"}, {}))
    assert str(patient.deceasedDateTime).startswith("2020-05-01")
    assert patient.deceasedBoolean is None


def test_pid_30_indicator_used_when_no_death_date():
    patient = _patient(_message({30: "N"}, {}))
    assert patient.deceasedBoolean is False
    assert patient.deceasedDateTime is None


def test_unrecognized_yes_no_value_leaves_the_field_unset():
    # "U" is not a value the ID table defines, and guessing a boolean from
    # it would state something the message never said.
    patient = _patient(_message({24: "U", 30: "U"}, {}))
    assert patient.multipleBirthBoolean is None
    assert patient.deceasedBoolean is None


def test_pid_3_and_pid_13_read_every_repetition():
    # Both are 0..-1; reading only the first silently dropped the rest.
    patient = _patient(_message({3: "MRN1^^^HOSP^MR~ALT9^^^OTHER^PI", 13: "555-0100~555-0199"}, {}))
    assert [i.value for i in patient.identifier] == ["MRN1", "ALT9"]
    assert [i.type.coding[0].code for i in patient.identifier] == ["MR", "PI"]
    assert [t.value for t in patient.telecom] == ["555-0100", "555-0199"]

