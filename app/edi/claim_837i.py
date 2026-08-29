"""X12 837I (Health Care Claim: Institutional, 005010X223A2) -> FHIR
`Claim` (`use="claim"`, `type="institutional"`).

**Verification gap, shared by every EDI family here**: X12's TR3 is
paywalled and no free X12-to-FHIR crosswalk exists. The segment shape is
verified against a real X12.org example
(`005010x223/example-1a-institutional-claim`) plus free CMS/Palmetto/CGS
companion guides. The FHIR side is firmer - NUBC Revenue Codes, NUBC
Patient Discharge Status, `ClaimInformationCategoryCodes` and
`claimcareteamrole` were each confirmed by direct fetch.

Shares 837P's 3-level HL chain (2000A Billing Provider / 2000B Subscriber,
with the payer `NM1*PR` nested in that same loop / optional 2000C
Patient), confirmed against the real example rather than assumed from the
family name. `resolve_837_loops()` lives in `common.py` now that 837D is a
third independently-tested consumer.

**Where 837I genuinely differs from 837P:**
  - `CLM05-1` is a UB-04 Type of Bill, not a Place of Service - a
    different vocabulary describing what *kind* of stay this was, not
    where service happened. No free FHIR-canonical CodeSystem was found
    for it, so it is unmapped rather than forced into
    `Claim.item.locationCodeableConcept`.
  - `CL1` has no 837P equivalent. Only CL103 (Patient Status) is mapped,
    to `Claim.supportingInfo` with category `discharge` and the NUBC
    Patient Discharge Status CodeSystem. CL101/CL102 have no fitting
    category and are unmapped.
  - `HI` carries occurrence/value/condition codes (`BH`/`BE`/`BG`)
    alongside diagnoses, and institutional claims split them across
    several `HI` segments. Only diagnosis-qualified composites are read
    (see `iter_diagnosis_hi_segments`); the rest are skipped so they
    cannot pollute `Claim.diagnosis[]`, and are unmapped.
  - `SV2` replaces `SV1`: `SV2-01` (Revenue Code, NUBC) is required and
    primary, `SV2-02`'s procedure composite is optional, and there is no
    diagnosis-pointer composite at all - institutional diagnoses apply at
    claim level via `HI`.
  - Attending Provider (`NM1*71`) replaces 837P's Rendering Provider
    (`NM1*82`) as the one 2310-level role mapped, to `Claim.careTeam[]`
    with role `primary` (no `attending` code exists in the 4-code value
    set).

**Scope limits**: only the first 2300 claim per transaction set; 2010AB,
2310B/C, 2310E and the 2320/2330 COB loops are never read; `SV206`/
`SV207` (non-covered charges) are not read."""

import uuid

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
    DTP_SERVICE_DATE,
    assemble_bundle,
    build_coverage,
    build_diagnosis_codeable_concepts,
    build_organization_from_nm1,
    build_patient_from_nm1_dmg,
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


# Resolved837iLoops/resolve_837i_loops/_find_nm1/find_dtp_by_qualifier/
# DTP_SERVICE_DATE were promoted to app/edi/common.py as
# Resolved837Loops/resolve_837_loops/find_nm1_by_entity_code/
# find_dtp_by_qualifier/DTP_SERVICE_DATE once claim_837d.py became a third
# independently-tested implementation of the identical algorithm - see
# common.py's own Resolved837Loops docstring for the full reasoning
# (unchanged from what lived here before the promotion).


def _build_procedure_concept(
    sv2_02: str, delimiters: Delimiters, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> CodeableConcept | None:
    if not sv2_02:
        return None
    qualifier = component(sv2_02, delimiters, 1).strip().upper()
    code = component(sv2_02, delimiters, 2)
    if not code:
        return None
    system = f"{_PROCEDURE_QUALIFIER_FALLBACK_SYSTEM}:{qualifier}" if qualifier else _PROCEDURE_QUALIFIER_FALLBACK_SYSTEM
    if recorder and resource_id and relative_path:
        recorder.record(resource_id, f"{relative_path}.coding[0].code", edi_location("SV2", 2, component=2), code)
    return CodeableConcept(coding=[Coding(system=system, code=code)])


def _build_service_line_item(
    sequence: int,
    sv2: Segment,
    dtp: Segment | None,
    delimiters: Delimiters,
    care_team_sequence: int | None,
    resource_id: str | None = None,
    recorder=None,
) -> ClaimItem:
    item_path = f"item[{sequence - 1}]"
    revenue_code = element(sv2, 1)
    procedure = _build_procedure_concept(
        element(sv2, 2), delimiters, resource_id=resource_id, relative_path=f"{item_path}.productOrService", recorder=recorder
    )
    if procedure is not None:
        product_or_service = procedure
    elif revenue_code:
        product_or_service = CodeableConcept(text=f"Revenue code {revenue_code}")
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.productOrService.text", edi_location("SV2", 1), product_or_service.text)
    else:
        product_or_service = CodeableConcept(text="Unspecified service")
        if recorder and resource_id:
            recorder.record_inferred(
                resource_id,
                f"{item_path}.productOrService.text",
                "This service line's own SV2-02 (procedure composite) and SV2-01 (revenue code) are both absent - Claim.item[].productOrService defaults to a generic placeholder text.",
                "Unspecified service",
            )
    item = ClaimItem(sequence=sequence, productOrService=product_or_service)
    if revenue_code:
        item.revenue = CodeableConcept(coding=[Coding(system=_REVENUE_CODE_SYSTEM, code=revenue_code)])
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.revenue.coding[0].code", edi_location("SV2", 1), revenue_code)
    if care_team_sequence is not None:
        item.careTeamSequence = [care_team_sequence]

    charge_raw = element(sv2, 3)
    charge = parse_decimal(charge_raw)
    if charge is not None:
        item.unitPrice = Money(value=charge, currency="USD")
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.unitPrice.value", edi_location("SV2", 3), charge_raw)

    quantity_raw = element(sv2, 5)
    quantity_value = parse_decimal(quantity_raw)
    if quantity_value is not None:
        item.quantity = Quantity(value=quantity_value)
        if recorder and resource_id:
            recorder.record(resource_id, f"{item_path}.quantity.value", edi_location("SV2", 5), quantity_raw)

    if dtp is not None and element(dtp, 2) == "D8":
        dtp3_raw = element(dtp, 3)
        serviced = parse_hl7_date(dtp3_raw)
        if serviced:
            item.servicedDate = serviced
            if recorder and resource_id:
                recorder.record(
                    resource_id, f"{item_path}.servicedDate", edi_location("DTP", 3), serviced, source_value=dtp3_raw
                )

    return item


def _build_discharge_status_supporting_info(
    cl1: Segment | None, sequence: int, resource_id: str | None = None, recorder=None
) -> ClaimSupportingInfo | None:
    if cl1 is None:
        return None
    status_code = element(cl1, 3)
    if not status_code:
        return None
    supporting_info_path = f"supportingInfo[{sequence - 1}]"
    if recorder and resource_id:
        recorder.record_inferred(
            resource_id,
            f"{supporting_info_path}.category.coding[0].code",
            'Every discharge-status ClaimSupportingInfo entry this app builds from CL103 uses category="discharge" - the closest fit in FHIR\'s own ClaimInformationCategoryCodes value set, not read from any X12 field.',
            _DISCHARGE_STATUS_CATEGORY,
        )
        recorder.record(resource_id, f"{supporting_info_path}.code.coding[0].code", edi_location("CL1", 3), status_code)
    return ClaimSupportingInfo(
        sequence=sequence,
        category=CodeableConcept(coding=[Coding(system=_SUPPORTING_INFO_CATEGORY_SYSTEM, code=_DISCHARGE_STATUS_CATEGORY)]),
        code=CodeableConcept(coding=[Coding(system=_DISCHARGE_STATUS_CODE_SYSTEM, code=status_code)]),
    )


class Edi837iBuilder(EdiTransactionBuilder):
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

        coverage = build_coverage(patient, payer, subscriber, recorder=recorder)

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

        cl1 = find_segment(loops.claim_loop.member_segments, "CL1")
        supporting_info = _build_discharge_status_supporting_info(cl1, sequence=1, resource_id=claim_id, recorder=recorder)

        attending_nm1, attending_nm1_members = find_nm1_group(loops.claim_loop.member_segments, _NM1_ATTENDING_PROVIDER)
        attending_provider: Resource | None = None
        if attending_nm1 is not None:
            attending_provider = (
                build_practitioner_from_nm1(attending_nm1, recorder=recorder, members=attending_nm1_members)
                if is_person_entity(attending_nm1)
                else build_organization_from_nm1(attending_nm1, recorder=recorder, members=attending_nm1_members)
            )

        bht04_raw = element(bht, 4)
        bht05_raw = element(bht, 5)
        created = parse_x12_datetime(bht04_raw, bht05_raw)
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
                    len(items) + 1,
                    sv2,
                    dtp,
                    delimiters,
                    1 if attending_provider is not None else None,
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
            if recorder:
                recorder.record_inferred(
                    claim_id,
                    "careTeam[0].role.coding[0].code",
                    'Every attending-provider care team entry this app builds from an 837I claim has role="primary" - the closest fit in FHIR\'s own 4-code claimcareteamrole value set (there is no "attending" code), not read from any X12 field.',
                    _ATTENDING_PROVIDER_ROLE,
                )

        if items:
            claim.item = items

        resources: list[Resource] = [billing_provider, payer, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.append(coverage)
        if attending_provider is not None:
            resources.append(attending_provider)
        resources.append(claim)

        if recorder:
            recorder.record_inferred(
                claim_id,
                "status",
                "Every Claim this app builds from an 837I transaction set has status=\"active\" - not read from any X12 field.",
                "active",
            )
            recorder.record_inferred(
                claim_id,
                "type.coding[0].code",
                "Every Claim this app builds from an 837I transaction set has type=\"institutional\" - that's what an 837I transaction fundamentally represents, not read from any field.",
                DEFAULT_CLAIM_TYPE,
            )
            recorder.record_inferred(
                claim_id,
                "use",
                'Every Claim this app builds from an 837I transaction set has use="claim" - not read from any X12 field.',
                "claim",
            )
            recorder.record_inferred(
                claim_id,
                "priority.text",
                "837I carries no data element for a claim's own priority - Claim.priority defaults to \"normal\", the same \"default to the most common real value\" precedent as 837P's own DEFAULT_PRIORITY.",
                DEFAULT_PRIORITY,
            )
            recorder.record(
                claim_id, "created", f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}", created, source_value=bht04_raw + bht05_raw
            )
            recorder.record(claim_id, "total.value", edi_location("CLM", 2), total_raw)

        return assemble_bundle(bht, *resources, recorder=recorder)
