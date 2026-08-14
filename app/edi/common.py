"""Cross-transaction-set X12 shared logic - the app/cda/common.py /
app/mappings/common.py equivalent for the EDI input format. NM1/DMG shape
and Bundle/date assembly are standardized across X12 healthcare transaction
sets generally, not 270/271-specific, so this lives here rather than in
either eligibility module - a future 276/277 builder reuses it directly."""

import uuid
from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.resource import Resource

from app.edi.parser import HlLoop, Segment, element, find_segment, group_by_hl_hierarchy
from app.fhir_models.builders import parse_hl7_date, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError

# X12 270/271 (and, prospectively, other HL-hierarchy transaction sets)
# carries no data element for "why are we asking" -
# CoverageEligibilityRequest/Response.purpose is FHIR-required (confirmed
# by direct construction - see CLAUDE.md) with no source field to derive
# it from. Defaults to "benefits", the dominant real-world use - the same
# "default to the most common real value when no unknown option exists"
# judgment already made for Medications' moodCode / Immunizations'
# statusCode fallbacks.
DEFAULT_PURPOSE = "benefits"

# X12 has no official FHIR-canonical system URI for its own Service Type
# Code external code list (270's EQ01 / 271's EB03 - the same list, used
# both directions) - a disclosed local system, same category as CDA's
# _OID_TO_FHIR_SYSTEM fallback and MDM's TXA-3 table.
SERVICE_TYPE_CODE_SYSTEM = "urn:interop-tools:x12-service-type-code"

# X12 code list 1068 (DMG03, Gender Code) uses M/F/U - a genuinely
# different vocabulary from HL7v2 PID-8's M/F/O (U vs O), the same
# "different-but-similar-looking vocabulary needs its own table" situation
# CDA's AdministrativeGender (F/M/UN) already was relative to HL7v2 - not a
# reuse of app.fhir_models.builders::hl7_sex_to_fhir_gender's _GENDER_MAP.
_DMG_GENDER_MAP = {"M": "male", "F": "female", "U": "unknown"}

# NM108 (Identification Code Qualifier) -> a FHIR canonical system URI,
# where one is officially recognized. PI (Payor ID) and MI (Member ID) have
# no universal FHIR canonical system - fall back to a disclosed local
# system URI keyed by the qualifier itself, the same "disclosed local
# system fallback" already used for CDA's _OID_TO_FHIR_SYSTEM and MDM's
# TXA-3 MIME table. Public - X12's own Identification Code Qualifier list
# (element 66) is shared across NM1 and N1 alike, so remittance_835.py's
# N1-shaped identifier builder reuses this table directly rather than
# re-declaring a duplicate copy for the same underlying code list.
NM1_ID_QUALIFIER_SYSTEM = {
    "XX": "http://hl7.org/fhir/sid/us-npi",
    "SY": "http://hl7.org/fhir/sid/us-ssn",
    "EI": "urn:interop-tools:x12-ein",
}
_NM1_ID_FALLBACK_SYSTEM = "urn:interop-tools:x12-nm1-id"

# HL03 level codes for the 270/271 HL hierarchy - shared here (not in either
# eligibility module) since both the request and response builders, plus
# app/edi/validation.py's own plausibility rules, all need to identify the
# same four loop levels.
HL_INFORMATION_SOURCE = "20"
HL_INFORMATION_RECEIVER = "21"
HL_SUBSCRIBER = "22"
HL_DEPENDENT = "23"

# The system URI used for a Reference-by-identifier back to BHT03 (both
# Bundle.identifier in assemble_bundle below, and CoverageEligibilityResponse
# .request in eligibility_271.py, which has no real originating request
# resource to point at in a standalone-271 conversion) - a single named
# constant so the two uses can't silently drift apart.
BHT_REFERENCE_SYSTEM = "urn:interop-tools:x12-bht-reference"


def parse_x12_datetime(date_str: str, time_str: str = "") -> str | None:
    """Concatenate X12's separate date (CCYYMMDD) and time (HHMM[SS])
    elements and delegate to the existing HL7 TS parsers - X12's digit
    shapes are byte-for-byte the same as HL7v2's (parse_hl7_datetime
    requires >=12 digits, exactly CCYYMMDD+HHMM's combined length; a longer
    HHMMSS time still parses correctly via the same function), a direct
    reuse opportunity the same way app/cda/common.py::parse_partial_ts
    already reused these once for CDA. Falls back to a date-only result
    when time_str is empty or the combined string doesn't parse as a full
    datetime (mirrors parse_hl7_datetime/parse_hl7_date's own
    None-on-failure contracts) - returns None only when date_str itself is
    empty or invalid."""
    if not date_str:
        return None
    if time_str:
        full = parse_hl7_datetime(date_str + time_str)
        if full:
            return full
    return parse_hl7_date(date_str)


def resolve_id_qualifier_system(qualifier: str, fallback_system: str) -> str:
    """NM108/N103-shaped "Identification Code Qualifier -> a FHIR-canonical
    system where one is officially recognized, else a disclosed
    caller-specific local system" resolution, shared by `_build_nm1_identifier`
    below and `app.edi.remittance_835._build_n1_identifier` - both read the
    identical `NM1_ID_QUALIFIER_SYSTEM` table, differing only in which
    segment shape (and element positions) they pull `qualifier`/the id
    value from, not in how the qualifier itself resolves to a system.
    `qualifier` must already be `.strip().upper()`-normalized by the
    caller (both current callers do this at their own element-read site)."""
    system = NM1_ID_QUALIFIER_SYSTEM.get(qualifier)
    if system is not None:
        return system
    return f"{fallback_system}:{qualifier}" if qualifier else fallback_system


def _build_nm1_identifier(nm1: Segment) -> Identifier | None:
    # Normalized the same way DMG03's gender code is normalized below (and
    # the way app/mappings/mdm.py::_resolve_content_type normalizes TXA-3
    # before its own disclosed-fallback coded lookup) - a real-world sender
    # emitting a lowercase qualifier (e.g. "xx" instead of "XX") must still
    # resolve to the canonical NPI/SSN system, not silently fall through to
    # the disclosed per-qualifier fallback system.
    qualifier = element(nm1, 8).strip().upper()
    value = element(nm1, 9)
    if not value:
        return None
    return Identifier(system=resolve_id_qualifier_system(qualifier, _NM1_ID_FALLBACK_SYSTEM), value=value)


def find_child_loop(loop: HlLoop, hl03: str):
    """Return the first direct child of `loop` whose HL03 (level code)
    matches, or None - the HL-hierarchy equivalent of app.cda.parser::
    find_child, used by both eligibility_270.py and eligibility_271.py to
    walk 2000A->2000B->2000C->2000D without duplicating the same lookup."""
    for child in loop.children:
        if child.hl03 == hl03:
            return child
    return None


@dataclass
class ResolvedEligibilityParties:
    """Everything eligibility_270.py/271.py's build_bundle() and
    app/edi/validation.py's own 270/271 plausibility rules need from the
    2000A/2000B/2000C/2000D loop walk - the one real implementation of
    "which loop is which, and which one is the patient" (see
    resolve_eligibility_parties below). patient_is_dependent tells a caller
    whether it still needs to materialize a second Patient resource for the
    dependent (when False, patient_nm1/_dmg are just the subscriber's own,
    already built)."""

    payer_nm1: Segment
    provider_nm1: Segment
    subscriber_nm1: Segment
    subscriber_dmg: Segment | None
    patient_nm1: Segment
    patient_dmg: Segment | None
    patient_is_dependent: bool
    patient_loop_members: list[Segment]


def resolve_eligibility_parties(segments: list[Segment], transaction_set_id: str) -> ResolvedEligibilityParties:
    """Walk the strict 2000A(20)->2000B(21)->2000C(22)->2000D(23) parent
    chain every 270/271 transaction set requires, then resolve which NM1/DMG
    pair is "the patient" per the documented precedence rule (the 2000D
    dependent when present AND its own NM1 resolves, else the 2000C
    subscriber themselves).

    This is the single shared implementation eligibility_270.py,
    eligibility_271.py, and app/edi/validation.py's plausibility rules must
    all call, rather than each re-deriving the walk - a code review caught
    that validation.py's own earlier re-derivation had already drifted from
    this one in two ways (a global whole-tree search for the subscriber loop
    instead of the strict parent chain, and no check that a present
    dependent loop's NM1 actually resolves before treating it as the
    patient), producing plausibility findings computed against a different
    segment set than what conversion would actually use. Raises
    MissingSegmentError (not a validation finding) when the 2000A/2000B/2000C
    loops or their required NM1 segments are absent - the same "raise the
    business-rule failure, let the caller's convertibility check turn it
    into a finding" contract every other required-field check in this app
    already follows."""
    roots = group_by_hl_hierarchy(segments)
    source_loop = next((loop for loop in roots if loop.hl03 == HL_INFORMATION_SOURCE), None)
    if source_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000A Information Source loop")
    receiver_loop = find_child_loop(source_loop, HL_INFORMATION_RECEIVER)
    if receiver_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000B Information Receiver loop")
    subscriber_loop = find_child_loop(receiver_loop, HL_SUBSCRIBER)
    if subscriber_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000C Subscriber loop")
    dependent_loop = find_child_loop(subscriber_loop, HL_DEPENDENT)

    payer_nm1 = find_segment(source_loop.member_segments, "NM1")
    if payer_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000A loop is missing its NM1 (payer) segment")

    provider_nm1 = find_segment(receiver_loop.member_segments, "NM1")
    if provider_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1 (provider) segment")

    subscriber_nm1 = find_segment(subscriber_loop.member_segments, "NM1")
    if subscriber_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000C loop is missing its NM1 (subscriber) segment")
    subscriber_dmg = find_segment(subscriber_loop.member_segments, "DMG")

    patient_nm1 = subscriber_nm1
    patient_dmg = subscriber_dmg
    patient_is_dependent = False
    patient_loop_members = subscriber_loop.member_segments
    if dependent_loop is not None:
        dependent_nm1 = find_segment(dependent_loop.member_segments, "NM1")
        if dependent_nm1 is not None:
            patient_nm1 = dependent_nm1
            patient_dmg = find_segment(dependent_loop.member_segments, "DMG")
            patient_is_dependent = True
            patient_loop_members = dependent_loop.member_segments

    return ResolvedEligibilityParties(
        payer_nm1=payer_nm1,
        provider_nm1=provider_nm1,
        subscriber_nm1=subscriber_nm1,
        subscriber_dmg=subscriber_dmg,
        patient_nm1=patient_nm1,
        patient_dmg=patient_dmg,
        patient_is_dependent=patient_is_dependent,
        patient_loop_members=patient_loop_members,
    )


def build_service_type_category(code: str) -> CodeableConcept | None:
    """270's EQ01 and 271's EB03 (Service Type Code) share the same X12
    external code list - one shared builder for both directions, on
    SERVICE_TYPE_CODE_SYSTEM (see module-level comment)."""
    if not code:
        return None
    return CodeableConcept(coding=[Coding(system=SERVICE_TYPE_CODE_SYSTEM, code=code)])


def is_person_entity(nm1: Segment) -> bool:
    """NM102 (Entity Type Qualifier): "1" = Person, "2" = Non-Person Entity
    (organization). Callers use this to decide whether a given NM1 loop
    (e.g. the 2100B provider loop, which can legally be either) should
    materialize an Organization or a Practitioner/Patient."""
    return element(nm1, 2) == "1"


def build_organization_from_nm1(nm1: Segment) -> Organization:
    """NM1 (non-person entity) -> Organization. Used for the payer (NM1*PR)
    loop, and the provider (NM1*1P/41) loop when NM102 indicates an
    organization rather than an individual."""
    organization = Organization(id=str(uuid.uuid4()))
    name = element(nm1, 3)
    if name:
        organization.name = name
    identifier = _build_nm1_identifier(nm1)
    if identifier:
        organization.identifier = [identifier]
    return organization


def _build_person_name(nm1: Segment) -> HumanName | None:
    family = element(nm1, 3)
    given = element(nm1, 4)
    if not family and not given:
        return None
    name = HumanName()
    if family:
        name.family = family
    if given:
        name.given = [given]
    return name


def build_practitioner_from_nm1(nm1: Segment) -> Practitioner:
    """NM1 (person) -> Practitioner. Used for the provider (NM1*1P/41) loop
    when NM102 indicates an individual rather than an organization."""
    practitioner = Practitioner(id=str(uuid.uuid4()))
    name = _build_person_name(nm1)
    if name:
        practitioner.name = [name]
    identifier = _build_nm1_identifier(nm1)
    if identifier:
        practitioner.identifier = [identifier]
    return practitioner


def build_patient_from_nm1_dmg(nm1: Segment, dmg: Segment | None) -> Patient:
    """NM1 (person) + optional DMG -> Patient. Used for the subscriber
    (NM1*IL) and dependent (NM1*QC) loops - both are always person entities
    in 270/271, unlike the payer/provider loops."""
    patient = Patient(id=str(uuid.uuid4()))
    name = _build_person_name(nm1)
    if name:
        patient.name = [name]
    identifier = _build_nm1_identifier(nm1)
    if identifier:
        patient.identifier = [identifier]

    if dmg is not None:
        # DMG01 is always "D8" (CCYYMMDD) in practice for healthcare 270/
        # 271 - other X12-legal date/time qualifiers exist but aren't used
        # in this transaction family, disclosed rather than handled.
        if element(dmg, 1) == "D8":
            birth_date = parse_hl7_date(element(dmg, 2))
            if birth_date:
                patient.birthDate = birth_date
        gender_code = element(dmg, 3).strip().upper()
        gender = _DMG_GENDER_MAP.get(gender_code)
        if gender:
            patient.gender = gender

    return patient


def assemble_bundle(bht: Segment, *resources: Resource) -> Bundle:
    """Wrap any number of resources into a Bundle, with BHT-derived
    metadata (BHT03 Reference Identification -> Bundle.identifier, BHT04+
    BHT05 Date+Time -> Bundle.timestamp) - the X12-header equivalent of
    app/cda/common.py::assemble_bundle, which reads ClinicalDocument
    fields instead. BHT is used rather than the ISA envelope's own
    ISA13 interchange control number, since BHT03 is the transaction's own
    application-level reference identifier - more meaningful to a consumer
    than the interchange-level control number. Bundle.type = "collection",
    matching every existing HL7v2/C-CDA pipeline's output shape."""
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")

    reference_id = element(bht, 3)
    if reference_id:
        bundle.identifier = Identifier(system=BHT_REFERENCE_SYSTEM, value=reference_id)

    # Bundle.timestamp is FHIR "instant", which (unlike Coverage's or
    # CoverageEligibilityRequest's plain `date`/`dateTime` fields) has NO
    # date-only form - parse_x12_datetime's date-only fallback would
    # produce a value the instant regex rejects, crashing resource
    # construction on an otherwise perfectly convertible transaction set
    # whenever BHT05 (time) is empty or unparseable. This is the exact bug
    # app/cda/common.py::assemble_bundle already shipped once and disclosed
    # in CLAUDE.md - call parse_hl7_datetime directly here (no date-only
    # fallback) rather than parse_x12_datetime, matching that fix.
    timestamp = parse_hl7_datetime(element(bht, 4) + element(bht, 5))
    if timestamp:
        bundle.timestamp = timestamp

    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources]
    return bundle
