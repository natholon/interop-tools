"""X12 837D (Health Care Claim: Dental, 005010X224) -> FHIR `Claim`
(`use="claim"`, `type="oral"`).

**Disclosed verification-source gap, same as every EDI phase in this app**:
X12's own TR3 for 837D is paywalled (no official free X12-to-FHIR IG exists
for this transaction set). The X12-side segment shape below is verified
against a real X12.org-published example
(`x12.org/examples/005010x224/example-01-commercial-health-insurance`,
fetched and quoted verbatim) plus cross-referenced free sources (a Stedi
X12 segment reference for `SV3`'s own element list, since the one real
example alone doesn't label every position) - not the paywalled primary
TR3 itself. The FHIR-side CodeSystem claims (ADA Code on Dental Procedures
and Nomenclature/CDT, ADA Universal Tooth Designation System, ADA Tooth
Surface Codes) were each confirmed by direct fetch of `terminology.hl7.org`.

**The third real consumer of the same 3-level HL chain 837P/837I already
established, confirmed genuinely identical by the same real example, not
assumed from the "another 837 variant" family resemblance**: 2000A(`"20"`,
Billing Provider)->`NM1*85`, 2000B(`"22"`, Subscriber)->`NM1*IL`+`DMG` with
the payer `NM1*PR` nested in the same loop's own members, 2000C(`"23"`,
Patient, optional)->`NM1*QC`+`DMG`. This module has its own independent
`resolve_837d_loops()` rather than importing `claim_837p.py`'s or
`claim_837i.py`'s directly - same "extract on second use, once genuinely
proven, not preemptively" discipline `claim_837i.py`'s own module docstring
already established; a shared `resolve_837_loops()` in `common.py` remains
a natural future step once a third independently-tested implementation
exists to actually prove the whole shape (not just the happy path one real
example exercises) is identical across all three.

**Where 837D genuinely diverges from 837P/837I, confirmed by the same real
example and a cross-referenced X12 segment reference, not assumed**:
  - **`CLM05-1` uses the SAME Place-of-Service vocabulary as 837P's - NOT
    837I's UB-04 Type of Bill** (confirmed both by the real example's own
    value, `"11"` = Office, and by `SV3-03`'s own field description,
    "Place of Service Codes for Professional **or Dental** Services" -
    the two formats genuinely share this vocabulary, not a coincidence).
    This module reuses `common.py::POS_CODE_SYSTEM`/
    `build_place_of_service_from_clm05` directly - the same claim-wide
    default pattern 837P already established (a per-line `SV3-03`
    override is real but out of scope this slice, the same simplification
    837P already made for its own analogous `SV1-05`).
  - **`SV3` (Dental Service) replaces `SV1`/`SV2` - genuinely different
    from both**: `SV3-01`'s procedure-code qualifier is `"AD"` in the real
    example (American Dental Association / CDT codes) - unlike 837P's own
    `"HC"` qualifier (a genuinely unresolvable CPT-vs-HCPCS-Level-II
    ambiguity, disclosed there), dental claims overwhelmingly use exactly
    one code system (CDT) for procedure codes in practice, so `"AD"` maps
    directly to the real, verified `http://www.ada.org/cdt` canonical
    system rather than a disclosed local placeholder - any other qualifier
    (rare in practice) still falls back to a disclosed local system, the
    same "canonical when recognized, disclosed fallback otherwise"
    pattern every other coded lookup in this app follows. `SV3-06`
    (Quantity/Procedure Count) -> `Claim.item.quantity`. `SV3-11`
    (Diagnosis Code Pointer) -> `Claim.item.diagnosisSequence`, resolved
    against `Claim.diagnosis[]` the identical way 837P's own `SV1-07`
    already is (up to 4 1-based positions, out-of-range/non-numeric
    pointers silently skipped) - **its own composite sub-structure was
    not independently confirmed against a primary source** (the one real
    example never populates it), inferred by analogy with `SV1-07`'s own
    confirmed shape rather than guessed at from nothing, and disclosed as
    such. `SV3-04` (Oral Cavity Designation, a genuinely separate concept
    from tooth number/surface) has no FHIR target mapped this slice.
  - **`TOO` (Tooth Information) is its own segment, not part of `SV3` at
    all, but a member of the identical `LX` service-line group** - the
    real example confirms this directly (`LX*1~` / `SV3*...~` / `TOO*...~`
    / `LX*2~`, `TOO` following `SV3` within one line group). `TOO02`
    (tooth number) -> `Claim.item.bodySite`, `TOO03` (a genuinely
    *sub-composite* shape - up to 5 colon-separated single-letter surface
    codes, since a real tooth has at most 5 relevant surfaces) ->
    `Claim.item.subSite` (a `list[CodeableConcept]`, confirmed via direct
    construction that this field is genuinely a list, unlike `bodySite`).
    `TOO01` (Code List Qualifier) is `"JP"` in the real example - the
    Universal/National Tooth Designation System, the dominant real-world
    US system - confirmed to map to the real, verified
    `http://terminology.hl7.org/CodeSystem/ADAUniversalToothDesignationSystem`
    canonical system; **deliberately NOT the FHIR `ex-tooth`/`tooth`
    ValueSet's own backing CodeSystem**, which is FDI (international)
    numbering - a genuinely different vocabulary using different digits
    for the same physical tooth, confirmed by direct fetch rather than
    assumed from the shared "tooth numbering" concept. `TOO03`'s own
    surface letters (`M`/`O`/`D`/`B`/`L`/`F`/`I`) map to the real, verified
    `http://terminology.hl7.org/CodeSystem/ADAToothSurfaceCodes` -
    confirmed to use the identical letter codes by direct fetch, not
    assumed from the similar-sounding international `FDI-surface`
    CodeSystem (a real trap this app has hit before with similarly-named-
    but-different vocabularies, e.g. CDA's `F`/`M`/`UN` gender codes vs.
    HL7v2's `M`/`F`/`O`). Any `TOO01` other than `"JP"` falls back to a
    disclosed local system for `bodySite` rather than being forced into
    the Universal system's own numbering, since no other qualifier's
    mapping was verified.
  - **`DTP*472` (Service Date) commonly appears at the CLAIM level (2300)
    for dental claims, not per-service-line (2400) the way 837P/837I's own
    service lines carry it** - confirmed directly by the real example,
    where neither of its two service lines carries its own `DTP`, but one
    claim-level `DTP*472` does. This module resolves `Claim.item.
    servicedDate` per line from a per-line `DTP*472` when present (the
    same qualifier-filtered `find_dtp_by_qualifier` every sibling family
    already uses), falling back to the one claim-level `DTP*472` as a
    claim-wide default otherwise - the same "claim-wide default, per-line
    override when present" pattern `CLM05-1`'s own place-of-service
    mapping already establishes, applied here because the real example
    genuinely needs it to produce a useful `servicedDate` at all.
  - **Rendering Provider uses `NM1*82` - the same code as 837P's, NOT
    837I's `NM1*71` Attending Provider** - confirmed directly by the real
    example. Mapped to `Claim.careTeam[]` with `role="primary"` the
    identical way 837P's own rendering-provider mapping already is, with
    every `Claim.item[]` carrying `careTeamSequence=[1]` back to it when
    present (a follow-up code review caught this exact linkage missing
    once already, for 837I - built correctly here from the start).

Disclosed Phase-7 scope limits, decided up front, mirroring 837P/837I's own
disclosure style: only the first 2300 claim per transaction set is mapped;
2010AB Pay-to Provider, 2310C Service Facility Location, and the 2320/2330
Other Subscriber Information (COB) loops are never read; `SV3-03`'s own
per-line place-of-service override, `SV3-04` (Oral Cavity Designation),
`SV3-05` (Prosthesis/Crown/Inlay Code), `SV3-07` through `SV3-10` (free-text
reason, copay status, provider agreement, predetermination indicator) have
no mapped FHIR target this slice."""

import uuid
from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.claim import Claim, ClaimCareTeam, ClaimDiagnosis, ClaimInsurance, ClaimItem
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
    build_place_of_service_from_clm05,
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

# Same HL03 level codes as claim_837p.py/claim_837i.py - kept as this
# module's own local constants for the same reason those modules' own
# bullets give.
_HL_BILLING_PROVIDER = "20"
_HL_SUBSCRIBER = "22"
_HL_PATIENT = "23"

_NM1_BILLING_PROVIDER = "85"
_NM1_SUBSCRIBER = "IL"
_NM1_PAYER = "PR"
_NM1_PATIENT = "QC"
_NM1_RENDERING_PROVIDER = "82"

# Real, verified FHIR-canonical CodeSystem (confirmed by direct fetch) for
# CDT (Current Dental Terminology) procedure codes - SV3-01's "AD" qualifier
# maps directly here, unlike 837P's own genuinely-ambiguous "HC" qualifier
# (see module docstring).
_CDT_CODE_SYSTEM = "http://www.ada.org/cdt"
_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM = "urn:interop-tools:x12-procedure-qualifier"

# TOO01 (Code List Qualifier) - "JP" is the Universal/National Tooth
# Designation System, the dominant real-world US system (confirmed by the
# real example), mapping to the real, verified ADA Universal Tooth
# Designation System CodeSystem. Deliberately NOT FHIR's own `ex-tooth`
# CodeSystem, which is FDI (international) numbering - a genuinely
# different vocabulary for the same physical tooth (see module docstring).
_TOO_UNIVERSAL_QUALIFIER = "JP"
_TOOTH_NUMBER_SYSTEM = "http://terminology.hl7.org/CodeSystem/ADAUniversalToothDesignationSystem"
_TOOTH_NUMBER_FALLBACK_SYSTEM = "urn:interop-tools:x12-too-tooth-number"
# Real, verified FHIR-canonical CodeSystem (confirmed by direct fetch) for
# tooth surface letters (M/O/D/B/L/F/I) - TOO03's own composite shape, up
# to 5 surfaces (a real tooth has at most 5 relevant surfaces).
_TOOTH_SURFACE_SYSTEM = "http://terminology.hl7.org/CodeSystem/ADAToothSurfaceCodes"
_MAX_TOOTH_SURFACES = 5

_CLAIM_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claim-type"
DEFAULT_CLAIM_TYPE = "oral"
DEFAULT_PRIORITY = "normal"
_CARE_TEAM_ROLE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claimcareteamrole"
_RENDERING_PROVIDER_ROLE = "primary"

_DTP_SERVICE_DATE = "472"
_MAX_DIAGNOSIS_POINTERS = 4  # inferred by analogy with SV1-07's own confirmed cap - see module docstring


@dataclass
class Resolved837dLoops:
    """Mirrors claim_837p.py's/claim_837i.py's own Resolved837*Loops
    field-for-field - see claim_837i.py's own dataclass docstring for why
    claim_loop/patient_is_dependent are gated independently."""

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


def find_dtp_by_qualifier(segments: list[Segment], qualifier: str) -> Segment | None:
    return next((seg for seg in segments if seg[0] == "DTP" and element(seg, 1) == qualifier), None)


def find_claim_level_segments(claim_loop_members: list[Segment]) -> list[Segment]:
    """Everything in the claim loop's own flat member list that appears
    *before* the first LX (2400 service line) - i.e. genuinely 2300-level
    segments. Needed specifically for the claim-level DTP*472 lookup: since
    `claim_loop.member_segments` is one flat list spanning both 2300 and
    every repeated 2400 group with no structural boundary beyond segment
    order itself, and DTP*472 legitimately appears at *both* levels (2300
    for the claim-wide default this module reads, 2400 per service line),
    naively scanning the whole flat list for "the first DTP" could
    silently grab a per-line DTP instead of a genuine claim-level one on a
    claim shaped differently than this module's own verified real example.
    Public - claim_837d_validation.py needs the identical boundary."""
    first_lx_index = next((i for i, seg in enumerate(claim_loop_members) if seg[0] == "LX"), len(claim_loop_members))
    return claim_loop_members[:first_lx_index]


def resolve_837d_loops(segments: list[Segment], transaction_set_id: str) -> Resolved837dLoops:
    """Walk the strict 2000A(20)->2000B(22)->[2000C(23)] parent chain an
    837D transaction set requires - see claim_837i.py::resolve_837i_loops
    for the identical algorithm and the reasoning behind every design
    choice in it."""
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

    return Resolved837dLoops(
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


def _build_procedure_concept(sv3_01: str, delimiters: Delimiters) -> CodeableConcept | None:
    if not sv3_01:
        return None
    qualifier = component(sv3_01, delimiters, 1).strip().upper()
    code = component(sv3_01, delimiters, 2)
    if not code:
        return None
    system = _CDT_CODE_SYSTEM if qualifier == "AD" else f"{_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else _PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
    return CodeableConcept(coding=[Coding(system=system, code=code)])


def _build_diagnosis_pointers(sv3: Segment, delimiters: Delimiters, num_diagnoses: int) -> list[int]:
    composite = element(sv3, 11)
    if not composite:
        return []
    pointers = []
    for position in range(1, _MAX_DIAGNOSIS_POINTERS + 1):
        raw = component(composite, delimiters, position)
        if not raw:
            break
        try:
            pointer = int(raw)
        except ValueError:
            continue
        if 1 <= pointer <= num_diagnoses:
            pointers.append(pointer)
    return pointers


def _build_tooth_body_site(too: Segment | None) -> CodeableConcept | None:
    if too is None:
        return None
    tooth_number = element(too, 2)
    if not tooth_number:
        return None
    qualifier = element(too, 1).strip().upper()
    system = _TOOTH_NUMBER_SYSTEM if qualifier == _TOO_UNIVERSAL_QUALIFIER else f"{_TOOTH_NUMBER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else _TOOTH_NUMBER_FALLBACK_SYSTEM
    return CodeableConcept(coding=[Coding(system=system, code=tooth_number)])


def _build_tooth_sub_sites(too: Segment | None, delimiters: Delimiters) -> list[CodeableConcept]:
    if too is None:
        return []
    surfaces_composite = element(too, 3)
    if not surfaces_composite:
        return []
    sub_sites = []
    for position in range(1, _MAX_TOOTH_SURFACES + 1):
        surface_code = component(surfaces_composite, delimiters, position)
        if not surface_code:
            break
        sub_sites.append(CodeableConcept(coding=[Coding(system=_TOOTH_SURFACE_SYSTEM, code=surface_code)]))
    return sub_sites


def resolve_line_dtp_raw_date(dtp: Segment | None, claim_level_dtp: Segment | None) -> str | None:
    """Per-line DTP*472 when present and D8-qualified, else the one
    claim-level DTP*472 as a claim-wide default - see module docstring for
    why dental claims commonly need this fallback (the real example this
    builder was verified against has no per-line dates at all). Public and
    returns the *raw* date string (not yet parsed) - claim_837d_validation.py's
    own service-date-in-future rule calls this directly and feeds the
    result straight to `not_in_future`, so conversion and validation can
    never resolve a different "which DTP applies to this line" answer."""
    for candidate in (dtp, claim_level_dtp):
        if candidate is not None and element(candidate, 2) == "D8":
            raw_date = element(candidate, 3)
            if raw_date:
                return raw_date
    return None


def _build_service_line_item(
    sequence: int,
    sv3: Segment,
    too: Segment | None,
    dtp: Segment | None,
    claim_level_dtp: Segment | None,
    delimiters: Delimiters,
    location: CodeableConcept | None,
    num_diagnoses: int,
    care_team_sequence: int | None,
) -> ClaimItem:
    procedure = _build_procedure_concept(element(sv3, 1), delimiters) or CodeableConcept(text="Unspecified procedure")
    item = ClaimItem(sequence=sequence, productOrService=procedure)

    charge = parse_decimal(element(sv3, 2))
    if charge is not None:
        item.unitPrice = Money(value=charge, currency="USD")

    quantity_value = parse_decimal(element(sv3, 6))
    if quantity_value is not None:
        item.quantity = Quantity(value=quantity_value)

    raw_date = resolve_line_dtp_raw_date(dtp, claim_level_dtp)
    if raw_date:
        serviced = parse_hl7_date(raw_date)
        if serviced:
            item.servicedDate = serviced

    if location is not None:
        item.locationCodeableConcept = location

    body_site = _build_tooth_body_site(too)
    if body_site is not None:
        item.bodySite = body_site
    sub_sites = _build_tooth_sub_sites(too, delimiters)
    if sub_sites:
        item.subSite = sub_sites

    pointers = _build_diagnosis_pointers(sv3, delimiters, num_diagnoses)
    if pointers:
        item.diagnosisSequence = pointers

    if care_team_sequence is not None:
        item.careTeamSequence = [care_team_sequence]

    return item


class Edi837dBuilder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_837d_loops(transaction_set.segments, self.transaction_set_id)

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

        rendering_nm1 = _find_nm1(loops.claim_loop.member_segments, _NM1_RENDERING_PROVIDER)
        rendering_provider: Resource | None = None
        if rendering_nm1 is not None:
            rendering_provider = (
                build_practitioner_from_nm1(rendering_nm1)
                if is_person_entity(rendering_nm1)
                else build_organization_from_nm1(rendering_nm1)
            )

        created = parse_x12_datetime(element(bht, 4), element(bht, 5))
        if created is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's BHT segment has no resolvable date (BHT04)")

        location = build_place_of_service_from_clm05(clm, delimiters)

        # Claim-level DTP*472 - the fallback servicedDate default every
        # service line uses when it has no DTP of its own (see module
        # docstring for why dental claims commonly need this). Scoped to
        # segments before the first LX only - see find_claim_level_segments's
        # own docstring for why a naive whole-claim-loop scan risks grabbing
        # a per-line DTP instead.
        claim_level_dtp = find_dtp_by_qualifier(
            find_claim_level_segments(loops.claim_loop.member_segments), _DTP_SERVICE_DATE
        )

        line_groups = group_by_leader(loops.claim_loop.member_segments, "LX", ["SV3", "TOO", "DTP"])
        items = []
        for _lx, members in line_groups:
            sv3 = find_segment(members, "SV3")
            if sv3 is None:
                continue
            too = find_segment(members, "TOO")
            dtp = find_dtp_by_qualifier(members, _DTP_SERVICE_DATE)
            items.append(
                _build_service_line_item(
                    len(items) + 1,
                    sv3,
                    too,
                    dtp,
                    claim_level_dtp,
                    delimiters,
                    location,
                    len(diagnosis_concepts),
                    1 if rendering_provider is not None else None,
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

        if rendering_provider is not None:
            claim.careTeam = [
                ClaimCareTeam(
                    sequence=1,
                    provider=Reference(reference=f"urn:uuid:{rendering_provider.id}"),
                    role=CodeableConcept(coding=[Coding(system=_CARE_TEAM_ROLE_SYSTEM, code=_RENDERING_PROVIDER_ROLE)]),
                )
            ]

        if items:
            claim.item = items

        resources: list[Resource] = [billing_provider, payer, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.append(coverage)
        if rendering_provider is not None:
            resources.append(rendering_provider)
        resources.append(claim)

        return assemble_bundle(bht, *resources)
