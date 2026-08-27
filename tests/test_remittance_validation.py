from app.edi.parser import parse_interchange
from app.edi.validation import validate_interchange

_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260812*1200*^*00501*000000001*0*P*:~"

_835_BODY = [
    "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
    "TRN*1*1512345678*9876543210~",
    "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
    "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
    "CLP*PCN12345*1*500.00*150.00*350.00*MC*PAYERCTRL987*11*1~",
    # The 2100 person loop: without it the claim builds no ClaimResponse,
    # which is itself a finding.
    "NM1*QC*1*DOE*JANE****MI*MEMBER001~",
]


def _build(body: list[str]) -> str:
    se01 = len(body) + 2
    segments = [
        _ISA,
        "GS*HP*SENDERID*RECEIVERID*20260812*1200*1*X*005010X221A1~",
        "ST*835*0001~",
        *body,
        f"SE*{se01}*0001~",
        "GE*1*1~",
        "IEA*1*000000001~",
    ]
    return "".join(segments)


def test_clean_835_produces_no_findings():
    report = validate_interchange(parse_interchange(_build(_835_BODY)))
    assert report.findings == []
    assert report.is_valid is True
    assert report.message_type == "EDI"
    assert report.trigger_event == "835"


def test_835_bpr02_total_mismatch_is_warning():
    body = [
        "BPR*I*999.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
        "CLP*PCN12345*1*500.00*150.00*350.00*MC*PAYERCTRL987*11*1~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.835-bpr02-total-mismatch")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_835_missing_payee_name_is_info():
    body = [
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
        "N1*PE~",
        "CLP*PCN12345*1*500.00*150.00*350.00*MC*PAYERCTRL987*11*1~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.835-missing-payee-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_835_claim_paid_exceeds_charge_is_warning():
    body = [
        "BPR*I*600.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
        "CLP*PCN12345*1*500.00*600.00*0.00*MC*PAYERCTRL987*11*1~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.835-claim-paid-exceeds-charge")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_would_not_convert_is_error_when_bpr_missing():
    body = [
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_835_missing_payer_name_is_info():
    body = [
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR~",
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
        "CLP*PCN12345*1*500.00*150.00*350.00*MC*PAYERCTRL987*11*1~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.835-missing-payer-name")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_835_bpr16_in_future_is_warning():
    body = [
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20991231~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
        "CLP*PCN12345*1*500.00*150.00*350.00*MC*PAYERCTRL987*11*1~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.835-bpr16-in-future")
    assert finding.severity == "warning"
    assert report.is_valid is True


def test_835_non_finite_clp_amounts_do_not_crash_validation():
    # Decimal("NaN")/"Infinity" don't raise InvalidOperation in Python - a
    # malformed CLP03/CLP04 value spelled that way must not crash the
    # paid-exceeds-charge or bpr02-total-mismatch rules (parse_decimal's
    # own .is_finite() guard is what prevents this).
    body = [
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
        "N1*PE*GENERAL HOSPITAL*XX*1234567890~",
        "CLP*PCN12345*1*NaN*Infinity*350.00*MC*PAYERCTRL987*11*1~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    assert not any(f.rule_id == "edi.835-claim-paid-exceeds-charge" for f in report.findings)
    assert not any(f.rule_id == "edi.835-bpr02-total-mismatch" for f in report.findings)


def test_would_not_convert_is_error_when_payee_missing():
    body = [
        "BPR*I*150.00*C*ACH*CCP*01*123456789*DA*9876543210*1512345678**01*987654321*DA*1234509876*20260812~",
        "TRN*1*1512345678*9876543210~",
        "N1*PR*ACME HEALTH PLAN*XV*PAYERID001~",
    ]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_835_claim_without_a_2100_person_loop_is_info():
    # ClaimResponse.patient is 1..1, so a claim naming neither the patient
    # nor the insured builds none - and with it goes the only place CLP03
    # and CLP05 have to live.
    body = [seg for seg in _835_BODY if not seg.startswith("NM1*QC")]
    report = validate_interchange(parse_interchange(_build(body)))
    finding = next(f for f in report.findings if f.rule_id == "edi.835-claim-missing-patient")
    assert finding.severity == "info"
    assert "PCN12345" in finding.message


def test_835_claim_naming_only_the_insured_produces_no_finding():
    # NM1*IL alone is how an 835 says the patient is the subscriber.
    body = [seg.replace("NM1*QC", "NM1*IL") for seg in _835_BODY]
    report = validate_interchange(parse_interchange(_build(body)))
    assert not [f for f in report.findings if f.rule_id == "edi.835-claim-missing-patient"]

