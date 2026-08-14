from pathlib import Path

import pytest

from app.edi.parser import first_transaction_set, parse_interchange
from app.edi.remittance_835 import Edi835Builder
from app.hl7.errors import MappingError, MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi835Builder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_payer_payee_and_one_detail():
    bundle = _build_bundle("edi_835_basic.x12")
    by_type = _entries_by_type(bundle)

    assert bundle.identifier.value == "1512345678"  # TRN02

    payer = by_type["Organization"][0]
    assert payer.name == "ACME HEALTH PLAN"
    assert payer.identifier[0].value == "PAYERID001"
    payee = by_type["Organization"][1]
    assert payee.name == "GENERAL HOSPITAL"
    assert payee.identifier[0].system == "http://hl7.org/fhir/sid/us-npi"

    pr = by_type["PaymentReconciliation"][0]
    assert pr.status == "active"
    assert float(pr.paymentAmount.value) == 150.00
    assert pr.paymentAmount.currency == "USD"
    assert pr.paymentDate.isoformat() == "2026-08-12"
    assert pr.created.isoformat() == "2026-08-12"
    assert pr.paymentIssuer.reference == f"urn:uuid:{payer.id}"
    assert pr.requestor.reference == f"urn:uuid:{payee.id}"

    assert len(pr.detail) == 1
    detail = pr.detail[0]
    assert detail.identifier.value == "PCN12345"
    assert float(detail.amount.value) == 150.00
    assert detail.type.coding[0].code == "1"  # CLP02 Processed as Primary


def test_multi_claim_fixture_maps_one_detail_per_claim():
    bundle = _build_bundle("edi_835_multi_claim.x12")
    by_type = _entries_by_type(bundle)

    pr = by_type["PaymentReconciliation"][0]
    assert float(pr.paymentAmount.value) == 250.00
    assert len(pr.detail) == 2

    details_by_claim = {d.identifier.value: d for d in pr.detail}
    assert set(details_by_claim.keys()) == {"PCN22222", "PCN33333"}
    assert float(details_by_claim["PCN22222"].amount.value) == 250.00
    assert details_by_claim["PCN22222"].type.coding[0].code == "1"
    assert float(details_by_claim["PCN33333"].amount.value) == 0.00
    assert details_by_claim["PCN33333"].type.coding[0].code == "4"  # Denied


def test_missing_bpr_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_835_missing_bpr.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError):
        Edi835Builder().build_bundle(transaction_set, interchange.delimiters)


def test_missing_payee_raises_missing_segment_error():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HP*SENDERID*RECEIVERID*20260812*1200*1*X*005010X221A1~"
        "ST*835*0001~"
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~"
        "TRN*1*1512345678*9876543210~"
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~"
        "SE*5*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="N1\\*PE"):
        Edi835Builder().build_bundle(transaction_set, interchange.delimiters)


def test_no_claims_produces_no_detail_entries():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HP*SENDERID*RECEIVERID*20260812*1200*1*X*005010X221A1~"
        "ST*835*0001~"
        "BPR*I*0.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~"
        "TRN*1*1512345678*9876543210~"
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~"
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~"
        "SE*6*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi835Builder().build_bundle(transaction_set, interchange.delimiters)
    pr = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation")
    assert pr.detail is None


def test_missing_trn_raises_missing_segment_error():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HP*SENDERID*RECEIVERID*20260812*1200*1*X*005010X221A1~"
        "ST*835*0001~"
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~"
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~"
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~"
        "SE*5*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="TRN"):
        Edi835Builder().build_bundle(transaction_set, interchange.delimiters)


def test_bpr02_unresolvable_raises_mapping_error_not_missing_segment_error():
    # BPR is present (so this is a "segment present, value doesn't resolve"
    # case, not an absent-segment one) - matching eligibility_270.py's own
    # BHT04-unresolvable precedent, MappingError not MissingSegmentError.
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HP*SENDERID*RECEIVERID*20260812*1200*1*X*005010X221A1~"
        "ST*835*0001~"
        "BPR*I***ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~"
        "TRN*1*1512345678*9876543210~"
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~"
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~"
        "SE*6*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="BPR02"):
        Edi835Builder().build_bundle(transaction_set, interchange.delimiters)


def test_bpr16_unresolvable_raises_mapping_error_not_missing_segment_error():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HP*SENDERID*RECEIVERID*20260812*1200*1*X*005010X221A1~"
        "ST*835*0001~"
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*~"
        "TRN*1*1512345678*9876543210~"
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~"
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~"
        "SE*6*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="BPR16"):
        Edi835Builder().build_bundle(transaction_set, interchange.delimiters)


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_835_multi_claim.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
