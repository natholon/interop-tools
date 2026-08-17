"""Shared X12-reverse-direction helpers - the app/transform/ mirror of
app/edi/common.py's own role for the forward direction, extracted once
app/transform/edi_271.py became a second real consumer of the identical
NM1/DMG-building and payer/provider/subscriber-dependent-resolution logic
app/transform/edi_270.py had already built. 270 and 271 share their entire
envelope/HL-hierarchy shape (see app/edi/eligibility_271.py's own
docstring: "Envelope/HL/NM1/DMG walk is identical to 270's"), so this
module holds everything both transaction sets' reverse builders need,
leaving only each family's own leader-segment (EQ vs EB) and target-
resource construction in its own file - the same split
app/edi/eligibility_270.py/_271.py themselves already use for the forward
direction."""

import datetime

from fhir.resources.R4B.bundle import Bundle

from app.edi.common import NM1_ID_QUALIFIER_SYSTEM
from app.edi.generator import build_isa, format_x12_date, format_x12_time
from app.transform.common import find_resource, find_resources, format_hl7_date

GS08_VERSION = "005010X279A1"
# Reverse of app.edi.common.NM1_ID_QUALIFIER_SYSTEM - "MI" (Member ID) is
# the disclosed default for a resource with no cleanly-recoverable
# qualifier, the same category of "most common real value" default this
# app's own forward-direction fallbacks already use elsewhere (e.g.
# Medications' moodCode fallback).
_SYSTEM_TO_NM1_QUALIFIER = {system: qualifier for qualifier, system in NM1_ID_QUALIFIER_SYSTEM.items()}
_DEFAULT_ID_QUALIFIER = "MI"
_NM1_ID_MARKER = "urn:interop-tools:x12-nm1-id:"
_GENDER_TO_DMG_CODE = {"male": "M", "female": "F", "unknown": "U"}

DEFAULT_SENDER_ID = "SENDERINTEROP"
DEFAULT_RECEIVER_ID = "RECEIVERINTEROP"
DEFAULT_ISA_CONTROL = "000000001"
DEFAULT_GS_CONTROL = "1"
DEFAULT_ST_CONTROL = "0001"


def reverse_nm1_qualifier(identifier) -> str:
    if identifier is None or not identifier.system:
        return _DEFAULT_ID_QUALIFIER
    if identifier.system in _SYSTEM_TO_NM1_QUALIFIER:
        return _SYSTEM_TO_NM1_QUALIFIER[identifier.system]
    if identifier.system.startswith(_NM1_ID_MARKER):
        return identifier.system[len(_NM1_ID_MARKER) :]
    return _DEFAULT_ID_QUALIFIER


def build_org_nm1(entity_code: str, organization) -> str:
    name = organization.name or "UNKNOWN"
    identifier = organization.identifier[0] if organization.identifier else None
    if identifier and identifier.value:
        qualifier = reverse_nm1_qualifier(identifier)
        return f"NM1*{entity_code}*2*{name}*****{qualifier}*{identifier.value}~"
    return f"NM1*{entity_code}*2*{name}~"


def build_person_nm1(entity_code: str, person, include_id: bool) -> str:
    name = person.name[0] if person.name else None
    family = (name.family or "") if name else ""
    given = name.given[0] if name and name.given else ""
    identifier = person.identifier[0] if person.identifier else None
    if include_id and identifier and identifier.value:
        qualifier = reverse_nm1_qualifier(identifier)
        return f"NM1*{entity_code}*1*{family}*{given}****{qualifier}*{identifier.value}~"
    return f"NM1*{entity_code}*1*{family}*{given}~"


def build_dmg(person) -> str:
    if not person.birthDate and not person.gender:
        return ""
    dob = format_hl7_date(person.birthDate) if person.birthDate else ""
    sex = _GENDER_TO_DMG_CODE.get(person.gender, "U") if person.gender else "U"
    return f"DMG*D8*{dob}*{sex}~"


def _strip_uuid_prefix(reference: str | None) -> str | None:
    return reference.removeprefix("urn:uuid:") if reference else None


def resolve_payer_and_provider(bundle: Bundle, insurer_reference, provider_reference):
    """`insurer_reference`/`provider_reference` are FHIR `Reference` objects
    (or `None`) - 270's `CoverageEligibilityRequest` has both fields, 271's
    `CoverageEligibilityResponse` has only `.insurer` (confirmed via
    `model_fields` - no `.provider` field exists on that resource at all),
    so `provider_reference` is `None` for a 271 caller and this function
    falls back to "the Organization/Practitioner that isn't the payer"
    rather than assuming a field that doesn't exist."""
    organizations = find_resources(bundle, "Organization")
    by_id = {o.id: o for o in organizations}
    payer = by_id.get(_strip_uuid_prefix(insurer_reference.reference)) if insurer_reference else None
    provider = None
    if provider_reference is not None:
        provider_id = _strip_uuid_prefix(provider_reference.reference)
        provider = by_id.get(provider_id) or find_resource(bundle, "Practitioner")
        if provider is not None and provider.get_resource_type() not in ("Organization", "Practitioner"):
            provider = None
    if payer is None and organizations:
        payer = organizations[0]
    if provider is None:
        remaining_orgs = [o for o in organizations if payer is None or o.id != payer.id]
        provider = remaining_orgs[0] if remaining_orgs else find_resource(bundle, "Practitioner")
    return payer, provider


def resolve_subscriber_and_dependent(bundle: Bundle, patients: list):
    """Returns (subscriber, dependent_or_none). Nothing on a bare FHIR
    Patient distinguishes "subscriber" from "dependent" - both are plain
    Patient resources - so this reads Coverage.subscriber/.beneficiary
    (which app.edi.common.build_coverage always populates on both the 270
    and 271 forward sides) to tell them apart, rather than guessing from
    Bundle order."""
    if len(patients) == 1:
        return patients[0], None
    coverage = find_resource(bundle, "Coverage")
    if coverage is None or not coverage.subscriber or not coverage.beneficiary:
        # No reliable signal - fall back to Bundle order rather than
        # raising, the same "degrade gracefully on ambiguous input" spirit
        # every disclosed fallback in this app already follows.
        return patients[0], patients[1]
    by_id = {p.id: p for p in patients}
    subscriber_id = _strip_uuid_prefix(coverage.subscriber.reference)
    beneficiary_id = _strip_uuid_prefix(coverage.beneficiary.reference)
    subscriber = by_id.get(subscriber_id, patients[0])
    dependent = by_id.get(beneficiary_id) if beneficiary_id != subscriber_id else None
    return subscriber, dependent


def envelope_datetime(bundle_timestamp) -> datetime.datetime:
    return bundle_timestamp if bundle_timestamp is not None else datetime.datetime.now(datetime.timezone.utc)


def build_envelope_segments(now: datetime.datetime) -> list[str]:
    return [
        build_isa(DEFAULT_ISA_CONTROL, DEFAULT_SENDER_ID, DEFAULT_RECEIVER_ID, now),
        (
            f"GS*HS*{DEFAULT_SENDER_ID}*{DEFAULT_RECEIVER_ID}*{format_x12_date(now)}*"
            f"{format_x12_time(now)}*{DEFAULT_GS_CONTROL}*X*{GS08_VERSION}~"
        ),
    ]


def build_trailer_segments(st_to_hl_segments: list[str], body_segments: list[str]) -> list[str]:
    se01 = len(st_to_hl_segments) + len(body_segments) + 1
    return [
        f"SE*{se01}*{DEFAULT_ST_CONTROL}~",
        f"GE*1*{DEFAULT_GS_CONTROL}~",
        f"IEA*1*{DEFAULT_ISA_CONTROL}~",
    ]
