from pathlib import Path

import pytest

from app.edi.parser import first_transaction_set, parse_interchange
from app.edi.prior_auth import Edi278Builder
from app.hl7.errors import MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi278Builder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_request_basic_fixture_maps_claim_with_diagnoses_and_no_response():
    bundle = _build_bundle("edi_278_request_basic.x12")
    by_type = _entries_by_type(bundle)

    assert bundle.identifier.value == "B56789"
    assert "ClaimResponse" not in by_type  # BHT02="13" is a request - no HCR, no response

    payer = by_type["Organization"][0]
    assert payer.name == "MARYLAND CAPITAL INSURANCE COMPANY"
    requester = by_type["Practitioner"][0]
    assert requester.name[0].family == "WATSON"
    patient = by_type["Patient"][0]
    assert patient.name[0].family == "SMITH"

    claim = by_type["Claim"][0]
    assert claim.use == "preauthorization"
    assert claim.status == "active"
    assert claim.patient.reference == f"urn:uuid:{patient.id}"
    assert claim.provider.reference == f"urn:uuid:{requester.id}"
    assert claim.insurer.reference == f"urn:uuid:{payer.id}"
    assert len(claim.diagnosis) == 2
    dx_codes = {d.diagnosisCodeableConcept.coding[0].code for d in claim.diagnosis}
    assert dx_codes == {"1831", "2630"}
    # The fixture's HI segment uses the "BF" qualifier, which is ICD-9-CM
    # (not ICD-10-CM - "ABF" is the ICD-10-CM one) per X12 code list 1270,
    # verified directly. An earlier version of HI_QUALIFIER_SYSTEM mapped
    # "BF" itself to ICD-10-CM, which this assertion originally (wrongly)
    # matched - see app/edi/common.py::HI_QUALIFIER_SYSTEM's own comment.
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].system == "http://hl7.org/fhir/sid/icd-9-cm"

    # A code review caught that claim.insurance[].coverage originally
    # pointed directly at the payer Organization instead of a real
    # Coverage resource (ClaimInsurance.coverage's own field description:
    # "Reference to the insurance card level information contained in the
    # Coverage resource") - assert the real resource exists and is wired
    # up correctly, not just that *something* is referenced.
    coverage = by_type["Coverage"][0]
    assert claim.insurance[0].coverage.reference == f"urn:uuid:{coverage.id}"
    assert coverage.beneficiary.reference == f"urn:uuid:{patient.id}"
    assert coverage.subscriber.reference == f"urn:uuid:{patient.id}"  # no dependent in this fixture
    assert coverage.payor[0].reference == f"urn:uuid:{payer.id}"
    # UM03 (service type) is empty in this fixture (mirrors the real
    # X12.org example it's built from) - productOrService falls back to
    # text-only; the response_certified fixture below exercises the coded
    # path, where UM03 does resolve.
    assert claim.item[0].productOrService.coding is None
    assert claim.item[0].productOrService.text == "Unspecified service"


def test_request_with_dependent_fixture_patient_is_the_dependent():
    bundle = _build_bundle("edi_278_request_with_dependent.x12")
    by_type = _entries_by_type(bundle)

    patients = {p.name[0].given[0]: p for p in by_type["Patient"]}
    assert set(patients.keys()) == {"JANE", "JIMMY"}
    dependent = patients["JIMMY"]
    assert dependent.gender == "male"
    assert dependent.birthDate.isoformat() == "2015-06-15"

    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{dependent.id}"

    # coverage.beneficiary must be the dependent (the patient), but
    # coverage.subscriber must stay the subscriber themselves - the two
    # are genuinely different people here, unlike the no-dependent fixture.
    coverage = by_type["Coverage"][0]
    subscriber = patients["JANE"]
    assert coverage.beneficiary.reference == f"urn:uuid:{dependent.id}"
    assert coverage.subscriber.reference == f"urn:uuid:{subscriber.id}"


def test_response_certified_fixture_sets_complete_outcome_and_auth_ref():
    bundle = _build_bundle("edi_278_response_certified.x12")
    by_type = _entries_by_type(bundle)

    claim = by_type["Claim"][0]
    response = by_type["ClaimResponse"][0]
    coverage = by_type["Coverage"][0]
    assert response.outcome == "complete"
    assert response.preAuthRef == "AUTH0001"
    assert response.request.reference == f"urn:uuid:{claim.id}"
    assert response.patient.reference == claim.patient.reference
    assert response.insurance[0].coverage.reference == f"urn:uuid:{coverage.id}"
    assert claim.insurance[0].coverage.reference == response.insurance[0].coverage.reference
    codes = [c.code for a in response.item[0].adjudication for c in a.category.coding]
    assert codes == ["A1"]
    # UM03="3" resolves here (unlike the request_basic fixture's empty
    # UM03), exercising build_service_type_category's coded path.
    assert claim.item[0].productOrService.coding[0].code == "3"


def test_lowercase_hcr_action_code_still_resolves_correct_outcome():
    # A code review caught that HCR01 was read without .strip().upper()
    # normalization, unlike NM108/EB01 elsewhere in this package - a
    # lowercase action code silently fell through to the "complete"
    # default (wrong for "a4" Pended, which should be "queued") instead
    # of resolving correctly.
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HI*SENDERID*RECEIVERID*20260812*1200*1*X*005010X217~"
        "ST*278*0001~"
        "BHT*0007*11*A00002*20260812*1102~"
        "HL*1**20*1~"
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~"
        "HL*2*1*21*1~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~"
        "HL*4*3*EV*0~"
        "UM*HS*I**12:B~"
        "HCR*a4~"
        "SE*12*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi278Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    response = by_type["ClaimResponse"][0]
    assert response.outcome == "queued"


def test_response_denied_fixture_maps_a3_to_complete_outcome_with_reason():
    bundle = _build_bundle("edi_278_response_denied.x12")
    by_type = _entries_by_type(bundle)

    response = by_type["ClaimResponse"][0]
    assert response.outcome == "complete"  # A3 (Not Certified) is still a completed decision
    assert response.preAuthRef is None  # HCR02 empty in this fixture
    codes = [c.code for a in response.item[0].adjudication for c in a.category.coding]
    assert codes == ["A3", "93"]  # action code + reason code (HCR03)


def test_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_278_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError):
        Edi278Builder().build_bundle(transaction_set, interchange.delimiters)


def test_missing_patient_event_loop_raises_missing_segment_error():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HI*SENDERID*RECEIVERID*20260812*1200*1*X*005010X217~"
        "ST*278*0001~"
        "BHT*0007*13*B99999*20260812*1430~"
        "HL*1**20*1~"
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~"
        "HL*2*1*21*1~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~"
        "SE*9*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="2000E Patient Event"):
        Edi278Builder().build_bundle(transaction_set, interchange.delimiters)


def test_organization_type_requester_materializes_an_organization():
    # NM102="2" (non-person entity) on the 2000B loop - a requester that's
    # an organization rather than an individual practitioner, the other
    # legal shape build_bundle() must handle (see is_person_entity()).
    bundle = _build_bundle("edi_278_request_with_dependent.x12")
    by_type = _entries_by_type(bundle)
    assert "Practitioner" not in by_type
    org_names = {o.name for o in by_type["Organization"]}
    assert "GENERAL HOSPITAL" in org_names


def test_unrecognized_hcr_action_code_defaults_to_complete_outcome():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HI*SENDERID*RECEIVERID*20260812*1200*1*X*005010X217~"
        "ST*278*0001~"
        "BHT*0007*11*B00001*20260812*1430~"
        "HL*1**20*1~"
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~"
        "HL*2*1*21*1~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~"
        "HL*4*3*EV*0~"
        "UM*HS*I**12:B~"
        "HCR*Z9~"
        "SE*12*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi278Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    response = by_type["ClaimResponse"][0]
    assert response.outcome == "complete"


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_278_response_certified.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
