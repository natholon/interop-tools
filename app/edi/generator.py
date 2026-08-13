"""Synthetic X12 270/271 generator - the app/edi/ mirror of
app/cda/generator.py and the HL7v2 generators in app/generators/. Built via
positional string-joins per segment (X12 is delimited text like HL7v2, not
tree-built like CDA's XML).

Reuses app.generators.base's format-agnostic primitives directly (maybe(),
random_person_name(), random_sex(), random_identifier(),
random_datetime_near_now()) rather than re-deriving name pools; net-new
here is only format_x12_date()/format_x12_time() (X12 splits date and time
into separate elements, unlike HL7's single concatenated TS field) and a
small Service-Type-Code/payer-name/provider-name pool.

Self-checked via parse_interchange() before returning, mirroring every
other generator's parse-back self-check - a generator bug should raise
EdiParseError, not return broken X12 text."""

import random

from app.edi.parser import parse_interchange
from app.generators.base import maybe, random_datetime_near_now, random_identifier, random_person_name, random_sex

_PAYER_NAMES = [
    "ACME HEALTH PLAN",
    "BLUEHARBOR INSURANCE",
    "SUMMIT HEALTH PARTNERS",
    "PINECREST MUTUAL",
    "HORIZON BENEFIT GROUP",
]
_PROVIDER_ORG_NAMES = [
    "GENERAL HOSPITAL",
    "RIVERVIEW CLINIC",
    "MAINSTREET MEDICAL GROUP",
    "LAKESIDE FAMILY PRACTICE",
    "CITY HEALTH CENTER",
]
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


def format_x12_date(dt) -> str:
    return dt.strftime("%Y%m%d")


def format_x12_time(dt) -> str:
    return dt.strftime("%H%M")


def _build_isa(control_number: str, sender_id: str, receiver_id: str, dt) -> str:
    fields = [
        "00",
        " " * 10,
        "00",
        " " * 10,
        "ZZ",
        sender_id.ljust(15)[:15],
        "ZZ",
        receiver_id.ljust(15)[:15],
        format_x12_date(dt)[2:8],  # ISA09 is 2-digit-year YYMMDD, unlike every other X12 date field
        format_x12_time(dt),
        "^",
        "00501",
        control_number,
        "0",
        "P",
        ":",
    ]
    return "ISA*" + "*".join(fields) + "~"


def _build_org_nm1(rng: random.Random, entity_code: str, names_pool: list[str]) -> str:
    name = rng.choice(names_pool)
    identifier = random_identifier(rng, digits=8)
    return f"NM1*{entity_code}*2*{name}*****XX*{identifier}~"


def _build_person_nm1(rng: random.Random, entity_code: str, sex: str, include_id: bool) -> str:
    family, given = random_person_name(rng, sex=sex)
    if include_id:
        identifier = random_identifier(rng, digits=8)
        return f"NM1*{entity_code}*1*{family}*{given}****MI*{identifier}~"
    return f"NM1*{entity_code}*1*{family}*{given}~"


def _build_dmg(rng: random.Random, sex: str) -> str:
    dob = random_datetime_near_now(rng, min_days=-365 * 80, max_days=-365)
    return f"DMG*D8*{format_x12_date(dob)}*{sex}~"


def _build_eq(rng: random.Random) -> str:
    code, _ = rng.choice(_SERVICE_TYPE_CODES)
    return f"EQ*{code}~"


def _build_dtp(now) -> str:
    return f"DTP*291*D8*{format_x12_date(now)}~"


def _build_eb(rng: random.Random) -> str:
    code, _ = rng.choice(_SERVICE_TYPE_CODES)
    eb01 = rng.choice(_EB01_CODES)
    network = rng.choice(_NETWORK_INDICATORS)
    plan_description = f"{rng.choice(_PAYER_NAMES)} Plan"
    return f"EB*{eb01}*IND*{code}*HM*{plan_description}*******{network}~"


def _build_aaa(rng: random.Random) -> str:
    # AAA01 "Y"/"N" (Valid Request Y/N), AAA03 a disclosed small subset of
    # reject-reason codes, AAA04 Follow-up Action Code.
    reject_code = rng.choice(("15", "42", "72"))
    return f"AAA*N*{rng.choice(('15', '42'))}*{reject_code}*C~"


class _EligibilityDraft:
    """Intermediate state threaded from _generate_eligibility() to
    _assemble() - explicit segment lists (not a joined string later
    searched for markers) so SE01's segment count can be computed exactly,
    with no risk of a random name/identifier value coincidentally
    containing a substring that looks like a segment boundary."""

    def __init__(self, envelope_segments: list[str], st_to_hl_segments: list[str], now, st_control, gs_control, isa_control):
        self.envelope_segments = envelope_segments  # ISA, GS - never part of the SE01 count
        self.st_to_hl_segments = st_to_hl_segments  # ST, BHT, HL/NM1/DMG... - SE01 counts these
        self.now = now
        self.st_control = st_control
        self.gs_control = gs_control
        self.isa_control = isa_control


def _generate_eligibility(rng: random.Random, st01: str, bht02: str) -> _EligibilityDraft:
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
        _build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HS*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*{_GS08_VERSION}~",
    ]
    st_to_hl_segments = [
        f"ST*{st01}*{st_control}~",
        f"BHT*0022*{bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
        "HL*1**20*1~",
        _build_org_nm1(rng, "PR", _PAYER_NAMES),
        "HL*2*1*21*1~",
    ]
    # Provider (2000B) is an organization ~70% of the time, an individual
    # practitioner otherwise - direct fuzz coverage of build_bundle()'s
    # is_person_entity() branch on both mapper sides.
    if maybe(rng, 0.7):
        st_to_hl_segments.append(_build_org_nm1(rng, "1P", _PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(_build_person_nm1(rng, "1P", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*3*2*22*1~", _build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    if maybe(rng, 0.7):
        st_to_hl_segments.append(_build_dmg(rng, subscriber_sex))

    # A dependent loop (2000D) is present ~40% of the time - direct fuzz
    # coverage of both mappers' "patient = dependent when present, else
    # subscriber" precedence rule.
    if maybe(rng, 0.4):
        dependent_sex = random_sex(rng)
        st_to_hl_segments += ["HL*4*3*23*0~", _build_person_nm1(rng, "QC", dependent_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(_build_dmg(rng, dependent_sex))

    return _EligibilityDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control)


def _assemble(rng: random.Random, draft: _EligibilityDraft, body_segments: list[str]) -> str:
    """Append the patient-loop-specific body, then the trailer segments
    (SE/GE/IEA), deliberately wrong ~12% of the time on SE01/GE01/IEA02
    (trailer element counts) to fuzz-exercise the validator's
    count-mismatch warning findings - mirroring ORU's deliberate ~30%
    out-of-range OBX-5 fuzzing precedent. A generator that never produces a
    mismatch would leave those rules permanently untested."""
    # SE01 counts every segment from ST through SE inclusive.
    se01 = len(draft.st_to_hl_segments) + len(body_segments) + 1  # +1 for SE itself
    ge01 = 1
    iea02 = 1
    if maybe(rng, 0.12):
        se01 += rng.choice((-1, 1))
    if maybe(rng, 0.12):
        ge01 = 2
    if maybe(rng, 0.12):
        iea02 = 2

    trailer = [
        f"SE*{se01}*{draft.st_control}~",
        f"GE*{ge01}*{draft.gs_control}~",
        f"IEA*{iea02}*{draft.isa_control}~",
    ]
    text = "".join(draft.envelope_segments + draft.st_to_hl_segments + body_segments + trailer)
    parse_interchange(text)  # self-check: a generator bug should raise, not return broken X12
    return text


def generate_270(rng: random.Random) -> str:
    draft = _generate_eligibility(rng, "270", "13")
    body = [_build_eq(rng)]
    if maybe(rng):
        body.append(_build_dtp(draft.now))
    if maybe(rng, 0.3):
        body.append(_build_eq(rng))
    return _assemble(rng, draft, body)


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
    return _assemble(rng, draft, body)


# --- 276/277 Claim Status Request/Response ----------------------------

# (category, status) pairs - a representative subset of X12's Claim Status
# Category Code (STC01-1) / Claim Status Code (STC01-2) external code
# lists, spanning every prefix app.edi.claim_status::
# _STC_CATEGORY_PREFIX_TO_TASK_STATUS recognizes (A/P/F/R/E) plus one
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
    envelope/NM1/DMG segment builders _generate_eligibility already uses,
    but not _generate_eligibility itself - the extra 2000C provider loop
    makes this a genuinely different shape, not a parameterization of the
    same one (see app/edi/claim_status.py's own module docstring for the
    same "different HL03 table, don't force a shared walk" reasoning)."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=5)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)
    trn01 = "2" if include_status else "1"  # TRN01: 1=current trace, 2=referenced (echoed back)

    envelope_segments = [
        _build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HR*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*005010X212~",
    ]
    st_to_hl_segments = [
        f"ST*{st01}*{st_control}~",
        f"BHT*0010*{bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
        "HL*1**20*1~",
        _build_org_nm1(rng, "PR", _PAYER_NAMES),
        "HL*2*1*21*1~",
    ]
    # Information receiver (2000B) is an organization ~70% of the time, an
    # individual otherwise - direct fuzz coverage of is_person_entity()'s
    # branch on both the receiver and provider loops.
    if maybe(rng, 0.7):
        st_to_hl_segments.append(_build_org_nm1(rng, "41", _PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(_build_person_nm1(rng, "41", random_sex(rng), include_id=True))

    st_to_hl_segments.append("HL*3*2*19*1~")
    if maybe(rng, 0.7):
        st_to_hl_segments.append(_build_org_nm1(rng, "1P", _PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(_build_person_nm1(rng, "1P", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*4*3*22*1~", _build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    st_to_hl_segments += _build_claim_status_group(rng, trn01, now, include_status)

    # A dependent loop (2000E) is present ~40% of the time - direct fuzz
    # coverage of the builder's "walk both patient loops, one Task per
    # claim group" behavior (not a precedence rule like 270/271's, since
    # 276/277 can report on claims for both the subscriber and a
    # dependent within the same transaction set).
    if maybe(rng, 0.4):
        dependent_sex = random_sex(rng)
        st_to_hl_segments += ["HL*5*4*23*0~", _build_person_nm1(rng, "QC", dependent_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(_build_dmg(rng, dependent_sex))
        st_to_hl_segments += _build_claim_status_group(rng, trn01, now, include_status)

    return _assemble(
        rng,
        _EligibilityDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control),
        [],
    )


def generate_276(rng: random.Random) -> str:
    return _generate_claim_status(rng, "276", "13", include_status=False)


def generate_277(rng: random.Random) -> str:
    return _generate_claim_status(rng, "277", "08", include_status=True)


# --- 278 Health Care Services Review (Prior Authorization) --------------

# UM01 (Request Category Code) - a representative subset.
_UM01_REQUEST_CATEGORY_CODES = ["HS", "SC", "AR", "IN"]
# UM02 (Certification Type Code): I=Initial, R=Renewal, S=Revised.
_UM02_CERTIFICATION_TYPE_CODES = ["I", "R", "S"]
# HI qualifier BF = ICD-10-CM Diagnosis (the same qualifier the real
# X12.org example this module's builder was verified against uses).
_HI_DIAGNOSIS_QUALIFIER = "BF"
_DIAGNOSIS_CODES = ["E119", "I10", "M5450", "J449", "N390", "M25561"]
# HCR01 (Action Code) - every prefix app.edi.prior_auth::
# _HCR01_TO_OUTCOME recognizes (A1/A2/A3/A4), plus one deliberately
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
    codes = rng.sample(_DIAGNOSIS_CODES, count)
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
    now = random_datetime_near_now(rng, min_days=-5, max_days=5)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)

    envelope_segments = [
        _build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HI*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*005010X217~",
    ]
    st_to_hl_segments = [
        f"ST*278*{st_control}~",
        f"BHT*0007*{bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
        "HL*1**20*1~",
        _build_org_nm1(rng, "X3", _PAYER_NAMES),
        "HL*2*1*21*1~",
    ]
    # Requester (2000B) is an organization ~60% of the time, an individual
    # otherwise - direct fuzz coverage of is_person_entity()'s branch.
    if maybe(rng, 0.6):
        st_to_hl_segments.append(_build_org_nm1(rng, "1P", _PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(_build_person_nm1(rng, "1P", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += ["HL*3*2*22*1~", _build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
    if maybe(rng, 0.7):
        st_to_hl_segments.append(_build_dmg(rng, subscriber_sex))

    # A dependent loop (2000D) is present ~40% of the time - direct fuzz
    # coverage of the "patient = dependent when present and NM1 resolves,
    # else subscriber" precedence rule, same as every other EDI family.
    patient_hl_id = "3"
    next_hl_id = 4
    if maybe(rng, 0.4):
        dependent_sex = random_sex(rng)
        st_to_hl_segments += [f"HL*4*3*23*1~", _build_person_nm1(rng, "QC", dependent_sex, include_id=False)]
        if maybe(rng, 0.7):
            st_to_hl_segments.append(_build_dmg(rng, dependent_sex))
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

    return _assemble(
        rng,
        _EligibilityDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control),
        [],
    )


def generate_278_request(rng: random.Random) -> str:
    return _generate_prior_auth(rng, "13")


def generate_278_response(rng: random.Random) -> str:
    return _generate_prior_auth(rng, "11")
