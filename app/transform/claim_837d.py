"""FHIR Bundle -> X12 837D (Health Care Claim: Dental, 005010X224A2).

Reverses `app/edi/claim_837d.py` field-for-field: `CLM01`/`CLM02`/`CLM05`
(claim id, total charge, place-of-service - the SAME vocabulary as 837P's,
unlike 837I's UB-04 one), `HI` diagnosis composites via the shared
`build_hi_segment`, and one `LX`+`SV3`(+`TOO`)(+`DTP`) group per
`Claim.item[]`.

Resolution is identical to 837P's/837I's, via the shared
`edi_common.resolve_by_reference`/`org_or_person_nm1`:
`Claim.provider`/`.insurer`/`.careTeam[].provider` are direct references.
Note the rendering provider is `NM1*82` (as 837P), not 837I's `NM1*71`
attending provider. Subscriber/dependent resolution reuses 270/271's
shared resolver.

Two pieces of reversal logic neither 837P nor 837I needs:
- **The procedure-code qualifier defaults to `"AD"` (CDT)**, not `"HC"`.
  A coding whose system starts with `PROCEDURE_QUALIFIER_FALLBACK_SYSTEM`
  recovers its original qualifier; `CDT_CODE_SYSTEM` recovers `"AD"`.
- **`TOO` (Tooth Information) has no 837P/837I equivalent.** Reverses
  `Claim.item.bodySite` (tooth number) and `.subSite` (up to 5 surface
  letters, `:`-joined into TOO03) into one segment, emitted whenever
  *either* resolves so a one-sided item still round-trips. `subSite`'s
  system is not re-checked, since nothing else in this app populates it.

Disclosed round-trip gaps:
- `DTP*472` is always regenerated **per line**, never claim-level.
  `Claim.item.servicedDate` keeps no record of which source it came from,
  and a per-line DTP is read first regardless, so this is round-trip-safe
  without reproducing the original split.
- `SV3-04`/`SV3-05`/`SV3-07`-`SV3-10` and `2010AB`/`2310C`/`2320`/`2330`
  have no FHIR-side home - the forward mapper never reads them.
- SV3-11 is left **empty** when `diagnosisSequence` is unset, unlike
  837P/837I which default to pointer `[1]`: dental claims commonly carry
  no diagnosis, so a fabricated `"1"` could point at a position
  `Claim.diagnosis` does not have."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.claim_837d import (
    CDT_CODE_SYSTEM,
    TOO_UNIVERSAL_QUALIFIER,
    TOOTH_NUMBER_FALLBACK_SYSTEM,
    TOOTH_NUMBER_SYSTEM,
)
from app.edi.claim_837p import PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
from app.edi.common import POS_CODE_SYSTEM
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
    build_prv_segment,
    build_sbr_segment,
    org_or_person_nm1,
    resolve_by_reference,
    resolve_subscriber_and_dependent,
    sanitize_x12_text,
)

VERSION = "005010X224A2"

_MAX_DIAGNOSIS_POINTERS = 4
_MAX_TOOTH_SURFACES = 5


def _reverse_procedure_composite(item) -> str:
    coding = item.productOrService.coding[0] if item.productOrService and item.productOrService.coding else None
    if coding is None or not coding.code:
        return "AD:"
    if coding.system == CDT_CODE_SYSTEM:
        return f"AD:{coding.code}"
    marker = f"{PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:"
    if coding.system and coding.system.startswith(marker):
        return f"{coding.system[len(marker):]}:{coding.code}"
    return f"AD:{coding.code}"


def _reverse_tooth_number(item) -> str:
    if not item.bodySite or not item.bodySite.coding:
        return ""
    return item.bodySite.coding[0].code or ""


def _reverse_tooth_qualifier(item) -> str:
    if not item.bodySite or not item.bodySite.coding:
        return TOO_UNIVERSAL_QUALIFIER
    coding = item.bodySite.coding[0]
    if coding.system == TOOTH_NUMBER_SYSTEM:
        return TOO_UNIVERSAL_QUALIFIER
    marker = f"{TOOTH_NUMBER_FALLBACK_SYSTEM}:"
    if coding.system and coding.system.startswith(marker):
        return coding.system[len(marker):]
    return TOO_UNIVERSAL_QUALIFIER


def _reverse_tooth_surfaces(item) -> str:
    if not item.subSite:
        return ""
    codes = []
    for concept in item.subSite[:_MAX_TOOTH_SURFACES]:
        if concept.coding and concept.coding[0].code:
            codes.append(concept.coding[0].code)
    return ":".join(codes)


def _build_too_segment(item) -> str:
    tooth_number = _reverse_tooth_number(item)
    surfaces = _reverse_tooth_surfaces(item)
    if not tooth_number and not surfaces:
        return ""
    qualifier = _reverse_tooth_qualifier(item) if tooth_number else TOO_UNIVERSAL_QUALIFIER
    return f"TOO*{qualifier}*{tooth_number}*{surfaces}~"


def _build_sv3_segment(item) -> str:
    """Built via a positionally-indexed 11-element fields list, not
    hand-counted asterisks - the same discipline
    app/edi/claim_837d_generator.py::_build_sv3 already established for
    this identical segment, adopted here after an earlier manual
    star-counting mistake elsewhere in this project's own EDI fixtures
    (see docs/build-history.md's own testing guidance)."""
    fields = [""] * 11
    fields[0] = _reverse_procedure_composite(item)  # SV3-01
    # :.2f, not str(Decimal) - see remittance_835.py's own
    # _build_bpr_segment comment for why str() is vulnerable to
    # trailing-zero loss once a Bundle has round-tripped through JSON.
    fields[1] = f"{item.unitPrice.value:.2f}" if item.unitPrice else "0.00"  # SV3-02
    fields[5] = str(item.quantity.value) if item.quantity else "1"  # SV3-06
    pointers = (item.diagnosisSequence or [])[:_MAX_DIAGNOSIS_POINTERS]
    if pointers:
        fields[10] = ":".join(str(p) for p in pointers)  # SV3-11
    return "SV3*" + "*".join(fields) + "~"


def _build_service_line_segments(sequence: int, item) -> list[str]:
    segments = [f"LX*{sequence}~", _build_sv3_segment(item)]
    too_segment = _build_too_segment(item)
    if too_segment:
        segments.append(too_segment)
    if item.servicedDate:
        segments.append(f"DTP*472*D8*{format_x12_date(item.servicedDate)}~")
    return segments


def _resolve_pos_code(claim) -> str:
    if not claim.item:
        return ""
    location = claim.item[0].locationCodeableConcept
    if not location or not location.coding:
        return ""
    for coding in location.coding:
        if coding.system == POS_CODE_SYSTEM and coding.code:
            return coding.code
    return ""


def _build_clm_segment(claim) -> str:
    claim_id = sanitize_x12_text(claim.identifier[0].value) if claim.identifier and claim.identifier[0].value else "0000000000"
    # :.2f, not str(Decimal) - see remittance_835.py's own _build_bpr_segment
    # comment for why str() is vulnerable to trailing-zero loss once a
    # Bundle has round-tripped through JSON.
    total = f"{claim.total.value:.2f}" if claim.total else "0.00"
    pos_code = _resolve_pos_code(claim)
    location_composite = f"{pos_code}:B:1" if pos_code else ""
    fields = [claim_id, total, "", "", location_composite, "Y", "A", "Y", "I"]
    return "CLM*" + "*".join(fields) + "~"


class Edi837dBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        claim = find_resource(bundle, "Claim")
        if claim is None:
            raise MappingError("Bundle has no Claim resource - cannot build an 837D message")

        billing_provider = resolve_by_reference(bundle, claim.provider)
        if billing_provider is None:
            raise MappingError("Bundle has no resolvable billing provider - cannot build an 837D message")
        payer = resolve_by_reference(bundle, claim.insurer)
        if payer is None:
            raise MappingError("Bundle has no resolvable payer - cannot build an 837D message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build an 837D message")
        subscriber, dependent = resolve_subscriber_and_dependent(bundle, patients)
        patient_loop_is_dependent = dependent is not None

        rendering_provider = None
        if claim.careTeam:
            rendering_provider = resolve_by_reference(bundle, claim.careTeam[0].provider)

        now = envelope_datetime(bundle.timestamp)
        bht_reference = sanitize_x12_text(bundle.identifier.value) if bundle.identifier and bundle.identifier.value else "REF00000001"

        envelope_segments = build_envelope_segments(now)
        st_to_hl_segments = [
            f"ST*837*{DEFAULT_ST_CONTROL}*{VERSION}~",
            f"BHT*0019*00*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}*CH~",
            "HL*1**20*1~",
            org_or_person_nm1("85", billing_provider),
            f"HL*2*1*22*{1 if patient_loop_is_dependent else 0}~",
        ]
        # SBR precedes NM1*IL in the 2000B loop, which is where the forward
        # direction reads it from.
        sbr = build_sbr_segment(find_resource(bundle, "Coverage"), not patient_loop_is_dependent)
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
        hi_segment = build_hi_segment(claim.diagnosis)
        if hi_segment:
            st_to_hl_segments.append(hi_segment)
        if rendering_provider is not None:
            st_to_hl_segments.append(org_or_person_nm1("82", rendering_provider))
            prv = build_prv_segment(claim, "PE")
            if prv:
                st_to_hl_segments.append(prv)

        for sequence, item in enumerate(claim.item or [], start=1):
            st_to_hl_segments.extend(_build_service_line_segments(sequence, item))

        trailer_segments = build_trailer_segments(st_to_hl_segments, [])
        return "".join(envelope_segments + st_to_hl_segments + trailer_segments)
