"""FHIR Bundle -> X12 270 (Health Care Eligibility Benefit Inquiry) - the
third reverse-direction slice, and the first proof this architecture
generalizes to a delimited-text format with its own self-describing
envelope (ISA/GS/ST...SE/GE/IEA) and HL-hierarchy loop structure, not just
to pipe-delimited HL7v2 and XML C-CDA. Scoped to 270's own real Phase-1
forward scope (see app/edi/eligibility_270.py's own module docstring: payer
2000A / provider 2000B / subscriber 2000C / optional dependent 2000D, one
EQ item, one DTP serviced date) - 270/271 had no earlier, narrower slice
the way CCD's Problems-only or ADT's A01-only slices did, so there's no
"first slice within 270" to further reduce this to.

Reverses app/edi/common.py::build_organization_from_nm1/
build_practitioner_from_nm1/build_patient_from_nm1_dmg/
build_coverage/build_service_type_category field-for-field, using each
one's own exact NM1/DMG element positions and the same NM1_ID_QUALIFIER_
SYSTEM table (in reverse) that resolve_id_qualifier_system builds forward.

**Resolving which Patient is the subscriber vs. the dependent is the one
piece of real cross-cutting logic this slice needed that the other two
didn't**: unlike Patient/Practitioner/Organization, nothing on a bare
FHIR Patient resource itself distinguishes "subscriber" from "dependent" -
both are plain Patient resources. The signal lives one hop away, on the
Coverage resource this app's own build_coverage() always produces
alongside them: Coverage.subscriber references the subscriber Patient,
Coverage.beneficiary references "the patient" (the dependent when one
exists, else the subscriber itself) - resolving both references is how
this builder tells the two apart, rather than guessing from Bundle order.

**Resolving which Organization is the payer vs. the provider is the same
kind of one-hop lookup**: CoverageEligibilityRequest.insurer references the
payer, .provider references the provider - used instead of assuming Bundle
order, even though the forward mapper's own resource-append order happens
to make order-based guessing work too (payer, provider, subscriber, ...) -
resolving the real reference is the more honest approach, not reliant on
an ordering contract this module doesn't own.

**A real, disclosed round-trip fidelity gap, not a bug**: several 270
fields the forward mapper reads have no FHIR-side home at all (ISA/GS
envelope-level sender/receiver ids, control numbers) and get a fixed,
disclosed placeholder value on the way back out, the same "no source
field, use a disclosed placeholder" precedent app/transform/hl7_adt_a01.py
already established for MSH-3/4/5/6."""

import datetime

from fhir.resources.R4B.bundle import Bundle

from app.edi.common import NM1_ID_QUALIFIER_SYSTEM, SERVICE_TYPE_CODE_SYSTEM
from app.edi.generator import build_isa, format_x12_date, format_x12_time
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_date

_GS08_VERSION = "005010X279A1"
# Reverse of app.edi.common.NM1_ID_QUALIFIER_SYSTEM - "MI" (Member ID) is
# the disclosed default for a resource with no cleanly-recoverable
# qualifier, the same category of "most common real value" default this
# app's own forward-direction fallbacks already use elsewhere (e.g.
# Medications' moodCode fallback).
_SYSTEM_TO_NM1_QUALIFIER = {system: qualifier for qualifier, system in NM1_ID_QUALIFIER_SYSTEM.items()}
_DEFAULT_ID_QUALIFIER = "MI"
_NM1_ID_MARKER = "urn:interop-tools:x12-nm1-id:"
_GENDER_TO_DMG_CODE = {"male": "M", "female": "F", "unknown": "U"}

_DEFAULT_SENDER_ID = "SENDERINTEROP"
_DEFAULT_RECEIVER_ID = "RECEIVERINTEROP"
_DEFAULT_ISA_CONTROL = "000000001"
_DEFAULT_GS_CONTROL = "1"
_DEFAULT_ST_CONTROL = "0001"


def _reverse_nm1_qualifier(identifier) -> str:
    if identifier is None or not identifier.system:
        return _DEFAULT_ID_QUALIFIER
    if identifier.system in _SYSTEM_TO_NM1_QUALIFIER:
        return _SYSTEM_TO_NM1_QUALIFIER[identifier.system]
    if identifier.system.startswith(_NM1_ID_MARKER):
        return identifier.system[len(_NM1_ID_MARKER) :]
    return _DEFAULT_ID_QUALIFIER


def _build_org_nm1(entity_code: str, organization) -> str:
    name = organization.name or "UNKNOWN"
    identifier = organization.identifier[0] if organization.identifier else None
    if identifier and identifier.value:
        qualifier = _reverse_nm1_qualifier(identifier)
        return f"NM1*{entity_code}*2*{name}*****{qualifier}*{identifier.value}~"
    return f"NM1*{entity_code}*2*{name}~"


def _build_person_nm1(entity_code: str, person, include_id: bool) -> str:
    name = person.name[0] if person.name else None
    family = (name.family or "") if name else ""
    given = name.given[0] if name and name.given else ""
    identifier = person.identifier[0] if person.identifier else None
    if include_id and identifier and identifier.value:
        qualifier = _reverse_nm1_qualifier(identifier)
        return f"NM1*{entity_code}*1*{family}*{given}****{qualifier}*{identifier.value}~"
    return f"NM1*{entity_code}*1*{family}*{given}~"


def _build_dmg(person) -> str:
    if not person.birthDate and not person.gender:
        return ""
    dob = format_hl7_date(person.birthDate) if person.birthDate else ""
    sex = _GENDER_TO_DMG_CODE.get(person.gender, "U") if person.gender else "U"
    return f"DMG*D8*{dob}*{sex}~"


def _resolve_payer_and_provider(bundle: Bundle, request):
    organizations = find_resources(bundle, "Organization")
    by_id = {o.id: o for o in organizations}
    payer = by_id.get(request.insurer.reference.removeprefix("urn:uuid:")) if request and request.insurer else None
    provider = None
    if request is not None and request.provider:
        provider_id = request.provider.reference.removeprefix("urn:uuid:")
        provider = by_id.get(provider_id) or find_resource(bundle, "Practitioner")
        if provider is not None and provider.get_resource_type() not in ("Organization", "Practitioner"):
            provider = None
    if payer is None and organizations:
        payer = organizations[0]
    if provider is None:
        remaining_orgs = [o for o in organizations if payer is None or o.id != payer.id]
        provider = remaining_orgs[0] if remaining_orgs else find_resource(bundle, "Practitioner")
    return payer, provider


def _resolve_subscriber_and_dependent(bundle: Bundle, patients: list):
    """Returns (subscriber, dependent_or_none) - see module docstring for
    why this needs the Coverage resource, not just the Patient list."""
    if len(patients) == 1:
        return patients[0], None
    coverage = find_resource(bundle, "Coverage")
    if coverage is None or not coverage.subscriber or not coverage.beneficiary:
        # No reliable signal - fall back to Bundle order rather than
        # raising, the same "degrade gracefully on ambiguous input" spirit
        # every disclosed fallback in this app already follows.
        return patients[0], patients[1]
    by_id = {p.id: p for p in patients}
    subscriber_id = coverage.subscriber.reference.removeprefix("urn:uuid:")
    beneficiary_id = coverage.beneficiary.reference.removeprefix("urn:uuid:")
    subscriber = by_id.get(subscriber_id, patients[0])
    dependent = by_id.get(beneficiary_id) if beneficiary_id != subscriber_id else None
    return subscriber, dependent


def _envelope_datetime(bundle_timestamp) -> datetime.datetime:
    return bundle_timestamp if bundle_timestamp is not None else datetime.datetime.now(datetime.timezone.utc)


class Edi270Builder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        request = find_resource(bundle, "CoverageEligibilityRequest")
        payer, provider = _resolve_payer_and_provider(bundle, request)
        if payer is None:
            raise MappingError("Bundle has no Organization resource - cannot resolve the payer for a 270 message")
        if provider is None:
            raise MappingError("Bundle has no provider resource (Organization/Practitioner) for a 270 message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build a 270 message")
        subscriber, dependent = _resolve_subscriber_and_dependent(bundle, patients)

        now = _envelope_datetime(bundle.timestamp)
        bht_reference = bundle.identifier.value if bundle.identifier else "REF00000001"

        envelope_segments = [
            build_isa(_DEFAULT_ISA_CONTROL, _DEFAULT_SENDER_ID, _DEFAULT_RECEIVER_ID, now),
            (
                f"GS*HS*{_DEFAULT_SENDER_ID}*{_DEFAULT_RECEIVER_ID}*{format_x12_date(now)}*"
                f"{format_x12_time(now)}*{_DEFAULT_GS_CONTROL}*X*{_GS08_VERSION}~"
            ),
        ]

        st_to_hl_segments = [
            f"ST*270*{_DEFAULT_ST_CONTROL}~",
            f"BHT*0022*13*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
            "HL*1**20*1~",
            _build_org_nm1("PR", payer),
            "HL*2*1*21*1~",
            _build_org_nm1("1P", provider)
            if provider.get_resource_type() == "Organization"
            else _build_person_nm1("1P", provider, include_id=True),
            f"HL*3*2*22*{1 if dependent else 0}~",
            _build_person_nm1("IL", subscriber, include_id=True),
        ]
        subscriber_dmg = _build_dmg(subscriber)
        if subscriber_dmg:
            st_to_hl_segments.append(subscriber_dmg)
        if dependent is not None:
            st_to_hl_segments.append("HL*4*3*23*0~")
            st_to_hl_segments.append(_build_person_nm1("QC", dependent, include_id=False))
            dependent_dmg = _build_dmg(dependent)
            if dependent_dmg:
                st_to_hl_segments.append(dependent_dmg)

        body_segments = []
        if request is not None and request.item:
            for item in request.item:
                if item.category and item.category.coding:
                    coding = item.category.coding[0]
                    if coding.system == SERVICE_TYPE_CODE_SYSTEM and coding.code:
                        body_segments.append(f"EQ*{coding.code}~")
        if not body_segments:
            body_segments.append("EQ*30~")
        if request is not None and request.servicedDate:
            body_segments.append(f"DTP*291*D8*{format_hl7_date(request.servicedDate)}~")

        se01 = len(st_to_hl_segments) + len(body_segments) + 1
        trailer_segments = [
            f"SE*{se01}*{_DEFAULT_ST_CONTROL}~",
            f"GE*1*{_DEFAULT_GS_CONTROL}~",
            f"IEA*1*{_DEFAULT_ISA_CONTROL}~",
        ]

        return "".join(envelope_segments + st_to_hl_segments + body_segments + trailer_segments)
