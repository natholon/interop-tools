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

from app.edi.common import (
    EIN_IDENTIFIER_SYSTEM,
    HI_QUALIFIER_SYSTEM,
    NM1_ID_QUALIFIER_SYSTEM,
    SBR_RESPONSIBILITY_TO_ORDER,
)
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


_X12_RESERVED_CHARS = "*:~\r\n"


def sanitize_x12_text(value) -> str:
    """Strips X12's own reserved delimiter characters - element separator
    `*`, component separator `:`, segment terminator `~` (this app's own
    fixed choices for every generated interchange - see
    `build_envelope_segments`/`DEFAULT_ST_CONTROL` below) - plus newlines
    (a segment must stay on one physical line), from a value before it's
    interpolated into a generated segment.

    A genuine, disclosed lossy simplification, not an oversight: X12
    defines no universal escape-character convention, and this app's own
    parser (`app/edi/parser.py`) splits purely positionally on the raw
    delimiter byte with no escape awareness at all - so a literal reserved
    character inside regenerated free text can't be round-tripped
    losslessly without extending the parser itself, a forward-direction
    change out of scope for a reverse-direction fix. Stripping keeps the
    regenerated segment well-formed rather than silently corrupting every
    later positional field's own offset the way an unescaped reserved
    character previously did - reproduced directly: an `Organization.name`
    of `"Smith*Jones Medical Group"` previously split `NM103` into
    `"Smith"`/`"Jones Medical Group"` across two elements, shifting the id
    qualifier/value fields that follow out of position with no error
    raised anywhere. Every builder that writes a resource-derived name,
    identifier value, or free-text field into a segment calls this first -
    `build_org_nm1`/`build_person_nm1` below, `remittance_835.py`'s own N1
    builder, and `edi_271.py`'s own EB05 description field."""
    if value is None:
        return ""
    text = str(value)
    for char in _X12_RESERVED_CHARS:
        text = text.replace(char, " ")
    return text


def reverse_nm1_qualifier(identifier) -> str:
    if identifier is None or not identifier.system:
        return _DEFAULT_ID_QUALIFIER
    if identifier.system in _SYSTEM_TO_NM1_QUALIFIER:
        return _SYSTEM_TO_NM1_QUALIFIER[identifier.system]
    if identifier.system.startswith(_NM1_ID_MARKER):
        return identifier.system[len(_NM1_ID_MARKER) :]
    return _DEFAULT_ID_QUALIFIER


# PER03/05/07 name what PER04/06/08 carries - the inverse of
# app.edi.common's own _PER_QUALIFIER_TO_SYSTEM.
_SYSTEM_TO_PER_QUALIFIER = {"phone": "TE", "fax": "FX", "email": "EM", "url": "UR"}


def build_address_segments(party) -> str:
    """Address -> N3/N4, and telecom -> PER. Emitted after the NM1 they
    describe, which is where X12 expects them and where the forward
    direction reads them from."""
    parts = []
    address = party.address[0] if getattr(party, "address", None) else None
    if address is not None:
        lines = [sanitize_x12_text(line) for line in (address.line or []) if line]
        if lines:
            parts.append("N3*" + "*".join(lines[:2]) + "~")
        n4 = [
            sanitize_x12_text(address.city or ""),
            sanitize_x12_text(address.state or ""),
            sanitize_x12_text(address.postalCode or ""),
            sanitize_x12_text(address.country or ""),
        ]
        if any(n4):
            parts.append("N4*" + "*".join(n4).rstrip("*") + "~")
    fields = []
    for telecom in (getattr(party, "telecom", None) or [])[:3]:
        qualifier = _SYSTEM_TO_PER_QUALIFIER.get(telecom.system)
        if qualifier and telecom.value:
            fields += [qualifier, sanitize_x12_text(telecom.value)]
    # REF follows N3/N4 in the TR3's own 2010 loop order.
    for identifier in getattr(party, "identifier", None) or []:
        if identifier.system == EIN_IDENTIFIER_SYSTEM and identifier.value:
            parts.append(f"REF*EI*{sanitize_x12_text(identifier.value)}~")
            break
    if fields:
        # PER01 is the contact function code; "IC" (Information Contact) is
        # the one this app can state truthfully, since the forward direction
        # reads no function code of its own. PER02 (the contact's name) has
        # no FHIR source on a bare telecom list, so it is left empty.
        # PER02 is the contact's own name and has no FHIR source on a bare
        # telecom list, so it stays empty - the qualifier/value pairs start
        # at PER03, which is where the forward direction reads them.
        parts.append("PER*IC*" + "*".join([""] + fields) + "~")
    return "".join(parts)


def build_org_nm1(entity_code: str, organization) -> str:
    name = sanitize_x12_text(organization.name) or "UNKNOWN"
    identifier = _first_nm1_identifier(organization)
    if identifier is not None and identifier.value:
        qualifier = reverse_nm1_qualifier(identifier)
        nm1 = f"NM1*{entity_code}*2*{name}*****{qualifier}*{sanitize_x12_text(identifier.value)}~"
    else:
        nm1 = f"NM1*{entity_code}*2*{name}~"
    return nm1 + build_address_segments(organization)


def build_person_nm1(entity_code: str, person, include_id: bool) -> str:
    name = person.name[0] if person.name else None
    given_names = (name.given if name and name.given else []) or []
    family = sanitize_x12_text(name.family) if name and name.family else ""
    given = sanitize_x12_text(given_names[0]) if given_names else ""
    # NM105 is the middle name - HumanName's second .given entry - and
    # NM107 the suffix.
    middle = sanitize_x12_text(given_names[1]) if len(given_names) > 1 else ""
    suffix = sanitize_x12_text(name.suffix[0]) if name and name.suffix else ""
    identifier = _first_nm1_identifier(person)
    fields = [entity_code, "1", family, given, middle, "", suffix]
    if include_id and identifier is not None and identifier.value:
        fields += [reverse_nm1_qualifier(identifier), sanitize_x12_text(identifier.value)]
    return "NM1*" + "*".join(fields).rstrip("*") + "~" + build_address_segments(person)


def _first_nm1_identifier(resource):
    """The identifier NM108/09 should carry - never the EIN, which has its
    own REF*EI segment and would otherwise displace the real one."""
    for identifier in resource.identifier or []:
        if identifier.system != EIN_IDENTIFIER_SYSTEM and identifier.value:
            return identifier
    return None


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


# Inverses of app.edi.common's own two tables. The relationship inverse
# keeps only the codes that map back unambiguously - three X12 codes share
# FHIR's "other", so that one cannot be reversed and is left unwritten
# rather than guessed at.
ORDER_TO_SBR_RESPONSIBILITY = {v: k for k, v in SBR_RESPONSIBILITY_TO_ORDER.items()}
COVERAGE_RELATIONSHIP_TO_RELATIONSHIP_CODE = {
    "self": "18",
    "spouse": "01",
    "child": "19",
    "parent": "76",
    "common": "53",
}


def build_sbr_segment(coverage, patient_is_subscriber: bool = True) -> str:
    """Coverage.order -> SBR01, .type -> SBR09 (Claim Filing Indicator).

    SBR02 gets the relationship only when the subscriber *is* the patient.
    It states the subscriber's own relationship to the insured, so writing
    a dependent's relationship there asserts the subscriber is a child.
    """
    if coverage is None:
        return ""
    fields = [""] * 9
    if coverage.order:
        fields[0] = ORDER_TO_SBR_RESPONSIBILITY.get(coverage.order, "")
    if patient_is_subscriber and coverage.relationship and coverage.relationship.coding:
        fields[1] = COVERAGE_RELATIONSHIP_TO_RELATIONSHIP_CODE.get(
            coverage.relationship.coding[0].code, ""
        )
    if coverage.type and coverage.type.coding and coverage.type.coding[0].code:
        fields[8] = sanitize_x12_text(coverage.type.coding[0].code)
    if not any(fields):
        return ""
    return "SBR*" + "*".join(fields).rstrip("*") + "~"


def build_pat_segment(coverage) -> str:
    """Coverage.relationship -> PAT01, for a dependent's own 2000C loop.

    The forward direction reads SBR02 first and falls back to PAT01, so a
    relationship is written to whichever loop is actually being built.
    """
    if coverage is None or not coverage.relationship or not coverage.relationship.coding:
        return ""
    code = COVERAGE_RELATIONSHIP_TO_RELATIONSHIP_CODE.get(coverage.relationship.coding[0].code, "")
    return f"PAT*{code}~" if code else ""




def build_prv_segment(claim, role_code: str) -> str:
    """Claim.careTeam[0].qualification -> PRV*<role>*PXC*<taxonomy>.

    PRV02 is fixed to PXC because that is the only code list this app maps
    the qualification from - see app.edi.common.build_taxonomy_qualification.
    """
    care_team = (claim.careTeam or [None])[0]
    qualification = care_team.qualification if care_team is not None else None
    if qualification is None or not qualification.coding or not qualification.coding[0].code:
        return ""
    return f"PRV*{role_code}*PXC*{sanitize_x12_text(qualification.coding[0].code)}~"

def reverse_quantity_unit(item, default: str = "UN") -> str:
    """Claim.item.quantity.code -> the X12 unit-of-measure element.

    Falls back to "UN" (units) when the Bundle carries no code."""
    quantity = getattr(item, "quantity", None)
    return sanitize_x12_text(quantity.code) if quantity is not None and quantity.code else default

def org_or_person_nm1(entity_code: str, resource, include_id: bool = True) -> str:
    """Dispatches to build_org_nm1/build_person_nm1 by the resolved
    resource's own FHIR type - the same third-consumer promotion as
    resolve_by_reference above, shared by every 837 variant's reverse
    builder for its billing/subscriber/dependent/payer/rendering-or-
    attending-provider NM1 segments."""
    nm1 = (
        build_org_nm1(entity_code, resource)
        if resource.get_resource_type() == "Organization"
        else build_person_nm1(entity_code, resource, include_id=include_id)
    )
    return nm1


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
    # Segments, not list entries: an NM1 now comes with the N3/N4/PER/REF
    # that describe it, in one string. sanitize_x12_text keeps the
    # terminator out of every value, so counting them is exact.
    se01 = sum(s.count("~") for s in st_to_hl_segments) + sum(s.count("~") for s in body_segments) + 1
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
