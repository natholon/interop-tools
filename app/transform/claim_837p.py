"""FHIR Bundle -> X12 837P (Health Care Claim: Professional, 005010X222A2).

Reverses `app/edi/claim_837p.py` field-for-field: `CLM01`/`CLM02`/`CLM05`
(claim id, total charge, place-of-service), `HI` diagnosis composites via
the shared `build_hi_segment`, and one `LX`+`SV1`(+`DTP`) group per
`Claim.item[]` (procedure composite, charge, quantity, diagnosis pointers,
service date).

**Payer/billing/rendering-provider resolution is a plain by-id lookup**
over every `Organization`/`Practitioner` in the Bundle, keyed off
`Claim.provider`/`.insurer`/`.careTeam[].provider` - all real, direct
references. Deliberately **not** `edi_common.resolve_payer_and_provider`:
that assumes one payer plus one non-payer participant, but 837P has a
third (the rendering provider) which may be Practitioner-typed just like
the billing provider, so its Organization-first fallback could resolve to
the wrong one.

**Subscriber/dependent resolution reuses 270/271's shared resolver**: 837P
builds a real `Coverage`, so the signal is there. Whenever a dependent
exists at all it is unconditionally also the patient (per
`Resolved837Loops`), so `dependent is not None` alone decides which loop
the claim and service lines attach to.

Disclosed round-trip fidelity gaps:
- `SV1-01`'s `"HC"` qualifier ambiguity (CPT vs HCPCS) is preserved on the
  forward side's disclosed placeholder system and reversed back to the
  original qualifier when recoverable.
- `CLM03`/`CLM04`/`CLM06`-`CLM11`, `SV1-05`'s per-line place-of-service
  override, and `2010AB`/`2310C`/`2320`/`2330` have no FHIR home - the
  forward mapper never reads them - so each is omitted or fixed."""

from fhir.resources.R4B.bundle import Bundle

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
    reverse_quantity_unit,
    resolve_by_reference,
    resolve_subscriber_and_dependent,
    sanitize_x12_text,
)

VERSION = "005010X222A2"

_MAX_DIAGNOSIS_POINTERS = 4


def _reverse_procedure_composite(item) -> str:
    coding = item.productOrService.coding[0] if item.productOrService and item.productOrService.coding else None
    if coding is None or not coding.code:
        return "HC:"
    qualifier = "HC"
    marker = f"{PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:"
    if coding.system and coding.system.startswith(marker):
        qualifier = coding.system[len(marker) :]
    return f"{qualifier}:{coding.code}"


def _build_sv1_segment(item) -> str:
    procedure_composite = _reverse_procedure_composite(item)
    # :.2f, not str(Decimal) - see remittance_835.py's own
    # _build_bpr_segment comment for why str() is vulnerable to
    # trailing-zero loss once a Bundle has round-tripped through JSON.
    charge = f"{item.unitPrice.value:.2f}" if item.unitPrice else "0.00"
    quantity = str(item.quantity.value) if item.quantity else "1"
    pointer_composite = ":".join(str(p) for p in (item.diagnosisSequence or [1])[:_MAX_DIAGNOSIS_POINTERS])
    fields = [procedure_composite, charge, reverse_quantity_unit(item), quantity, "", "", pointer_composite]
    return "SV1*" + "*".join(fields) + "~"


def _build_service_line_segments(sequence: int, item) -> list[str]:
    segments = [f"LX*{sequence}~", _build_sv1_segment(item)]
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
    total = f"{claim.total.value:.2f}" if claim.total else "0.00"
    pos_code = _resolve_pos_code(claim)
    location_composite = f"{pos_code}:B:1" if pos_code else ""
    fields = [claim_id, total, "", "", location_composite, "Y", "A", "Y", "I"]
    return "CLM*" + "*".join(fields) + "~"


class Edi837pBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        claim = find_resource(bundle, "Claim")
        if claim is None:
            raise MappingError("Bundle has no Claim resource - cannot build an 837P message")

        billing_provider = resolve_by_reference(bundle, claim.provider)
        if billing_provider is None:
            raise MappingError("Bundle has no resolvable billing provider - cannot build an 837P message")
        payer = resolve_by_reference(bundle, claim.insurer)
        if payer is None:
            raise MappingError("Bundle has no resolvable payer - cannot build an 837P message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build an 837P message")
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
