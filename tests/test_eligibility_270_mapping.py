from pathlib import Path

import pytest

from app.edi.eligibility_270 import Edi270Builder
from app.edi.parser import first_transaction_set, parse_interchange
from app.hl7.errors import MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi270Builder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_payer_provider_subscriber_and_dependent():
    bundle = _build_bundle("edi_270_basic.x12")
    by_type = _entries_by_type(bundle)

    assert bundle.type == "collection"
    assert bundle.identifier.value == "10001234"
    assert bundle.timestamp.isoformat() == "2026-08-12T12:00:00+00:00"

    payer = by_type["Organization"][0]
    assert payer.name == "ACME HEALTH PLAN"
    assert payer.identifier[0].value == "PAYERID001"

    # Dependent present -> patient = dependent, not the subscriber.
    patients = {p.name[0].family + "/" + p.name[0].given[0]: p for p in by_type["Patient"]}
    assert set(patients.keys()) == {"DOE/JANE", "DOE/JIMMY"}
    dependent = patients["DOE/JIMMY"]
    assert dependent.gender == "male"
    assert dependent.birthDate.isoformat() == "2015-06-15"

    request = by_type["CoverageEligibilityRequest"][0]
    assert request.status == "active"
    assert request.purpose == ["benefits"]
    assert request.patient.reference == f"urn:uuid:{dependent.id}"
    assert request.insurer.reference == f"urn:uuid:{payer.id}"
    assert request.servicedDate.isoformat() == "2026-08-12"
    assert len(request.item) == 1
    assert request.item[0].category.coding[0].code == "30"

    coverage = by_type["Coverage"][0]
    assert coverage.status == "active"
    assert coverage.beneficiary.reference == f"urn:uuid:{dependent.id}"
    subscriber = patients["DOE/JANE"]
    assert coverage.subscriber.reference == f"urn:uuid:{subscriber.id}"


def test_no_dependent_fixture_patient_is_the_subscriber():
    bundle = _build_bundle("edi_270_no_dependent.x12")
    by_type = _entries_by_type(bundle)

    assert len(by_type["Patient"]) == 1
    subscriber = by_type["Patient"][0]
    assert subscriber.name[0].family == "SMITH"

    request = by_type["CoverageEligibilityRequest"][0]
    assert request.patient.reference == f"urn:uuid:{subscriber.id}"

    coverage = by_type["Coverage"][0]
    assert coverage.beneficiary.reference == coverage.subscriber.reference == f"urn:uuid:{subscriber.id}"


def test_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_270_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError):
        Edi270Builder().build_bundle(transaction_set, interchange.delimiters)


def test_person_type_provider_materializes_a_practitioner():
    # NM102="1" (person) on the 2000B loop - a provider that's an
    # individual practitioner rather than an organization, the other
    # legal shape build_bundle() must handle (see is_person_entity()).
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*270*0001~"
        "BHT*0022*13*10002222*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*0~"
        "NM1*1P*1*WELBY*MARCUS****XX*1112223334~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "SE*10*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi270Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    assert "Practitioner" in by_type
    assert "Organization" not in by_type or len(by_type["Organization"]) == 1  # only the payer
    practitioner = by_type["Practitioner"][0]
    assert practitioner.name[0].family == "WELBY"


def test_lowercase_nm108_qualifier_still_resolves_canonical_system():
    # A code review caught that _build_nm1_identifier read NM108 without
    # normalizing case, unlike DMG03's gender code three lines below in the
    # same file - a lowercase "xx" silently fell through to the disclosed
    # per-qualifier fallback system instead of the canonical NPI system.
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*270*0001~"
        "BHT*0022*13*10007777*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*0~"
        "NM1*1P*2*GENERAL HOSPITAL*****xx*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "SE*10*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi270Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    provider = next(o for o in by_type["Organization"] if o.name == "GENERAL HOSPITAL")
    assert provider.identifier[0].system == "http://hl7.org/fhir/sid/us-npi"


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_270_basic.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
