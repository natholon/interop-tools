"""X12 837P (Health Care Claim: Professional, 005010X222A2) -> FHIR
`Claim` (`use="claim"`, `type="professional"`).

Verified against a real X12.org example
(`x12.org/examples/005010x222/example-3a-claim-billing-provider-payer`).
Worth quoting the raw example rather than a summarised reference: a
secondary source had 2010BB (payer) using `NM1*41`, which is actually the
1000A Submitter loop this app never materialises - the payer is `NM1*PR`.

Loop shape - **a three-level HL chain, not 270/271/278's four**, and the
HL03 codes mean different things here despite numerically coinciding
(HL03 meaning is per-TR3, never assume it carries across):

    2000A  HL03="20"  Billing Provider (NOT "Information Source")
           NM1*85 -> billing provider Organization/Practitioner
    2000B  HL03="22"  Subscriber
           NM1*IL + DMG -> subscriber Patient
           NM1*PR (payer) is a member of THIS loop, not its own HL level -
           a real structural difference from every other EDI family here
    2000C  HL03="23"  Patient (optional, only when not the subscriber)
           NM1*QC + DMG -> patient Patient

**The 2300 Claim Information loop is not its own HL level**: X12's HL
segments stop at the patient, so CLM/HI/NM1*82/LX/SV1/DTP are flat members
of whichever loop is the patient (2000C when present, else 2000B), grouped
by `group_by_leader` on the `LX` leader.

Field notes:
- `CLM05-1` (Place of Service) -> the real CMS CodeSystem
  (`common.py::POS_CODE_SYSTEM`), one claim-wide default applied to every
  `Claim.item[].locationCodeableConcept`.
- `SV1-01`'s "HC" qualifier is **genuinely ambiguous** - it covers both
  CPT and HCPCS Level II with nothing in the element to say which, an open
  FHIR/X12 gap. It stays on a disclosed local placeholder rather than
  being forced into one canonical system and being wrong half the time.
- `SV1-07` (diagnosis pointers, 1-based into `HI`'s order) resolves against
  `Claim.diagnosis[]` sequence numbers. A pointer that resolves to nothing
  is skipped, not raised - one malformed pointer should not block the line.
- `NM1*82` (Rendering Provider) -> `Claim.careTeam[]` with `role="primary"`,
  distinct from `Claim.provider`, which FHIR defines as the provider
  responsible for the claim (the biller, not necessarily the renderer).
  Every `Claim.item[]` carries `careTeamSequence=[1]` back to it.

Scope limits:
- Only the **first** 2300 claim in the transaction set is mapped (837 files
  are commonly batched), matching the first-transaction-set limit every EDI
  family here already discloses.
- Never read: 2010AB Pay-to Provider, 2310C Service Facility Location, the
  2320/2330 COB loops, and `SV1-05`'s per-line place-of-service override.
- `CLM03`/`CLM04`/`CLM06`-`CLM11` have no clean target in base FHIR `Claim`."""

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
    find_nm1_by_entity_code,
    is_person_entity,
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

# SV1-01's procedure-code qualifier (X12 code list 235) - "HC" is a
# genuinely unresolvable CPT-vs-HCPCS-Level-II ambiguity at the X12 level
# (see module docstring), so it stays on a disclosed local placeholder
# rather than being forced into either real canonical system. Public (not
# module-private) - app/transform/claim_837p.py became a real reverse-
# direction consumer, reversing this same fallback marker.
PROCEDURE_QUALIFIER_FALLBACK_SYSTEM = "urn:interop-tools:x12-procedure-qualifier"

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

# Resolved837pLoops/resolve_837p_loops/_find_nm1/find_dtp_by_qualifier/
# DTP_SERVICE_DATE were promoted to app/edi/common.py as
# Resolved837Loops/resolve_837_loops/find_nm1_by_entity_code/
# find_dtp_by_qualifier/DTP_SERVICE_DATE once claim_837d.py became a third
# independently-tested implementation of the identical algorithm - see
# common.py's own Resolved837Loops docstring for the full reasoning
# (unchanged from what lived here before the promotion).


def _build_procedure_concept(
    sv1_01: str, delimiters: Delimiters, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> CodeableConcept | None:
    qualifier = component(sv1_01, delimiters, 1).strip().upper()
    code = component(sv1_01, delimiters, 2)
    if not code:
        return None
    system = f"{PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
    if recorder and resource_id and relative_path:
        recorder.record(resource_id, f"{relative_path}.coding[0].code", edi_location("SV1", 1, component=2), code)
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
    resource_id: str | None = None,
    recorder=None,
) -> ClaimItem:
    item_path = f"item[{sequence - 1}]"
    procedure = _build_procedure_concept(
        element(sv1, 1), delimiters, resource_id=resource_id, relative_path=f"{item_path}.productOrService", recorder=recorder
    )
    if procedure is None:
        procedure = CodeableConcept(text="Unspecified procedure")
        if recorder and resource_id:
            recorder.record_inferred(
                resource_id,
                f"{item_path}.productOrService.text",
                "This service line's own SV1-01 (Composite Medical Procedure Identifier) has no resolvable code - Claim.item[].productOrService defaults to a generic placeholder text.",
                "Unspecified procedure",
            )
    item = ClaimItem(sequence=sequence, productOrService=procedure)

    charge_raw = element(sv1, 2)
    charge = parse_decimal(charge_raw)
    if charge is not None:
        item.unitPrice = Money(value=charge, currency="USD")
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.unitPrice.value", edi_location("SV1", 2), charge_raw)

    quantity_raw = element(sv1, 4)
    quantity_value = parse_decimal(quantity_raw)
    if quantity_value is not None:
        item.quantity = Quantity(value=quantity_value)
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.quantity.value", edi_location("SV1", 4), quantity_raw)

    if dtp is not None and element(dtp, 2) == "D8":
        dtp3_raw = element(dtp, 3)
        serviced = parse_hl7_date(dtp3_raw)
        if serviced:
            item.servicedDate = serviced
            if recorder and resource_id:
                recorder.record(
                    resource_id, f"{item_path}.servicedDate", edi_location("DTP", 3), serviced, source_value=dtp3_raw
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

    pointers = _build_diagnosis_pointers(sv1, delimiters, num_diagnoses)
    if pointers:
        item.diagnosisSequence = pointers
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"{item_path}.diagnosisSequence",
                edi_location("SV1", 7),
                ",".join(str(p) for p in pointers),
            )

    if care_team_sequence is not None:
        item.careTeamSequence = [care_team_sequence]

    return item


class Edi837pBuilder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_837_loops(transaction_set.segments, self.transaction_set_id)

        billing_provider: Resource = (
            build_practitioner_from_nm1(loops.billing_provider_nm1, recorder=recorder)
            if is_person_entity(loops.billing_provider_nm1)
            else build_organization_from_nm1(loops.billing_provider_nm1, recorder=recorder)
        )
        payer = build_organization_from_nm1(loops.payer_nm1, recorder=recorder)
        subscriber = build_patient_from_nm1_dmg(loops.subscriber_nm1, loops.subscriber_dmg, recorder=recorder)
        patient = (
            build_patient_from_nm1_dmg(loops.patient_nm1, loops.patient_dmg, recorder=recorder)
            if loops.patient_is_dependent
            else subscriber
        )

        coverage = build_coverage(patient, payer, subscriber, recorder=recorder)

        clm = find_segment(loops.claim_loop.member_segments, "CLM")
        if clm is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its 2300 CLM (claim) segment")

        total_raw = element(clm, 2)
        total = parse_decimal(total_raw)
        if total is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's CLM segment has no resolvable total charge (CLM02)")

        claim_id = str(uuid.uuid4())

        hi = find_segment(loops.claim_loop.member_segments, "HI")
        diagnosis_concepts = build_diagnosis_codeable_concepts(
            hi, delimiters, resource_id=claim_id, relative_path_prefix="diagnosis", recorder=recorder
        )

        rendering_nm1 = find_nm1_by_entity_code(loops.claim_loop.member_segments, _NM1_RENDERING_PROVIDER)
        rendering_provider: Resource | None = None
        if rendering_nm1 is not None:
            rendering_provider = (
                build_practitioner_from_nm1(rendering_nm1, recorder=recorder)
                if is_person_entity(rendering_nm1)
                else build_organization_from_nm1(rendering_nm1, recorder=recorder)
            )

        bht04_raw = element(bht, 4)
        bht05_raw = element(bht, 5)
        created = parse_x12_datetime(bht04_raw, bht05_raw)
        if created is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's BHT segment has no resolvable date (BHT04)")

        location = build_place_of_service_from_clm05(clm, delimiters)

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
                    'Every rendering-provider care team entry this app builds from an 837P claim has role="primary" - the closest fit in FHIR\'s own 4-code claimcareteamrole value set, not read from any X12 field.',
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
                "Every Claim this app builds from an 837P transaction set has status=\"active\" - not read from any X12 field.",
                "active",
            )
            recorder.record_inferred(
                claim_id,
                "type.coding[0].code",
                "Every Claim this app builds from an 837P transaction set has type=\"professional\" - that's what an 837P transaction fundamentally represents, not read from any field.",
                DEFAULT_CLAIM_TYPE,
            )
            recorder.record_inferred(
                claim_id,
                "use",
                'Every Claim this app builds from an 837P transaction set has use="claim" - not read from any X12 field.',
                "claim",
            )
            recorder.record_inferred(
                claim_id,
                "priority.text",
                "837P carries no data element for a claim's own priority - Claim.priority defaults to \"normal\", the same \"default to the most common real value\" precedent as 270's DEFAULT_PURPOSE/278's DEFAULT_PRIORITY.",
                DEFAULT_PRIORITY,
            )
            recorder.record(
                claim_id, "created", f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}", created, source_value=bht04_raw + bht05_raw
            )
            recorder.record(claim_id, "total.value", edi_location("CLM", 2), total_raw)

        return assemble_bundle(bht, *resources, recorder=recorder)
