"""Synthetic X12 270/271 generator - split out of app/edi/generator.py, the
same file-per-family reorganization that module's own docstring describes.
Shared primitives (`build_isa`, `build_org_nm1`, `build_person_nm1`,
`build_dmg`, `EdiDraft`, `assemble_generated_interchange`,
`PAYER_NAMES`/`PROVIDER_ORG_NAMES`) come from `app.edi.generator`."""

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

# (code, description) - a representative subset of the X12 Service Type
# Code external code list (EQ01/EB03) - see app/edi/common.py's
# SERVICE_TYPE_CODE_SYSTEM for why these are carried as a disclosed local
# system rather than an official FHIR-canonical one.
_SERVICE_TYPE_CODES = [
    ("30", "Health Benefit Plan Coverage"),
    ("1", "Medical Care"),
    ("35", "Dental Care"),
    ("88", "Pharmacy"),
    ("98", "Professional (Physician) Visit - Office"),
    ("A6", "Psychotherapy"),
]
# EB01 (Eligibility/Benefit Information Code) - the subset app.edi.
# eligibility_271._EB01_EXCLUDED_MAP actually recognizes.
_EB01_CODES = ["1", "6", "I"]
_NETWORK_INDICATORS = ["Y", "N", "U"]
_GS08_VERSION = "005010X279A1"


def _build_eq(rng: random.Random) -> str:
    code, _ = rng.choice(_SERVICE_TYPE_CODES)
    return f"EQ*{code}~"


def _build_dtp(now) -> str:
    return f"DTP*291*D8*{format_x12_date(now)}~"


def _build_eb(rng: random.Random) -> str:
    code, _ = rng.choice(_SERVICE_TYPE_CODES)
    eb01 = rng.choice(_EB01_CODES)
    network = rng.choice(_NETWORK_INDICATORS)
    plan_description = f"{rng.choice(PAYER_NAMES)} Plan"
    return f"EB*{eb01}*IND*{code}*HM*{plan_description}*******{network}~"


def _build_aaa(rng: random.Random) -> str:
    # AAA01 "Y"/"N" (Valid Request Y/N), AAA03 a disclosed small subset of
    # reject-reason codes, AAA04 Follow-up Action Code.
    reject_code = rng.choice(("15", "42", "72"))
    return f"AAA*N*{rng.choice(('15', '42'))}*{reject_code}*C~"


def _generate_eligibility(rng: random.Random, st01: str, bht02: str) -> EdiDraft:
    """Shared envelope + HL-hierarchy (2000A payer / 2000B provider /
    2000C subscriber / optional 2000D dependent) generation for both 270
    and 271 - the two transaction sets are identical through this point,
    diverging only in their patient-loop leader segment (EQ vs EB) and
    271's optional AAA rejection, both appended by the caller-specific
    branch below."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=5)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)

    envelope_segments = [
        build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HS*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*{_GS08_VERSION}~",
    ]
    st_to_hl_segments = [
        f"ST*{st01}*{st_control}~",
        f"BHT*0022*{bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
        "HL*1**20*1~",
        build_org_nm1(rng, "PR", PAYER_NAMES),
        "HL*2*1*21*1~",
    ]
    # Provider (2000B) is an organization ~70% of the time, an individual
    # practitioner otherwise - direct fuzz coverage of build_bundle()'s
    # is_person_entity() branch on both mapper sides.
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_org_nm1(rng, "1P", PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(build_person_nm1(rng, "1P", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*3*2*22*1~", build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_dmg(rng, subscriber_sex))

    # A dependent loop (2000D) is present ~40% of the time - direct fuzz
    # coverage of both mappers' "patient = dependent when present, else
    # subscriber" precedence rule.
    if maybe(rng, 0.4):
        dependent_sex = random_sex(rng)
        st_to_hl_segments += ["HL*4*3*23*0~", build_person_nm1(rng, "QC", dependent_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(build_dmg(rng, dependent_sex))

    return EdiDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control)


def generate_270(rng: random.Random) -> str:
    draft = _generate_eligibility(rng, "270", "13")
    body = [_build_eq(rng)]
    if maybe(rng):
        body.append(_build_dtp(draft.now))
    if maybe(rng, 0.3):
        body.append(_build_eq(rng))
    return assemble_generated_interchange(rng, draft, body)


def generate_271(rng: random.Random) -> str:
    draft = _generate_eligibility(rng, "271", "11")
    body = [_build_eb(rng)]
    if maybe(rng, 0.3):
        body.append(_build_eb(rng))
    # A rejection (AAA01="N") occurs ~15% of the time - direct fuzz
    # coverage of _resolve_outcome_and_disposition()'s "error" branch,
    # which would otherwise never be exercised by generated data.
    if maybe(rng, 0.15):
        body.append(_build_aaa(rng))
    return assemble_generated_interchange(rng, draft, body)
