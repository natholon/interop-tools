from pathlib import Path

import pytest

from app.edi.claim_837d import Edi837dBuilder
from app.edi.parser import first_transaction_set, parse_interchange
from app.hl7.errors import MappingError, MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_claim_with_two_service_lines_and_tooth_info():
    bundle = _build_bundle("edi_837d_basic.x12")
    by_type = _entries_by_type(bundle)

    billing_provider = by_type["Organization"][0]
    assert billing_provider.name == "DENTAL ASSOCIATES"
    payer = by_type["Organization"][1]
    assert payer.name == "INSURANCE COMPANY XYZ"
    # Both the subscriber (Jane) and the dependent patient (Ted) share the
    # SMITH family name in this fixture - disambiguate by given name.
    patients = {p.name[0].given[0]: p for p in by_type["Patient"]}
    assert set(patients.keys()) == {"JANE", "TED"}
    subscriber = patients["JANE"]
    patient = patients["TED"]
    coverage = by_type["Coverage"][0]
    rendering = by_type["Practitioner"][0]
    assert rendering.name[0].family == "KILDARE"

    claim = by_type["Claim"][0]
    assert claim.use == "claim"
    assert claim.status == "active"
    assert claim.type.coding[0].code == "oral"
    assert claim.patient.reference == f"urn:uuid:{patient.id}"
    assert coverage.beneficiary.reference == f"urn:uuid:{patient.id}"
    assert coverage.subscriber.reference == f"urn:uuid:{subscriber.id}"
    assert claim.provider.reference == f"urn:uuid:{billing_provider.id}"
    assert claim.insurer.reference == f"urn:uuid:{payer.id}"
    assert claim.insurance[0].coverage.reference == f"urn:uuid:{coverage.id}"
    assert claim.identifier[0].value == "26403774"  # CLM01
    assert float(claim.total.value) == 150  # CLM02
    # The real X12.org example this fixture is adapted from carries no HI
    # (diagnosis) segment at all - a common, legitimate real-world shape
    # for dental claims, not an oversight in the fixture.
    assert claim.diagnosis is None

    assert claim.careTeam[0].role.coding[0].code == "primary"
    assert claim.careTeam[0].provider.reference == f"urn:uuid:{rendering.id}"

    assert len(claim.item) == 2
    line1, line2 = claim.item
    assert line1.sequence == 1
    assert line1.productOrService.coding[0].system == "http://www.ada.org/cdt"
    assert line1.productOrService.coding[0].code == "D2150"
    assert float(line1.unitPrice.value) == 100
    assert float(line1.quantity.value) == 1
    assert line1.locationCodeableConcept.coding[0].code == "11"  # CLM05-1
    # Neither service line has its own DTP - both fall back to the one
    # claim-level DTP*472 (2026-08-10).
    assert line1.servicedDate.isoformat() == "2026-08-10"
    assert line1.bodySite.coding[0].system == "http://terminology.hl7.org/CodeSystem/ADAUniversalToothDesignationSystem"
    assert line1.bodySite.coding[0].code == "12"
    assert [s.coding[0].code for s in line1.subSite] == ["M", "O"]
    assert all(s.coding[0].system == "http://terminology.hl7.org/CodeSystem/ADAToothSurfaceCodes" for s in line1.subSite)
    assert line1.careTeamSequence == [1]

    assert line2.sequence == 2
    assert line2.productOrService.coding[0].code == "D1110"
    assert line2.servicedDate.isoformat() == "2026-08-10"
    assert line2.bodySite is None  # no TOO segment for this line
    assert line2.subSite is None
    assert line2.careTeamSequence == [1]


def test_no_dependent_fixture_maps_diagnosis_pointer_and_per_line_date():
    bundle = _build_bundle("edi_837d_no_dependent.x12")
    by_type = _entries_by_type(bundle)

    billing_provider = by_type["Organization"][0]
    assert billing_provider.name == "BRIGHT SMILES DENTAL"
    assert "Practitioner" not in by_type  # no rendering provider in this fixture

    patient = by_type["Patient"][0]
    assert patient.name[0].family == "JONES"

    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{patient.id}"
    assert claim.careTeam is None

    assert len(claim.diagnosis) == 1
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].code == "K0290"
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].system == "http://hl7.org/fhir/sid/icd-10-cm"

    item = claim.item[0]
    assert item.productOrService.coding[0].code == "D1120"
    assert float(item.quantity.value) == 1
    # This fixture's own DTP is per-line (no claim-level one), proving the
    # per-line branch of resolve_line_dtp_raw_date, not just the fallback.
    assert item.servicedDate.isoformat() == "2026-08-10"
    assert item.diagnosisSequence == [1]  # SV3-11 pointer
    assert item.careTeamSequence is None
    assert item.bodySite is None  # no TOO segment


def test_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_837d_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="BHT"):
        Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)


def _minimal_raw(body: str, control: str = "0001") -> str:
    body_segment_count = len([s for s in body.split("~") if s])
    se01 = body_segment_count + 2
    return (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260816*1200*^*00501*000000001*0*P*:~"
        "GS*HC*SENDERID*RECEIVERID*20260816*1200*1*X*005010X224A2~"
        f"ST*837*{control}*005010X224A2~"
        f"{body}"
        f"SE*{se01}*{control}~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )


def test_missing_clm_raises_missing_segment_error():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="CLM"):
        Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_clm02_unresolvable_raises_mapping_error():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q****11:B:1*Y*A*Y*I~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="CLM02"):
        Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_bht04_unresolvable_raises_mapping_error():
    body = (
        "BHT*0019*00*0999~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="BHT04"):
        Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_dependent_loop_without_nm1_falls_back_to_subscriber():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*1~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "HL*3*2*23*0~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    assert len(by_type["Patient"]) == 1
    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{by_type['Patient'][0].id}"


def test_diagnosis_pointer_out_of_range_is_skipped():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
        "HI*ABK:J209~"
        "LX*1~"
        "SV3*AD:D1120*85****1*****9~"  # pointer 9 (SV3-11) - only 1 diagnosis exists
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item[0].diagnosisSequence is None


def test_occurrence_value_condition_hi_segments_do_not_pollute_diagnosis():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
        "HI*ABK:J209~"
        "HI*BG:09~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert len(claim.diagnosis) == 1
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].code == "J209"


def test_service_line_without_sv3_is_skipped():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
        "LX*1~"
        "TOO*JP*12*M~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item is None


def test_unrecognized_procedure_qualifier_falls_back_to_disclosed_local_system():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
        "LX*1~"
        "SV3*ZZ:99999*85****1~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item[0].productOrService.coding[0].system == "urn:interop-tools:x12-procedure-qualifier:ZZ"


def test_unrecognized_tooth_qualifier_falls_back_to_disclosed_local_system():
    body = (
        "BHT*0019*00*0999*20260816*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*JONES*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*DELTA DENTAL*****PI*99887766~"
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~"
        "LX*1~"
        "SV3*AD:D2150*100****1~"
        "TOO*ZZ*12*M~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837dBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item[0].bodySite.coding[0].system == "urn:interop-tools:x12-too-tooth-number:ZZ"


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_837d_basic.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
