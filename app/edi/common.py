"""Cross-transaction-set X12 shared logic - the app/cda/common.py /
app/mappings/common.py equivalent for the EDI input format. NM1/DMG shape
and Bundle/date assembly are standardized across X12 healthcare transaction
sets generally, not 270/271-specific, so this lives here rather than in
either eligibility module - a future 276/277 builder reuses it directly."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.coverage import Coverage
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.parser import Delimiters, HlLoop, Segment, component, element, find_segment, group_by_hl_hierarchy
from app.fhir_models.builders import parse_hl7_date, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError
from app.provenance.location import edi_location
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding

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

# ST03 (Implementation Convention Reference) substrings that identify the
# 837I/837D families among transaction sets sharing the literal ST01="837"
# (see app/edi/registry.py::get_transaction_builder's own docstring for why
# ST03, not GS08, is this app's dispatch signal). Shared here - not
# declared locally in registry.py - once app/edi/validation.py became a
# second real consumer needing the identical check for its own 837-family
# rule dispatch: both sides must never disagree about which variant a given
# ST03 value indicates, the same "one real implementation, not two
# independently-drifting copies" discipline every other shared EDI helper
# in this app already follows.
ST03_837I_MARKER = "X223"
ST03_837D_MARKER = "X224"


def is_837i_transaction(st03: str) -> bool:
    return ST03_837I_MARKER in st03.strip().upper()


def is_837d_transaction(st03: str) -> bool:
    return ST03_837D_MARKER in st03.strip().upper()


def resolve_837_variant(st03: str) -> str:
    """Returns "837I", "837D", or "837P" (the default) - the single real
    decision tree behind which 837 variant a given ST03 value indicates,
    shared by registry.py::get_transaction_builder and validation.py's own
    837-family dispatch so the two can never disagree about which family a
    transaction belongs to. Previously each side wrote out the identical
    is_837i_transaction -> is_837d_transaction -> default if/elif chain
    independently, kept in sync by comment discipline alone rather than
    shared code - promoted here once that duplication was flagged."""
    if is_837i_transaction(st03):
        return "837I"
    if is_837d_transaction(st03):
        return "837D"
    return "837P"


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


def _build_nm1_identifier(nm1: Segment, resource_id: str | None = None, recorder=None) -> Identifier | None:
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
    if recorder and resource_id:
        recorder.record(resource_id, "identifier[0].value", edi_location("NM1", 9), value)
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


def build_service_type_category(
    code: str,
    resource_id: str | None = None,
    relative_path: str | None = None,
    source_location: str | None = None,
    recorder=None,
) -> CodeableConcept | None:
    """270's EQ01 and 271's EB03 (Service Type Code) share the same X12
    external code list - one shared builder for both directions, on
    SERVICE_TYPE_CODE_SYSTEM (see module-level comment). `source_location`
    is passed in by the caller (rather than composed here from a raw
    segment, the way app.fhir_models.builders::build_codeable_concept_from_cwe
    does) since this function only ever receives the already-extracted code
    string, not the segment itself - 270's own EQ-1 and 271's own EB-3 are
    two different segment/element pairs for the identical target field."""
    if not code:
        return None
    if recorder and resource_id and relative_path and source_location:
        recorder.record(resource_id, f"{relative_path}.coding[0].code", source_location, code)
    return CodeableConcept(coding=[Coding(system=SERVICE_TYPE_CODE_SYSTEM, code=code)])


# HI composite qualifier (component 1) -> a FHIR-canonical system URI,
# from X12 code list 1270. Shared by 278 and the 837 family.
#
# **The two pairs are easy to transpose**: "ABK"/"ABF" are the 5010
# ICD-10-CM principal/other qualifiers, "BK"/"BF" the legacy ICD-9-CM
# ones - so "BF" is ICD-9-CM, not ICD-10-CM. An earlier table had exactly
# that wrong; see test_bf_qualifier_resolves_to_icd9_not_icd10.
HI_QUALIFIER_SYSTEM = {
    "ABK": "http://hl7.org/fhir/sid/icd-10-cm",  # ICD-10-CM Principal Diagnosis
    "ABF": "http://hl7.org/fhir/sid/icd-10-cm",  # ICD-10-CM Other Diagnosis
    "BK": "http://hl7.org/fhir/sid/icd-9-cm",  # ICD-9-CM Principal Diagnosis (legacy)
    "BF": "http://hl7.org/fhir/sid/icd-9-cm",  # ICD-9-CM Other Diagnosis (legacy)
}
_HI_QUALIFIER_FALLBACK_SYSTEM = "urn:interop-tools:x12-hi-qualifier"
_MAX_HI_DIAGNOSIS_POSITIONS = 12  # HI01-HI12, per the 5010 IG's own cap on both 278 and 837


def build_diagnosis_codeable_concepts(
    hi: Segment | None,
    delimiters: Delimiters,
    resource_id: str | None = None,
    relative_path_prefix: str | None = None,
    recorder=None,
    index_offset: int = 0,
    segment_repetition: int | None = None,
) -> list[CodeableConcept]:
    """Parse an HI segment's repeating diagnosis composites
    (`HI*ABK:J209*ABF:E119~` is two diagnoses, not two segments) into one
    CodeableConcept each, in position order. Component 1 is the qualifier,
    component 2 the code.

    A position with no code component is skipped rather than ending the scan -
    one malformed composite should not drop every diagnosis after it - but an
    entirely empty element does stop it, since HI's repetition is positional
    and left-packed, so a real sender leaves no gaps.

    Recording (`resource_id`/`relative_path_prefix`/`recorder`) hardcodes the
    `.diagnosisCodeableConcept.coding[0].code` wrapper, which is safe because
    both consumers wrap the result in the same `Claim.diagnosis[]` field.

    `index_offset` and `segment_repetition` exist for 837I/837D, where a claim
    carries several HI segments (one per code-list type), each parsed by its
    own call. Without `index_offset` every call would start recording at FHIR
    index 0 and overwrite the previous segment's facts; without
    `segment_repetition` two diagnoses at intra-segment position 1 in
    different physical segments would both record the ambiguous `"HI-1.2"`."""
    if hi is None:
        return []
    concepts = []
    for position in range(1, _MAX_HI_DIAGNOSIS_POSITIONS + 1):
        composite = element(hi, position)
        if not composite:
            break
        qualifier = component(composite, delimiters, 1).strip().upper()
        code = component(composite, delimiters, 2)
        if not code:
            continue
        system = HI_QUALIFIER_SYSTEM.get(qualifier, f"{_HI_QUALIFIER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else _HI_QUALIFIER_FALLBACK_SYSTEM)
        concepts.append(CodeableConcept(coding=[Coding(system=system, code=code)]))
        if recorder and resource_id and relative_path_prefix:
            recorder.record(
                resource_id,
                f"{relative_path_prefix}[{index_offset + len(concepts) - 1}].diagnosisCodeableConcept.coding[0].code",
                edi_location("HI", position, component=2, segment_repetition=segment_repetition),
                code,
            )
    return concepts


def iter_diagnosis_hi_segments(claim_loop_members: list[Segment], delimiters: Delimiters) -> list[Segment]:
    """Promoted here from claim_837i.py once claim_837d.py became a second
    real consumer - both institutional and dental claims can carry several
    HI segments per claim, each dedicated to one code-list "type"
    (principal/other diagnosis, occurrence, value, condition, ...)
    distinguished only by the qualifier of its own composites, not any
    segment-level flag - confirmed directly against real X12.org examples
    for both families, which split Principal Diagnosis (BK/ABK) and Other
    Diagnosis (BF/ABF) into two separate HI segment instances
    (`HI*BK:3669~` then, separately, `HI*BF:4019*BF:79431~`). Only segments
    whose first composite uses a recognized diagnosis qualifier are
    treated as diagnosis segments at all - occurrence/value/condition-coded
    HI segments (BH/BE/BG) are structurally skipped here, not merely
    unrecognized, since feeding them through build_diagnosis_codeable_
    concepts would otherwise fold them into Claim.diagnosis[] as bogus
    "unrecognized diagnosis qualifier" entries rather than the
    disclosed-and-deferred data they actually are. Public - each family's
    own `_iter_*_validation.py` missing-diagnosis rule needs the identical
    filtering, so validation can never disagree with conversion about
    which HI segments actually count as diagnosis-bearing."""
    diagnosis_segments = []
    for hi in claim_loop_members:
        if hi[0] != "HI":
            continue
        first_qualifier = component(element(hi, 1), delimiters, 1).strip().upper()
        if first_qualifier in HI_QUALIFIER_SYSTEM:
            diagnosis_segments.append(hi)
    return diagnosis_segments


# CMS's Place of Service code set - a real FHIR-canonical CodeSystem,
# unlike most of this app's local fallbacks for X12 code lists with no
# official FHIR home. Shared by 837P and 837D: CLM05-1 uses the identical
# vocabulary for professional and dental claims (per SV3-03's own field
# description, "Place of Service Codes for
# Professional or Dental Services") - a genuine, confirmed structural match,
# not a coincidence the way HL03 numeric codes have sometimes only
# *appeared* to match across other EDI families. 837I's own CLM05-1 uses a
# completely different vocabulary (UB-04 Type of Bill) and does not use
# this constant - see claim_837i.py's own module docstring for why.
POS_CODE_SYSTEM = "https://www.cms.gov/Medicare/Coding/place-of-service-codes/Place_of_Service_Code_Set"


def build_place_of_service_from_clm05(clm: Segment, delimiters: Delimiters) -> CodeableConcept | None:
    facility_code = component(element(clm, 5), delimiters, 1)
    if not facility_code:
        return None
    return CodeableConcept(coding=[Coding(system=POS_CODE_SYSTEM, code=facility_code)])


# HL03 level codes shared by all three 837 variants (Professional/
# Institutional/Dental) - "20"/"22"/"23" here mean Billing Provider/
# Subscriber/Patient, a genuinely different semantic role than 270/271/278's
# own "20"/"22"/"23" (Information Source/Subscriber/Dependent) despite the
# numeric coincidence (see each 837 builder's own module docstring for the
# "don't assume a numeric HL03 code carries the same meaning across TR3s"
# discipline this reflects) - kept as their own named constants here rather
# than reused from HL_INFORMATION_SOURCE/HL_SUBSCRIBER/HL_DEPENDENT above,
# to avoid implying a shared semantic that doesn't exist.
_HL_837_BILLING_PROVIDER = "20"
_HL_837_SUBSCRIBER = "22"
_HL_837_PATIENT = "23"

_NM1_837_BILLING_PROVIDER = "85"
_NM1_837_SUBSCRIBER = "IL"
_NM1_837_PAYER = "PR"
_NM1_837_PATIENT = "QC"


def find_nm1_by_entity_code(segments: list[Segment], entity_code: str) -> Segment | None:
    """NM1 entity identifier code (element 98) lookup, scoped to a specific
    loop's own flat member list - multiple NM1 segments with different
    entity codes can appear within one HL loop (e.g. 837's subscriber loop
    carries both NM1*IL and NM1*PR), so every 837-family caller filters by
    entity code rather than taking "the first NM1" blindly. Promoted here
    once claim_837d.py became a third independently-tested consumer of the
    identical one-line helper claim_837p.py/claim_837i.py each defined
    privately - also used by each builder's own rendering/attending
    provider lookup (NM1*82/NM1*71), not just inside resolve_837_loops."""
    return next((seg for seg in segments if seg[0] == "NM1" and element(seg, 1) == entity_code), None)


# DTP01 (Date/Time Qualifier) - "472" is Service Date, the only DTP any 837
# variant's own item-building code reads. A 2400 loop's member list can
# carry other DTP-qualified segments too (e.g. "463" Prescription Date on a
# DME line) - filtering by DTP01 rather than taking "the first DTP" avoids
# silently attributing a differently-qualified date to servicedDate, the
# same "check the specific qualifier, don't just grab the first segment of
# this type" discipline STC01/TXA dedup already established elsewhere in
# this app. Public - each family's own *_validation.py service-date rule
# must filter the identical way, so validation can never disagree with
# conversion about which DTP counts. Promoted here once claim_837d.py
# became a third real consumer of the identical one-line lookup.
DTP_SERVICE_DATE = "472"


def find_dtp_by_qualifier(segments: list[Segment], qualifier: str) -> Segment | None:
    return next((seg for seg in segments if seg[0] == "DTP" and element(seg, 1) == qualifier), None)


@dataclass
class Resolved837Loops:
    """Everything any 837 variant's build_bundle() needs from its 2000A-
    2000C loop walk - promoted here once claim_837d.py became a third
    independently-tested implementation of the identical algorithm
    claim_837p.py/claim_837i.py each carried privately (see each builder's
    own module docstring for why the promotion was deferred until a third,
    genuinely proven consumer existed rather than assumed identical from
    two).

    `claim_loop` and `patient_is_dependent` are deliberately gated
    *independently*, unlike 270/271/278's own dependent-loop resolvers:
    `claim_loop` is resolved purely structurally (the 2000C loop whenever
    HL03="23" was emitted at all, regardless of whether its own NM1
    resolves) since CLM/HI/service lines are physically nested wherever
    that HL loop's members actually are - X12 doesn't re-attribute them to
    2000B just because 2000C's NM1 happens to be malformed, so this
    resolver can't either. `patient_is_dependent` (and `patient_nm1`/
    `patient_dmg`) instead gate *which Patient resource* is "the patient"
    for `Claim.patient`/`Coverage.beneficiary` - only true when the 2000C
    loop's own NM1*QC actually resolves. Getting this wrong (reusing a
    single combined gate) let a malformed-but-present 2000C loop's own CLM
    go invisible to build_bundle() once already, for 837P - caught before
    commit by a regression test, not a later review."""

    billing_provider_loop: HlLoop
    subscriber_loop: HlLoop
    claim_loop: HlLoop
    billing_provider_nm1: Segment
    subscriber_nm1: Segment
    subscriber_dmg: Segment | None
    payer_nm1: Segment
    patient_nm1: Segment | None
    patient_dmg: Segment | None
    patient_is_dependent: bool


def resolve_837_loops(segments: list[Segment], transaction_set_id: str) -> Resolved837Loops:
    """Walk the strict 2000A(20)->2000B(22)->[2000C(23)] parent chain every
    837 variant (Professional/Institutional/Dental) requires - identical
    across all three, confirmed genuinely (not assumed) by each variant's
    own real X12.org-published example. See Resolved837Loops' own
    docstring for why claim_loop/patient_is_dependent are gated
    independently."""
    roots = group_by_hl_hierarchy(segments)
    billing_provider_loop = next((loop for loop in roots if loop.hl03 == _HL_837_BILLING_PROVIDER), None)
    if billing_provider_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000A Billing Provider loop")

    subscriber_loop = next(
        (child for child in billing_provider_loop.children if child.hl03 == _HL_837_SUBSCRIBER), None
    )
    if subscriber_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000B Subscriber loop")

    billing_provider_nm1 = find_nm1_by_entity_code(billing_provider_loop.member_segments, _NM1_837_BILLING_PROVIDER)
    if billing_provider_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000A loop is missing its NM1*85 (billing provider) segment")

    subscriber_nm1 = find_nm1_by_entity_code(subscriber_loop.member_segments, _NM1_837_SUBSCRIBER)
    if subscriber_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1*IL (subscriber) segment")
    subscriber_dmg = find_segment(subscriber_loop.member_segments, "DMG")

    payer_nm1 = find_nm1_by_entity_code(subscriber_loop.member_segments, _NM1_837_PAYER)
    if payer_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1*PR (payer) segment")

    # claim_loop is resolved structurally (loop presence), independent of
    # whether that loop's own NM1 resolves - see Resolved837Loops' own
    # docstring for why this must NOT reuse the "no NM1 -> treat loop as
    # absent" gate every other EDI family's dependent-loop resolver uses.
    patient_loop = next((child for child in subscriber_loop.children if child.hl03 == _HL_837_PATIENT), None)
    claim_loop = patient_loop if patient_loop is not None else subscriber_loop

    patient_nm1 = None
    patient_dmg = None
    patient_is_dependent = False
    if patient_loop is not None:
        patient_nm1 = find_nm1_by_entity_code(patient_loop.member_segments, _NM1_837_PATIENT)
        if patient_nm1 is not None:
            patient_dmg = find_segment(patient_loop.member_segments, "DMG")
            patient_is_dependent = True

    return Resolved837Loops(
        billing_provider_loop=billing_provider_loop,
        subscriber_loop=subscriber_loop,
        claim_loop=claim_loop,
        billing_provider_nm1=billing_provider_nm1,
        subscriber_nm1=subscriber_nm1,
        subscriber_dmg=subscriber_dmg,
        payer_nm1=payer_nm1,
        patient_nm1=patient_nm1,
        patient_dmg=patient_dmg,
        patient_is_dependent=patient_is_dependent,
    )


def is_person_entity(nm1: Segment) -> bool:
    """NM102 (Entity Type Qualifier): "1" = Person, "2" = Non-Person Entity
    (organization). Callers use this to decide whether a given NM1 loop
    (e.g. the 2100B provider loop, which can legally be either) should
    materialize an Organization or a Practitioner/Patient."""
    return element(nm1, 2) == "1"


def build_organization_from_nm1(nm1: Segment, recorder=None) -> Organization:
    """NM1 (non-person entity) -> Organization. Used for the payer (NM1*PR)
    loop, and the provider (NM1*1P/41) loop when NM102 indicates an
    organization rather than an individual."""
    organization_id = str(uuid.uuid4())
    organization = Organization(id=organization_id)
    name = element(nm1, 3)
    if name:
        organization.name = name
        if recorder:
            recorder.record(organization_id, "name", edi_location("NM1", 3), name)
    identifier = _build_nm1_identifier(nm1, resource_id=organization_id, recorder=recorder)
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


def _record_person_name(resource_id: str, name: HumanName | None, recorder) -> None:
    if not recorder or name is None:
        return
    if name.family:
        recorder.record(resource_id, "name[0].family", edi_location("NM1", 3), name.family)
    if name.given:
        recorder.record(resource_id, "name[0].given[0]", edi_location("NM1", 4), name.given[0])


def build_practitioner_from_nm1(nm1: Segment, recorder=None) -> Practitioner:
    """NM1 (person) -> Practitioner. Used for the provider (NM1*1P/41) loop
    when NM102 indicates an individual rather than an organization."""
    practitioner_id = str(uuid.uuid4())
    practitioner = Practitioner(id=practitioner_id)
    name = _build_person_name(nm1)
    if name:
        practitioner.name = [name]
        _record_person_name(practitioner_id, name, recorder)
    identifier = _build_nm1_identifier(nm1, resource_id=practitioner_id, recorder=recorder)
    if identifier:
        practitioner.identifier = [identifier]
    return practitioner


def build_patient_from_nm1_dmg(nm1: Segment, dmg: Segment | None, recorder=None) -> Patient:
    """NM1 (person) + optional DMG -> Patient. Used for the subscriber
    (NM1*IL) and dependent (NM1*QC) loops - both are always person entities
    in 270/271, unlike the payer/provider loops."""
    patient_id = str(uuid.uuid4())
    patient = Patient(id=patient_id)
    name = _build_person_name(nm1)
    if name:
        patient.name = [name]
        _record_person_name(patient_id, name, recorder)
    identifier = _build_nm1_identifier(nm1, resource_id=patient_id, recorder=recorder)
    if identifier:
        patient.identifier = [identifier]

    if dmg is not None:
        # DMG01 is always "D8" (CCYYMMDD) in practice for healthcare 270/
        # 271 - other X12-legal date/time qualifiers exist but aren't used
        # in this transaction family, disclosed rather than handled.
        if element(dmg, 1) == "D8":
            dmg02_raw = element(dmg, 2)
            birth_date = parse_hl7_date(dmg02_raw)
            if birth_date:
                patient.birthDate = birth_date
                if recorder:
                    recorder.record(patient_id, "birthDate", edi_location("DMG", 2), birth_date, source_value=dmg02_raw)
        dmg03_raw = element(dmg, 3)
        gender_code = dmg03_raw.strip().upper()
        gender = _DMG_GENDER_MAP.get(gender_code)
        if gender:
            patient.gender = gender
            if recorder:
                recorder.record(patient_id, "gender", edi_location("DMG", 3), gender, source_value=dmg03_raw)

    return patient


def build_coverage(patient: Resource, payer: Resource, subscriber: Resource, recorder=None) -> Coverage:
    """A minimal, always-active Coverage linking a beneficiary (patient),
    payer, and subscriber - independently written the identical way three
    separate times (eligibility_270.py, eligibility_271.py, prior_auth.py)
    before being promoted here once claim_837p.py became a fourth real
    consumer of the exact same shape. `patient`/`subscriber` are the same
    resource whenever the patient isn't a dependent - callers pass
    whichever resource they already resolved for each role rather than
    this function re-deriving patient/dependent precedence itself."""
    coverage_id = str(uuid.uuid4())
    if recorder:
        recorder.record_inferred(
            coverage_id,
            "status",
            "Every Coverage this app builds is a minimal, synthesized resource always recorded as status=\"active\" - no X12 field in any family this converter reads carries a real policy-status value to read this from.",
            "active",
        )
    return Coverage(
        id=coverage_id,
        status="active",
        beneficiary=Reference(reference=f"urn:uuid:{patient.id}"),
        payor=[Reference(reference=f"urn:uuid:{payer.id}")],
        subscriber=Reference(reference=f"urn:uuid:{subscriber.id}"),
    )


def assemble_bundle(bht: Segment, *resources: Resource, recorder=None) -> Bundle:
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
        if recorder:
            recorder.record(bundle.id, "identifier.value", edi_location("BHT", 3), reference_id)

    # Bundle.timestamp is FHIR "instant", which (unlike Coverage's or
    # CoverageEligibilityRequest's plain `date`/`dateTime` fields) has NO
    # date-only form - parse_x12_datetime's date-only fallback would
    # produce a value the instant regex rejects, crashing resource
    # construction on an otherwise perfectly convertible transaction set
    # whenever BHT05 (time) is empty or unparseable. This is the exact bug
    # app/cda/common.py::assemble_bundle already shipped once and disclosed
    # in CLAUDE.md - call parse_hl7_datetime directly here (no date-only
    # fallback) rather than parse_x12_datetime, matching that fix.
    bht04_raw = element(bht, 4)
    bht05_raw = element(bht, 5)
    timestamp = parse_hl7_datetime(bht04_raw + bht05_raw)
    if timestamp:
        bundle.timestamp = timestamp
        if recorder:
            recorder.record(
                bundle.id,
                "timestamp",
                f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}",
                timestamp,
                source_value=bht04_raw + bht05_raw,
            )

    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources]
    return bundle


# The two helpers below build ValidationFinding objects, unlike everything
# else in this module - a deliberate, established exception to "common.py
# is conversion-shared logic," not scope creep: every *_validation.py
# module in this package already imports non-Finding conversion helpers
# from here directly (resolve_eligibility_parties, iter_diagnosis_hi_
# segments, ...) specifically so validation can never see a different
# segment set than conversion does, so this module has already been a
# validation consumer's dependency from the start. Promoting the two
# rule shapes that turned out to be genuinely identical across 6 (missing
# subscriber name) and 3 (service date in future) sibling *_validation.py
# files here - rather than a new module just for these two functions -
# keeps that single existing dependency direction intact instead of adding
# a second shared-helpers module for EDI validation alone.


def build_missing_subscriber_name_finding(
    rule_id: str, segment_path: str, subscriber_nm1: Segment
) -> ValidationFinding | None:
    """The "subscriber's NM1 has no resolvable name" info finding, body-
    identical (modulo rule_id/segment_path) across all six EDI families
    with a subscriber loop (270, 276, 278, 837P, 837I, 837D) - each family's
    own segment_path differs only because loop depth differs (e.g. 270's
    2000C vs 837's 2000B), not because the check itself differs."""
    if element(subscriber_nm1, 3) or element(subscriber_nm1, 4):
        return None
    return ValidationFinding(
        severity="info",
        rule_id=rule_id,
        segment=segment_path,
        message="The subscriber's NM1 segment has no resolvable name - the converter will still build a Patient, just with no HumanName.",
    )


def check_service_lines_for_future_date(
    service_lines: list[tuple[Segment, list[Segment]]],
    resolve_raw_date: Callable[[list[Segment]], str | None],
    rule_id: str,
    segment_path: str,
    message: str,
    now: datetime,
) -> list[ValidationFinding]:
    """The "a service line's date is in the future" warning, body-identical
    (modulo how each family resolves a line's own raw date string) across
    837P/837I/837D - `resolve_raw_date` is where the three genuinely
    diverge: 837P/837I resolve strictly from that line's own DTP*472, while
    837D additionally falls back to one claim-level DTP*472 default when
    the line has none of its own (see claim_837d.py's own
    resolve_line_dtp_raw_date), a real structural difference the other two
    don't have. Returns as soon as the first future-dated line is found,
    matching every sibling rule's own "one finding is enough" precedent."""
    for _leader, members in service_lines:
        raw_date = resolve_raw_date(members)
        if not raw_date:
            continue
        if not_in_future(raw_date, now) is False:
            return [ValidationFinding(severity="warning", rule_id=rule_id, segment=segment_path, message=message)]
    return []
