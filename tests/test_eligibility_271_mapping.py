from pathlib import Path

import pytest

from app.edi.eligibility_271 import Edi271Builder
from app.edi.parser import first_transaction_set, parse_interchange
from app.hl7.errors import MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi271Builder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_two_benefit_items_and_marks_inforce():
    bundle = _build_bundle("edi_271_basic.x12")
    by_type = _entries_by_type(bundle)

    assert bundle.identifier.value == "10001234"

    patients = {p.name[0].family + "/" + p.name[0].given[0]: p for p in by_type["Patient"]}
    dependent = patients["DOE/JIMMY"]

    response = by_type["CoverageEligibilityResponse"][0]
    assert response.status == "active"
    assert response.purpose == ["benefits"]
    assert response.outcome == "complete"
    assert response.disposition is None
    assert response.patient.reference == f"urn:uuid:{dependent.id}"
    # No real CoverageEligibilityRequest resource exists in this standalone
    # conversion - referenced by the shared BHT03 identifier instead.
    assert response.request.reference is None
    assert response.request.identifier.value == "10001234"

    insurance = response.insurance[0]
    assert insurance.inforce is True
    assert len(insurance.item) == 2
    categories = {item.category.coding[0].code for item in insurance.item}
    assert categories == {"30", "88"}
    for item in insurance.item:
        assert item.excluded is False
        assert item.network.text == "In Network"


def test_rejected_fixture_sets_error_outcome_and_disposition():
    bundle = _build_bundle("edi_271_rejected.x12")
    by_type = _entries_by_type(bundle)
    response = by_type["CoverageEligibilityResponse"][0]
    assert response.outcome == "error"
    assert response.disposition == "Rejected: 72"
    # No dependent loop in this fixture - patient is the subscriber.
    assert len(by_type["Patient"]) == 1


def test_missing_bht_raises_missing_segment_error():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*271*0001~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "SE*3*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError):
        Edi271Builder().build_bundle(transaction_set, interchange.delimiters)


def test_eb01_inactive_and_non_covered_set_excluded_true():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*271*0001~"
        "BHT*0022*11*10003333*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*0~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "EB*6*IND*30~"
        "SE*10*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi271Builder().build_bundle(transaction_set, interchange.delimiters)
    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse")
    assert response.insurance[0].inforce is False
    assert response.insurance[0].item[0].excluded is True


def test_aaa_rejection_with_empty_reason_code_still_sets_error_outcome():
    # A code review caught that _resolve_outcome_and_disposition originally
    # required a non-empty AAA03 before treating AAA01="N" as a rejection,
    # contradicting the module's own documented "any AAA01='N' -> error"
    # rule and silently reporting outcome="complete" for a genuinely
    # rejected request whose reason code just didn't resolve.
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*271*0001~"
        "BHT*0022*11*10004444*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*0~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "AAA*N**~"
        "SE*10*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi271Builder().build_bundle(transaction_set, interchange.delimiters)
    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse")
    assert response.outcome == "error"
    assert response.disposition == "Rejected"


def test_lowercase_eb01_still_resolves_excluded():
    # NM108/EB01/EB12 all needed the same normalize-before-lookup treatment
    # - only EB12 had it originally. EB01="i" (Non-Covered, lowercase - the
    # EB segment ID itself is always uppercase by X12 spec and is matched
    # case-sensitively throughout this codebase, same as ISA; only the
    # EB01 data element's own value is at risk of case variation) must
    # still resolve to _EB01_EXCLUDED_MAP["I"]=True rather than silently
    # leaving .excluded unset.
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*271*0001~"
        "BHT*0022*11*10005555*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*0~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "EB*i*IND*30~"
        "SE*10*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi271Builder().build_bundle(transaction_set, interchange.delimiters)
    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse")
    assert response.insurance[0].item[0].excluded is True
    assert response.insurance[0].inforce is False


def test_dependent_loop_without_nm1_falls_back_to_subscriber():
    # A code review caught that validation.py's own re-derived walk treated
    # any present HL03="23" loop as "the patient" without checking it had a
    # resolvable NM1, diverging from build_bundle()'s actual precedence
    # rule (dependent wins only when its own NM1 resolves). This proves the
    # real builder's fallback directly; test_edi_validation.py proves
    # validation now sees the identical segment set via the shared helper.
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~"
        "ST*271*0001~"
        "BHT*0022*11*10006666*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*0~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*3*2*22*1~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "EB*1*IND*30~"
        "HL*4*3*23*0~"
        "DMG*D8*20150601*F~"
        "SE*12*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi271Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    # No dependent NM1 -> only the subscriber Patient is materialized, and
    # the subscriber's own EB segment (not the NM1-less dependent loop's,
    # which has none) is what item resolution reads.
    assert len(by_type["Patient"]) == 1
    response = by_type["CoverageEligibilityResponse"][0]
    assert len(response.insurance[0].item) == 1


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_271_basic.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
