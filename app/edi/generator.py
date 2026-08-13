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
