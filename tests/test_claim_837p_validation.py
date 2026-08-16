from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260813*1200*^*00501*000000001*0*P*:~"

_837P_BODY = [
    "BHT*0019*00*0123*20260813*1023*CH~",
    "HL*1**20*1~",
    "NM1*85*1*KILDARE*BEN****XX*1999996666~",
    "HL*2*1*22*0~",
    "NM1*IL*1*SMITH*JANE****MI*111223333~",
    "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
    "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
    "HI*ABK:J209*ABF:E119~",
    "LX*1~",
    "SV1*HC:99213*43*UN*1***1:2~",
    "DTP*472*D8*20260810~",
]


def _build(body: list[str]) -> str:
    se01 = len(body) + 2
    segments = [
        _ISA,
        "GS*HC*SENDERID*RECEIVERID*20260813*1200*1*X*005010X222A2~",
        "ST*837*0001*005010X222A2~",
        *body,
        f"SE*{se01}*0001~",
        "GE*1*1~",
        "IEA*1*000000001~",
    ]
    return "".join(segments)


def test_clean_837p_produces_no_findings():
    report = validate_interchange(parse_interchange(_build(_837P_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "837"


def test_837p_missing_subscriber_name_is_info():
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1~",
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837p-missing-subscriber-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837p_missing_diagnosis_is_info():
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
        # No HI segment.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837p-missing-diagnosis")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837p_service_date_in_future_is_warning():
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
        "HI*ABK:J209~",
        "LX*1~",
        "SV1*HC:99213*43*UN*1***1~",
        "DTP*472*D8*20991231~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837p-service-date-in-future")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_837p_wrongly_qualified_future_dtp_does_not_trigger_service_date_rule():
    # Matches claim_837p.py's own DTP01-qualifier fix - a future-dated DTP
    # with a qualifier other than "472" (Service Date) must not trigger
    # edi.837p-service-date-in-future, since the real converter would
    # never have read it as the service date either.
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
        "HI*ABK:J209~",
        "LX*1~",
        "SV1*HC:99213*43*UN*1***1~",
        "DTP*463*D8*20991231~",  # Prescription Date, not Service Date
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    assert "edi.837p-service-date-in-future" not in {f.rule_id for f in report.findings}


def test_837p_diagnosis_pointer_unresolved_is_info():
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
        "HI*ABK:J209~",
        "LX*1~",
        "SV1*HC:99213*43*UN*1***1:9~",  # pointer 9 doesn't resolve - only 1 diagnosis
        "DTP*472*D8*20260810~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837p-diagnosis-pointer-unresolved")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837p_resolved_diagnosis_pointer_produces_no_finding():
    report = validate_interchange(parse_interchange(_build(_837P_BODY)))
    assert "edi.837p-diagnosis-pointer-unresolved" not in {f.rule_id for f in report.findings}


def test_would_not_convert_is_error_when_clm_missing():
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~",
        # No CLM segment.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_would_not_convert_is_error_when_payer_missing():
    body = [
        "BHT*0019*00*0123*20260813*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*KILDARE*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        # No NM1*PR payer segment.
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False
