"""Synthetic X12 837I generator - the app/edi/ mirror of
claim_837p_generator.py, following the same file-per-family layout
app/edi/generator.py's own docstring describes."""

import random

from app.edi.generator import ICD10_DIAGNOSIS_CODES, assemble_generated_interchange, build_837_envelope, build_person_nm1, format_x12_date
from app.generators.base import maybe, random_identifier, random_sex

# CLM05-1 (institutional Facility Type Code, UB-04 Type of Bill's first two
# digits) - a representative subset. Not mapped to any FHIR field this
# phase (see claim_837i.py's own module docstring for why), but still
# generated for realism/round-trip fidelity.
_FACILITY_TYPE_CODES = ["11", "12", "13", "14", "21"]
# Revenue codes (SV2-01), a representative subset spanning both
# procedure-paired (0305 lab, 0730 EKG) and procedure-less (0120 room and
# board, 0250 pharmacy) real-world shapes.
_REVENUE_CODES_WITH_PROCEDURE = [("0305", "85025"), ("0730", "93005"), ("0450", "99283"), ("0250", "90999")]
_REVENUE_CODES_WITHOUT_PROCEDURE = ["0120", "0250", "0300"]
# CL103 (Patient Status Code, NUBC code source 239) - a representative
# subset (01=Discharged to home, 02=Discharged to another hospital,
# 20=Expired, 30=Still a patient).
_PATIENT_STATUS_CODES = ["01", "02", "20", "30"]
# CL101 (Admission Type Code) / CL102 (Admission Source Code).
_ADMISSION_TYPE_CODES = ["1", "2", "3"]
_ADMISSION_SOURCE_CODES = ["1", "2", "7"]


def _build_hi_837i(rng: random.Random, count: int) -> list[str]:
    """Same ABK(principal)/ABF(other) distinction claim_837p_generator.py's
    own _build_hi_837p makes - but split across TWO separate HI segments
    (Principal Diagnosis gets its own single-composite HI segment, Other
    Diagnosis gets a second one), matching the real X12.org example this
    module's builder was verified against, which does exactly this rather
    than combining every diagnosis into one HI segment the way 837P does."""
    codes = rng.sample(ICD10_DIAGNOSIS_CODES, count)
    segments = [f"HI*ABK:{codes[0]}~"]
    if len(codes) > 1:
        other_composites = "*".join(f"ABF:{code}" for code in codes[1:])
        segments.append(f"HI*{other_composites}~")
    return segments


def _build_occurrence_value_condition_hi(rng: random.Random, now) -> list[str]:
    """Occurrence (BH)/value (BE)/condition (BG) HI segments - generated
    purely for realism and to fuzz-exercise
    _iter_diagnosis_hi_segments's own "skip non-diagnosis HI segments,
    don't fold them into Claim.diagnosis[]" filtering (see that function's
    own docstring) - none of these are mapped to any FHIR field this
    phase, so generating them is what actually proves the filter works,
    not just that it's theoretically reachable."""
    segments = []
    if maybe(rng, 0.5):
        segments.append(f"HI*BH:A1:D8:{format_x12_date(now)}~")
    if maybe(rng, 0.3):
        segments.append("HI*BE:A2:::15.31~")
    if maybe(rng, 0.3):
        segments.append("HI*BG:09~")
    return segments


def _build_sv2(rng: random.Random) -> str:
    charge = round(rng.uniform(15, 3500), 2)
    # A procedure code (SV2-02) is present ~65% of the time - direct fuzz
    # coverage of both branches of ClaimItem.productOrService's
    # coded-vs-revenue-code-text fallback (see claim_837i.py's own
    # module docstring for why productOrService must always resolve
    # despite SV2-02 being genuinely optional in real institutional lines).
    if maybe(rng, 0.65):
        revenue, procedure = rng.choice(_REVENUE_CODES_WITH_PROCEDURE)
        return f"SV2*{revenue}*HC:{procedure}*{charge:.2f}*UN*1~"
    revenue = rng.choice(_REVENUE_CODES_WITHOUT_PROCEDURE)
    return f"SV2*{revenue}**{charge:.2f}*DA*1~"


def generate_837i(rng: random.Random) -> str:
    """Mirrors claim_837p_generator.py's own generate_837p almost exactly
    at the envelope/HL-hierarchy level (see claim_837i.py's own module
    docstring for why that top-level shape is genuinely, not
    coincidentally, identical between the two families) - both share
    app/edi/generator.py::build_837_envelope() for that portion now -
    diverges once the claim loop itself starts, per the same real
    structural differences (CL1, split diagnosis/other-category HI
    segments, SV2 instead of SV1, NM1*71 instead of NM1*82) claim_837i.py's
    own builder discloses. ST03 (Implementation Convention Reference) is
    what app/edi/registry.py::get_transaction_builder uses to tell 837I
    apart from 837P, both sharing the literal ST01="837" - always populated
    here, matching the real X12.org example this module was verified
    against (a real sender omitting it is disclosed and covered separately
    by test_get_transaction_builder_defaults_837_to_professional_when_
    st03_absent, not by this generator)."""
    draft = build_837_envelope(rng, "005010X223A2", billing_org_probability=0.7, sbr_segment="SBR*P*18*****MB~", include_pat_segment=False)
    now = draft.now
    st_to_hl_segments = draft.st_to_hl_segments

    charge = round(rng.uniform(100, 5000), 2)
    facility_type = rng.choice(_FACILITY_TYPE_CODES)
    claim_id = f"CLM{random_identifier(rng, digits=8)}"
    st_to_hl_segments.append(f"CLM*{claim_id}*{charge:.2f}***{facility_type}:A:1**A*Y*Y~")

    admission_type = rng.choice(_ADMISSION_TYPE_CODES)
    admission_source = rng.choice(_ADMISSION_SOURCE_CODES)
    patient_status = rng.choice(_PATIENT_STATUS_CODES)
    st_to_hl_segments.append(f"CL1*{admission_type}*{admission_source}*{patient_status}~")

    num_diagnoses = rng.randint(1, 3)
    st_to_hl_segments.extend(_build_hi_837i(rng, num_diagnoses))
    st_to_hl_segments.extend(_build_occurrence_value_condition_hi(rng, now))

    # An attending provider (2310A, NM1*71) is present ~70% of the time -
    # direct fuzz coverage of Claim.careTeam's own present/absent branch.
    if maybe(rng, 0.7):
        st_to_hl_segments.append(build_person_nm1(rng, "71", random_sex(rng), include_id=False))

    line_count = rng.randint(1, 3)
    for line_number in range(1, line_count + 1):
        st_to_hl_segments.append(f"LX*{line_number}~")
        st_to_hl_segments.append(_build_sv2(rng))
        if maybe(rng, 0.8):
            st_to_hl_segments.append(f"DTP*472*D8*{format_x12_date(now)}~")

    return assemble_generated_interchange(rng, draft, [])
