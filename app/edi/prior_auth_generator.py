"""Synthetic X12 278 generator - split out of app/edi/generator.py, the
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

# UM01 (Request Category Code) - a representative subset.
_UM01_REQUEST_CATEGORY_CODES = ["HS", "SC", "AR", "IN"]
# UM02 (Certification Type Code): I=Initial, R=Renewal, S=Revised.
_UM02_CERTIFICATION_TYPE_CODES = ["I", "R", "S"]
# (code, description) - the same pool eligibility_generator.py's own EQ/EB
# builders use for the identical X12 Service Type Code external code list
# (UM03 reads the same list) - kept as its own small copy here rather than
# imported, since neither module otherwise depends on the other.
_SERVICE_TYPE_CODES = [
    ("30", "Health Benefit Plan Coverage"),
    ("1", "Medical Care"),
    ("35", "Dental Care"),
    ("88", "Pharmacy"),
    ("98", "Professional (Physician) Visit - Office"),
    ("A6", "Psychotherapy"),
]
# HI qualifier BF = ICD-10-CM Diagnosis (the same qualifier the real
# X12.org example this module's builder was verified against uses).
_HI_DIAGNOSIS_QUALIFIER = "BF"
# HCR01 (Action Code) - every prefix app.edi.prior_auth::
# HCR01_TO_OUTCOME recognizes (A1/A2/A3/A4), plus one deliberately
# unrecognized code ("Z9") to exercise the "completed" fallback.
_HCR01_ACTION_CODES = ["A1", "A2", "A3", "A4", "Z9"]
_HCR03_REASON_CODES = ["93", "197", ""]


def _build_um(rng: random.Random) -> str:
    um01 = rng.choice(_UM01_REQUEST_CATEGORY_CODES)
    um02 = rng.choice(_UM02_CERTIFICATION_TYPE_CODES)
    # UM03 (service type code) populated ~70% of the time - direct fuzz
    # coverage of build_service_type_category's None-vs-coded branches.
    um03 = rng.choice(_SERVICE_TYPE_CODES)[0] if maybe(rng, 0.7) else ""
    return f"UM*{um01}*{um02}*{um03}*12:B~"


def _build_hi(rng: random.Random) -> str:
    count = rng.randint(1, 3)
    codes = rng.sample(ICD10_DIAGNOSIS_CODES, count)
    composites = "*".join(f"{_HI_DIAGNOSIS_QUALIFIER}:{code}" for code in codes)
    return f"HI*{composites}~"


def _build_hcr(rng: random.Random) -> str:
    action_code = rng.choice(_HCR01_ACTION_CODES)
    auth_ref = f"AUTH{random_identifier(rng, digits=6)}" if maybe(rng, 0.8) else ""
    reason_code = rng.choice(_HCR03_REASON_CODES) if action_code in ("A3", "A4") else ""
    return f"HCR*{action_code}*{auth_ref}*{reason_code}~"


def _generate_prior_auth(rng: random.Random, bht02: str) -> str:
    """Shared envelope + HL-hierarchy generation for 278 request/response -
    unlike every other EDI pair in this app, both share the literal
    ST01="278" (see app/edi/prior_auth.py's own module docstring), so
    request vs. response is purely a BHT02 difference, not a body-shape
    one at the envelope level; the two callers below differ only in
    bht02 and whether an HCR segment gets appended."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=0)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)

    envelope_segments = [
        build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HI*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*005010X217~",
    ]
    st_to_hl_segments = [
        f"ST*278*{st_control}~",
        f"BHT*0007*{bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
        "HL*1**20*1~",
        build_org_nm1(rng, "X3", PAYER_NAMES),
        "HL*2*1*21*1~",
    ]
    # Requester (2000B) is an organization ~60% of the time, an individual
    # otherwise - direct fuzz coverage of is_person_entity()'s branch.
    if maybe(rng, 0.6):
        st_to_hl_segments.append(build_org_nm1(rng, "1P", PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(build_person_nm1(rng, "1P", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*3*2*22*1~", build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_dmg(rng, subscriber_sex))

    # A dependent loop (2000D) is present ~40% of the time - direct fuzz
    # coverage of the "patient = dependent when present and NM1 resolves,
    # else subscriber" precedence rule, same as every other EDI family.
    patient_hl_id = "3"
    next_hl_id = 4
    if maybe(rng, 0.4):
        dependent_sex = random_sex(rng)
        st_to_hl_segments += ["HL*4*3*23*1~", build_person_nm1(rng, "QC", dependent_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(build_dmg(rng, dependent_sex))
        patient_hl_id = "4"
        next_hl_id = 5

    st_to_hl_segments.append(f"HL*{next_hl_id}*{patient_hl_id}*EV*0~")
    st_to_hl_segments.append(_build_um(rng))
    if maybe(rng, 0.7):
        st_to_hl_segments.append(_build_hi(rng))
    # A response (BHT02="11") carries an HCR certification decision ~85%
    # of the time, not always - direct fuzz coverage of
    # _build_claim_response()'s own "no HCR -> no ClaimResponse" branch,
    # which would otherwise never be exercised by generated response data.
    if bht02 == "11" and maybe(rng, 0.85):
        st_to_hl_segments.append(_build_hcr(rng))

    return assemble_generated_interchange(
        rng,
        EdiDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control),
        [],
    )


def generate_278_request(rng: random.Random) -> str:
    return _generate_prior_auth(rng, "13")


def generate_278_response(rng: random.Random) -> str:
    return _generate_prior_auth(rng, "11")
