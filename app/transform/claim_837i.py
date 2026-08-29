"""FHIR Bundle -> X12 837I (Health Care Claim: Institutional, 005010X223A2).

Reverses `app/edi/claim_837i.py` field-for-field: `CLM01`/`CLM02` (claim
id, total charge - **not** `CLM05`, which the forward mapper never reads,
a real structural fact rather than an oversight carried over from 837P),
`CL103` (discharge status, via `Claim.supportingInfo`), `HI` diagnosis
composites via the shared `build_hi_segment`, and one `LX`+`SV2`(+`DTP`)
group per `Claim.item[]` - revenue code, optional procedure composite,
charge, quantity, service date, and **no** diagnosis-pointer composite,
since `SV2` genuinely has none.

Resolution is identical to 837P's: `Claim.provider`/`.insurer`/
`.careTeam[].provider` (here the *attending* provider, `NM1*71`, not
837P's rendering `NM1*82`) resolved by direct by-id lookup rather than
`resolve_payer_and_provider` - see `claim_837p.py` for why. Subscriber/
dependent reuses 270/271's shared resolver, with the same
dependent-is-always-the-patient simplification, since both variants share
`Resolved837Loops`.

Disclosed round-trip fidelity gaps:
- `CLM05-1` (UB-04 Type of Bill), `CL101`/`CL102` (admission type/source)
  and `HI`'s occurrence/value/condition-code usages (`BH`/`BE`/`BG`) have
  no FHIR home - the forward mapper never reads them.
- **The forward multi-`HI` split is not reproduced.** Principal and Other
  diagnoses arrive in separate segments but are regenerated as one
  combined `HI`. `iter_diagnosis_hi_segments` only inspects a segment's
  *first* composite qualifier to decide inclusion, so one combined segment
  parses identically to several. This only matters above 12 diagnoses (the
  per-segment composite cap), which neither real fixture exercises."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.claim_837p import PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
from app.edi.generator import format_x12_date, format_x12_time
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources
from app.transform.edi_common import (
    DEFAULT_ST_CONTROL,
    build_dmg,
    build_envelope_segments,
    build_hi_segment,
    build_trailer_segments,
    envelope_datetime,
    build_pat_segment,
    build_sbr_segment,
    org_or_person_nm1,
    resolve_by_reference,
    resolve_subscriber_and_dependent,
    sanitize_x12_text,
)

VERSION = "005010X223A2"

# Both real, verified FHIR-canonical CodeSystems (confirmed by direct
# fetch on the forward side, not disclosed local placeholders) - hardcoded
# directly here rather than imported from app.edi.claim_837i, since
# neither needs to be promoted for a single reverse consumer to reuse a
# literal string it can just as easily hold itself.
_SUPPORTING_INFO_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/claiminformationcategory"
_DISCHARGE_STATUS_CATEGORY = "discharge"
_DISCHARGE_STATUS_CODE_SYSTEM = "https://www.nubc.org/CodeSystem/PatDischargeStatus"
_REVENUE_CODE_SYSTEM = "https://www.nubc.org/CodeSystem/RevenueCodes"
_DEFAULT_REVENUE_CODE = "0001"


def _reverse_procedure_composite(item) -> str:
    coding = item.productOrService.coding[0] if item.productOrService and item.productOrService.coding else None
    if coding is None or not coding.code:
        return ""
    qualifier = "HC"
    marker = f"{PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:"
    if coding.system and coding.system.startswith(marker):
        qualifier = coding.system[len(marker) :]
    return f"{qualifier}:{coding.code}"


def _resolve_revenue_code(item) -> str:
    if item.revenue and item.revenue.coding:
        for coding in item.revenue.coding:
            if coding.system == _REVENUE_CODE_SYSTEM and coding.code:
                return coding.code
    return _DEFAULT_REVENUE_CODE


def _build_sv2_segment(item) -> str:
    # :.2f, not str(Decimal) - see remittance_835.py's own
    # _build_bpr_segment comment for why str() is vulnerable to
    # trailing-zero loss once a Bundle has round-tripped through JSON.
    fields = [
        _resolve_revenue_code(item),
        _reverse_procedure_composite(item),
        f"{item.unitPrice.value:.2f}" if item.unitPrice else "0.00",
        "UN",
        str(item.quantity.value) if item.quantity else "1",
    ]
    return "SV2*" + "*".join(fields) + "~"


def _build_service_line_segments(sequence: int, item) -> list[str]:
    segments = [f"LX*{sequence}~", _build_sv2_segment(item)]
    if item.servicedDate:
        segments.append(f"DTP*472*D8*{format_x12_date(item.servicedDate)}~")
    return segments


def _resolve_discharge_status(claim) -> str:
    for info in claim.supportingInfo or []:
        category_matches = bool(
            info.category
            and info.category.coding
            and any(
                c.system == _SUPPORTING_INFO_CATEGORY_SYSTEM and c.code == _DISCHARGE_STATUS_CATEGORY
                for c in info.category.coding
            )
        )
        if not category_matches or not info.code or not info.code.coding:
            continue
        for coding in info.code.coding:
            if coding.system == _DISCHARGE_STATUS_CODE_SYSTEM and coding.code:
                return coding.code
    return ""


def _build_cl1_segment(claim) -> str:
    status_code = _resolve_discharge_status(claim)
    if not status_code:
        return ""
    return "CL1*" + "*".join(["", "", status_code]) + "~"


def _build_clm_segment(claim) -> str:
    claim_id = sanitize_x12_text(claim.identifier[0].value) if claim.identifier and claim.identifier[0].value else "0000000000"
    total = f"{claim.total.value:.2f}" if claim.total else "0.00"
    fields = [claim_id, total, "", "", "", "Y", "A", "Y", "Y"]
    return "CLM*" + "*".join(fields) + "~"


class Edi837iBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        claim = find_resource(bundle, "Claim")
        if claim is None:
            raise MappingError("Bundle has no Claim resource - cannot build an 837I message")

        billing_provider = resolve_by_reference(bundle, claim.provider)
        if billing_provider is None:
            raise MappingError("Bundle has no resolvable billing provider - cannot build an 837I message")
        payer = resolve_by_reference(bundle, claim.insurer)
        if payer is None:
            raise MappingError("Bundle has no resolvable payer - cannot build an 837I message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build an 837I message")
        subscriber, dependent = resolve_subscriber_and_dependent(bundle, patients)
        patient_loop_is_dependent = dependent is not None

        attending_provider = None
        if claim.careTeam:
            attending_provider = resolve_by_reference(bundle, claim.careTeam[0].provider)

        now = envelope_datetime(bundle.timestamp)
        bht_reference = sanitize_x12_text(bundle.identifier.value) if bundle.identifier and bundle.identifier.value else "REF00000001"

        envelope_segments = build_envelope_segments(now)
        st_to_hl_segments = [
            f"ST*837*{DEFAULT_ST_CONTROL}*{VERSION}~",
            f"BHT*0019*00*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}*CH~",
            "HL*1**20*1~",
            org_or_person_nm1("85", billing_provider),
            f"HL*2*1*22*{0 if patient_loop_is_dependent else 1}~",
        ]
        # SBR precedes NM1*IL in the 2000B loop, which is where the forward
        # direction reads it from.
        sbr = build_sbr_segment(find_resource(bundle, "Coverage"))
        if sbr:
            st_to_hl_segments.append(sbr)
        st_to_hl_segments.extend([
            org_or_person_nm1("IL", subscriber),
        ])
        subscriber_dmg = build_dmg(subscriber)
        if subscriber_dmg:
            st_to_hl_segments.append(subscriber_dmg)
        st_to_hl_segments.append(org_or_person_nm1("PR", payer))

        if patient_loop_is_dependent:
            st_to_hl_segments.append("HL*3*2*23*0~")
            pat = build_pat_segment(find_resource(bundle, "Coverage"))
            if pat:
                st_to_hl_segments.append(pat)
            st_to_hl_segments.append(org_or_person_nm1("QC", dependent, include_id=False))
            dependent_dmg = build_dmg(dependent)
            if dependent_dmg:
                st_to_hl_segments.append(dependent_dmg)

        st_to_hl_segments.append(_build_clm_segment(claim))
        cl1_segment = _build_cl1_segment(claim)
        if cl1_segment:
            st_to_hl_segments.append(cl1_segment)
        hi_segment = build_hi_segment(claim.diagnosis)
        if hi_segment:
            st_to_hl_segments.append(hi_segment)
        if attending_provider is not None:
            st_to_hl_segments.append(org_or_person_nm1("71", attending_provider))

        for sequence, item in enumerate(claim.item or [], start=1):
            st_to_hl_segments.extend(_build_service_line_segments(sequence, item))

        trailer_segments = build_trailer_segments(st_to_hl_segments, [])
        return "".join(envelope_segments + st_to_hl_segments + trailer_segments)
