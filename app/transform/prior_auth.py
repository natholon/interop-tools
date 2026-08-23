"""FHIR Bundle -> X12 278 (Health Care Services Review), request or response.

**The direction is chosen by the caller's target trigger, not read off any
FHIR field** - mirroring the forward direction's unusual shape, where one
`ST01="278"` covers both and only `BHT02` separates them. So there are two
builders, `Edi278RequestBuilder`/`Edi278ResponseBuilder`, emitting a
`Claim`-only or `Claim`+`ClaimResponse` message respectively, registered
under synthetic triggers `"278REQUEST"`/`"278RESPONSE"` (the same synthetic
strings `app/edi/generator.py` uses for this family) since there is no
second `ST01` to key a second entry off.

Reverses `app/edi/prior_auth.py::_build_claim`/`_build_claim_response`
field-for-field: `UM03` (service type, reusing `SERVICE_TYPE_CODE_SYSTEM`),
`HI` diagnosis composites via the shared `build_hi_segment`, and
`HCR01`/`HCR02`/`HCR03` for the response.

**Resolution reuses 270/271's shared resolvers directly**, unlike
`claim_status.py`'s: 278 builds a real `Coverage` (276/277 never does), so
`resolve_subscriber_and_dependent` has its signal, and
`Claim.insurer`/`.provider` are direct references, so
`resolve_payer_and_provider` applies unchanged. As with 837P, a dependent
that exists at all is unconditionally also the patient - the one carrying
the 2000E loop - so `dependent is not None` alone decides it.

Disclosed round-trip fidelity gaps:
- `HI`'s qualifier cannot be recovered: `"ABK"`/`"ABF"` (ICD-10-CM) and
  `"BK"`/`"BF"` (ICD-9-CM) each map to one FHIR system per pair. Reversed
  by the positional convention every real sender uses - position 1 is
  principal, every other position is other.
- `HCR01`/`HCR03` are read back from the adjudication's own coding, which
  carries the exact original code (not the coarser `.outcome` derived from
  it), falling back to `"A1"` only when no adjudication exists.
- `UM01`/`UM02`/`UM04`+ have no FHIR home and get fixed placeholders."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.common import SERVICE_TYPE_CODE_SYSTEM
from app.edi.generator import format_x12_date, format_x12_time
from app.edi.prior_auth import BHT02_REQUEST, BHT02_RESPONSE, HCR_ACTION_SYSTEM, HCR_REASON_SYSTEM
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources
from app.transform.edi_common import (
    DEFAULT_ST_CONTROL,
    build_dmg,
    build_envelope_segments,
    build_hi_segment,
    build_org_nm1,
    build_person_nm1,
    build_trailer_segments,
    envelope_datetime,
    resolve_payer_and_provider,
    resolve_subscriber_and_dependent,
    sanitize_x12_text,
)

# Reverse of app.edi.prior_auth.HCR01_TO_OUTCOME - "complete" is genuinely
# many-to-one (A1/A3 both map to it); "A1" (Certified in Total) is the
# disclosed representative, preferred over "A3" (Not Certified) as the
# more common real-world outcome, matching this app's own "default to the
# most common real value" precedent used throughout.
_OUTCOME_TO_HCR01 = {"complete": "A1", "partial": "A2", "queued": "A4"}
_DEFAULT_HCR01 = "A1"


def _build_um_segment(claim) -> str:
    item = claim.item[0] if claim.item else None
    coding = item.productOrService.coding[0] if item and item.productOrService and item.productOrService.coding else None
    service_type_code = coding.code if coding and coding.system == SERVICE_TYPE_CODE_SYSTEM else ""
    return f"UM*HS*I*{service_type_code}~"


def _resolve_hcr_fields(response) -> tuple[str, str]:
    """Reverse of `_build_claim_response`'s own `HCR01`/`HCR03` handling:
    `ClaimResponseItemAdjudication`'s own coding carries the real,
    original code (used directly when present), falling back to a
    disclosed representative only when no adjudication exists at all -
    see this module's own docstring for why that's the 276-request-side
    case here, not something a real 278 response should ever hit."""
    action_code = ""
    reason_code = ""
    for adjudication in (response.item[0].adjudication if response.item else []) or []:
        if not (adjudication.category and adjudication.category.coding):
            continue
        for coding in adjudication.category.coding:
            if coding.system == HCR_ACTION_SYSTEM and coding.code:
                action_code = coding.code
            elif coding.system == HCR_REASON_SYSTEM and coding.code:
                reason_code = coding.code
    if not action_code:
        action_code = _OUTCOME_TO_HCR01.get(response.outcome, _DEFAULT_HCR01)
    return action_code, reason_code


def _build_hcr_segment(response) -> str:
    action_code, reason_code = _resolve_hcr_fields(response)
    fields = [action_code, response.preAuthRef or "", reason_code]
    while fields and not fields[-1]:
        fields.pop()
    return f"HCR*{'*'.join(fields)}~"


class _BasePriorAuthBuilder(MessageBuilder):
    bht02: str
    include_response: bool

    def build_message(self, bundle: Bundle) -> str:
        claim = find_resource(bundle, "Claim")
        if claim is None:
            raise MappingError("Bundle has no Claim resource - cannot build a 278 message")
        payer, requester = resolve_payer_and_provider(bundle, claim.insurer, claim.provider)
        if payer is None:
            raise MappingError("Bundle has no resolvable payer - cannot build a 278 message")
        if requester is None:
            raise MappingError("Bundle has no resolvable requester - cannot build a 278 message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build a 278 message")
        subscriber, dependent = resolve_subscriber_and_dependent(bundle, patients)
        patient_loop_is_dependent = dependent is not None

        now = envelope_datetime(bundle.timestamp)
        bht_reference = sanitize_x12_text(bundle.identifier.value) if bundle.identifier and bundle.identifier.value else "REF00000001"

        envelope_segments = build_envelope_segments(now)
        st_to_hl_segments = [
            f"ST*278*{DEFAULT_ST_CONTROL}~",
            f"BHT*0007*{self.bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
            "HL*1**20*1~",
            build_org_nm1("X3", payer) if payer.get_resource_type() == "Organization" else build_person_nm1(
                "X3", payer, include_id=True
            ),
            "HL*2*1*21*1~",
            build_org_nm1("1P", requester)
            if requester.get_resource_type() == "Organization"
            else build_person_nm1("1P", requester, include_id=True),
            f"HL*3*2*22*{0 if patient_loop_is_dependent else 1}~",
            build_person_nm1("IL", subscriber, include_id=True),
        ]
        subscriber_dmg = build_dmg(subscriber)
        if subscriber_dmg:
            st_to_hl_segments.append(subscriber_dmg)

        patient_loop_hl_id = 3
        next_hl = 4
        if patient_loop_is_dependent:
            st_to_hl_segments.append(f"HL*{next_hl}*3*23*1~")
            # Unlike 270's own dependent (which real 270 examples commonly
            # leave without a member-specific id), a real 278 dependent
            # loop does carry its own NM109 - the fetched X12.org example
            # this family's own module docstring cites includes one, and
            # the forward reader (_build_nm1_identifier) reads it whenever
            # present regardless of role - so omitting it here would
            # silently drop real data the source message actually had.
            st_to_hl_segments.append(build_person_nm1("QC", dependent, include_id=True))
            dependent_dmg = build_dmg(dependent)
            if dependent_dmg:
                st_to_hl_segments.append(dependent_dmg)
            patient_loop_hl_id = next_hl
            next_hl += 1

        st_to_hl_segments.append(f"HL*{next_hl}*{patient_loop_hl_id}*EV*0~")
        st_to_hl_segments.append(_build_um_segment(claim))
        hi_segment = build_hi_segment(claim.diagnosis)
        if hi_segment:
            st_to_hl_segments.append(hi_segment)

        body_segments = []
        if self.include_response:
            response = find_resource(bundle, "ClaimResponse")
            if response is not None:
                body_segments.append(_build_hcr_segment(response))

        trailer_segments = build_trailer_segments(st_to_hl_segments, body_segments)
        return "".join(envelope_segments + st_to_hl_segments + body_segments + trailer_segments)


class Edi278RequestBuilder(_BasePriorAuthBuilder):
    bht02 = BHT02_REQUEST
    include_response = False


class Edi278ResponseBuilder(_BasePriorAuthBuilder):
    bht02 = BHT02_RESPONSE
    include_response = True
