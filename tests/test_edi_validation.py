import pytest

from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange
from app.hl7.errors import MissingSegmentError

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260812*1200*^*00501*000000001*0*P*:~"

_270_BODY = [
    "BHT*0022*13*10001234*20260812*1200~",
    "HL*1**20*1~",
    "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
    "HL*2*1*21*1~",
    "NM1*1P*2*PROVIDER NAME*****XX*1234567890~",
    "HL*3*2*22*0~",
    "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
    "DMG*D8*19800101*F~",
    "EQ*30~",
    "DTP*291*D8*20260812~",
]


def _build(st01: str, body: list[str], se01=None, ge01=None, iea02=None, isa15: str = "P") -> str:
    se01 = se01 if se01 is not None else len(body) + 2
    ge01 = ge01 if ge01 is not None else 1
    iea02 = iea02 if iea02 is not None else 1
    isa = _ISA if isa15 == "P" else _ISA[:-4] + isa15 + _ISA[-3:]
    segments = [
        isa,
        "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~",
        f"ST*{st01}*0001~",
        *body,
        f"SE*{se01}*0001~",
        f"GE*{ge01}*1~",
        f"IEA*{iea02}*000000001~",
    ]
    return "".join(segments)


def test_clean_270_produces_no_findings():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "270"


def test_doubled_minus_sign_in_trailer_count_does_not_crash():
    # A code review caught that _int_or_none's old
    # `stripped.lstrip("-").isdigit()` guard strips EVERY leading "-", not
    # just one, so a malformed "--5" passed the guard but then int("--5")
    # raised an uncaught ValueError - turning validate_interchange() into a
    # raw crash on exactly the kind of fat-fingered trailer count the
    # validator exists to flag as a mismatch, not blow up on. An
    # unparseable declared count is treated as "can't tell" (same as a
    # genuinely absent trailer) rather than a mismatch - the point of this
    # test is that it doesn't raise, not what finding it produces.
    report = validate_interchange(parse_interchange(_build("270", _270_BODY, se01="--5")))
    assert "edi.st-se-count-mismatch" not in {f.rule_id for f in report.findings}


def test_st_se_count_mismatch_is_warning():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY, se01=99)))
    finding = next(f for f in report.findings if f.rule_id == "edi.st-se-count-mismatch")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_gs_ge_count_mismatch_is_warning():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY, ge01=5)))
    finding = next(f for f in report.findings if f.rule_id == "edi.gs-ge-count-mismatch")
    assert finding.severity == "warning"


def test_isa_iea_count_mismatch_is_warning():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY, iea02=5)))
    finding = next(f for f in report.findings if f.rule_id == "edi.isa-iea-count-mismatch")
    assert finding.severity == "warning"


def test_correct_counts_produce_no_count_mismatch_findings():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY)))
    rule_ids = {f.rule_id for f in report.findings}
    assert not rule_ids & {"edi.st-se-count-mismatch", "edi.gs-ge-count-mismatch", "edi.isa-iea-count-mismatch"}


def test_isa_usage_indicator_test_is_info():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY, isa15="T")))
    finding = next(f for f in report.findings if f.rule_id == "edi.isa-usage-indicator-test")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_isa_usage_indicator_production_produces_no_finding():
    report = validate_interchange(parse_interchange(_build("270", _270_BODY, isa15="P")))
    assert "edi.isa-usage-indicator-test" not in {f.rule_id for f in report.findings}


def test_hl_parent_not_found_is_error():
    body = [
        "BHT*0022*13*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
        "HL*2*99*21*1~",  # HL02="99" does not resolve to any HL01
        "NM1*1P*2*PROVIDER NAME*****XX*1234567890~",
    ]
    report = validate_interchange(parse_interchange(_build("270", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.hl-parent-not-found")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_270_missing_subscriber_name_is_info():
    body = [
        "BHT*0022*13*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*PROVIDER NAME*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1~",
        "EQ*30~",
    ]
    report = validate_interchange(parse_interchange(_build("270", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.270-missing-subscriber-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_270_serviced_date_in_future_is_warning():
    body = [
        "BHT*0022*13*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*PROVIDER NAME*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1*DOE*JANE~",
        "EQ*30~",
        "DTP*291*D8*20990101~",
    ]
    report = validate_interchange(parse_interchange(_build("270", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.eligibility-servicedate-in-future")
    assert finding.severity == "warning"


def test_271_no_service_type_in_any_eb_is_info():
    body = [
        "BHT*0022*11*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*PROVIDER NAME*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1*DOE*JANE~",
        "EB*1*IND~",
    ]
    report = validate_interchange(parse_interchange(_build("271", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.271-no-service-type-in-any-eb")
    assert finding.severity == "info"


def test_271_no_service_type_rule_follows_dependent_only_when_nm1_resolves():
    # A code review caught that validation's own re-derived "which loop is
    # the patient" walk treated any present HL03="23" loop as the patient,
    # without checking it had a resolvable NM1 - diverging from the real
    # builder's fallback-to-subscriber behavior. Here the dependent loop
    # has no NM1 (only a DMG), so both conversion and this rule must read
    # the subscriber's own EB segment (which has a resolvable service type)
    # - the rule must NOT fire, since it would if it were still looking at
    # the (EB-less) dependent loop instead.
    body = [
        "BHT*0022*11*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*PROVIDER NAME*****XX*1234567890~",
        "HL*3*2*22*1~",
        "NM1*IL*1*DOE*JANE~",
        "EB*1*IND*30~",
        "HL*4*3*23*0~",
        "DMG*D8*20150601*F~",
    ]
    report = validate_interchange(parse_interchange(_build("271", body)))
    rule_ids = {f.rule_id for f in report.findings}
    assert "edi.271-no-service-type-in-any-eb" not in rule_ids
    assert report.is_valid is True


def test_would_not_convert_is_error():
    body = [
        "HL*1**20*1~",
        "NM1*PR*2*PAYER NAME*****PI*PAYERID001~",
    ]  # no BHT - missing segment on conversion
    report = validate_interchange(parse_interchange(_build("270", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_unsupported_transaction_set_is_info():
    body = ["BHT*0022*13*1*20260812*1200~"]
    report = validate_interchange(parse_interchange(_build("837", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.unsupported-transaction-set")
    assert finding.severity == "info"
    assert report.is_valid is True
    assert report.trigger_event == "837"


def test_empty_interchange_reports_no_transaction_set_info_finding():
    raw = _ISA + "GS*HS*A*B*20260812*1200*1*X*005010X279A1~" + "GE*0*1~" + "IEA*1*000000001~"
    report = validate_interchange(parse_interchange(raw))
    finding = next(f for f in report.findings if f.rule_id == "edi.no-transaction-set")
    assert finding.severity == "info"
    assert report.is_valid is True
    assert report.trigger_event is None


def test_unexpected_convertibility_crash_is_absorbed_into_a_finding(monkeypatch):
    class _ExplodingBuilder:
        transaction_set_id = "270"

        def build_bundle(self, transaction_set):
            raise RuntimeError("boom")

    def _fake_get_transaction_builder(st01):
        return _ExplodingBuilder()

    monkeypatch.setattr("app.edi.registry.get_transaction_builder", _fake_get_transaction_builder)
    report = validate_interchange(parse_interchange(_build("270", _270_BODY)))
    finding = next(f for f in report.findings if f.rule_id == "edi.convertibility-check-failed")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_validate_interchange_never_raises_missing_segment_error_directly():
    # A defensive proof that validate_interchange() itself never lets
    # MissingSegmentError escape - it must always be turned into a finding
    # via edi.would-not-convert.
    body = ["HL*1**20*1~"]
    try:
        validate_interchange(parse_interchange(_build("270", body)))
    except MissingSegmentError:
        pytest.fail("validate_interchange() must not raise MissingSegmentError - it should be a finding")
