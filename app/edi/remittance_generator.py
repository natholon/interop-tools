"""Synthetic X12 835 generator - split out of app/edi/generator.py, the
same file-per-family reorganization that module's own docstring describes."""

import random
from decimal import Decimal

from app.edi.generator import (
    PAYER_NAMES,
    PROVIDER_ORG_NAMES,
    EdiDraft,
    assemble_generated_interchange,
    build_dmg,
    build_isa,
    build_person_nm1,
    format_x12_date,
    format_x12_time,
)
from app.generators.base import maybe, random_datetime_near_now, random_identifier, random_sex

# CLP02 (Claim Status Code) - a representative subset (1=Processed as
# Primary, 2=Secondary, 3=Tertiary, 4=Denied, 22=Reversal of Previous
# Payment).
_CLP_STATUS_CODES = ["1", "2", "3", "4", "22"]
# CAS01 (Claim Adjustment Group Code) - CO=Contractual Obligation,
# PR=Patient Responsibility, OA=Other Adjustment, PI=Payer Initiated,
# CR=Correction.
_CAS_GROUP_CODES = ["CO", "PR", "OA", "PI", "CR"]
_CAS_REASON_CODES = ["45", "96", "97", "1", "2"]
# CLP06 Claim Filing Indicator (X12 element 1032) - MC Medicaid, MB
# Medicare Part B, CI Commercial, HM HMO, BL Blue Cross/Blue Shield.
_CLAIM_FILING_INDICATORS = ["MC", "MB", "CI", "HM", "BL"]


def _build_clp(rng: random.Random) -> tuple[list[str], Decimal]:
    """One CLP-led claim-payment group, with an optional CAS adjustment -
    returns its own segments plus the paid amount (CLP04), so the caller
    can sum every claim's paid amount into BPR02 (the payment total),
    keeping the generated 835 internally consistent the same way a real
    one is."""
    claim_id = f"PCN{random_identifier(rng, digits=5)}"
    status = rng.choice(_CLP_STATUS_CODES)
    charge = Decimal(str(round(rng.uniform(50, 1000), 2)))
    paid_fraction = Decimal(str(round(rng.uniform(0, 1), 2)))
    paid = (charge * paid_fraction).quantize(Decimal("0.01"))
    responsibility = (charge - paid).quantize(Decimal("0.01"))
    segments = [
        f"CLP*{claim_id}*{status}*{charge:.2f}*{paid:.2f}*{responsibility:.2f}*"
        f"{rng.choice(_CLAIM_FILING_INDICATORS)}*"
        f"PAYERCTRL{random_identifier(rng, digits=6)}*11*1~"
    ]
    # The 2100 person loop. QC names the patient; IL alone is how an 835
    # says the patient is the subscriber, and only one of them builds a
    # ClaimResponse in either case - so both branches need generating, and
    # a claim with neither has to occur too, since that is the one shape
    # that produces no ClaimResponse at all.
    if maybe(rng, 0.85):
        sex = random_sex(rng)
        entity_code = "QC" if maybe(rng, 0.75) else "IL"
        segments.append(build_person_nm1(rng, entity_code, sex, include_id=True))
        if maybe(rng, 0.5):
            segments.append(build_dmg(rng, sex))
    # A CAS adjustment occurs ~80% of the time - direct fuzz coverage of
    # group_by_leader's CLP->CAS member association (deliberately not
    # mapped to any FHIR field this phase, see the module docstring, but
    # still exercised so a future SVC/CAS-nested-STC-style bug in the
    # leader/member walk itself would be caught).
    if maybe(rng, 0.8):
        group = rng.choice(_CAS_GROUP_CODES)
        reason = rng.choice(_CAS_REASON_CODES)
        segments.append(f"CAS*{group}*{reason}*{responsibility:.2f}~")
    return segments, paid


def _build_n1(rng: random.Random, entity_code: str, names_pool: list[str], id_qualifier: str) -> str:
    name = rng.choice(names_pool)
    identifier = random_identifier(rng, digits=8)
    return f"N1*{entity_code}*{name}*{id_qualifier}*{identifier}~"


def generate_835(rng: random.Random) -> str:
    """Unlike every other EDI generator in this app, 835 has no HL
    hierarchy and no BHT segment (see app/edi/remittance_835.py's own
    module docstring) - the envelope + N1 header pair + repeating CLP/CAS
    body is built directly, still reusing EdiDraft/assemble_generated_
    interchange for the generic envelope/trailer-count machinery."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=0)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"

    # Claims are built first so BPR02 (payment total) can be the real sum
    # of every claim's own paid amount - the same "internally consistent,
    # not just individually valid" discipline every other generator here
    # follows for its own header/body relationship.
    body: list[str] = []
    total_paid = Decimal("0.00")
    for _ in range(rng.randint(1, 3)):
        clp_segments, paid = _build_clp(rng)
        body.extend(clp_segments)
        total_paid += paid

    trace_number = random_identifier(rng, digits=10)
    envelope_segments = [
        build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HP*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*005010X221A1~",
    ]
    st_to_hl_segments = [
        f"ST*835*{st_control}~",
        f"BPR*I*{total_paid:.2f}*C*ACH*CCP*01*{random_identifier(rng, digits=9)}*DA*"
        f"{random_identifier(rng, digits=10)}*{trace_number}**01*{random_identifier(rng, digits=9)}*DA*"
        f"{random_identifier(rng, digits=10)}*{format_x12_date(now)}~",
        f"TRN*1*{trace_number}*9876543210~",
        _build_n1(rng, "PR", PAYER_NAMES, "XV"),
        _build_n1(rng, "PE", PROVIDER_ORG_NAMES, "XX"),
    ]

    return assemble_generated_interchange(
        rng,
        EdiDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control),
        body,
    )
