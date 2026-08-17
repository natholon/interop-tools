"""FHIR Bundle -> X12 837D (Health Care Claim: Dental, 005010X224A2) - the
seventeenth reverse-direction slice, completing the reverse direction for
every EDI transaction-set family this app's forward direction supports (the
full "big five" HIPAA suite: 270/271, 276/277, 278, 835, 837P/837I/837D).

Reverses `app/edi/claim_837d.py::_build_service_line_item`/
`_build_procedure_concept`/`_build_tooth_body_site`/`_build_tooth_sub_sites`/
`resolve_line_dtp_raw_date`/`Edi837dBuilder.build_bundle` field-for-field:
`CLM01`/`CLM02`/`CLM05` (claim id/total charge/place-of-service composite -
the SAME vocabulary as 837P's, confirmed by the forward module's own
docstring, unlike 837I's genuinely different UB-04 one), `HI` (diagnosis
composites, reusing `app.transform.edi_common.build_hi_segment`, its fourth
real consumer), one `LX`+`SV3`(+`TOO`)(+`DTP`) group per `Claim.item[]`
(CDT procedure composite, charge, quantity, diagnosis-pointer composite,
tooth number/surfaces, service date).

**Resolution logic is identical to 837P's/837I's own, now via the shared
`app.transform.edi_common.resolve_by_reference`/`org_or_person_nm1`
promoted alongside this slice**: `Claim.provider`/`.insurer`/
`.careTeam[].provider` (rendering provider, `NM1*82` - the same code as
837P's, NOT 837I's `NM1*71` attending provider) are all real, direct
references. This module is the THIRD real consumer of what had been two
independently-duplicated private copies (`_resolve_by_reference`/
`_org_or_person_nm1`) in `claim_837p.py`/`claim_837i.py` - promoted here to
`edi_common.py` as `resolve_by_reference`/`org_or_person_nm1`, mirroring
the identical "extract on third real consumer" discipline the *forward*
side's own `resolve_837_loops` was promoted under once 837D itself became
its third consumer (see `app/edi/common.py::Resolved837Loops`'s own
docstring) - both `claim_837p.py` and `claim_837i.py` were updated to call
the shared functions instead of keeping their own copies. Subscriber/
dependent resolution reuses 270/271's own shared resolver directly, the
identical way 837P's/837I's own reverse slices already do.

**Two genuinely new pieces of reversal logic neither 837P nor 837I needed**:
  - **The procedure-code qualifier defaults to `"AD"` (CDT), not `"HC"`**
    (837P's/837I's own default) - `_reverse_procedure_composite` reuses the
    identical disclosed-fallback-marker pattern (a coding whose `system`
    starts with `app.edi.claim_837p.PROCEDURE_QUALIFIER_FALLBACK_SYSTEM`
    recovers its original qualifier; `app.edi.claim_837d.CDT_CODE_SYSTEM`
    recovers `"AD"`; anything else defaults to `"AD"`, the dominant
    real-world dental case per the forward module's own docstring) - reused
    directly from `app.edi.claim_837p`'s now-public constant rather than
    duplicated, since both 837P's and 837D's own forward modules use the
    identical fallback-marker string (confirmed by direct comparison, not
    assumed).
  - **`TOO` (Tooth Information) has no 837P/837I equivalent at all** -
    `_build_too_segment` reverses `Claim.item.bodySite` (tooth number, via
    the same disclosed-fallback-marker pattern using the newly-public
    `app.edi.claim_837d.TOO_UNIVERSAL_QUALIFIER`/`TOOTH_NUMBER_SYSTEM`/
    `TOOTH_NUMBER_FALLBACK_SYSTEM`) and `Claim.item.subSite` (up to 5
    surface-letter codings, joined `:`-separated back into TOO03's own
    composite) into one `TOO` segment - emitted whenever either field is
    present, with the other field left empty when only one of the two
    resolves, so a `bodySite`-only or `subSite`-only item still round-trips
    its one populated field rather than losing it to an all-or-nothing emit
    decision. Each `subSite` entry's own coding `system` isn't re-checked
    against `app.edi.claim_837d.TOOTH_SURFACE_SYSTEM` before reversal -
    unlike `bodySite`'s own disclosed-fallback-marker check, nothing else
    in this app ever populates `Claim.item.subSite`, so there's no
    ambiguous source to disambiguate.

**A genuinely different DTP*472 reversal than 837P's/837I's own, disclosed
rather than guessed at**: the forward module's own `resolve_line_dtp_raw_date`
prefers a per-line `DTP*472` over the one claim-level default, but nothing
on the resulting `Claim.item.servicedDate` distinguishes which source it
actually came from - both collapse to the identical FHIR field. This
builder always regenerates a per-line `DTP*472` for every item that has a
`servicedDate` (never a claim-level one), the same "always the more
specific, always-correct-on-the-next-forward-pass shape" simplification
`hl7_oru.py`'s own OBX-11 status gap and `cda_ccd.py`'s own Procedures-
section identifier gap already established elsewhere in this app - a
per-line DTP is read first by `resolve_line_dtp_raw_date` regardless, so
this is round-trip-safe even though it doesn't reproduce the original
claim-level-vs-per-line split.

**Disclosed round-trip fidelity gaps inherited directly from the forward
side, the same "no source field" precedent every earlier EDI reverse slice
already established**: `SV3-04`/`SV3-05`/`SV3-07`-`SV3-10` (oral cavity
designation, prosthesis code, free-text reason, copay/agreement/
predetermination indicators) and `2010AB`/`2310C`/`2320`/`2330` (Pay-to
Provider/Service Facility Location/Other Subscriber COB) have no FHIR-side
home at all - the forward mapper never reads any of them - so none are
regenerated here. Unlike 837P's/837I's own diagnosis-pointer reversal
(which defaults to pointer `[1]` when `Claim.item.diagnosisSequence` is
unset), this builder leaves SV3-11 genuinely empty when no pointers are
present, since dental claims commonly carry no diagnosis at all - forcing
a fabricated pointer `"1"` when `Claim.diagnosis` might not even have a
position 1 would regenerate an unresolvable pointer, not a safe default."""

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
    org_or_person_nm1,
    resolve_by_reference,
    resolve_subscriber_and_dependent,
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
    (see CLAUDE.md's own testing guidance)."""
    fields = [""] * 11
    fields[0] = _reverse_procedure_composite(item)  # SV3-01
    fields[1] = str(item.unitPrice.value) if item.unitPrice else "0.00"  # SV3-02
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
    claim_id = claim.identifier[0].value if claim.identifier else "0000000000"
    total = str(claim.total.value) if claim.total else "0.00"
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
        bht_reference = bundle.identifier.value if bundle.identifier else "REF00000001"

        envelope_segments = build_envelope_segments(now)
        st_to_hl_segments = [
            f"ST*837*{DEFAULT_ST_CONTROL}*{VERSION}~",
            f"BHT*0019*00*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}*CH~",
            "HL*1**20*1~",
            org_or_person_nm1("85", billing_provider),
            f"HL*2*1*22*{0 if patient_loop_is_dependent else 1}~",
            org_or_person_nm1("IL", subscriber),
        ]
        subscriber_dmg = build_dmg(subscriber)
        if subscriber_dmg:
            st_to_hl_segments.append(subscriber_dmg)
        st_to_hl_segments.append(org_or_person_nm1("PR", payer))

        if patient_loop_is_dependent:
            st_to_hl_segments.append("HL*3*2*23*0~")
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

        for sequence, item in enumerate(claim.item or [], start=1):
            st_to_hl_segments.extend(_build_service_line_segments(sequence, item))

        trailer_segments = build_trailer_segments(st_to_hl_segments, [])
        return "".join(envelope_segments + st_to_hl_segments + trailer_segments)
