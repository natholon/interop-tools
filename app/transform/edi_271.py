"""FHIR Bundle -> X12 271 (Health Care Eligibility Benefit Response) - the
fourth reverse-direction slice, 270's own sibling. Envelope/HL/NM1/DMG
construction is identical to 270's (see app/edi/eligibility_271.py's own
forward-direction docstring: "Envelope/HL/NM1/DMG walk is identical to
270's"), so this builder reuses every shared helper in
app/transform/edi_common.py directly - only the patient-loop leader
segment (`EB`, not `EQ`) and the target-resource field reads genuinely
diverge, mirroring the same "genuinely different structure, don't force
it through one shared helper" boundary app/edi/eligibility_270.py/
_271.py themselves already draw on the forward side.

Reverses app/edi/eligibility_271.py's own `_build_insurance_items`/
`_resolve_outcome_and_disposition` field-for-field: `EB01`/`EB03`/`EB05`/
`EB12` from `CoverageEligibilityResponseInsuranceItem.excluded`/
`.category`/`.description`/`.network` (local reverse dicts, plus the now-
public `EB12_NETWORK_TEXT` reversed - the same "only the disclosed subset
each maps cleanly back" way `service_type_category` already is for
`EQ`/`EB03`),
and `AAA01`/`AAA03` from `.outcome`/`.disposition` - the one field this
slice does NOT have a clean, general reverse for: `.disposition` is
free text built forward from a `", ".join`-ed set of reject-reason codes
with no delimiter contract a reverse parser could safely split back
apart, so this builder emits a single disclosed placeholder reject code
(`"85"`, X12's own generic "Dependent Ineligible" AAA03 catch-all) whenever
`.outcome == "error"`, rather than attempting to un-parse `.disposition`'s
own free text.

**`CoverageEligibilityResponse.provider` doesn't exist as a field at
all** (confirmed via `model_fields` while building `eligibility_271.py`
itself, forward direction) - `resolve_payer_and_provider` is called with
`provider_reference=None` here, falling back straight to "the
Organization/Practitioner that isn't the payer", unlike 270's own call
which has a real `.provider` reference to try first."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.common import SERVICE_TYPE_CODE_SYSTEM
from app.edi.eligibility_271 import EB12_NETWORK_TEXT
from app.edi.generator import format_x12_date, format_x12_time
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources
from app.transform.edi_common import (
    DEFAULT_ST_CONTROL,
    build_dmg,
    build_envelope_segments,
    build_org_nm1,
    build_person_nm1,
    build_trailer_segments,
    envelope_datetime,
    resolve_payer_and_provider,
    resolve_subscriber_and_dependent,
    sanitize_x12_text,
)

# Reverse of eligibility_271.py's own _EB01_EXCLUDED_MAP (module-private
# there, not reused directly - only its two values are needed here) - "1"
# (Active Coverage) is the disclosed default for an item with `.excluded`
# unset (a real, common real-world shape: not every EB carries an EB01
# this app's own forward mapper resolves).
_EXCLUDED_TO_EB01 = {False: "1", True: "6"}
# Reverse of eligibility_271.py's own EB12_NETWORK_TEXT.
_NETWORK_TEXT_TO_EB12 = {text: code for code, text in EB12_NETWORK_TEXT.items()}
# X12's own generic "Dependent Ineligible" AAA03 code - a disclosed
# placeholder emitted whenever .outcome == "error", since .disposition's
# own free text has no safe way to un-parse back into individual codes -
# see module docstring.
_DEFAULT_REJECT_CODE = "85"


def _build_eb(item) -> str:
    eb01 = "1"
    if item.excluded is not None:
        eb01 = _EXCLUDED_TO_EB01.get(item.excluded, "1")
    code = ""
    if item.category and item.category.coding:
        coding = item.category.coding[0]
        if coding.system == SERVICE_TYPE_CODE_SYSTEM:
            code = coding.code or ""
    description = sanitize_x12_text(item.description) if item.description else ""
    network = ""
    if item.network and item.network.text:
        network = _NETWORK_TEXT_TO_EB12.get(item.network.text, "")
    return f"EB*{eb01}*IND*{code}*HM*{description}*******{network}~"


class Edi271Builder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        response = find_resource(bundle, "CoverageEligibilityResponse")
        insurer_reference = response.insurer if response else None
        payer, provider = resolve_payer_and_provider(bundle, insurer_reference, None)
        if payer is None:
            raise MappingError("Bundle has no Organization resource - cannot resolve the payer for a 271 message")
        if provider is None:
            raise MappingError("Bundle has no provider resource (Organization/Practitioner) for a 271 message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build a 271 message")
        subscriber, dependent = resolve_subscriber_and_dependent(bundle, patients)

        now = envelope_datetime(bundle.timestamp)
        bht_reference = sanitize_x12_text(bundle.identifier.value) if bundle.identifier and bundle.identifier.value else "REF00000001"

        envelope_segments = build_envelope_segments(now)

        st_to_hl_segments = [
            f"ST*271*{DEFAULT_ST_CONTROL}~",
            f"BHT*0022*11*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
            "HL*1**20*1~",
            build_org_nm1("PR", payer),
            "HL*2*1*21*1~",
            build_org_nm1("1P", provider)
            if provider.get_resource_type() == "Organization"
            else build_person_nm1("1P", provider, include_id=True),
            f"HL*3*2*22*{1 if dependent else 0}~",
            build_person_nm1("IL", subscriber, include_id=True),
        ]
        subscriber_dmg = build_dmg(subscriber)
        if subscriber_dmg:
            st_to_hl_segments.append(subscriber_dmg)
        if dependent is not None:
            st_to_hl_segments.append("HL*4*3*23*0~")
            st_to_hl_segments.append(build_person_nm1("QC", dependent, include_id=False))
            dependent_dmg = build_dmg(dependent)
            if dependent_dmg:
                st_to_hl_segments.append(dependent_dmg)

        body_segments = []
        insurance = response.insurance[0] if response is not None and response.insurance else None
        items = insurance.item if insurance is not None and insurance.item else []
        for item in items:
            body_segments.append(_build_eb(item))
        if not body_segments:
            body_segments.append("EB*1*IND*30*HM*******~")
        if response is not None and response.outcome == "error":
            body_segments.append(f"AAA*N**{_DEFAULT_REJECT_CODE}*C~")

        trailer_segments = build_trailer_segments(st_to_hl_segments, body_segments)

        return "".join(envelope_segments + st_to_hl_segments + body_segments + trailer_segments)
