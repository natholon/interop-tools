"""Synthetic X12 837D generator - the app/edi/ mirror of
claim_837p_generator.py/claim_837i_generator.py, following the same
file-per-family layout app/edi/generator.py's own docstring describes."""

import random

from app.edi.generator import ICD10_DIAGNOSIS_CODES, assemble_generated_interchange, build_837_envelope, build_person_nm1, format_x12_date
from app.generators.base import maybe, random_identifier, random_sex

# CLM05-1 uses the SAME Place-of-Service vocabulary as 837P's (see
# claim_837d.py's own module docstring for why this is confirmed, not
# assumed, unlike 837I's genuinely different UB-04 Type of Bill) - reusing
# 837P's own representative subset.
_PLACE_OF_SERVICE_CODES = ["11", "21", "22", "23", "02"]
# SV3-01 procedure codes, all under the "AD" (CDT) qualifier - a
# representative subset spanning exams/x-rays/cleanings/restorations/perio/
# extraction, the same real-world-shape goal every other family's own
# procedure-code pool already follows.
_CDT_PROCEDURE_CODES = ["D0120", "D0150", "D0220", "D1110", "D1120", "D2140", "D2330", "D4341", "D7140"]
# TOO03's own surface-letter composite - a real tooth has at most 5
# relevant surfaces (see claim_837d.py's own _MAX_TOOTH_SURFACES).
_TOOTH_SURFACE_CODES = ["M", "O", "D", "B", "L", "F", "I"]


def _build_hi_837d(rng: random.Random, count: int) -> str:
    """Same ABK(principal)/ABF(other) distinction claim_837p_generator.py's
    own _build_hi_837p makes. Only ever called when count > 0 -
    generate_837d omits HI entirely a good fraction of the time instead
    (see its own comment below), since dental claims commonly carry no
    diagnosis at all - confirmed directly against the real X12.org example
    claim_837d.py's own module docstring cites, which has no HI segment."""
    codes = rng.sample(ICD10_DIAGNOSIS_CODES, count)
    composites = [f"ABK:{codes[0]}"] + [f"ABF:{code}" for code in codes[1:]]
    return "HI*" + "*".join(composites) + "~"


def _build_sv3(rng: random.Random, num_diagnoses: int) -> str:
    """Built via a positionally-indexed fields list, not hand-counted
    asterisks - SV3 has 11 elements and only SV3-01/02/06/11 are populated
    this slice, so hand-typing the right asterisk count invites exactly the
    off-by-one mistake this app already hit once building this same
    segment by hand for a test fixture (see CLAUDE.md's own testing
    guidance on verifying field positions programmatically)."""
    procedure = rng.choice(_CDT_PROCEDURE_CODES)
    charge = round(rng.uniform(20, 800), 2)
    quantity = rng.randint(1, 2)

    fields = [""] * 11
    fields[0] = f"AD:{procedure}"  # SV3-01
    fields[1] = f"{charge:.2f}"  # SV3-02
    fields[5] = str(quantity)  # SV3-06

    # SV3-11: 1-based pointers into HI's own diagnosis order - up to the
    # smaller of 4 and however many diagnoses this claim actually carries,
    # so a generated pointer resolves. ~10% of the time, one pointer is
    # deliberately pushed out of range instead - direct fuzz coverage of
    # the diagnosis-pointer-unresolved path, mirroring 837P's identical
    # fuzz precedent for SV1-07 (a generator that never produces a mismatch
    # would leave that branch permanently untested). No pointers are
    # generated at all when this claim has no diagnoses.
    if num_diagnoses:
        pointer_count = rng.randint(1, min(4, num_diagnoses))
        pointers_list = sorted(rng.sample(range(1, num_diagnoses + 1), pointer_count))
        if maybe(rng, 0.1):
            pointers_list[-1] = num_diagnoses + rng.randint(1, 3)
        fields[10] = ":".join(str(p) for p in pointers_list)  # SV3-11

    return "SV3*" + "*".join(fields) + "~"


def _build_too(rng: random.Random) -> str:
    tooth_number = str(rng.randint(1, 32))
    surfaces = rng.sample(_TOOTH_SURFACE_CODES, rng.randint(1, 3))
    return f"TOO*JP*{tooth_number}*{':'.join(surfaces)}~"


def generate_837d(rng: random.Random) -> str:
    """Mirrors claim_837p_generator.py's/claim_837i_generator.py's own
    generate_837p/generate_837i almost exactly at the envelope/HL-hierarchy
    level (see claim_837d.py's own module docstring for why that top-level
    shape is genuinely, not coincidentally, identical across all three 837
    variants) - all three now share app/edi/generator.py::
    build_837_envelope() for that portion - diverges once the claim loop
    itself starts, per the real structural differences (SV3 instead of
    SV1/SV2, a separate TOO segment, claim-level-vs-per-line DTP*472,
    diagnosis frequently absent entirely) claim_837d.py's own builder
    discloses. ST03 is what app/edi/registry.py::get_transaction_builder
    uses to tell 837D apart from 837P/837I, all three sharing the literal
    ST01="837" - always populated here, matching the real X12.org example
    this module was verified against (a real sender omitting it defaults
    to Edi837pBuilder, covered separately by test_edi_registry.py, not by
    this generator)."""
    draft = build_837_envelope(rng, "005010X224A2", billing_org_probability=0.6, sbr_segment="SBR*P*******CI~", include_pat_segment=False)
    now = draft.now
    st_to_hl_segments = draft.st_to_hl_segments

    charge = round(rng.uniform(50, 1500), 2)
    place_of_service = rng.choice(_PLACE_OF_SERVICE_CODES)
    claim_id = f"CLM{random_identifier(rng, digits=8)}"
    st_to_hl_segments.append(f"CLM*{claim_id}*{charge:.2f}***{place_of_service}:B:1*Y*A*Y*I~")

    # Dental claims commonly carry the service date at the claim level
    # rather than per line (see claim_837d.py's own module docstring,
    # confirmed against the real X12.org example) - generated ~60% of the
    # time, independently of whether any given line below has its own DTP.
    if maybe(rng, 0.6):
        st_to_hl_segments.append(f"DTP*472*D8*{format_x12_date(now)}~")

    # Dental claims commonly carry no diagnosis at all (the real X12.org
    # example this builder was verified against has no HI segment) - so
    # unlike 837P/837I, generate_837d omits HI entirely ~40% of the time,
    # direct fuzz coverage of edi.837d-missing-diagnosis's own info finding.
    num_diagnoses = rng.randint(1, 3) if maybe(rng, 0.6) else 0
    if num_diagnoses:
        st_to_hl_segments.append(_build_hi_837d(rng, num_diagnoses))

    # A rendering provider (2310B, NM1*82) is present ~60% of the time -
    # direct fuzz coverage of Claim.careTeam's own present/absent branch.
    if maybe(rng, 0.6):
        st_to_hl_segments.append(build_person_nm1(rng, "82", random_sex(rng), include_id=True))

    line_count = rng.randint(1, 3)
    for line_number in range(1, line_count + 1):
        st_to_hl_segments.append(f"LX*{line_number}~")
        st_to_hl_segments.append(_build_sv3(rng, num_diagnoses))
        # Tooth information is genuinely optional per line (a cleaning or
        # exam commonly carries none) - present ~60% of the time, direct
        # fuzz coverage of Claim.item.bodySite/subSite's present/absent
        # branch.
        if maybe(rng, 0.6):
            st_to_hl_segments.append(_build_too(rng))
        # A per-line DTP*472 overrides the claim-level default when
        # present - generated independently at ~30%, so both the
        # per-line-wins and claim-level-fallback paths (and, when neither
        # is present, the no-servicedDate path) get exercised across seeds.
        if maybe(rng, 0.3):
            st_to_hl_segments.append(f"DTP*472*D8*{format_x12_date(now)}~")

    return assemble_generated_interchange(rng, draft, [])
