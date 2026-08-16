from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260815*1200*^*00501*000000001*0*P*:~"


def _sv3(procedure_composite: str = "AD:D1110", charge: str = "89.93", quantity: str = "", pointers: str = "") -> str:
    # Built via a positionally-indexed fields list, not hand-counted
    # asterisks - see claim_837d_generator.py's own _build_sv3 for why.
    fields = [""] * 11
    fields[0] = procedure_composite
    fields[1] = charge
    fields[5] = quantity
    fields[10] = pointers
    return "SV3*" + "*".join(fields) + "~"


# A "clean" 837D body carrying the claim-level DTP*472 fallback shape (the
# real X12.org example claim_837d.py was verified against has no per-line
# dates at all - see that module's own docstring) rather than 837P/837I's
# always-per-line shape.
_837D_BODY = [
    "BHT*0019*00*0123*20260815*1023*CH~",
    "HL*1**20*1~",
    "NM1*85*1*JONES*BEN****XX*1999996666~",
    "HL*2*1*22*0~",
    "NM1*IL*1*SMITH*JANE****MI*111223333~",
    "NM1*PR*2*DELTA DENTAL****PI*00435~",
    "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
    "HI*ABK:K0201~",
    "DTP*472*D8*20260810~",
    "LX*1~",
    _sv3(quantity="1", pointers="1"),
    "TOO*JP*3*O:B~",
]


def _build(body: list[str]) -> str:
    se01 = len(body) + 2
    segments = [
        _ISA,
        "GS*HC*SENDERID*RECEIVERID*20260815*1200*1*X*005010X224A2~",
        "ST*837*0001*005010X224A2~",
        *body,
        f"SE*{se01}*0001~",
        "GE*1*1~",
        "IEA*1*000000001~",
    ]
    return "".join(segments)


def test_clean_837d_produces_no_findings():
    report = validate_interchange(parse_interchange(_build(_837D_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "837"


def test_837d_missing_subscriber_name_is_info():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837d-missing-subscriber-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837d_missing_diagnosis_is_info():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
        # No HI segment at all - the common real-world dental shape.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837d-missing-diagnosis")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_837d_missing_diagnosis_is_info_when_only_non_diagnosis_hi_present():
    # Mirrors 837I's identical regression - an HI segment can be present
    # but carry only occurrence/value/condition codes (BH/BE/BG), none of
    # which count as a diagnosis (see the shared
    # common.py::iter_diagnosis_hi_segments filter).
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
        "HI*BG:09~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837d-missing-diagnosis")
    assert finding.severity == "info"


def test_837d_service_date_in_future_is_warning_via_claim_level_fallback():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
        "HI*ABK:K0201~",
        "DTP*472*D8*20991231~",  # claim-level, in the future
        "LX*1~",
        _sv3(quantity="1", pointers="1"),
        # No line-level DTP - the line must fall back to the claim-level one.
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.837d-service-date-in-future")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_837d_line_level_dtp_takes_precedence_over_future_claim_level_dtp():
    # Dental-specific twist neither 837P nor 837I has: a line's own DTP*472
    # must win over the claim-level fallback even when the claim-level one
    # would otherwise be flagged - proving resolve_line_dtp_raw_date's own
    # (dtp, claim_level_dtp) precedence order, not just that a fallback
    # exists at all.
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
        "HI*ABK:K0201~",
        "DTP*472*D8*20991231~",  # claim-level, in the future
        "LX*1~",
        _sv3(quantity="1", pointers="1"),
        "DTP*472*D8*20260810~",  # this line's own DTP, safely in the past
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    assert "edi.837d-service-date-in-future" not in {f.rule_id for f in report.findings}
    assert report.is_valid is True


def test_837d_wrongly_qualified_dtp_does_not_trigger_service_date_rule():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
        "HI*ABK:K0201~",
        "DTP*463*D8*20991231~",  # Prescription Date, not Service Date
        "LX*1~",
        _sv3(quantity="1", pointers="1"),
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    assert "edi.837d-service-date-in-future" not in {f.rule_id for f in report.findings}


def test_would_not_convert_is_error_when_clm_missing():
    body = [
        "BHT*0019*00*0123*20260815*1023*CH~",
        "HL*1**20*1~",
        "NM1*85*1*JONES*BEN****XX*1999996666~",
        "HL*2*1*22*0~",
        "NM1*IL*1*SMITH*JANE****MI*111223333~",
        "NM1*PR*2*DELTA DENTAL****PI*00435~",
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
        "CLM*756048Q*89.93***11:B:1*Y*A*Y*I~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_837p_and_837i_rules_do_not_fire_for_837d_transaction():
    # The 837 dispatch branches on ST03 - an 837D transaction (ST03
    # containing "X224") must never run 837P's or 837I's own rule sets.
    report = validate_interchange(parse_interchange(_build(_837D_BODY)))
    rule_ids = {f.rule_id for f in report.findings}
    assert not any(rule_id.startswith("edi.837p-") or rule_id.startswith("edi.837i-") for rule_id in rule_ids)
