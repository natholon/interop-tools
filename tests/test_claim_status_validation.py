from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260812*1200*^*00501*000000001*0*P*:~"

_276_BODY = [
    "BHT*0010*13*10001234*20260812*1200~",
    "HL*1**20*1~",
    "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
    "HL*2*1*21*1~",
    "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
    "HL*3*2*19*1~",
    "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
    "HL*4*3*22*0~",
    "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
    "TRN*1*TRACE0001*1512345678~",
]


def _build(st01: str, body: list[str]) -> str:
    se01 = len(body) + 2
    segments = [
        _ISA,
        "GS*HR*SENDERID*RECEIVERID*20260812*1200*1*X*005010X212~",
        f"ST*{st01}*0001~",
        *body,
        f"SE*{se01}*0001~",
        "GE*1*1~",
        "IEA*1*000000001~",
    ]
    return "".join(segments)


def test_clean_276_produces_no_findings():
    report = validate_interchange(parse_interchange(_build("276", _276_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "276"


def test_276_missing_subscriber_name_is_info():
    body = [
        "BHT*0010*13*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        "HL*3*2*19*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*4*3*22*0~",
        "NM1*IL*1~",
        "TRN*1*TRACE0001*1512345678~",
    ]
    report = validate_interchange(parse_interchange(_build("276", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.276-missing-subscriber-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_277_unrecognized_status_category_is_info():
    body = [
        "BHT*0010*08*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        "HL*3*2*19*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*4*3*22*0~",
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
        "TRN*2*TRACE0001*1512345678~",
        "STC*Z9:1:PR*20260805~",
    ]
    report = validate_interchange(parse_interchange(_build("277", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.277-unrecognized-status-category")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_277_recognized_status_category_produces_no_unrecognized_finding():
    body = [
        "BHT*0010*08*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        "HL*3*2*19*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*4*3*22*0~",
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
        "TRN*2*TRACE0001*1512345678~",
        "STC*F1:1:PR*20260805~",
    ]
    report = validate_interchange(parse_interchange(_build("277", body)))
    assert "edi.277-unrecognized-status-category" not in {f.rule_id for f in report.findings}


def test_dependent_loop_without_nm1_stc_is_not_reported():
    # A code review caught that resolve_claim_status_loops originally
    # returned an NM1-less 2000E dependent loop unconditionally, so this
    # module's own rules (_iter_claim_status_stc in particular) could see
    # an unrecognized STC category from a dependent loop the real builder
    # silently drops entirely (no NM1 -> no Patient, no Task). The
    # unrecognized-category "Z9" here must NOT produce a finding, since
    # after the fix the dependent loop is treated as if it weren't there -
    # matching what build_bundle() actually does (see the paired
    # regression in test_claim_status_mapping.py).
    body = [
        "BHT*0010*08*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        "HL*3*2*19*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*4*3*22*1~",
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
        "TRN*2*TRACE0001*1512345678~",
        "STC*F1:1:PR*20260805~",
        "HL*5*4*23*0~",
        "DMG*D8*20150615*M~",
        "TRN*2*TRACE0002*1512345678~",
        "STC*Z9:1:PR*20260805~",
    ]
    report = validate_interchange(parse_interchange(_build("277", body)))
    assert "edi.277-unrecognized-status-category" not in {f.rule_id for f in report.findings}
    assert report.is_valid is True


def test_277_status_date_in_future_is_warning():
    body = [
        "BHT*0010*08*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        "HL*3*2*19*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*4*3*22*0~",
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
        "TRN*2*TRACE0001*1512345678~",
        "STC*F1:1:PR*20990101~",
    ]
    report = validate_interchange(parse_interchange(_build("277", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.277-status-date-in-future")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_would_not_convert_is_error_when_no_claim_status_entry():
    body = [
        "BHT*0010*13*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*1~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        "HL*3*2*19*1~",
        "NM1*1P*2*GENERAL HOSPITAL*****XX*1234567890~",
        "HL*4*3*22*0~",
        "NM1*IL*1*DOE*JANE****MI*MEMBERID001~",
        # No TRN - nothing to report status for.
    ]
    report = validate_interchange(parse_interchange(_build("276", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_would_not_convert_is_error_when_provider_loop_missing():
    body = [
        "BHT*0010*13*10001234*20260812*1200~",
        "HL*1**20*1~",
        "NM1*PR*2*ACME HEALTH PLAN*****PI*PAYERID001~",
        "HL*2*1*21*0~",
        "NM1*41*2*SUBMITTER CLINIC*****46*SUB001~",
        # No 2000C provider loop, unlike 270/271's shallower chain.
    ]
    report = validate_interchange(parse_interchange(_build("276", body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False
