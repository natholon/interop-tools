"""Shared X12 generator primitives - the app/edi/ mirror of
app/generators/base.py, used by every family's own generator module
(eligibility_generator.py for 270/271, claim_status_generator.py for
276/277, prior_auth_generator.py for 278, remittance_generator.py for 835,
claim_837p_generator.py for 837P). Built via positional string-joins per
segment (X12 is delimited text like HL7v2, not tree-built like CDA's XML).

Deliberately holds no `generate_*` functions itself and does not re-export
them - each family module imports these primitives one-directionally, and
`app/generators/registry.py`/every `test_generate_*.py` file import each
`generate_*` function directly from its own family module (e.g.
`from app.edi.eligibility_generator import generate_270`), not from here.
A re-export shim here would work (Python resolves it via definition order)
but relies on this file's own primitives staying textually before any
cross-import, a fragile ordering constraint not worth taking on for a
convenience import path.

Reuses app.generators.base's format-agnostic primitives directly (maybe(),
random_person_name(), random_sex(), random_identifier(),
random_datetime_near_now()) rather than re-deriving name pools; net-new
here is only format_x12_date()/format_x12_time() (X12 splits date and time
into separate elements, unlike HL7's single concatenated TS field) and a
small set of shared name/code pools reused across two or more families
(`PAYER_NAMES`/`PROVIDER_ORG_NAMES` by nearly every family;
`ICD10_DIAGNOSIS_CODES` by both prior_auth_generator.py and
claim_837p_generator.py, both of which read the identical HI composite
shape). Family-specific pools (Service Type Codes, Claim Status categories,
HCR action codes, ...) stay in their own family's generator module instead.

`build_837_envelope()` is the one function-shaped (not a bare pool/
primitive) exception to "no generate_* functions" - it isn't one itself
(it returns a partial EdiDraft, not finished X12 text), and it's shared by
exactly the 837 family (Professional/Institutional/Dental), not every
family the way PAYER_NAMES is - but a genuinely-not-quite-universal shared
primitive already has precedent here (ICD10_DIAGNOSIS_CODES is shared by
2 of N families, not all), so it lives here rather than a fourth new
module for one function. Promoted once claim_837p_generator.py,
claim_837i_generator.py, and claim_837d_generator.py were each found
independently rebuilding the identical ISA/GS/ST/BHT/HL*1 envelope +
2000A billing provider + 2000B subscriber + optional 2000C patient loop
construction, unlike every other EDI pair's own generator module (which
already shares this portion via one `_generate_<family>()` helper) -
each family's own `generate_837{p,i,d}()` still builds its own claim-level
segments (CLM, HI, service lines, ...) after this returns, exactly where
the three genuinely diverge.

Self-checked via parse_interchange() before returning, mirroring every
other generator's parse-back self-check - a generator bug should raise
EdiParseError, not return broken X12 text."""

import random

from app.edi.parser import parse_interchange
from app.generators.base import maybe, random_datetime_near_now, random_identifier, random_person_name, random_sex

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


def build_org_nm1(rng: random.Random, entity_code: str, names_pool: list[str]) -> str:
    name = rng.choice(names_pool)
    identifier = random_identifier(rng, digits=8)
    return f"NM1*{entity_code}*2*{name}*****XX*{identifier}~"


def build_person_nm1(rng: random.Random, entity_code: str, sex: str, include_id: bool) -> str:
    family, given = random_person_name(rng, sex=sex)
    if include_id:
        identifier = random_identifier(rng, digits=8)
        return f"NM1*{entity_code}*1*{family}*{given}****MI*{identifier}~"
    return f"NM1*{entity_code}*1*{family}*{given}~"


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


def assemble_generated_interchange(rng: random.Random, draft: EdiDraft, body_segments: list[str]) -> str:
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
    st_to_hl_segments += ["HL*2*1*22*1~", sbr_segment, build_person_nm1(rng, "IL", subscriber_sex, include_id=True)]
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
