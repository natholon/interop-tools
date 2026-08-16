"""Synthetic X12 837P generator - split out of app/edi/generator.py, the
same file-per-family reorganization that module's own docstring describes."""

import random

from app.edi.generator import (
    ICD10_DIAGNOSIS_CODES,
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

# CLM05-1 (Facility Code Value / Place of Service) - a representative
# subset of the real CMS Place of Service code set (see
# app/edi/claim_837p.py's own _POS_CODE_SYSTEM for the verified canonical
# system this feeds).
_PLACE_OF_SERVICE_CODES = ["11", "21", "22", "23", "02"]
# SV1-01 procedure codes, all under the "HC" qualifier (see claim_837p.py's
# module docstring for the disclosed CPT-vs-HCPCS-Level-II ambiguity this
# qualifier carries).
_PROCEDURE_CODES = ["99213", "99214", "90782", "80053", "85025", "93000"]


def _build_hi_837p(rng: random.Random, count: int) -> str:
    """Unlike 278's own _build_hi (which reuses the same "BF" qualifier for
    every position - a disclosed simplification for that phase), this
    generator distinguishes ABK (principal, position 1) from ABF (other,
    every subsequent position) - the real semantic distinction the 5010 IG
    itself draws, and the one this generator can exercise cleanly since
    837P's own diagnosis-pointer composite (SV1-07) gives a concrete reason
    to care which position is which."""
    codes = rng.sample(ICD10_DIAGNOSIS_CODES, count)
    composites = [f"ABK:{codes[0]}"] + [f"ABF:{code}" for code in codes[1:]]
    return "HI*" + "*".join(composites) + "~"


def _build_sv1(rng: random.Random, num_diagnoses: int) -> str:
    procedure = rng.choice(_PROCEDURE_CODES)
    charge = round(rng.uniform(15, 500), 2)
    # SV1-07: 1-based pointers into HI's own diagnosis order - up to the
    # smaller of _MAX_DIAGNOSIS_POINTERS(4) and however many diagnoses this
    # claim actually carries, so a generated pointer resolves. ~10% of the
    # time, one pointer is deliberately pushed out of range instead - direct
    # fuzz coverage of edi.837p-diagnosis-pointer-unresolved, mirroring
    # ORU's own deliberate ~30% out-of-range OBX-5 fuzzing precedent (a
    # generator that never produces an unresolved pointer would leave that
    # rule permanently untested).
    pointer_count = rng.randint(1, min(4, num_diagnoses)) if num_diagnoses else 0
    pointers_list = sorted(rng.sample(range(1, num_diagnoses + 1), pointer_count)) if pointer_count else []
    if pointers_list and maybe(rng, 0.1):
        pointers_list[-1] = num_diagnoses + rng.randint(1, 3)
    pointers = ":".join(str(p) for p in pointers_list)
    return f"SV1*HC:{procedure}*{charge:.2f}*UN*1***{pointers}~"


def generate_837p(rng: random.Random) -> str:
    """Unlike every earlier EDI generator in this app, the 837P HL chain is
    only 3 levels deep (2000A Billing Provider/2000B Subscriber/optional
    2000C Patient - see app/edi/claim_837p.py's own module docstring for
    why "20"/"22"/"23" mean something different here than in 270/271/278
    despite the numeric coincidence), and the payer NM1*PR is generated as
    a member of the *subscriber* loop, not its own root loop - a genuine
    structural difference from every sibling generator's own envelope
    shape, not a copy-paste of eligibility_generator.py's."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=5)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)

    envelope_segments = [
        build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HC*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*005010X222A2~",
    ]
    st_to_hl_segments = [
        f"ST*837*{st_control}*005010X222A2~",
        f"BHT*0019*00*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}*CH~",
        "HL*1**20*1~",
    ]
    # Billing provider (2000A) is an organization ~60% of the time, an
    # individual (sole proprietor) otherwise - direct fuzz coverage of
    # is_person_entity()'s branch on this loop.
    if maybe(rng, 0.6):
        st_to_hl_segments.append(build_org_nm1(rng, "85", PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(build_person_nm1(rng, "85", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*2*1*22*1~", "SBR*P*******CI~", build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_dmg(rng, subscriber_sex))
    st_to_hl_segments.append(build_org_nm1(rng, "PR", PAYER_NAMES))

    # A patient loop (2000C) is present ~40% of the time - direct fuzz
    # coverage of Claim.patient/Coverage.beneficiary's "patient = the
    # 2000C patient when present and NM1 resolves, else the subscriber"
    # precedence rule, same as every other EDI family's dependent loop.
    if maybe(rng, 0.4):
        patient_sex = random_sex(rng)
        st_to_hl_segments += ["HL*3*2*23*0~", "PAT*19~", build_person_nm1(rng, "QC", patient_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(build_dmg(rng, patient_sex))

    charge = round(rng.uniform(50, 1000), 2)
    facility = rng.choice(_PLACE_OF_SERVICE_CODES)
    claim_id = f"CLM{random_identifier(rng, digits=8)}"
    st_to_hl_segments.append(f"CLM*{claim_id}*{charge:.2f}***{facility}:B:1*Y*A*Y*I~")

    num_diagnoses = rng.randint(1, 3)
    st_to_hl_segments.append(_build_hi_837p(rng, num_diagnoses))

    # A rendering provider (2310B, NM1*82) is present ~60% of the time -
    # direct fuzz coverage of Claim.careTeam's own present/absent branch.
    if maybe(rng, 0.6):
        st_to_hl_segments.append(build_person_nm1(rng, "82", random_sex(rng), include_id=True))

    line_count = rng.randint(1, 3)
    for line_number in range(1, line_count + 1):
        st_to_hl_segments.append(f"LX*{line_number}~")
        st_to_hl_segments.append(_build_sv1(rng, num_diagnoses))
        if maybe(rng, 0.8):
            st_to_hl_segments.append(f"DTP*472*D8*{format_x12_date(now)}~")

    return assemble_generated_interchange(
        rng,
        EdiDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control),
        [],
    )
