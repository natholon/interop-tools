from pathlib import Path

import pytest

from app.edi.parser import first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle
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


ADJUSTMENT_TYPE = "http://terminology.hl7.org/CodeSystem/payment-type"


def _payments(payment_reconciliation):
    """Claim-payment details only - .detail[] now also carries one entry
    per CAS adjustment triplet."""
    return [
        d for d in payment_reconciliation.detail
        if not any(c.system == ADJUSTMENT_TYPE for c in (d.type.coding or []))
    ]


def _adjustments(payment_reconciliation):
    return [
        d for d in payment_reconciliation.detail
        if any(c.system == ADJUSTMENT_TYPE for c in (d.type.coding or []))
    ]


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

    assert len(_payments(pr)) == 1
    detail = pr.detail[0]
    assert detail.identifier.value == "PCN12345"
    assert float(detail.amount.value) == 150.00
    assert detail.type.coding[0].code == "1"  # CLP02 Processed as Primary


def test_multi_claim_fixture_maps_one_detail_per_claim():
    bundle = _build_bundle("edi_835_multi_claim.x12")
    by_type = _entries_by_type(bundle)

    pr = by_type["PaymentReconciliation"][0]
    assert float(pr.paymentAmount.value) == 250.00
    assert len(_payments(pr)) == 2

    details_by_claim = {d.identifier.value: d for d in _payments(pr)}
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


def test_cas_adjustments_become_adjustment_details():
    # CAS carries one group code and up to six (reason, amount, quantity)
    # triplets, each a distinct adjustment with its own amount. FHIR's own
    # payment-type CodeSystem has an "adjustment" code for exactly this,
    # and detail.type binds to it at example strength - so the X12 group
    # and reason codes ride alongside it in the same CodeableConcept,
    # which is what this app already does with CLP02.
    from app.edi.remittance_835 import CAS_GROUP_SYSTEM, CAS_REASON_SYSTEM

    pr = _entries_by_type(_build_bundle("edi_835_basic.x12"))["PaymentReconciliation"][0]

    adjustments = _adjustments(pr)
    assert len(adjustments) == 1
    codings = {c.system: c.code for c in adjustments[0].type.coding}
    assert codings[ADJUSTMENT_TYPE] == "adjustment"
    assert codings[CAS_GROUP_SYSTEM] == "CO"
    assert codings[CAS_REASON_SYSTEM] == "45"
    assert float(adjustments[0].amount.value) == 350.00


def test_each_claims_adjustments_are_all_carried():
    # Two claims, one adjustment each, with different reason codes - so a
    # CAS is attributed to the claim that precedes it rather than all of
    # them landing on the first.
    from app.edi.remittance_835 import CAS_REASON_SYSTEM

    pr = _entries_by_type(_build_bundle("edi_835_multi_claim.x12"))["PaymentReconciliation"][0]

    reasons = [
        next(c.code for c in d.type.coding if c.system == CAS_REASON_SYSTEM)
        for d in _adjustments(pr)
    ]
    assert reasons == ["45", "96"]


def _claim_responses(bundle):
    return [e.resource for e in bundle.entry if e.resource.get_resource_type() == "ClaimResponse"]


def _total(claim_response, code: str):
    for total in claim_response.total or []:
        if total.category.coding[0].code == code:
            return str(total.amount.value)
    return None


def test_claim_money_lands_on_a_claim_response():
    # R4 PaymentReconciliationDetail carries one amount and a claim has
    # three, so the charge and the patient responsibility go where FHIR
    # models adjudication totals.
    bundle = convert_edi_to_bundle(read_fixture("edi_835_basic.x12"))
    claim_response = _claim_responses(bundle)[0]
    assert _total(claim_response, "submitted") == "500.00"
    assert _total(claim_response, "benefit") == "150.00"
    assert _total(claim_response, "patient-responsibility") == "350.00"


def test_claim_response_carries_the_payer_control_number_and_filing_indicator():
    bundle = convert_edi_to_bundle(read_fixture("edi_835_basic.x12"))
    claim_response = _claim_responses(bundle)[0]
    assert claim_response.identifier[0].value == "PAYERCTRL987"
    assert claim_response.subType.coding[0].code == "MC"
    # The submitted Claim is not in this Bundle - an 835 stands alone - so
    # CLP01 comes back as a reference by identifier.
    assert claim_response.request.identifier.value == "PCN12345"


def test_claim_response_references_the_2100_patient_and_the_payer():
    bundle = convert_edi_to_bundle(read_fixture("edi_835_basic.x12"))
    claim_response = _claim_responses(bundle)[0]
    by_id = {e.resource.id: e.resource for e in bundle.entry}
    patient = by_id[claim_response.patient.reference.removeprefix("urn:uuid:")]
    assert patient.name[0].family == "DOE"
    assert str(patient.birthDate) == "1980-01-01"
    assert by_id[claim_response.insurer.reference.removeprefix("urn:uuid:")].name == "ACME HEALTH PLAN"


def test_payment_detail_points_at_its_own_claim_response():
    bundle = convert_edi_to_bundle(read_fixture("edi_835_multi_claim.x12"))
    reconciliation = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation"
    )
    by_id = {c.id: c for c in _claim_responses(bundle)}
    claims = [d for d in reconciliation.detail if d.response is not None]
    assert [by_id[d.response.reference.removeprefix("urn:uuid:")].identifier[0].value for d in claims] == [
        "PAYERCTRL111",
        "PAYERCTRL222",
    ]
    # A CAS adjustment is not a claim, so it gets no response.
    assert any(d.response is None for d in reconciliation.detail)


def test_insured_loop_is_used_when_no_patient_loop_is_present():
    # NM1*IL alone is how an 835 says the patient is the subscriber.
    bundle = convert_edi_to_bundle(read_fixture("edi_835_multi_claim.x12"))
    patients = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert sorted(p.name[0].family for p in patients) == ["BROWN", "SMITH"]


def test_claim_without_a_2100_person_loop_builds_no_claim_response():
    # ClaimResponse.patient is 1..1 - there would be nothing to point it
    # at, so the claim keeps its PaymentReconciliation detail and no more.
    raw = read_fixture("edi_835_basic.x12")
    stripped = raw.replace("NM1*QC*1*DOE*JANE****MI*MEMBER001~", "").replace("DMG*D8*19800101*F~", "")
    bundle = convert_edi_to_bundle(stripped)
    assert _claim_responses(bundle) == []
    reconciliation = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation"
    )
    assert all(d.response is None for d in reconciliation.detail)

