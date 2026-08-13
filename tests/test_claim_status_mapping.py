from pathlib import Path

import pytest

from app.edi.claim_status import Edi276Builder, Edi277Builder
from app.edi.parser import first_transaction_set, parse_interchange
from app.hl7.errors import MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(builder_cls, fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return builder_cls().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_276_basic_fixture_maps_payer_receiver_provider_and_two_patients():
    bundle = _build_bundle(Edi276Builder, "edi_276_basic.x12")
    by_type = _entries_by_type(bundle)

    assert bundle.identifier.value == "10001234"
    assert len(by_type["Organization"]) == 3  # payer, receiver, provider
    payer = next(o for o in by_type["Organization"] if o.name == "ACME HEALTH PLAN")

    patients = {p.name[0].family + "/" + p.name[0].given[0]: p for p in by_type["Patient"]}
    assert set(patients.keys()) == {"DOE/JANE", "DOE/JIMMY"}
    subscriber = patients["DOE/JANE"]
    dependent = patients["DOE/JIMMY"]
    assert dependent.gender == "male"
    assert dependent.birthDate.isoformat() == "2015-06-15"

    tasks = by_type["Task"]
    assert len(tasks) == 2
    task_by_patient = {t.for_fhir.reference: t for t in tasks}
    assert task_by_patient[f"urn:uuid:{subscriber.id}"].identifier[0].value == "TRACE0001"
    assert task_by_patient[f"urn:uuid:{dependent.id}"].identifier[0].value == "TRACE0002"
    for task in tasks:
        assert task.status == "requested"
        assert task.intent == "order"
        assert task.owner.reference == f"urn:uuid:{payer.id}"
        assert task.businessStatus is None  # 276 is a request - no STC yet


def test_276_no_dependent_fixture_has_one_task_for_the_subscriber():
    bundle = _build_bundle(Edi276Builder, "edi_276_no_dependent.x12")
    by_type = _entries_by_type(bundle)

    assert len(by_type["Patient"]) == 1
    subscriber = by_type["Patient"][0]
    assert subscriber.name[0].family == "SMITH"

    tasks = by_type["Task"]
    assert len(tasks) == 1
    assert tasks[0].for_fhir.reference == f"urn:uuid:{subscriber.id}"
    assert tasks[0].identifier[0].value == "TRACE0003"


def test_276_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_276_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError):
        Edi276Builder().build_bundle(transaction_set, interchange.delimiters)


def test_277_basic_fixture_sets_business_status_and_finalized_task_status():
    bundle = _build_bundle(Edi277Builder, "edi_277_basic.x12")
    by_type = _entries_by_type(bundle)

    tasks = {t.identifier[0].value: t for t in by_type["Task"]}
    assert tasks["TRACE0001"].status == "completed"  # STC category F1 -> Finalized -> completed
    assert [c.code for c in tasks["TRACE0001"].businessStatus.coding] == ["F1", "1"]

    assert tasks["TRACE0002"].status == "in-progress"  # STC category P1 -> Pending -> in-progress
    assert [c.code for c in tasks["TRACE0002"].businessStatus.coding] == ["P1", "1"]


def test_277_error_status_fixture_maps_category_e_to_failed_task_status():
    bundle = _build_bundle(Edi277Builder, "edi_277_error_status.x12")
    by_type = _entries_by_type(bundle)
    task = by_type["Task"][0]
    assert task.status == "failed"
    assert [c.code for c in task.businessStatus.coding] == ["E1", "42"]


def test_277_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_276_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    # Reuse the 276 fixture's shape but through the 277 builder - the
    # missing-BHT check happens before any 276/277-specific divergence.
    with pytest.raises(MissingSegmentError):
        Edi277Builder().build_bundle(transaction_set, interchange.delimiters)


def test_no_claim_status_entries_raises_missing_segment_error():
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HR*SENDERID*RECEIVERID*20260812*1200*1*X*005010X212~"
        "ST*276*0001~"
        "BHT*0010*13*10001234*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*1~"
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~"
        "HL*3*2*19*1~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*4*3*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "SE*11*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="no TRN-led claim-status entry"):
        Edi276Builder().build_bundle(transaction_set, interchange.delimiters)


def test_dependent_loop_without_nm1_is_silently_dropped_not_reported():
    # A code review caught that resolve_claim_status_loops originally
    # returned a present-but-NM1-less 2000E dependent loop unconditionally
    # (loop-existence only), unlike common.py::resolve_eligibility_parties'
    # equivalent "only counts as real once its own NM1 resolves" gate for
    # 270/271. Since build_bundle() itself only builds a dependent Patient/
    # Task when the dependent NM1 resolves, an NM1-less 2000E loop's claim
    # (STC*Z9...) must simply vanish from the Bundle - exactly one Task
    # (the subscriber's), not two, and no error either (the message is
    # otherwise perfectly convertible via the subscriber alone).
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HR*SENDERID*RECEIVERID*20260812*1200*1*X*005010X212~"
        "ST*277*0001~"
        "BHT*0010*08*10008888*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*1~"
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~"
        "HL*3*2*19*1~"
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~"
        "HL*4*3*22*1~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "TRN*2*TRACE0001*1512345678~"
        "STC*F1:1:PR*20260805~"
        "HL*5*4*23*0~"
        "DMG*D8*20150615*M~"
        "TRN*2*TRACE0002*1512345678~"
        "STC*Z9:1:PR*20260805~"
        "SE*17*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi277Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    assert len(by_type["Patient"]) == 1
    assert len(by_type["Task"]) == 1
    assert by_type["Task"][0].identifier[0].value == "TRACE0001"


def test_person_type_receiver_and_provider_materialize_practitioners():
    # NM102="1" (person) on the 2000B/2000C loops - a receiver/provider
    # that's an individual rather than an organization, the other legal
    # shape build_bundle() must handle (see is_person_entity()).
    raw = (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260812*1200*^*00501*000000001*0*P*:~"
        "GS*HR*SENDERID*RECEIVERID*20260812*1200*1*X*005010X212~"
        "ST*276*0001~"
        "BHT*0010*13*10002222*20260812*1200~"
        "HL*1**20*1~"
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~"
        "HL*2*1*21*1~"
        "NM1*41*1*RECEIVER*RITA*****46*SUB001~"
        "HL*3*2*19*1~"
        "NM1*1P*1*WELBY*MARCUS****XX*1112223334~"
        "HL*4*3*22*0~"
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
        "TRN*1*TRACE0004*1512345678~"
        "SE*12*0001~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi276Builder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    assert "Practitioner" in by_type
    practitioners = {p.name[0].family for p in by_type["Practitioner"]}
    assert practitioners == {"RECEIVER", "WELBY"}
    assert len(by_type["Organization"]) == 1  # only the payer


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle(Edi277Builder, "edi_277_basic.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
