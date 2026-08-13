from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260812*1200*^*00501*000000001*0*P*:~"

_278_REQUEST_BODY = [
    "BHT*0007*13*B56789*20260812*1430~",
    "HL*1**20*1~",
    "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~",
    "HL*2*1*21*1~",
    "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
    "HL*3*2*22*0~",
    "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~",
    "HL*4*3*EV*0~",
    "UM*HS*I**12:B~",
]


def _build(body: list[str]) -> str:
    se01 = len(body) + 2
    segments = [
        _ISA,
        "GS*HI*SENDERID*RECEIVERID*20260812*1200*1*X*005010X217~",
        "ST*278*0001~",
        *body,
        f"SE*{se01}*0001~",
        "GE*1*1~",
        "IEA*1*000000001~",
    ]
    return "".join(segments)


def test_clean_278_request_produces_no_findings():
    report = validate_interchange(parse_interchange(_build(_278_REQUEST_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "278"


def test_278_missing_subscriber_name_is_info():
    body = [
        "BHT*0007*13*B56789*20260812*1430~",
        "HL*1**20*1~",
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1~",
        "HL*4*3*EV*0~",
        "UM*HS*I**12:B~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.278-missing-subscriber-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_278_response_missing_hcr_is_info():
    body = [
        "BHT*0007*11*A12345*20260812*1102~",
        "HL*1**20*1~",
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~",
        "HL*4*3*EV*0~",
        "UM*HS*I**12:B~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.278-response-missing-hcr")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_278_request_never_triggers_missing_hcr_rule():
    # BHT02="13" (request) - the missing-HCR rule is response-only and
    # must not fire just because a request naturally has no HCR yet.
    report = validate_interchange(parse_interchange(_build(_278_REQUEST_BODY)))
    assert "edi.278-response-missing-hcr" not in {f.rule_id for f in report.findings}


def test_278_unrecognized_hcr_action_code_is_info():
    body = [
        "BHT*0007*11*A12345*20260812*1102~",
        "HL*1**20*1~",
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~",
        "HL*4*3*EV*0~",
        "UM*HS*I**12:B~",
        "HCR*Z9~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.278-unrecognized-hcr-action-code")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_278_recognized_hcr_action_code_produces_no_unrecognized_finding():
    body = [
        "BHT*0007*11*A12345*20260812*1102~",
        "HL*1**20*1~",
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~",
        "HL*4*3*EV*0~",
        "UM*HS*I**12:B~",
        "HCR*A1*AUTH0001~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    assert "edi.278-unrecognized-hcr-action-code" not in {f.rule_id for f in report.findings}


def test_would_not_convert_is_error_when_patient_event_loop_missing():
    body = [
        "BHT*0007*13*B56789*20260812*1430~",
        "HL*1**20*1~",
        "NM1*X3*2*ACME HEALTH PLAN*****46*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*3*2*22*0~",
        "NM1*IL*1*DOE*JANE*****MI*MEMBERID001~",
        # No 2000E Patient Event loop.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False
