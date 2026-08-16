from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260815*1200*^*00501*000000001*0*P*:~"

_837I_BODY = [
    "BHT*0019*00*0123*20260815*1023*CH~",
    "HL*1**20*1~",
    "NM1*85*1*JONES*BEN****XX*1999996666~",
    "HL*2*1*22*0~",
    "NM1*IL*1*SMITH*JANE****MI*111223333~",
    "NM1*PR*2*MEDICARE B****PI*00435~",
    "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
    "CL1*3**01~",
    "HI*ABK:J209~",
    "LX*1~",
    "SV2*0305*HC:85025*13.39*UN*1~",
    "DTP*472*D8*20260810~",
]


def _build(body: list[str]) -> str:
    se01 = len(body) + 2
    segments = [
        _ISA,
        "GS*HC*SENDERID*RECEIVERID*20260815*1200*1*X*005010X223A2~",
        "ST*837*0001*005010X223A2~",
        *body,
        f"SE*{se01}*0001~",
        "GE*1*1~",
        "IEA*1*000000001~",
    ]
    return "".join(segments)


def test_clean_837i_produces_no_findings():
    report = validate_interchange(parse_interchange(_build(_837I_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "837"


def test_837i_missing_subscriber_name_is_info():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837i-missing-subscriber-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837i_missing_diagnosis_is_info():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
        # No HI segment at all.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837i-missing-diagnosis")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837i_missing_diagnosis_is_info_when_only_non_diagnosis_hi_present():
    # An HI segment can be present but carry only occurrence/value/
    # condition codes (BH/BE/BG) - none of those count as a diagnosis, so
    # this must still fire the missing-diagnosis rule, not be fooled by
    # HI segment presence alone.
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
        "HI*BG:09~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837i-missing-diagnosis")
    assert finding.severity == "info"


def test_837i_missing_discharge_status_is_info_when_cl1_absent():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
        # No CL1 segment.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837i-missing-discharge-status")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837i_missing_discharge_status_is_info_when_cl103_unresolvable():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
        "CL1*3*~",  # CL103 empty
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837i-missing-discharge-status")
    assert finding.severity == "info"


def test_837i_service_date_in_future_is_warning():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
        "CL1*3**01~",
        "HI*ABK:J209~",
        "LX*1~",
        "SV2*0305*HC:85025*13.39*UN*1~",
        "DTP*472*D8*20991231~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837i-service-date-in-future")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_837i_wrongly_qualified_future_dtp_does_not_trigger_service_date_rule():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
        "CL1*3**01~",
        "HI*ABK:J209~",
        "LX*1~",
        "SV2*0305*HC:85025*13.39*UN*1~",
        "DTP*463*D8*20991231~",  # Prescription Date, not Service Date
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    assert "edi.837i-service-date-in-future" not in {f.rule_id for f in report.findings}


def test_would_not_convert_is_error_when_clm_missing():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*MEDICARE B****PI*00435~",
        # No CLM segment.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_would_not_convert_is_error_when_payer_missing():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        # No NM1*PR payer segment.
        "CLM*756048Q*89.93***14:A:1**A*Y*Y~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_837p_rules_do_not_fire_for_837i_transaction():
    # The 837 dispatch branches on ST03 - an 837I transaction (ST03
    # containing "X223") must never run 837P's own rule set.
    report = validate_interchange(parse_interchange(_build(_837I_BODY)))
    rule_ids = {f.rule_id for f in report.findings}
    assert not any(rule_id.startswith("edi.837p-") for rule_id in rule_ids)
