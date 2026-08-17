"""X12 270 (Health Care Eligibility Benefit Inquiry) -> FHIR
CoverageEligibilityRequest.

No official, free X12-to-FHIR ConceptMap IG exists for this transaction
(unlike HL7v2's v2-to-FHIR or C-CDA's C-CDA on FHIR - see CLAUDE.md's EDI
section for the full disclosed verification-source gap). Field mapping
here is checked against the base FHIR CoverageEligibilityRequest resource
definition (official, free) plus a hand-verified real 270 segment/loop
shape (HL03 level codes, NM1/DMG/EQ/DTP positions), not a ballot-published
crosswalk.

Loop shape (HL-hierarchy, HL03 level codes):
  2000A (HL03="20", Information Source)   NM1*PR    -> payer Organization
  2000B (HL03="21", Information Receiver) NM1*1P/41 -> provider Org/Practitioner
  2000C (HL03="22", Subscriber)           NM1*IL, DMG -> subscriber Patient
  2000D (HL03="23", Dependent, optional)  NM1*QC, DMG -> dependent Patient

.patient = the 2000D dependent when present, else the 2000C subscriber
themselves - the concrete "which segment wins" rule, documented explicitly
the same way ADT/SIU's own field-precedence rules are rather than left
implicit."""

import uuid

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.coverageeligibilityrequest import (
    CoverageEligibilityRequest,
    CoverageEligibilityRequestInsurance,
    CoverageEligibilityRequestItem,
)
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.base import EdiTransactionBuilder
from app.edi.common import (
    DEFAULT_PURPOSE,
    assemble_bundle,
    build_coverage,
    build_organization_from_nm1,
    build_patient_from_nm1_dmg,
    build_practitioner_from_nm1,
    build_service_type_category,
    is_person_entity,
    parse_x12_datetime,
    resolve_eligibility_parties,
)
from app.edi.parser import Delimiters, TransactionSet, element, find_segment, group_by_leader
from app.fhir_models.builders import parse_hl7_date
from app.hl7.errors import MappingError, MissingSegmentError
from app.provenance.location import edi_location

TRANSACTION_SET_ID = "270"


def _build_serviced_date(dtp_segments: list, resource_id: str | None = None, recorder=None) -> str | None:
    """DTP02 qualifier "D8" (a simple CCYYMMDD date) is handled; "RD8" (a
    CCYYMMDD-CCYYMMDD range) is deferred this slice, disclosed rather than
    guessed at - see the module's Phase-1 scope notes in CLAUDE.md."""
    for dtp in dtp_segments:
        if element(dtp, 2) == "D8":
            raw = element(dtp, 3)
            date_value = parse_hl7_date(raw)
            if date_value:
                if recorder and resource_id:
                    recorder.record(resource_id, "servicedDate", edi_location("DTP", 3), date_value, source_value=raw)
                return date_value
    return None


def _build_items_and_serviced_date(
    patient_loop_members: list, resource_id: str | None = None, recorder=None
) -> tuple[list[CoverageEligibilityRequestItem], str | None]:
    """CoverageEligibilityRequest.servicedDate is a single top-level field
    (not per-item, confirmed by inspecting model_fields directly rather
    than assumed) - takes the first resolvable DTP date found across every
    EQ group, a disclosed simplification for the common case where a 270
    carries one uniform service date across all its inquiries."""
    eq_groups = group_by_leader(patient_loop_members, "EQ", ["REF", "DTP", "MSG"])
    items: list[CoverageEligibilityRequestItem] = []
    serviced_date: str | None = None
    for eq, members in eq_groups:
        category = build_service_type_category(
            element(eq, 1),
            resource_id=resource_id,
            relative_path=f"item[{len(items)}]",
            source_location=edi_location("EQ", 1),
            recorder=recorder,
        )
        if category is not None:
            items.append(CoverageEligibilityRequestItem(category=category))
        if serviced_date is None:
            dtp_segments = [m for m in members if m[0] == "DTP"]
            serviced_date = _build_serviced_date(dtp_segments, resource_id=resource_id, recorder=recorder)
    return items, serviced_date


class Edi270Builder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        # delimiters unused - no field this builder reads is a composite
        # element. Accepted only to satisfy EdiTransactionBuilder's shared
        # interface (see its own docstring for why the parameter exists).
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError("270 transaction set is missing its BHT segment")

        parties = resolve_eligibility_parties(transaction_set.segments, "270")

        payer = build_organization_from_nm1(parties.payer_nm1, recorder=recorder)
        provider: Resource = (
            build_practitioner_from_nm1(parties.provider_nm1, recorder=recorder)
            if is_person_entity(parties.provider_nm1)
            else build_organization_from_nm1(parties.provider_nm1, recorder=recorder)
        )
        subscriber = build_patient_from_nm1_dmg(parties.subscriber_nm1, parties.subscriber_dmg, recorder=recorder)

        # .patient = the 2000D dependent when present (and its own NM1
        # resolves), else the subscriber themselves - see module docstring.
        patient = (
            build_patient_from_nm1_dmg(parties.patient_nm1, parties.patient_dmg, recorder=recorder)
            if parties.patient_is_dependent
            else subscriber
        )

        coverage = build_coverage(patient, payer, subscriber, recorder=recorder)

        request_id = str(uuid.uuid4())
        items, serviced_date = _build_items_and_serviced_date(
            parties.patient_loop_members, resource_id=request_id, recorder=recorder
        )

        # created is FHIR-required (confirmed by construction) with BHT04
        # (+ optional BHT05) as its only real source - a business-rule
        # requirement this converter enforces directly, the same way
        # AdtA03Mapper requires a resolvable discharge time rather than
        # guessing one.
        bht04_raw = element(bht, 4)
        bht05_raw = element(bht, 5)
        created = parse_x12_datetime(bht04_raw, bht05_raw)
        if created is None:
            raise MappingError("270 transaction set's BHT segment has no resolvable creation date (BHT04)")

        request = CoverageEligibilityRequest(
            id=request_id,
            status="active",
            purpose=[DEFAULT_PURPOSE],
            created=created,
            patient=Reference(reference=f"urn:uuid:{patient.id}"),
            insurer=Reference(reference=f"urn:uuid:{payer.id}"),
        )
        if recorder:
            recorder.record_inferred(
                request_id,
                "status",
                'Every CoverageEligibilityRequest this app builds is a synthesized, always-active resource - no X12 field carries a request-level status separate from whether it was successfully created.',
                "active",
            )
            recorder.record_inferred(
                request_id,
                "purpose[0]",
                '270 carries no data element for "why are we asking" - CoverageEligibilityRequest.purpose is FHIR-required with no source field to derive it from, so this always defaults to the dominant real-world use.',
                DEFAULT_PURPOSE,
            )
            recorder.record(
                request_id,
                "created",
                f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}",
                created,
                source_value=bht04_raw + bht05_raw,
            )
        request.provider = Reference(reference=f"urn:uuid:{provider.id}")
        request.insurance = [
            CoverageEligibilityRequestInsurance(coverage=Reference(reference=f"urn:uuid:{coverage.id}"))
        ]
        if items:
            request.item = items
        if serviced_date:
            request.servicedDate = serviced_date

        resources: list[Resource] = [payer, provider, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.extend([coverage, request])

        return assemble_bundle(bht, *resources, recorder=recorder)
