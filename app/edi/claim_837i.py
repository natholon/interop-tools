"""X12 837I (Health Care Claim: Institutional, 005010X223A2) -> FHIR
`Claim` (`use="claim"`, `type="institutional"`).

**Disclosed verification-source gap, same as every EDI phase in this app**:
X12's own authoritative TR3 for 837I is paywalled (X12 sells it commercially -
no official, free X12-to-FHIR ConceptMap IG exists for this transaction set,
unlike HL7v2's v2-to-FHIR or C-CDA's C-CDA on FHIR). The X12-side segment
shape below is verified against a real X12.org-published example (fetched
and quoted verbatim, not paraphrased from a secondary source) plus
cross-referenced free companion guides (CMS/Palmetto/CGS) where the one
example alone didn't label a field's exact position (e.g. `CL1`'s three
elements) - not the paywalled primary TR3 itself. The FHIR (target) side is
on firmer footing: every CodeSystem/value-set claim below (NUBC Revenue
Codes, NUBC Patient Discharge Status, `ClaimInformationCategoryCodes`,
`claimcareteamrole`) was confirmed by direct fetch of `hl7.org`/
`terminology.hl7.org`, not assumed from memory or a summarized secondary
page.

**The second real consumer of 837P's own 3-level HL chain** - verified
directly against a real X12.org-published example
(`x12.org/examples/005010x223/example-1a-institutional-claim`, quoted
verbatim rather than trusted from a secondary source) that the top-level
loop shape is genuinely identical to 837P's, not coincidentally similar:
2000A (HL03="20", Billing Provider, `NM1*85`), 2000B (HL03="22",
Subscriber, `NM1*IL`+`DMG`, with the payer `NM1*PR` nested inside this same
loop's own members - not its own root loop), 2000C (HL03="23", Patient,
optional, `NM1*QC`+`DMG`). This module has its own `resolve_837i_loops()`
rather than importing `claim_837p.py`'s `resolve_837p_loops()` directly -
deliberately, not an oversight: this codebase's own established discipline
is "extract a shared helper only once a second real, independently-tested
consumer exists," and pre-emptively coupling this module to 837P's
resolver before 837I's own test suite has independently proven every edge
case (the `claim_loop`/`patient_is_dependent` two-gate split in particular)
would risk the exact "assumed-identical, actually diverged" trap that
prompted extracting `build_coverage`/`build_diagnosis_codeable_concepts`
in the first place - if a follow-up review or a real 837I edge case proves
the *entire* resolver genuinely identical (not just the happy path this
one real example exercises), promoting it to `common.py` is the natural
next step, matching that same precedent.

**Where 837I genuinely diverges from 837P - confirmed by the same real
example, not assumed from the family name alone:**
  - `CLM05-1` is NOT a Place-of-Service code the way 837P's is - it's the
    first two digits of the UB-04 Type of Bill (Facility Type Code, e.g.
    "14"=Other, "11"=Hospital Inpatient), a genuinely different vocabulary.
    No verified free FHIR-canonical CodeSystem was found for it (unlike
    837P's real, verified CMS POS system) - disclosed and left unmapped
    entirely, rather than reusing `Claim.item.locationCodeableConcept`
    (which would be semantically wrong here: CLM05-1 for institutional
    claims describes what *kind* of stay this was, not *where* the service
    happened).
  - **`CL1` (Institutional Claim Code) has no 837P equivalent at all** -
    CL101 Admission Type Code, CL102 Admission Source Code, CL103 Patient
    Status Code. Only CL103 is mapped this slice, via `Claim.supportingInfo`
    with `category` = the real `discharge` code from FHIR's own
    `ClaimInformationCategoryCodes` value set ("Discharge status and
    discharge to locations" - a direct semantic match, confirmed by fetch)
    and `code` on the real, verified NUBC Patient Discharge Status
    CodeSystem (`https://www.nubc.org/CodeSystem/PatDischargeStatus` -
    confirmed by fetch, including a dedicated HL7 Terminology Authority
    page naming CL103 specifically). CL101/CL102 (admission type/source)
    have no comparably-fitting code in that value set (`hospitalized` is
    the closest conceptual neighbor but describes a span, not a type/source
    code) and are left disclosed-and-unmapped rather than forcing a
    mismatched category onto them.
  - **`HI`'s much richer institutional usage is only partially mapped this
    slice**: only the diagnosis-qualified composites (`ABK`/`ABF`/`BK`/`BF`
    - the identical qualifier set 837P and Phase 3's 278 both already use)
    feed `Claim.diagnosis[]`, via the same shared
    `common.py::build_diagnosis_codeable_concepts` 837P already uses -
    genuinely the same composite shape, confirmed by the real example.
    Occurrence codes (`BH` qualifier), value codes (`BE`), and condition
    codes (`BG`) - all real, present-in-the-actual-example HI usages with
    no equivalent in 837P at all - are disclosed and deliberately deferred:
    each would need its own `Claim.supportingInfo` category mapping
    decided independently (none map cleanly to `ClaimInformationCategoryCodes`
    either, confirmed by fetch), a large enough scope on its own to be a
    future slice rather than bundled into this one, matching this
    project's own "one thing per slice" precedent.
  - **`SV2` (Institutional Service Line) replaces `SV1` - a related but
    NOT identical composite shape, confirmed by direct verification, not
    assumed from the "both are service line segments" family
    resemblance**: `SV2-01` (Revenue Code) is the required, primary field
    - unlike `SV1-01`'s always-present procedure code, `SV2-02` (the
    composite procedure identifier) is genuinely *optional* in real
    institutional lines (e.g. a bare room-and-board revenue-code line
    carries no procedure code at all) - `ClaimItem.productOrService` is
    still FHIR-required regardless (confirmed by direct construction, the
    same "don't trust `model_fields`" lesson every EDI phase has already
    learned at least once), so this module falls back to
    `CodeableConcept(text=f"Revenue code {revenue_code}")` rather than a
    generic placeholder string, since the revenue code itself is always
    genuinely informative here. `SV2-01` maps to the real, verified NUBC
    Revenue Codes CodeSystem (`https://www.nubc.org/CodeSystem/RevenueCodes`
    - confirmed by fetch) via `ClaimItem.revenue`, a field 837P's own
    `SV1`-derived items never populate (professional claims don't carry
    revenue codes at all). **`SV2` has no diagnosis-pointer composite at
    all, unlike `SV1-07`** - confirmed directly (not assumed): institutional
    diagnoses apply at the claim level via `HI`, not per-service-line, so
    `Claim.item[].diagnosisSequence` is never populated by this module, a
    genuine structural fact about 837I, not a missing feature carried over
    incompletely from 837P.
  - **Attending Provider (`NM1*71`) replaces 837P's Rendering Provider
    (`NM1*82`)** as the one 2310-level provider role this slice
    materializes - institutional claims' primary "who is responsible for
    this patient" role is the attending physician, not a per-line
    rendering provider (which 837I also supports via its own `2310`
    sub-loop shapes, deferred here the same way 837P defers Service
    Facility Location). Mapped to `Claim.careTeam[]` with `role="primary"`
    (the same closest-fit code 837P's own rendering-provider mapping
    already uses, confirmed against the same 4-code
    `claimcareteamrole` value set - there's no `"attending"` code in that
    value set either).

Disclosed Phase scope limits, decided up front (mirroring 837P's own
disclosure style): only the first 2300 claim per transaction set is
mapped; 2010AB Pay-to Provider, 2310B Operating Physician, 2310C Other
Operating Physician, 2310E Service Facility Location, and the 2320/2330
Other Subscriber Information (COB) loops are never read; `CLM05-1`,
`CL101`/`CL102`, and `HI`'s occurrence/value/condition-code usages have no
mapped FHIR target this slice (see above); `SV206`/`SV207` (non-covered
charges) are not read."""

import uuid
from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.claim import Claim, ClaimCareTeam, ClaimDiagnosis, ClaimInsurance, ClaimItem, ClaimSupportingInfo
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.money import Money
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.base import EdiTransactionBuilder
from app.edi.common import (
    assemble_bundle,
    build_coverage,
    build_diagnosis_codeable_concepts,
    build_organization_from_nm1,
    build_patient_from_nm1_dmg,
    build_practitioner_from_nm1,
    is_person_entity,
    iter_diagnosis_hi_segments,
    parse_x12_datetime,
)
from app.edi.parser import (
    Delimiters,
    HlLoop,
    Segment,
    TransactionSet,
    component,
    element,
    find_segment,
    group_by_hl_hierarchy,
    group_by_leader,
    parse_decimal,
)
from app.fhir_models.builders import parse_hl7_date
from app.hl7.errors import MappingError, MissingSegmentError

TRANSACTION_SET_ID = "837"

# Same HL03 level codes as claim_837p.py, kept as this module's own local
# constants for the same reason that module's own bullet gives (not
# reused from common.py's HL_INFORMATION_SOURCE/HL_SUBSCRIBER, since their
# meaning here is Billing Provider/Subscriber, not Information Source).
_HL_BILLING_PROVIDER = "20"
_HL_SUBSCRIBER = "22"
_HL_PATIENT = "23"

_NM1_BILLING_PROVIDER = "85"
_NM1_SUBSCRIBER = "IL"
_NM1_PAYER = "PR"
_NM1_PATIENT = "QC"
_NM1_ATTENDING_PROVIDER = "71"

# NUBC Revenue Codes - a real, verified FHIR-canonical CodeSystem
# (confirmed by direct fetch of terminology.hl7.org's own THO entry),
# unlike most of this app's disclosed local-system fallbacks for X12 code
# lists with no official FHIR home.
_REVENUE_CODE_SYSTEM = "https://www.nubc.org/CodeSystem/RevenueCodes"

# Same genuinely-unresolvable CPT-vs-HCPCS-Level-II ambiguity claim_837p.py
# discloses for SV1-01's "HC" qualifier - SV2-02 shares the identical
# composite shape and code list.
_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM = "urn:interop-tools:x12-procedure-qualifier"

_CLAIM_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claim-type"
DEFAULT_CLAIM_TYPE = "institutional"
DEFAULT_PRIORITY = "normal"
_CARE_TEAM_ROLE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claimcareteamrole"
_ATTENDING_PROVIDER_ROLE = "primary"

# ClaimInformationCategoryCodes' own real "discharge" code - "Discharge
# status and discharge to locations" (confirmed by direct fetch of
# hl7.org/fhir/R4/valueset-claim-informationcategory.html) - a genuine
# semantic match for CL103, not a disclosed placeholder.
_SUPPORTING_INFO_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/claiminformationcategory"
_DISCHARGE_STATUS_CATEGORY = "discharge"
# NUBC Patient Discharge Status Codes - a real, verified FHIR-canonical
# CodeSystem (confirmed by direct fetch, including a dedicated HL7
# Terminology Authority page explicitly naming CL103 as this code list's
# X12 source field).
_DISCHARGE_STATUS_CODE_SYSTEM = "https://www.nubc.org/CodeSystem/PatDischargeStatus"


@dataclass
class Resolved837iLoops:
    """Mirrors claim_837p.py's own Resolved837pLoops field-for-field - see
    that dataclass's docstring for why claim_loop/patient_is_dependent are
    gated independently. Kept as this module's own type (not reused
    directly) since resolve_837i_loops() is this module's own independent
    implementation, per the module docstring's own "extract on second use,
    once proven" reasoning."""

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


def _find_nm1(segments: list[Segment], entity_code: str) -> Segment | None:
    return next((seg for seg in segments if seg[0] == "NM1" and element(seg, 1) == entity_code), None)


# Public - claim_837i_validation.py's own service-date-in-future rule must
# filter DTP the identical way, or it could report on (or silently ignore)
# a DTP the real builder would never even read (see claim_837p.py's own
# find_dtp_by_qualifier/DTP_SERVICE_DATE for the precedent this mirrors).
DTP_SERVICE_DATE = "472"


def find_dtp_by_qualifier(segments: list[Segment], qualifier: str) -> Segment | None:
    return next((seg for seg in segments if seg[0] == "DTP" and element(seg, 1) == qualifier), None)


def resolve_837i_loops(segments: list[Segment], transaction_set_id: str) -> Resolved837iLoops:
    """Walk the strict 2000A(20)->2000B(22)->[2000C(23)] parent chain an
    837I transaction set requires - see claim_837p.py::resolve_837p_loops
    for the identical algorithm and the reasoning behind every design
    choice in it (claim_loop resolved structurally regardless of NM1
    validity, payer read from the subscriber loop's own members, etc.)."""
    roots = group_by_hl_hierarchy(segments)
    billing_provider_loop = next((loop for loop in roots if loop.hl03 == _HL_BILLING_PROVIDER), None)
    if billing_provider_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000A Billing Provider loop")

    subscriber_loop = next(
        (child for child in billing_provider_loop.children if child.hl03 == _HL_SUBSCRIBER), None
    )
    if subscriber_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000B Subscriber loop")

    billing_provider_nm1 = _find_nm1(billing_provider_loop.member_segments, _NM1_BILLING_PROVIDER)
    if billing_provider_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000A loop is missing its NM1*85 (billing provider) segment")

    subscriber_nm1 = _find_nm1(subscriber_loop.member_segments, _NM1_SUBSCRIBER)
    if subscriber_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1*IL (subscriber) segment")
    subscriber_dmg = find_segment(subscriber_loop.member_segments, "DMG")

    payer_nm1 = _find_nm1(subscriber_loop.member_segments, _NM1_PAYER)
    if payer_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1*PR (payer) segment")

    patient_loop = next((child for child in subscriber_loop.children if child.hl03 == _HL_PATIENT), None)
    claim_loop = patient_loop if patient_loop is not None else subscriber_loop

    patient_nm1 = None
    patient_dmg = None
    patient_is_dependent = False
    if patient_loop is not None:
        patient_nm1 = _find_nm1(patient_loop.member_segments, _NM1_PATIENT)
        if patient_nm1 is not None:
            patient_dmg = find_segment(patient_loop.member_segments, "DMG")
            patient_is_dependent = True

    return Resolved837iLoops(
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


def _build_procedure_concept(sv2_02: str, delimiters: Delimiters) -> CodeableConcept | None:
    if not sv2_02:
        return None
    qualifier = component(sv2_02, delimiters, 1).strip().upper()
    code = component(sv2_02, delimiters, 2)
    if not code:
        return None
    system = f"{_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else _PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
    return CodeableConcept(coding=[Coding(system=system, code=code)])


def _build_service_line_item(
    sequence: int, sv2: Segment, dtp: Segment | None, delimiters: Delimiters, care_team_sequence: int | None
) -> ClaimItem:
    revenue_code = element(sv2, 1)
    procedure = _build_procedure_concept(element(sv2, 2), delimiters)
    item = ClaimItem(
        sequence=sequence,
        productOrService=procedure or CodeableConcept(text=f"Revenue code {revenue_code}" if revenue_code else "Unspecified service"),
    )
    if revenue_code:
        item.revenue = CodeableConcept(coding=[Coding(system=_REVENUE_CODE_SYSTEM, code=revenue_code)])
    if care_team_sequence is not None:
        item.careTeamSequence = [care_team_sequence]

    charge = parse_decimal(element(sv2, 3))
    if charge is not None:
        item.unitPrice = Money(value=charge, currency="USD")

    quantity_value = parse_decimal(element(sv2, 5))
    if quantity_value is not None:
        item.quantity = Quantity(value=quantity_value)

    if dtp is not None and element(dtp, 2) == "D8":
        serviced = parse_hl7_date(element(dtp, 3))
        if serviced:
            item.servicedDate = serviced

    return item


def _build_discharge_status_supporting_info(cl1: Segment | None, sequence: int) -> ClaimSupportingInfo | None:
    if cl1 is None:
        return None
    status_code = element(cl1, 3)
    if not status_code:
        return None
    return ClaimSupportingInfo(
        sequence=sequence,
        category=CodeableConcept(coding=[Coding(system=_SUPPORTING_INFO_CATEGORY_SYSTEM, code=_DISCHARGE_STATUS_CATEGORY)]),
        code=CodeableConcept(coding=[Coding(system=_DISCHARGE_STATUS_CODE_SYSTEM, code=status_code)]),
    )


class Edi837iBuilder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_837i_loops(transaction_set.segments, self.transaction_set_id)

        billing_provider: Resource = (
            build_practitioner_from_nm1(loops.billing_provider_nm1)
            if is_person_entity(loops.billing_provider_nm1)
            else build_organization_from_nm1(loops.billing_provider_nm1)
        )
        payer = build_organization_from_nm1(loops.payer_nm1)
        subscriber = build_patient_from_nm1_dmg(loops.subscriber_nm1, loops.subscriber_dmg)
        patient = (
            build_patient_from_nm1_dmg(loops.patient_nm1, loops.patient_dmg)
            if loops.patient_is_dependent
            else subscriber
        )

        coverage = build_coverage(patient, payer, subscriber)

        clm = find_segment(loops.claim_loop.member_segments, "CLM")
        if clm is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its 2300 CLM (claim) segment")

        total = parse_decimal(element(clm, 2))
        if total is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's CLM segment has no resolvable total charge (CLM02)")

        diagnosis_concepts = []
        for hi in iter_diagnosis_hi_segments(loops.claim_loop.member_segments, delimiters):
            diagnosis_concepts.extend(build_diagnosis_codeable_concepts(hi, delimiters))

        cl1 = find_segment(loops.claim_loop.member_segments, "CL1")
        supporting_info = _build_discharge_status_supporting_info(cl1, sequence=1)

        attending_nm1 = _find_nm1(loops.claim_loop.member_segments, _NM1_ATTENDING_PROVIDER)
        attending_provider: Resource | None = None
        if attending_nm1 is not None:
            attending_provider = (
                build_practitioner_from_nm1(attending_nm1)
                if is_person_entity(attending_nm1)
                else build_organization_from_nm1(attending_nm1)
            )

        created = parse_x12_datetime(element(bht, 4), element(bht, 5))
        if created is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's BHT segment has no resolvable date (BHT04)")

        line_groups = group_by_leader(loops.claim_loop.member_segments, "LX", ["SV2", "DTP"])
        items = []
        for _lx, members in line_groups:
            sv2 = find_segment(members, "SV2")
            if sv2 is None:
                continue
            dtp = find_dtp_by_qualifier(members, DTP_SERVICE_DATE)
            items.append(
                _build_service_line_item(
                    len(items) + 1, sv2, dtp, delimiters, 1 if attending_provider is not None else None
                )
            )

        claim = Claim(
            id=str(uuid.uuid4()),
            status="active",
            type=CodeableConcept(coding=[Coding(system=_CLAIM_TYPE_SYSTEM, code=DEFAULT_CLAIM_TYPE)]),
            use="claim",
            patient=Reference(reference=f"urn:uuid:{patient.id}"),
            created=created,
            provider=Reference(reference=f"urn:uuid:{billing_provider.id}"),
            priority=CodeableConcept(text=DEFAULT_PRIORITY),
            insurance=[ClaimInsurance(sequence=1, focal=True, coverage=Reference(reference=f"urn:uuid:{coverage.id}"))],
        )
        claim.insurer = Reference(reference=f"urn:uuid:{payer.id}")

        patient_control_number = element(clm, 1)
        if patient_control_number:
            claim.identifier = [Identifier(value=patient_control_number)]

        claim.total = Money(value=total, currency="USD")

        if diagnosis_concepts:
            claim.diagnosis = [
                ClaimDiagnosis(sequence=i, diagnosisCodeableConcept=concept)
                for i, concept in enumerate(diagnosis_concepts, start=1)
            ]

        if supporting_info is not None:
            claim.supportingInfo = [supporting_info]

        if attending_provider is not None:
            claim.careTeam = [
                ClaimCareTeam(
                    sequence=1,
                    provider=Reference(reference=f"urn:uuid:{attending_provider.id}"),
                    role=CodeableConcept(coding=[Coding(system=_CARE_TEAM_ROLE_SYSTEM, code=_ATTENDING_PROVIDER_ROLE)]),
                )
            ]

        if items:
            claim.item = items

        resources: list[Resource] = [billing_provider, payer, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.append(coverage)
        if attending_provider is not None:
            resources.append(attending_provider)
        resources.append(claim)

        return assemble_bundle(bht, *resources)
