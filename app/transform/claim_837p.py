"""FHIR Bundle -> X12 837P (Health Care Claim: Professional, 005010X222A2)
- the fourteenth reverse-direction slice, and the first EDI reverse slice
for the deepest, most cross-referential loop shape of any EDI phase this
app has reversed so far: a claim-level `HI` diagnosis list cross-
referenced by position from each service line's own diagnosis-pointer
composite, plus a rendering provider (`Claim.careTeam[]`) distinct from
the billing provider (`Claim.provider`).

Reverses `app/edi/claim_837p.py::_build_service_line_item`/
`_build_procedure_concept`/`_build_diagnosis_pointers`/`Edi837pBuilder.
build_bundle` field-for-field: `CLM01`/`CLM02`/`CLM05` (claim id/total
charge/place-of-service composite), `HI` (diagnosis composites, reusing
`app.transform.edi_common.build_hi_segment`, its second real consumer),
one `LX`+`SV1`(+`DTP`) group per `Claim.item[]` (procedure composite,
charge, quantity, diagnosis-pointer composite, service date).

**Payer/billing-provider/rendering-provider resolution is the simplest,
most direct of any EDI reverse slice so far**: `Claim.provider`/
`.insurer`/`.careTeam[].provider` are all real, direct references to
their own resource - no Bundle-order fallback, no exclusion logic, and
deliberately *not* a reuse of `edi_common.py`'s own `resolve_payer_and_
provider` (which assumes exactly one payer + one non-payer participant to
disambiguate - 837P has a *third* non-payer participant, the rendering
provider, that could just as easily be Practitioner-typed as the billing
provider, so that function's own Organization-only-then-first-
Practitioner fallback could silently resolve to the wrong one). A plain
by-id lookup across every `Organization`/`Practitioner` in the Bundle,
keyed off each field's own real reference, sidesteps the ambiguity
entirely rather than risking it.

**Subscriber/dependent resolution reuses 270/271's own shared resolver
directly**: 837P builds a real `Coverage` the identical way 278 does, so
`resolve_subscriber_and_dependent` already has the signal it needs. The
identical structural simplification 278's own reverse slice already
established applies again here, confirmed - not assumed - from `app/edi/
common.py::Resolved837Loops`'s own docstring: whenever a dependent exists
in the source Bundle at all, it is unconditionally also "the patient"
(`Claim.patient`/`Coverage.beneficiary`), so `dependent is not None` alone
decides which loop the claim/service-line segments attach to, with no
extra check against `Claim.patient` needed.

**Disclosed round-trip fidelity gaps**: `SV1-01`'s own procedure-code
qualifier ambiguity (`"HC"` covers both CPT and HCPCS Level II, per the
forward module's own disclosed gap) is preserved via the same disclosed
local placeholder system, reversed back to the real original qualifier
when recoverable. `CLM03`/`CLM04`/`CLM06`-`CLM11`, `SV1-05`'s own per-line
place-of-service override, and `2010AB`/`2310C`/`2320`/`2330` (Pay-to
Provider/Service Facility Location/Other Subscriber COB) have no FHIR-side
home at all - the forward mapper never reads any of them - so each gets a
fixed, disclosed placeholder or is omitted entirely, the same "no source
field" precedent every earlier EDI reverse slice already established."""

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
    org_or_person_nm1,
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
    fields = [procedure_composite, charge, "UN", quantity, "", "", pointer_composite]
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
