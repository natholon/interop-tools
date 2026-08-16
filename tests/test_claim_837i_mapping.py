from pathlib import Path

import pytest

from app.edi.claim_837i import Edi837iBuilder
from app.edi.parser import first_transaction_set, parse_interchange
from app.hl7.errors import MappingError, MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_claim_with_two_service_lines():
    bundle = _build_bundle("edi_837i_basic.x12")
    by_type = _entries_by_type(bundle)

    billing_provider = by_type["Organization"][0]
    assert billing_provider.name == "JONES HOSPITAL"
    payer = by_type["Organization"][1]
    assert payer.name == "MEDICARE B"
    patient = by_type["Patient"][0]
    assert patient.name[0].family == "DOE"
    coverage = by_type["Coverage"][0]
    attending = by_type["Practitioner"][0]
    assert attending.name[0].family == "JONES"

    claim = by_type["Claim"][0]
    assert claim.use == "claim"
    assert claim.status == "active"
    assert claim.type.coding[0].code == "institutional"
    assert claim.patient.reference == f"urn:uuid:{patient.id}"
    assert claim.provider.reference == f"urn:uuid:{billing_provider.id}"
    assert claim.insurer.reference == f"urn:uuid:{payer.id}"
    assert claim.insurance[0].coverage.reference == f"urn:uuid:{coverage.id}"
    assert claim.identifier[0].value == "756048Q"  # CLM01
    assert float(claim.total.value) == 89.93  # CLM02

    # Two HI segments in this fixture (Principal + Other diagnosis), each
    # its own physical segment - both must be captured, in composite order.
    assert [d.diagnosisCodeableConcept.coding[0].code for d in claim.diagnosis] == ["3669", "4019", "79431"]
    # The fixture uses the modern ABK/ABF qualifiers (adapted from the real
    # X12.org example, which used the legacy BK/BF - see the fixture's own
    # comment) - ABK/ABF resolve to ICD-10-CM, not ICD-9-CM.
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].system == "http://hl7.org/fhir/sid/icd-10-cm"

    # CL103 (Patient Status Code) -> supportingInfo, real NUBC canonical system.
    assert len(claim.supportingInfo) == 1
    info = claim.supportingInfo[0]
    assert info.category.coding[0].system == "http://terminology.hl7.org/CodeSystem/claiminformationcategory"
    assert info.category.coding[0].code == "discharge"
    assert info.code.coding[0].system == "https://www.nubc.org/CodeSystem/PatDischargeStatus"
    assert info.code.coding[0].code == "01"

    assert claim.careTeam[0].role.coding[0].code == "primary"
    assert claim.careTeam[0].provider.reference == f"urn:uuid:{attending.id}"

    assert len(claim.item) == 2
    line1, line2 = claim.item
    assert line1.sequence == 1
    assert line1.revenue.coding[0].system == "https://www.nubc.org/CodeSystem/RevenueCodes"
    assert line1.revenue.coding[0].code == "0305"
    assert line1.productOrService.coding[0].code == "85025"
    assert float(line1.unitPrice.value) == 13.39
    assert line1.servicedDate.isoformat() == "1996-09-11"
    assert line1.diagnosisSequence is None  # SV2 has no diagnosis-pointer composite, unlike 837P's SV1-07
    # A code review caught that service-line items never linked back to
    # Claim.careTeam via careTeamSequence, unlike the identical 837P shape.
    assert line1.careTeamSequence == [1]
    assert claim.careTeam[0].sequence == 1

    assert line2.sequence == 2
    assert line2.revenue.coding[0].code == "0730"
    assert line2.careTeamSequence == [1]
    assert line2.productOrService.coding[0].code == "93005"


def test_with_dependent_fixture_patient_is_the_dependent_and_no_procedure_code():
    bundle = _build_bundle("edi_837i_with_dependent.x12")
    by_type = _entries_by_type(bundle)

    billing_provider = by_type["Practitioner"][0]  # NM102="1" -> Practitioner, not Organization
    assert billing_provider.name[0].family == "JONES"

    patients = {p.name[0].given[0]: p for p in by_type["Patient"]}
    assert set(patients.keys()) == {"JANE", "TED"}
    dependent = patients["TED"]
    subscriber = patients["JANE"]

    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{dependent.id}"
    assert claim.careTeam is None  # no NM1*71 in this fixture

    coverage = by_type["Coverage"][0]
    assert coverage.beneficiary.reference == f"urn:uuid:{dependent.id}"
    assert coverage.subscriber.reference == f"urn:uuid:{subscriber.id}"

    # SV2*0120**250.00*DA*1~ - revenue code present, procedure code (SV2-02)
    # genuinely absent (a real room-and-board line shape) - productOrService
    # must still resolve (FHIR-required) via the revenue-code text fallback,
    # not a generic placeholder.
    assert claim.item[0].revenue.coding[0].code == "0120"
    assert claim.item[0].productOrService.coding is None
    assert claim.item[0].productOrService.text == "Revenue code 0120"
    assert claim.item[0].careTeamSequence is None  # no attending provider in this fixture

    assert claim.supportingInfo[0].code.coding[0].code == "02"


def test_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_837i_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="BHT"):
        Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)


def _minimal_raw(body: str, control: str = "0001") -> str:
    body_segment_count = len([s for s in body.split("~") if s])
    se01 = body_segment_count + 2
    return (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260815*1200*^*00501*000000001*0*P*:~"
        "GS*HC*SENDERID*RECEIVERID*20260815*1200*1*X*005010X223A2~"
        f"ST*837*{control}*005010X223A2~"
        f"{body}"
        f"SE*{se01}*{control}~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )


def test_missing_clm_raises_missing_segment_error():
    body = (
        "BHT*0019*00*0999*20260815*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*MEDICARE B****PI*00435~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="CLM"):
        Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_clm02_unresolvable_raises_mapping_error():
    body = (
        "BHT*0019*00*0999*20260815*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*MEDICARE B****PI*00435~"
        "CLM*756048Q****14:A:1**A*Y*Y~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="CLM02"):
        Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_bht04_unresolvable_raises_mapping_error():
    body = (
        "BHT*0019*00*0999~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*MEDICARE B****PI*00435~"
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="BHT04"):
        Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_occurrence_and_value_code_hi_segments_do_not_pollute_diagnosis():
    # A real institutional claim can carry HI segments for occurrence (BH),
    # value (BE), and condition (BG) codes alongside diagnosis ones - none
    # of those must be folded into Claim.diagnosis[] as bogus "unrecognized
    # qualifier" entries (see _iter_diagnosis_hi_segments's own docstring).
    body = (
        "BHT*0019*00*0999*20260815*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*MEDICARE B****PI*00435~"
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~"
        "HI*ABK:J209~"
        "HI*BH:A1:D8:19261111~"
        "HI*BE:A2:::15.31~"
        "HI*BG:09~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert len(claim.diagnosis) == 1
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].code == "J209"


def test_dependent_loop_without_nm1_falls_back_to_subscriber():
    body = (
        "BHT*0019*00*0999*20260815*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*1~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*MEDICARE B****PI*00435~"
        "HL*3*2*23*0~"
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    assert len(by_type["Patient"]) == 1
    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{by_type['Patient'][0].id}"


def test_service_line_without_sv2_is_skipped():
    body = (
        "BHT*0019*00*0999*20260815*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*MEDICARE B****PI*00435~"
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~"
        "LX*1~"
        "DTP*472*D8*19960911~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837iBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item is None


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_837i_basic.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
