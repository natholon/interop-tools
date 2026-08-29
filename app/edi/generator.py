"""Shared X12 generator primitives - the `app/edi/` mirror of
`app/generators/base.py`, used by every family's own generator module
(`eligibility_generator.py`, `claim_status_generator.py`,
`prior_auth_generator.py`, `remittance_generator.py`, and one per 837
variant). Built by positional string-joins per segment, X12 being
delimited text rather than a tree.

**Holds no `generate_*` functions and re-exports none.** Each family
module imports these primitives one-directionally, and
`app/generators/registry.py` and the tests import each `generate_*`
straight from its own family module. A re-export shim would work, but only
while this file's primitives stay textually before any cross-import - a
fragile ordering constraint for a convenience path.

Reuses `app.generators.base`'s format-agnostic primitives directly rather
than re-deriving name pools. Net-new here is `format_x12_date()`/
`format_x12_time()` (X12 splits date and time into separate elements,
unlike HL7's single concatenated TS) plus the pools shared by two or more
families - `PAYER_NAMES`/`PROVIDER_ORG_NAMES` by nearly all of them,
`ICD10_DIAGNOSIS_CODES` by 278 and 837P, which read the identical HI
composite. Family-specific pools stay in their own module.

`build_837_envelope()` is the one function-shaped exception. It is not a
generator (it returns a partial `EdiDraft`, not finished X12) and is
shared by the 837 trio rather than universally - but a
shared-by-some-families primitive already has precedent here, so it lives
in this file rather than a fourth module for one function. Each
`generate_837{p,i,d}()` still builds its own claim-level segments after it
returns, which is exactly where the three diverge.

Self-checked via `parse_interchange()` before returning: a generator bug
should raise `EdiParseError`, not hand back broken X12."""

import random

from app.edi.parser import parse_interchange
from app.generators.base import (
    maybe,
    random_address,
    random_datetime_near_now,
    random_identifier,
    random_person_name,
    random_sex,
)

PAYER_NAMES = [
    "ACME HEALTH PLAN",
    "BLUEHARBOR INSURANCE",
    "SUMMIT HEALTH PARTNERS",
    "PINECREST MUTUAL",
    "HORIZON BENEFIT GROUP",
]
PROVIDER_ORG_NAMES = [
    "GENERAL HOSPITAL",
    "RIVERVIEW CLINIC",
    "MAINSTREET MEDICAL GROUP",
    "LAKESIDE FAMILY PRACTICE",
    "CITY HEALTH CENTER",
]
# Representative ICD-10-CM-shaped diagnosis codes, shared by prior_auth_
# generator.py's HI segment and claim_837p_generator.py's own HI segment -
# both transaction sets read the identical HI composite shape (see
# app/edi/common.py::build_diagnosis_codeable_concepts).
ICD10_DIAGNOSIS_CODES = ["E119", "I10", "M5450", "J449", "N390", "M25561"]


def format_x12_date(dt) -> str:
    return dt.strftime("%Y%m%d")


def format_x12_time(dt) -> str:
    return dt.strftime("%H%M")


def build_isa(control_number: str, sender_id: str, receiver_id: str, dt) -> str:
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


# SBR01 Payer Responsibility, SBR02 Individual Relationship and SBR09
# Claim Filing Indicator all map to a real Coverage field, so each needs to
# vary rather than staying the one literal each family used to pass.
_SBR_RESPONSIBILITY_CODES = ["P", "P", "P", "S", "T"]
_SBR_RELATIONSHIP_CODES = ["18", "01", "19", "", "21"]
_CLAIM_FILING_INDICATORS = ["CI", "MB", "MC", "HM", "BL"]


def randomize_sbr(rng: random.Random, template: str) -> str:
    """Rebuild the caller's SBR positionally - SBR09 is the filing
    indicator, the position the real X12.org examples use."""
    fields = [""] * 9
    fields[0] = rng.choice(_SBR_RESPONSIBILITY_CODES)
    if maybe(rng, 0.7):
        fields[1] = rng.choice(_SBR_RELATIONSHIP_CODES)
    if maybe(rng, 0.8):
        fields[8] = rng.choice(_CLAIM_FILING_INDICATORS)
    return "SBR*" + "*".join(fields).rstrip("*") + "~"


def build_address_segments(rng: random.Random) -> str:
    """N3/N4, and sometimes PER - every party in every 837 can carry them,
    and each maps to a real Address/ContactPoint, so both present and
    absent need generating."""
    if not maybe(rng, 0.7):
        return ""
    line, city, state, zip_code = random_address(rng)
    segments = [f"N3*{line.upper()}~", f"N4*{city.upper()}*{state}*{zip_code}~"]
    if maybe(rng, 0.35):
        _, given = random_person_name(rng)
        segments.append(f"PER*IC*{given.upper()}*TE*{random_identifier(rng, digits=10)}~")
    return "".join(segments)


def build_org_nm1(rng: random.Random, entity_code: str, names_pool: list[str]) -> str:
    name = rng.choice(names_pool)
    identifier = random_identifier(rng, digits=8)
    return f"NM1*{entity_code}*2*{name}*****XX*{identifier}~" + build_address_segments(rng)


def build_person_nm1(rng: random.Random, entity_code: str, sex: str, include_id: bool) -> str:
    family, given = random_person_name(rng, sex=sex)
    address = build_address_segments(rng)
    if include_id:
        identifier = random_identifier(rng, digits=8)
        return f"NM1*{entity_code}*1*{family}*{given}****MI*{identifier}~" + address
    return f"NM1*{entity_code}*1*{family}*{given}~" + address


def build_dmg(rng: random.Random, sex: str) -> str:
    dob = random_datetime_near_now(rng, min_days=-365 * 80, max_days=-365)
    return f"DMG*D8*{format_x12_date(dob)}*{sex}~"


class EdiDraft:
    """Intermediate state threaded from each family's own `_generate_*()`
    to `assemble_generated_interchange()` - explicit segment lists (not a
    joined string later searched for markers) so SE01's segment count can
    be computed exactly, with no risk of a random name/identifier value
    coincidentally containing a substring that looks like a segment
    boundary. Fully generic across every EDI family (even 835, which has no
    HL hierarchy at all) - originally named `_EligibilityDraft` from when
    only 270/271 existed, renamed once every other family was already
    reusing it under that misleading name."""

    def __init__(self, envelope_segments: list[str], st_to_hl_segments: list[str], now, st_control, gs_control, isa_control):
        self.envelope_segments = envelope_segments  # ISA, GS - never part of the SE01 count
        self.st_to_hl_segments = st_to_hl_segments  # ST, BHT, HL/NM1/DMG... - SE01 counts these
        self.now = now
        self.st_control = st_control
        self.gs_control = gs_control
        self.isa_control = isa_control


def _segment_count(segments: list[str]) -> int:
    """How many X12 segments a list of built strings actually contains.

    Counting terminators rather than list entries, because a builder may
    return an NM1 together with the N3/N4/PER segments that describe it.
    Exact here for the same reason EdiDraft's own docstring gives for not
    scanning a joined string: every value these generators emit comes from
    a fixed pool or a digit string, so none can contain a terminator.
    """
    return sum(segment.count("~") for segment in segments)


def assemble_generated_interchange(rng: random.Random, draft: EdiDraft, body_segments: list[str]) -> str:
    """Append the patient-loop-specific body, then the trailer segments
    (SE/GE/IEA), deliberately wrong ~12% of the time on SE01/GE01/IEA02
    (trailer element counts) to fuzz-exercise the validator's
    count-mismatch warning findings - mirroring ORU's deliberate ~30%
    out-of-range OBX-5 fuzzing precedent. A generator that never produces a
    mismatch would leave those rules permanently untested."""
    # SE01 counts every segment from ST through SE inclusive - segments,
    # not list entries: a party's NM1 now comes with the N3/N4/PER that
    # describe it, in one string.
    se01 = _segment_count(draft.st_to_hl_segments) + _segment_count(body_segments) + 1  # +1 for SE itself
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


def build_837_envelope(
    rng: random.Random,
    version: str,
    billing_org_probability: float,
    sbr_segment: str,
    include_pat_segment: bool,
) -> EdiDraft:
    """Shared ISA/GS/ST/BHT/HL*1 envelope + 2000A billing provider + 2000B
    subscriber (SBR/NM1/DMG/payer) + optional 2000C patient loop
    construction - identical across all three 837 variants (see each
    builder's own module docstring for why that top-level shape is
    genuinely, not coincidentally, identical). Each family's own
    generate_837{p,i,d}() appends its own claim-level segments (CLM, HI,
    service lines, ...) directly onto the returned draft's own
    st_to_hl_segments list after this returns (EdiDraft stores the list by
    reference, not a copy, so further `.append()`/`.extend()` calls mutate
    the same segments this function already built) - the shared portion
    stops exactly where the three families' own shapes start to genuinely
    diverge (CLM's own composite shape, CL1/HI/service-line construction).

    `version` is used for both GS08 and ST03, which every real X12.org
    example this app's 837 builders were verified against confirms always
    carry the identical TR3 identifier string. `sbr_segment` is the one
    family-specific line within the otherwise-shared subscriber block
    (SBR02/SBR09 vary: 837P's "CI", 837I's own "18"/"MB" pair).
    `include_pat_segment` covers 837P's own optional `PAT*19~` segment
    on the dependent loop, which 837I/837D never emit."""
    now = random_datetime_near_now(rng, min_days=-5, max_days=0)
    sender_id = f"SENDER{random_identifier(rng, digits=4)}"
    receiver_id = f"RECEIVER{random_identifier(rng, digits=4)}"
    isa_control = random_identifier(rng, digits=9)
    gs_control = random_identifier(rng, digits=6)
    st_control = "0001"
    bht_reference = random_identifier(rng, digits=8)

    envelope_segments = [
        build_isa(isa_control, sender_id, receiver_id, now),
        f"GS*HC*{sender_id}*{receiver_id}*{format_x12_date(now)}*{format_x12_time(now)}*{gs_control}*X*{version}~",
    ]
    st_to_hl_segments = [
        f"ST*837*{st_control}*{version}~",
        f"BHT*0019*00*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}*CH~",
        "HL*1**20*1~",
    ]
    if maybe(rng, billing_org_probability):
        st_to_hl_segments.append(build_org_nm1(rng, "85", PROVIDER_ORG_NAMES))
    else:
        st_to_hl_segments.append(build_person_nm1(rng, "85", random_sex(rng), include_id=True))

    subscriber_sex = random_sex(rng)
    st_to_hl_segments += [
        "HL*2*1*22*1~",
        randomize_sbr(rng, sbr_segment),
        build_person_nm1(rng, "IL", subscriber_sex, include_id=True),
    ]
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_dmg(rng, subscriber_sex))
    st_to_hl_segments.append(build_org_nm1(rng, "PR", PAYER_NAMES))

    # A patient loop (2000C) is present ~40% of the time - same precedence
    # rule as every other EDI family's dependent loop.
    if maybe(rng, 0.4):
        patient_sex = random_sex(rng)
        st_to_hl_segments.append("HL*3*2*23*0~")
        if include_pat_segment:
            st_to_hl_segments.append("PAT*19~")
        st_to_hl_segments.append(build_person_nm1(rng, "QC", patient_sex, include_id=False))
        if maybe(rng, 0.7):
            st_to_hl_segments.append(build_dmg(rng, patient_sex))

    return EdiDraft(envelope_segments, st_to_hl_segments, now, st_control, gs_control, isa_control)
