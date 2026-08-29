"""X12 837D (Health Care Claim: Dental, 005010X224A2) -> FHIR `Claim`
(`use="claim"`, `type="oral"`), completing the "big five" HIPAA suite.

**Verification gap, shared by every EDI family here**: X12's TR3 is
paywalled and no free X12-to-FHIR crosswalk exists. Verified against a
real X12.org example (`005010x224/example-01-commercial-health-insurance`)
plus a Stedi segment reference; the FHIR side (CDT, ADA Universal Tooth
Designation, ADA Tooth Surface Codes) confirmed by direct fetch.

Shares 837P/837I's 3-level HL chain via the shared `resolve_837_loops()`.

**Where 837D differs:**
  - `CLM05-1` uses the *same* Place-of-Service vocabulary as 837P (not
    837I's UB-04 Type of Bill), confirmed by `SV3-03`'s own field
    description covering "Professional or Dental Services".
  - `SV3` replaces `SV1`/`SV2`. `SV3-01`'s qualifier is `"AD"` (CDT) in
    practice, mapped to the real `http://www.ada.org/cdt` system; any
    other qualifier falls back to a disclosed local system. `SV3-11` is
    the same `C004` Composite Diagnosis Code Pointer as `SV1-07` -
    originally disclosed as inferred-by-analogy since the example never
    populates it, later confirmed against a 2025 CMS/CGS companion guide
    and two X12 5010 segment schemas.
  - **`TOO` (Tooth Information) is its own segment**, a member of the
    `LX` line group rather than part of `SV3`. `TOO02` ->
    `Claim.item.bodySite`, `TOO03` (up to 5 colon-separated surface
    letters) -> `Claim.item.subSite` (confirmed a list, unlike
    `bodySite`). `TOO01="JP"` maps to ADAUniversalToothDesignationSystem -
    deliberately NOT FHIR's `ex-tooth` ValueSet, which is FDI numbering:
    a different vocabulary using different digits for the same tooth.
    Surface letters likewise map to ADAToothSurfaceCodes, not the
    similarly-named international `FDI-surface` (the same
    similar-name-different-vocabulary trap CDA's `F`/`M`/`UN` gender codes
    already sprang once). A non-`JP` qualifier falls back to a local
    system rather than assuming Universal numbering.
  - **`DTP*472` is commonly claim-level (2300) for dental**, not per-line
    - the real example's two service lines carry none. Resolved per line
    when present, else from the one claim-level default.
  - Rendering Provider is `NM1*82` (as 837P, not 837I's `NM1*71`), mapped
    to `Claim.careTeam[]` role `primary`, with each item carrying
    `careTeamSequence=[1]`.

**Scope limits**: only the first 2300 claim; 2010AB, 2310C and the
2320/2330 COB loops are never read; `SV3-03`'s per-line place-of-service
override, `SV3-04`, `SV3-05` and `SV3-07`-`SV3-10` are unmapped."""

import uuid

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
    DTP_SERVICE_DATE,
    assemble_bundle,
    build_coverage,
    build_diagnosis_codeable_concepts,
    build_organization_from_nm1,
    build_patient_from_nm1_dmg,
    build_place_of_service_from_clm05,
    build_practitioner_from_nm1,
    find_dtp_by_qualifier,
    find_nm1_group,
    is_person_entity,
    iter_diagnosis_hi_segments,
    parse_x12_datetime,
    resolve_837_loops,
)
from app.edi.parser import (
    Delimiters,
    Segment,
    TransactionSet,
    component,
    element,
    find_segment,
    group_by_leader,
    parse_decimal,
)
from app.fhir_models.builders import parse_hl7_date
from app.hl7.errors import MappingError, MissingSegmentError
from app.provenance.location import edi_location

TRANSACTION_SET_ID = "837"

_NM1_RENDERING_PROVIDER = "82"

# Real, verified FHIR-canonical CodeSystem (confirmed by direct fetch) for
# CDT (Current Dental Terminology) procedure codes - SV3-01's "AD" qualifier
# maps directly here, unlike 837P's own genuinely-ambiguous "HC" qualifier
# (see module docstring). Public - app/transform/claim_837d.py's own reverse
# builder needs it to reverse a coded procedure back to its original SV3-01
# qualifier, the same "promote once a real reverse-direction consumer exists"
# discipline every other disclosed local-system constant in this app follows.
CDT_CODE_SYSTEM = "http://www.ada.org/cdt"
_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM = "urn:interop-tools:x12-procedure-qualifier"

# TOO01 (Code List Qualifier) - "JP" is the Universal/National Tooth
# Designation System, the dominant real-world US system (confirmed by the
# real example), mapping to the real, verified ADA Universal Tooth
# Designation System CodeSystem. Deliberately NOT FHIR's own `ex-tooth`
# CodeSystem, which is FDI (international) numbering - a genuinely
# different vocabulary for the same physical tooth (see module docstring).
# All four public for the identical reverse-direction reason CDT_CODE_SYSTEM
# is public above.
TOO_UNIVERSAL_QUALIFIER = "JP"
TOOTH_NUMBER_SYSTEM = "http://terminology.hl7.org/CodeSystem/ADAUniversalToothDesignationSystem"
TOOTH_NUMBER_FALLBACK_SYSTEM = "urn:interop-tools:x12-too-tooth-number"
# Real, verified FHIR-canonical CodeSystem (confirmed by direct fetch) for
# tooth surface letters (M/O/D/B/L/F/I) - TOO03's own composite shape, up
# to 5 surfaces (a real tooth has at most 5 relevant surfaces).
TOOTH_SURFACE_SYSTEM = "http://terminology.hl7.org/CodeSystem/ADAToothSurfaceCodes"
_MAX_TOOTH_SURFACES = 5

_CLAIM_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claim-type"
DEFAULT_CLAIM_TYPE = "oral"
DEFAULT_PRIORITY = "normal"
_CARE_TEAM_ROLE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claimcareteamrole"
_RENDERING_PROVIDER_ROLE = "primary"

_MAX_DIAGNOSIS_POINTERS = 4  # SV3-11's own composite cap, confirmed identical to SV1-07's - see module docstring

# Resolved837dLoops/resolve_837d_loops/_find_nm1/find_dtp_by_qualifier/
# DTP_SERVICE_DATE were promoted to app/edi/common.py as
# Resolved837Loops/resolve_837_loops/find_nm1_by_entity_code/
# find_dtp_by_qualifier/DTP_SERVICE_DATE - this module is what made the
# third-consumer promotion bar its own earlier docstring described. See
# common.py's own Resolved837Loops docstring for the full reasoning.


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


def _resolve_qualifier_system(qualifier: str, recognized_qualifier: str, canonical_system: str, fallback_system: str) -> str:
    """Shared "canonical system when the qualifier is the one recognized
    value, else a disclosed local fallback keyed by the qualifier itself,
    else the bare fallback system" resolution - both SV3-01's procedure
    qualifier ("AD" -> CDT) and TOO01's tooth-number qualifier ("JP" ->
    Universal/National) follow this identical shape, previously each
    written out as its own hard-to-scan nested ternary."""
    if qualifier == recognized_qualifier:
        return canonical_system
    if qualifier:
        return f"{fallback_system}:{qualifier}"
    return fallback_system


def _build_procedure_concept(
    sv3_01: str, delimiters: Delimiters, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> CodeableConcept | None:
    if not sv3_01:
        return None
    qualifier = component(sv3_01, delimiters, 1).strip().upper()
    code = component(sv3_01, delimiters, 2)
    if not code:
        return None
    system = _resolve_qualifier_system(qualifier, "AD", CDT_CODE_SYSTEM, _PROCEDURE_QUALIFIER_FALLBACK_SYSTEM)
    if recorder and resource_id and relative_path:
        recorder.record(resource_id, f"{relative_path}.coding[0].code", edi_location("SV3", 1, component=2), code)
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


def _build_tooth_body_site(
    too: Segment | None, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> CodeableConcept | None:
    if too is None:
        return None
    tooth_number = element(too, 2)
    if not tooth_number:
        return None
    qualifier = element(too, 1).strip().upper()
    system = _resolve_qualifier_system(qualifier, TOO_UNIVERSAL_QUALIFIER, TOOTH_NUMBER_SYSTEM, TOOTH_NUMBER_FALLBACK_SYSTEM)
    if recorder and resource_id and relative_path:
        recorder.record(resource_id, f"{relative_path}.coding[0].code", edi_location("TOO", 2), tooth_number)
    return CodeableConcept(coding=[Coding(system=system, code=tooth_number)])


def _build_tooth_sub_sites(
    too: Segment | None, delimiters: Delimiters, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> list[CodeableConcept]:
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
        sub_sites.append(CodeableConcept(coding=[Coding(system=TOOTH_SURFACE_SYSTEM, code=surface_code)]))
        if recorder and resource_id and relative_path:
            recorder.record(
                resource_id,
                f"{relative_path}[{len(sub_sites) - 1}].coding[0].code",
                edi_location("TOO", 3, component=position),
                surface_code,
            )
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
    resource_id: str | None = None,
    recorder=None,
) -> ClaimItem:
    item_path = f"item[{sequence - 1}]"
    procedure = _build_procedure_concept(
        element(sv3, 1), delimiters, resource_id=resource_id, relative_path=f"{item_path}.productOrService", recorder=recorder
    )
    if procedure is None:
        procedure = CodeableConcept(text="Unspecified procedure")
        if recorder and resource_id:
            recorder.record_inferred(
                resource_id,
                f"{item_path}.productOrService.text",
                "This service line's own SV3-01 (Composite Dental Procedure Identifier) has no resolvable code - Claim.item[].productOrService defaults to a generic placeholder text.",
                "Unspecified procedure",
            )
    item = ClaimItem(sequence=sequence, productOrService=procedure)

    charge_raw = element(sv3, 2)
    charge = parse_decimal(charge_raw)
    if charge is not None:
        item.unitPrice = Money(value=charge, currency="USD")
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.unitPrice.value", edi_location("SV3", 2), charge_raw)

    quantity_raw = element(sv3, 6)
    quantity_value = parse_decimal(quantity_raw)
    if quantity_value is not None:
        item.quantity = Quantity(value=quantity_value)
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.quantity.value", edi_location("SV3", 6), quantity_raw)

    raw_date = resolve_line_dtp_raw_date(dtp, claim_level_dtp)
    if raw_date:
        serviced = parse_hl7_date(raw_date)
        if serviced:
            item.servicedDate = serviced
            if recorder and resource_id:
                # Re-derive (purely for provenance, no change to
                # resolve_line_dtp_raw_date's own behavior) whether the
                # per-line DTP or the claim-level default actually supplied
                # this value - the same "report which branch really fired"
                # discipline app/mappings/adt.py's own PV1-44-vs-EVN-2
                # resolution already established.
                line_dtp_fired = dtp is not None and element(dtp, 2) == "D8" and element(dtp, 3)
                source_location = edi_location("DTP", 3) if line_dtp_fired else edi_location("DTP", 3, segment_repetition=0)
                recorder.record(
                    resource_id, f"{item_path}.servicedDate", source_location, serviced, source_value=raw_date
                )

    if location is not None:
        item.locationCodeableConcept = location
        if recorder and resource_id and location.coding:
            recorder.record(
                resource_id,
                f"{item_path}.locationCodeableConcept.coding[0].code",
                edi_location("CLM", 5, component=1),
                location.coding[0].code,
            )

    body_site = _build_tooth_body_site(too, resource_id=resource_id, relative_path=f"{item_path}.bodySite", recorder=recorder)
    if body_site is not None:
        item.bodySite = body_site
    sub_sites = _build_tooth_sub_sites(too, delimiters, resource_id=resource_id, relative_path=f"{item_path}.subSite", recorder=recorder)
    if sub_sites:
        item.subSite = sub_sites

    pointers = _build_diagnosis_pointers(sv3, delimiters, num_diagnoses)
    if pointers:
        item.diagnosisSequence = pointers
        if recorder and resource_id:
            recorder.record(
                resource_id, f"{item_path}.diagnosisSequence", edi_location("SV3", 11), ",".join(str(p) for p in pointers)
            )

    if care_team_sequence is not None:
        item.careTeamSequence = [care_team_sequence]

    return item


class Edi837dBuilder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_837_loops(transaction_set.segments, self.transaction_set_id)

        billing_provider: Resource = (
            build_practitioner_from_nm1(loops.billing_provider_nm1, recorder=recorder, members=loops.billing_provider_members)
            if is_person_entity(loops.billing_provider_nm1)
            else build_organization_from_nm1(loops.billing_provider_nm1, recorder=recorder, members=loops.billing_provider_members)
        )
        payer = build_organization_from_nm1(loops.payer_nm1, recorder=recorder, members=loops.payer_members)
        subscriber = build_patient_from_nm1_dmg(loops.subscriber_nm1, loops.subscriber_dmg, recorder=recorder)
        patient = (
            build_patient_from_nm1_dmg(loops.patient_nm1, loops.patient_dmg, recorder=recorder)
            if loops.patient_is_dependent
            else subscriber
        )

        coverage = build_coverage(
            patient,
            payer,
            subscriber,
            recorder=recorder,
            sbr=find_segment(loops.subscriber_loop.member_segments, "SBR"),
            pat=find_segment(loops.claim_loop.member_segments, "PAT"),
        )

        clm = find_segment(loops.claim_loop.member_segments, "CLM")
        if clm is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its 2300 CLM (claim) segment")

        total_raw = element(clm, 2)
        total = parse_decimal(total_raw)
        if total is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's CLM segment has no resolvable total charge (CLM02)")

        claim_id = str(uuid.uuid4())

        diagnosis_concepts = []
        for hi_index, hi in enumerate(iter_diagnosis_hi_segments(loops.claim_loop.member_segments, delimiters)):
            new_concepts = build_diagnosis_codeable_concepts(
                hi,
                delimiters,
                resource_id=claim_id,
                relative_path_prefix="diagnosis",
                recorder=recorder,
                index_offset=len(diagnosis_concepts),
                segment_repetition=hi_index,
            )
            diagnosis_concepts.extend(new_concepts)

        rendering_nm1, rendering_nm1_members = find_nm1_group(loops.claim_loop.member_segments, _NM1_RENDERING_PROVIDER)
        rendering_provider: Resource | None = None
        if rendering_nm1 is not None:
            rendering_provider = (
                build_practitioner_from_nm1(rendering_nm1, recorder=recorder, members=rendering_nm1_members)
                if is_person_entity(rendering_nm1)
                else build_organization_from_nm1(rendering_nm1, recorder=recorder, members=rendering_nm1_members)
            )

        bht04_raw = element(bht, 4)
        bht05_raw = element(bht, 5)
        created = parse_x12_datetime(bht04_raw, bht05_raw)
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
            find_claim_level_segments(loops.claim_loop.member_segments), DTP_SERVICE_DATE
        )

        line_groups = group_by_leader(loops.claim_loop.member_segments, "LX", ["SV3", "TOO", "DTP"])
        items = []
        for _lx, members in line_groups:
            sv3 = find_segment(members, "SV3")
            if sv3 is None:
                continue
            too = find_segment(members, "TOO")
            dtp = find_dtp_by_qualifier(members, DTP_SERVICE_DATE)
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
                    resource_id=claim_id,
                    recorder=recorder,
                )
            )

        claim = Claim(
            id=claim_id,
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
            if recorder:
                recorder.record(claim_id, "identifier[0].value", edi_location("CLM", 1), patient_control_number)

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
            if recorder:
                recorder.record_inferred(
                    claim_id,
                    "careTeam[0].role.coding[0].code",
                    'Every rendering-provider care team entry this app builds from an 837D claim has role="primary" - the closest fit in FHIR\'s own 4-code claimcareteamrole value set (there is no "rendering" code), not read from any X12 field.',
                    _RENDERING_PROVIDER_ROLE,
                )

        if items:
            claim.item = items

        resources: list[Resource] = [billing_provider, payer, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.append(coverage)
        if rendering_provider is not None:
            resources.append(rendering_provider)
        resources.append(claim)

        if recorder:
            recorder.record_inferred(
                claim_id,
                "status",
                'Every Claim this app builds from an 837D transaction set has status="active" - not read from any X12 field.',
                "active",
            )
            recorder.record_inferred(
                claim_id,
                "type.coding[0].code",
                'Every Claim this app builds from an 837D transaction set has type="oral" - that\'s what an 837D transaction fundamentally represents, not read from any field.',
                DEFAULT_CLAIM_TYPE,
            )
            recorder.record_inferred(
                claim_id,
                "use",
                'Every Claim this app builds from an 837D transaction set has use="claim" - not read from any X12 field.',
                "claim",
            )
            recorder.record_inferred(
                claim_id,
                "priority.text",
                "837D carries no data element for a claim's own priority - Claim.priority defaults to \"normal\", the same \"default to the most common real value\" precedent as 837P's/837I's own DEFAULT_PRIORITY.",
                DEFAULT_PRIORITY,
            )
            recorder.record(
                claim_id, "created", f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}", created, source_value=bht04_raw + bht05_raw
            )
            recorder.record(claim_id, "total.value", edi_location("CLM", 2), total_raw)

        return assemble_bundle(bht, *resources, recorder=recorder)
