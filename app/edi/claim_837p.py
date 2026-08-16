"""X12 837P (Health Care Claim: Professional, 005010X222A2) -> FHIR
`Claim` (`use="claim"`, `type="professional"`).

**The deepest, most cross-referential loop shape of any EDI phase in this
app** - verified directly against a real X12.org-published example
(`x12.org/examples/005010x222/example-3a-claim-billing-provider-payer`,
quoted verbatim rather than trusted from an AI-summarized secondary source,
the same discipline that already caught a wrong secondary-source claim once
for 278's own HL03 table - here it was Stedi's page claiming 2010BB (payer)
uses `NM1*41`, which the real example disproves: `NM1*41` is the 1000A
Submitter loop, an interchange-level loop this app doesn't materialize at
all; the real payer loop is `NM1*PR`, confirmed directly).

Loop shape (HL-hierarchy, HL03 level codes - **a three-level chain, not
270/271/278's four**, and the numeric codes' *meaning* is different again
despite two of them numerically coinciding with 270/271/278's own table -
confirmed genuinely, not assumed, the same "don't assume a numeric HL03
code carries the same meaning across TR3s" discipline `claim_status.py`
already established):
  2000A (HL03="20", Billing Provider - NOT "Information Source" the way
         270/271/278 use "20") NM1*85 -> billing provider Org/Practitioner
  2000B (HL03="22", Subscriber)        NM1*IL, DMG -> subscriber Patient
                                        NM1*PR (payer) is ALSO a member of
                                        THIS loop, not its own HL level -
                                        a genuine structural difference
                                        from every other EDI family here,
                                        where the payer gets its own root
                                        HL loop.
  2000C (HL03="23", Patient, optional) NM1*QC, DMG -> patient Patient, only
                                        emitted when the patient is not the
                                        subscriber themselves.

Unlike every other EDI family in this app, **the 2300 Claim Information
loop and everything nested under it (HI, the 2310B rendering-provider NM1,
and the repeating 2400 service lines) are NOT their own HL level** - X12's
HL segments stop at the patient level, so CLM/HI/NM1*82/LX/SV1/DTP are all
flat members of whichever loop is "the patient" (the 2000C loop when
present, else 2000B itself) - the same `group_by_leader`-over-a-flat-
member-list shape `claim_status.py`'s TRN groups and `remittance_835.py`'s
CLP groups already use, just one level further nested. This is genuinely
convenient: no new loop-hierarchy primitive is needed, `group_by_hl_hierarchy`
already gives the right member list to group `LX`-leader service lines
from directly.

Disclosed Phase-5 scope limits, decided up front (837 files are commonly
batched with many billing providers, each with many subscribers, each with
many claims - the same "only the first ST/SE transaction set" batching
limit every EDI phase in this app already discloses, extended one level
further here): only the **first** 2300 claim within the transaction set is
mapped; 2010AB Pay-to Provider, 2310C Service Facility Location, and the
2320/2330 Other Subscriber Information (COB - coordination of benefits)
loops are captured by neither `find_segment` nor any loop walk here and are
simply never read; `SV1-05` (Place of Service override, situational -
absent in the real example above) is not read, so every service line's
`Claim.item[].locationCodeableConcept` is instead a single claim-wide
default from `CLM05-1` (the dominant real-world case, since a POS override
per line is comparatively rare); `CLM03`/`CLM04`/`CLM06`-`CLM11` beyond
`CLM05` have no clean target in base FHIR `Claim` and are left unmapped,
the same "no fitting field, disclosed and skipped" treatment 278's `UM01`
already gets.

`CLM05-1` (Facility Code Value, i.e. Place of Service) maps to the real,
verified CMS canonical CodeSystem (`_POS_CODE_SYSTEM` - confirmed by direct
fetch, not guessed at, unlike most of this app's disclosed local-system
fallbacks). `SV1-01` (Composite Medical Procedure Identifier)'s qualifier
"HC" is a **genuinely unresolvable ambiguity, confirmed directly rather
than assumed**: X12's own "HC" qualifier covers both CPT (AMA-owned) and
HCPCS Level II (CMS-owned) codes with no way to tell which from the X12
element alone (a real, open FHIR/X12 interop gap - even the Da Vinci PAS
IG's own JIRA tracker has an open issue about this exact ambiguity for the
sibling 278 transaction) - `_PROCEDURE_QUALIFIER_SYSTEM` therefore keeps
"HC" on a disclosed local placeholder rather than forcing it into either
canonical CPT or HCPCS system and being wrong roughly half the time.

`SV1-07` (Composite Diagnosis Code Pointer, up to 4 1-based positions
referencing `HI`'s own diagnosis order, e.g. `"1:2:3:4"`) resolves against
`Claim.diagnosis[]`'s own sequence numbers, which this module assigns in
the same left-to-right `HI` composite order `common.py::
build_diagnosis_codeable_concepts` already returns them in - a pointer
whose value doesn't resolve to any diagnosis actually built (out of range,
non-numeric) is skipped rather than raising, since a single malformed
pointer shouldn't block the rest of a service line.

`NM1*82` (2310B Rendering Provider, optional) materializes as a real
`Practitioner`/`Organization` (branching on `is_person_entity` the same way
every other provider-shaped NM1 loop in this app does) and is referenced
via `Claim.careTeam[]` with `role="primary"` (confirmed against
`hl7.org/fhir/R4/valueset-claim-careteamrole.html`'s own 4-code value set -
`primary` is the closest fit for "the provider who actually rendered this
service", distinct from `Claim.provider`, which FHIR defines as "the
provider... responsible for the claim" - the billing provider, not
necessarily whoever rendered it) - every `Claim.item[]` this module builds
carries `careTeamSequence=[1]` back to it when present."""

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
    build_practitioner_from_nm1,
    is_person_entity,
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

# Local, 837P-specific HL03 level codes - deliberately NOT imported from
# common.py's HL_INFORMATION_SOURCE/HL_SUBSCRIBER/HL_DEPENDENT even though
# two of the three literal string values coincide ("22"/"23"), since the
# *semantic role* of "20" genuinely differs here (Billing Provider, not
# Information Source) - reusing those names in an 837P context would read
# as though this file shared 270/271/278's loop semantics, which it does
# not (see module docstring's own "don't assume a numeric HL03 code carries
# the same meaning" note).
_HL_BILLING_PROVIDER = "20"
_HL_SUBSCRIBER = "22"
_HL_PATIENT = "23"

# NM1 entity identifier codes (element 98) this module reads, each scoped
# to a specific loop - multiple NM1 segments with different entity codes
# can appear within one flat HL member list (e.g. the subscriber loop
# carries both NM1*IL and NM1*PR), so every lookup here filters by entity
# code via _find_nm1 rather than taking "the first NM1" blindly.
_NM1_BILLING_PROVIDER = "85"
_NM1_SUBSCRIBER = "IL"
_NM1_PAYER = "PR"
_NM1_PATIENT = "QC"
_NM1_RENDERING_PROVIDER = "82"

# CMS's own Place of Service code set - a real, verified FHIR-canonical
# CodeSystem (confirmed by direct fetch), unlike most of this app's
# disclosed local-system fallbacks for X12 code lists with no official FHIR
# home. CLM05-1 (Facility Code Value) is coded against this list.
_POS_CODE_SYSTEM = "https://www.cms.gov/Medicare/Coding/place-of-service-codes/Place_of_Service_Code_Set"

# SV1-01's procedure-code qualifier (X12 code list 235) - "HC" is a
# genuinely unresolvable CPT-vs-HCPCS-Level-II ambiguity at the X12 level
# (see module docstring), so it stays on a disclosed local placeholder
# rather than being forced into either real canonical system.
_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM = "urn:interop-tools:x12-procedure-qualifier"

_CLAIM_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claim-type"
DEFAULT_CLAIM_TYPE = "professional"
# 837P has no source field for Claim.priority at all (same gap 278's own
# UM segment has for Claim.priority) - defaults to "normal", the same
# "default to the most common real value when no source field exists"
# precedent as 270's DEFAULT_PURPOSE / 278's own DEFAULT_PRIORITY.
DEFAULT_PRIORITY = "normal"
_CARE_TEAM_ROLE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claimcareteamrole"
_RENDERING_PROVIDER_ROLE = "primary"

_MAX_DIAGNOSIS_POINTERS = 4  # SV1-07's own composite cap, per the 5010 IG


@dataclass
class Resolved837pLoops:
    """Everything Edi837pBuilder.build_bundle() needs from the 2000A-2000C
    loop walk.

    `claim_loop` and `patient_is_dependent` are deliberately gated
    *independently*, unlike 270/271/278's own dependent-loop resolvers:
    `claim_loop` is resolved purely structurally (the 2000C loop whenever
    HL03="23" was emitted at all, regardless of whether its own NM1
    resolves) since CLM/HI/NM1*82/2400 service lines are physically nested
    wherever that HL loop's members actually are - X12 doesn't re-attribute
    them to 2000B just because 2000C's NM1 happens to be malformed, so
    this resolver can't either. `patient_is_dependent` (and
    `patient_nm1`/`patient_dmg`) instead gate *which Patient resource* is
    "the patient" for `Claim.patient`/`Coverage.beneficiary` - only true
    when the 2000C loop's own NM1*QC actually resolves, the same
    "resource construction and segment lookup are gated by different
    conditions" split every dependent/patient loop in this app needs to
    get right, just newly explicit here since 837P's claim data itself
    depends on loop *presence*, not loop *validity*, unlike every earlier
    EDI family in this app."""

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


# DTP01 (Date/Time Qualifier) - "472" is Service Date, the only DTP this
# module reads. A 2400 loop's member list can carry other DTP-qualified
# segments too (e.g. "463" Prescription Date on a DME line) - filtering by
# DTP01 rather than taking "the first DTP" avoids silently attributing a
# differently-qualified date to servicedDate, the same "check the specific
# qualifier, don't just grab the first segment of this type" discipline
# STC01/TXA dedup already established elsewhere in this app. Public - the
# validation.py plausibility rule for service-line dates must filter the
# identical way, or it could report on (or silently ignore) a DTP the real
# builder would never even read.
DTP_SERVICE_DATE = "472"


def find_dtp_by_qualifier(segments: list[Segment], qualifier: str) -> Segment | None:
    return next((seg for seg in segments if seg[0] == "DTP" and element(seg, 1) == qualifier), None)


def resolve_837p_loops(segments: list[Segment], transaction_set_id: str) -> Resolved837pLoops:
    """Walk the strict 2000A(20)->2000B(22)->[2000C(23)] parent chain an
    837P transaction set requires. Mirrors every other EDI family's own
    "raise here, not in the caller" discipline for each required loop/NM1 -
    see module docstring for why this chain is only three levels deep
    (unlike 270/271/278's four) and why the payer NM1 is read from the
    *subscriber* loop's own members rather than a separate root loop."""
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

    # claim_loop is resolved structurally (loop presence), independent of
    # whether that loop's own NM1 resolves - see Resolved837pLoops' own
    # docstring for why this must NOT reuse the "no NM1 -> treat loop as
    # absent" gate every other EDI family's dependent-loop resolver uses.
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

    return Resolved837pLoops(
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


def _build_place_of_service(clm: Segment, delimiters: Delimiters) -> CodeableConcept | None:
    facility_code = component(element(clm, 5), delimiters, 1)
    if not facility_code:
        return None
    return CodeableConcept(coding=[Coding(system=_POS_CODE_SYSTEM, code=facility_code)])


def _build_procedure_concept(sv1_01: str, delimiters: Delimiters) -> CodeableConcept | None:
    qualifier = component(sv1_01, delimiters, 1).strip().upper()
    code = component(sv1_01, delimiters, 2)
    if not code:
        return None
    system = f"{_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else _PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
    return CodeableConcept(coding=[Coding(system=system, code=code)])


def _build_diagnosis_pointers(sv1: Segment, delimiters: Delimiters, num_diagnoses: int) -> list[int]:
    composite = element(sv1, 7)
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


def _build_service_line_item(
    sequence: int,
    sv1: Segment,
    dtp: Segment | None,
    delimiters: Delimiters,
    location: CodeableConcept | None,
    num_diagnoses: int,
    care_team_sequence: int | None,
) -> ClaimItem:
    procedure = _build_procedure_concept(element(sv1, 1), delimiters) or CodeableConcept(text="Unspecified procedure")
    item = ClaimItem(sequence=sequence, productOrService=procedure)

    charge = parse_decimal(element(sv1, 2))
    if charge is not None:
        item.unitPrice = Money(value=charge, currency="USD")

    quantity_value = parse_decimal(element(sv1, 4))
    if quantity_value is not None:
        item.quantity = Quantity(value=quantity_value)

    if dtp is not None and element(dtp, 2) == "D8":
        serviced = parse_hl7_date(element(dtp, 3))
        if serviced:
            item.servicedDate = serviced

    if location is not None:
        item.locationCodeableConcept = location

    pointers = _build_diagnosis_pointers(sv1, delimiters, num_diagnoses)
    if pointers:
        item.diagnosisSequence = pointers

    if care_team_sequence is not None:
        item.careTeamSequence = [care_team_sequence]

    return item


class Edi837pBuilder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_837p_loops(transaction_set.segments, self.transaction_set_id)

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

        hi = find_segment(loops.claim_loop.member_segments, "HI")
        diagnosis_concepts = build_diagnosis_codeable_concepts(hi, delimiters)

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

        location = _build_place_of_service(clm, delimiters)

        line_groups = group_by_leader(loops.claim_loop.member_segments, "LX", ["SV1", "DTP", "PWK", "CRC"])
        items = []
        for _lx, members in line_groups:
            sv1 = find_segment(members, "SV1")
            if sv1 is None:
                continue
            dtp = find_dtp_by_qualifier(members, DTP_SERVICE_DATE)
            items.append(
                _build_service_line_item(
                    len(items) + 1,
                    sv1,
                    dtp,
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
