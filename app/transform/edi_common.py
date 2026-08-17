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

from app.edi.common import HI_QUALIFIER_SYSTEM, NM1_ID_QUALIFIER_SYSTEM
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


def resolve_by_reference(bundle: Bundle, reference):
    """Direct by-id resolution of a FHIR `Reference` against every entry in
    the Bundle - promoted here once app/transform/claim_837d.py became a
    third real consumer of the identical helper app/transform/claim_837p.py/
    claim_837i.py had each independently carried as a private
    `_resolve_by_reference`, mirroring app/edi/common.py's own
    `resolve_837_loops` extraction precedent (which itself waited for 837D
    to be the third forward-direction consumer before promoting). Used by
    every 837 variant's reverse builder for `Claim.provider`/`.insurer`/
    `.careTeam[].provider` - all real, direct references with no
    Bundle-order ambiguity to resolve, unlike `resolve_payer_and_provider`
    below (see claim_837p.py's own docstring for why that function isn't
    reused here instead)."""
    if reference is None or not reference.reference:
        return None
    resource_id = reference.reference.removeprefix("urn:uuid:")
    for entry in bundle.entry or []:
        if entry.resource.id == resource_id:
            return entry.resource
    return None


def org_or_person_nm1(entity_code: str, resource, include_id: bool = True) -> str:
    """Dispatches to build_org_nm1/build_person_nm1 by the resolved
    resource's own FHIR type - the same third-consumer promotion as
    resolve_by_reference above, shared by every 837 variant's reverse
    builder for its billing/subscriber/dependent/payer/rendering-or-
    attending-provider NM1 segments."""
    return (
        build_org_nm1(entity_code, resource)
        if resource.get_resource_type() == "Organization"
        else build_person_nm1(entity_code, resource, include_id=include_id)
    )


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


# Reverse of app.edi.common.HI_QUALIFIER_SYSTEM - both qualifiers in a
# principal/other pair (ABK/ABF for ICD-10-CM, BK/BF for ICD-9-CM) map to
# the identical FHIR system, so the position within Claim.diagnosis[]
# (1 = principal, every other = "other") is what actually decides which
# one gets regenerated, the real-world convention every sender of a
# single-HI-segment family (278, 837P) follows, not a guess. Promoted here
# from app/transform/prior_auth.py once app/transform/claim_837p.py became
# a second real consumer of the identical single-HI-segment reversal - 837I
# needs a genuinely different, multi-HI-segment version of this (see that
# module's own bullet once it exists), so this stays scoped to the
# single-segment shape both 278 and 837P actually share.
_HI_ICD10_PRINCIPAL, _HI_ICD10_OTHER = "ABK", "ABF"
_HI_ICD9_PRINCIPAL, _HI_ICD9_OTHER = "BK", "BF"
_HI_QUALIFIER_FALLBACK_PREFIX = "urn:interop-tools:x12-hi-qualifier:"


def reverse_hi_qualifier(system: str | None, position: int) -> str:
    if system == HI_QUALIFIER_SYSTEM.get(_HI_ICD10_PRINCIPAL):
        return _HI_ICD10_PRINCIPAL if position == 1 else _HI_ICD10_OTHER
    if system == HI_QUALIFIER_SYSTEM.get(_HI_ICD9_PRINCIPAL):
        return _HI_ICD9_PRINCIPAL if position == 1 else _HI_ICD9_OTHER
    if system and system.startswith(_HI_QUALIFIER_FALLBACK_PREFIX):
        return system[len(_HI_QUALIFIER_FALLBACK_PREFIX) :]
    return _HI_ICD10_PRINCIPAL if position == 1 else _HI_ICD10_OTHER


def build_hi_segment(diagnoses) -> str:
    composites = []
    for position, diagnosis in enumerate(diagnoses or [], start=1):
        concept = diagnosis.diagnosisCodeableConcept
        coding = concept.coding[0] if concept and concept.coding else None
        if coding is None or not coding.code:
            continue
        qualifier = reverse_hi_qualifier(coding.system, position)
        composites.append(f"{qualifier}:{coding.code}")
    return f"HI*{'*'.join(composites)}~" if composites else ""
