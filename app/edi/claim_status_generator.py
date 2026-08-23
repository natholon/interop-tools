"""Synthetic X12 276/277 generator - split out of app/edi/generator.py, the
same file-per-family reorganization that module's own docstring describes."""

import random

from app.edi.generator import (
    PAYER_NAMES,
    PROVIDER_ORG_NAMES,
    EdiDraft,
    assemble_generated_interchange,
    build_dmg,
    build_isa,
    build_org_nm1,
    build_person_nm1,
    format_x12_date,
    format_x12_time,
)
from app.generators.base import maybe, random_datetime_near_now, random_identifier, random_sex

# (category, status) pairs - a representative subset of X12's Claim Status
# Category Code (STC01-1) / Claim Status Code (STC01-2) external code
# lists, spanning every prefix app.edi.claim_status::
# STC_CATEGORY_PREFIX_TO_TASK_STATUS recognizes (A/P/F/R/E) plus one
# unrecognized prefix ("D0") to exercise the "completed" fallback.
_CLAIM_STATUS_CATEGORIES = [
    ("A1", "1"),
    ("F1", "1"),
    ("F2", "45"),
    ("P1", "1"),
    ("R3", "62"),
    ("E1", "42"),
    ("D0", "1"),
]


def _build_claim_status_group(rng: random.Random, trn01: str, now, include_status: bool) -> list[str]:
    """One TRN-led claim-status group - TRN (always present, trace number),
    optional REF (payer claim control number), and - 277 only - STC
    (claim status). Mirrors app.edi.claim_status's own reading of this
    exact group shape."""
    trace = f"TRACE{random_identifier(rng, digits=6)}"
    segments = [f"TRN*{trn01}*{trace}*{random_identifier(rng, digits=10)}~"]
    if include_status:
        category, status = rng.choice(_CLAIM_STATUS_CATEGORIES)
        segments.append(f"STC*{category}:{status}:PR*{format_x12_date(now)}~")
    if maybe(rng, 0.7):
        segments.append(f"REF*1K*{random_identifier(rng, digits=8)}~")
    return segments


def _generate_claim_status(rng: random.Random, st01: str, bht02: str, include_status: bool) -> str:
    """Shared envelope + HL-hierarchy generation for 276/277 - a deeper
    chain than 270/271's (2000A payer / 2000B information receiver / 2000C
    provider / 2000D subscriber / 2000E dependent), reusing the same
    envelope/NM1/DMG segment builders eligibility_generator.py's own
    _generate_eligibility already uses, but not that function itself - the
    extra 2000C provider loop makes this a genuinely different shape, not a
    parameterization of the same one (see app/edi/claim_status.py's own
    module docstring for the same "different HL03 table, don't force a
    shared walk" reasoning)."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=0)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)
    trn01 = "2" if include_status else "1"  # TRN01: 1=current trace, 2=referenced (echoed back)

    envelope_segments = [
        build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HR*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*005010X212~",
    ]
    st_to_hl_segments = [
        f"ST*{st01}*{st_control}~",
        f"BHT*0010*{bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
        "HL*1**20*1~",
        build_org_nm1(rng, "PR", PAYER_NAMES),
        "HL*2*1*21*1~",
    ]
    # Information receiver (2000B) is an organization ~70% of the time, an
    # individual otherwise - direct fuzz coverage of is_person_entity()'s
    # branch on both the receiver and provider loops.
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_org_nm1(rng, "41", PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(build_person_nm1(rng, "41", random_sex(rng), include_id=True))

    st_to_hl_segments.append("HL*3*2*19*1~")
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_org_nm1(rng, "1P", PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(build_person_nm1(rng, "1P", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*4*3*22*1~", build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    st_to_hl_segments += _build_claim_status_group(rng, trn01, now, include_status)

    # A dependent loop (2000E) is present ~40% of the time - direct fuzz
    # coverage of the builder's "walk both patient loops, one Task per
    # claim group" behavior (not a precedence rule like 270/271's, since
    # 276/277 can report on claims for both the subscriber and a
    # dependent within the same transaction set).
    if maybe(rng, 0.4):
        dependent_sex = random_sex(rng)
        st_to_hl_segments += ["HL*5*4*23*0~", build_person_nm1(rng, "QC", dependent_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(build_dmg(rng, dependent_sex))
        st_to_hl_segments += _build_claim_status_group(rng, trn01, now, include_status)

    return assemble_generated_interchange(
        rng,
        EdiDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control),
        [],
    )


def generate_276(rng: random.Random) -> str:
    return _generate_claim_status(rng, "276", "13", include_status=False)


def generate_277(rng: random.Random) -> str:
    return _generate_claim_status(rng, "277", "08", include_status=True)
