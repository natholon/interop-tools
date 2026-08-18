"""X12 271 (Health Care Eligibility Benefit Response) -> FHIR
CoverageEligibilityResponse.

Same disclosed verification-source gap as eligibility_270.py (no official
X12-to-FHIR ConceptMap IG exists) - checked against the base FHIR
CoverageEligibilityResponse resource definition and a hand-verified real
271 segment/loop shape. Envelope/HL/NM1/DMG walk is identical to 270's
(same loop shape, same HL03 level codes) - BHT02="11" distinguishes a 271
from 270's BHT02="13".

Loop shape mirrors 270 exactly through 2000A-2000D; where it diverges:
  2110C/2110D leader is `EB` (Eligibility/Benefit Information), not `EQ`
    -> insurance[].item[] (category from EB03, excluded from a disclosed
    EB01 subset, description from EB05, network from a disclosed EB12
    text mapping). insurance[].inforce = whether any EB01="1" (Active
    Coverage) was seen across all EB segments for this patient.
  `AAA` (Request Validation) segments, scanned across the whole
    transaction set rather than loop-scoped (a disclosed Phase-1
    simplification) -> .outcome ("error" if any AAA01="N" is present, else
    "complete" - the common-case default, matching Immunization.status's
    "default to the most common real value" precedent) and .disposition
    (free text - AAA03's reject-reason code has no verified FHIR
    CodeSystem crosswalk, so it is carried as text rather than guessed at,
    the same treatment MDM's TXA-17 already gets).

.request (FHIR-required) has no real originating CoverageEligibilityRequest
resource to point at - this converter processes a standalone 271, with no
linked 270 Bundle. Referenced by identifier (BHT03, the same transaction
reference number a real 271 typically echoes back from its prompting 270)
rather than a resolvable urn:uuid - a disclosed, deliberate use of FHIR's
own Reference-by-identifier shape for exactly this "the referenced resource
isn't actually in this Bundle" situation, not a workaround."""

import uuid

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coverageeligibilityresponse import (
    CoverageEligibilityResponse,
    CoverageEligibilityResponseInsurance,
    CoverageEligibilityResponseInsuranceItem,
)
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.base import EdiTransactionBuilder
from app.edi.common import (
    BHT_REFERENCE_SYSTEM,
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
from app.hl7.errors import MappingError, MissingSegmentError
from app.provenance.location import edi_location

TRANSACTION_SET_ID = "271"

# EB01 (Eligibility/Benefit Information Code, HL7-unrelated X12 table
# 1338) - a disclosed subset covering the common active/inactive/
# non-covered cases; unrecognized codes leave .excluded unset rather than
# guessed at.
_EB01_EXCLUDED_MAP = {
    "1": False,  # Active Coverage
    "6": True,  # Inactive
    "I": True,  # Non-Covered
}
_EB01_ACTIVE_COVERAGE = "1"

# EB12 (In Plan Network Indicator, Y/N/U) has no official FHIR CodeSystem
# for CoverageEligibilityResponseInsuranceItem.network (itself a
# CodeableConcept, confirmed by inspecting model_fields directly rather
# than assumed) - carried as disclosed free text rather than a guessed
# coding, the same "text-only when no verified coding system exists"
# treatment Allergies' "no known allergy" fallback already established.
# Public (not module-private) - app/transform/edi_271.py reuses this table
# in reverse to rebuild EB12 from CoverageEligibilityResponseInsuranceItem
# .network.text.
EB12_NETWORK_TEXT = {"Y": "In Network", "N": "Out of Network", "U": "Unknown"}


def _build_network(eb12: str) -> CodeableConcept | None:
    text = EB12_NETWORK_TEXT.get(eb12.strip().upper())
    return CodeableConcept(text=text) if text else None


def _build_insurance_items(
    patient_loop_members: list, resource_id: str | None = None, recorder=None
) -> tuple[list[CoverageEligibilityResponseInsuranceItem], bool]:
    eb_groups = group_by_leader(patient_loop_members, "EB", ["REF", "DTP", "MSG"])
    items: list[CoverageEligibilityResponseInsuranceItem] = []
    inforce = False
    inforce_source_eb01: str | None = None
    for eb, _members in eb_groups:
        # Normalized the same way EB12 is normalized a few lines below (via
        # _build_network) - a real-world sender emitting a lowercase EB01
        # must still resolve against _EB01_EXCLUDED_MAP/_EB01_ACTIVE_COVERAGE
        # rather than silently leaving .excluded/.inforce unset.
        eb01_raw = element(eb, 1)
        eb01 = eb01_raw.strip().upper()
        if eb01 == _EB01_ACTIVE_COVERAGE:
            inforce = True
            if inforce_source_eb01 is None:
                inforce_source_eb01 = eb01_raw
        item = CoverageEligibilityResponseInsuranceItem()
        item_path = f"insurance[0].item[{len(items)}]"
        category = build_service_type_category(
            element(eb, 3),
            resource_id=resource_id,
            relative_path=f"{item_path}.category",
            source_location=edi_location("EB", 3),
            recorder=recorder,
        )
        if category is not None:
            item.category = category
        excluded = _EB01_EXCLUDED_MAP.get(eb01)
        if excluded is not None:
            item.excluded = excluded
            if recorder and resource_id:
                recorder.record(
                    resource_id, f"{item_path}.excluded", edi_location("EB", 1), excluded, source_value=eb01_raw
                )
        description = element(eb, 5)
        if description:
            item.description = description
            if recorder and resource_id:
                recorder.record(resource_id, f"{item_path}.description", edi_location("EB", 5), description)
        eb12_raw = element(eb, 12)
        network = _build_network(eb12_raw)
        if network is not None:
            item.network = network
            if recorder and resource_id:
                recorder.record(
                    resource_id, f"{item_path}.network.text", edi_location("EB", 12), network.text, source_value=eb12_raw
                )
        items.append(item)

    if recorder and resource_id:
        if inforce:
            recorder.record(
                resource_id, "insurance[0].inforce", edi_location("EB", 1), True, source_value=inforce_source_eb01
            )
        else:
            recorder.record_inferred(
                resource_id,
                "insurance[0].inforce",
                'No EB segment carried EB01="1" (Active Coverage) across this patient\'s own EB groups - defaults to false.',
                False,
            )

    return items, inforce


def _resolve_outcome_and_disposition(
    segments: list, resource_id: str | None = None, recorder=None
) -> tuple[str, str | None]:
    """.outcome = "error" whenever ANY AAA01="N" (Request Validation
    failure) is present, per the module docstring - unconditional on
    whether AAA03 (the reject-reason code) itself resolved, since an empty
    AAA03 doesn't make the rejection any less real. .disposition is built
    from whichever AAA03 codes did resolve, and is left unset (rather than
    an empty "Rejected: " string) when none did."""
    rejections = [seg for seg in segments if seg[0] == "AAA" and element(seg, 1).strip().upper() == "N"]
    if not rejections:
        if recorder and resource_id:
            recorder.record_inferred(
                resource_id,
                "outcome",
                'No AAA segment with AAA01="N" (Request Validation rejection) was found across the transaction set - defaults to "complete", the common real-world case.',
                "complete",
            )
        return "complete", None
    reject_codes = [code for seg in rejections if (code := element(seg, 3))]
    disposition = f"Rejected: {', '.join(reject_codes)}" if reject_codes else "Rejected"
    if recorder and resource_id:
        recorder.record(resource_id, "outcome", edi_location("AAA", 1), "error", source_value=element(rejections[0], 1))
        if reject_codes:
            recorder.record(resource_id, "disposition", edi_location("AAA", 3), disposition)
    return "error", disposition


class Edi271Builder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        # delimiters unused - see EdiTransactionBuilder's own docstring.
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError("271 transaction set is missing its BHT segment")

        parties = resolve_eligibility_parties(transaction_set.segments, "271")

        payer = build_organization_from_nm1(parties.payer_nm1, recorder=recorder)
        provider: Resource = (
            build_practitioner_from_nm1(parties.provider_nm1, recorder=recorder)
            if is_person_entity(parties.provider_nm1)
            else build_organization_from_nm1(parties.provider_nm1, recorder=recorder)
        )
        subscriber = build_patient_from_nm1_dmg(parties.subscriber_nm1, parties.subscriber_dmg, recorder=recorder)

        # Same "dependent wins when present" rule as 270 - see
        # eligibility_270.py's module docstring for the full rationale.
        patient = (
            build_patient_from_nm1_dmg(parties.patient_nm1, parties.patient_dmg, recorder=recorder)
            if parties.patient_is_dependent
            else subscriber
        )

        coverage = build_coverage(patient, payer, subscriber, recorder=recorder)

        response_id = str(uuid.uuid4())
        items, inforce = _build_insurance_items(parties.patient_loop_members, resource_id=response_id, recorder=recorder)
        outcome, disposition = _resolve_outcome_and_disposition(
            transaction_set.segments, resource_id=response_id, recorder=recorder
        )

        bht04_raw = element(bht, 4)
        bht05_raw = element(bht, 5)
        created = parse_x12_datetime(bht04_raw, bht05_raw)
        if created is None:
            raise MappingError("271 transaction set's BHT segment has no resolvable creation date (BHT04)")

        bht03 = element(bht, 3)
        request_reference = (
            Reference(identifier=Identifier(system=BHT_REFERENCE_SYSTEM, value=bht03)) if bht03 else Reference()
        )

        response = CoverageEligibilityResponse(
            id=response_id,
            status="active",
            purpose=[DEFAULT_PURPOSE],
            created=created,
            patient=Reference(reference=f"urn:uuid:{patient.id}"),
            insurer=Reference(reference=f"urn:uuid:{payer.id}"),
            request=request_reference,
            outcome=outcome,
        )
        if disposition:
            response.disposition = disposition
        if recorder:
            recorder.record_inferred(
                response_id,
                "status",
                'Every CoverageEligibilityResponse this app builds is a synthesized, always-active resource - no X12 field carries a response-level status separate from whether it was successfully created.',
                "active",
            )
            recorder.record_inferred(
                response_id,
                "purpose[0]",
                '271 carries no data element for "why are we asking" - CoverageEligibilityResponse.purpose is FHIR-required with no source field to derive it from, so this always defaults to the dominant real-world use.',
                DEFAULT_PURPOSE,
            )
            recorder.record(
                response_id,
                "created",
                f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}",
                created,
                source_value=bht04_raw + bht05_raw,
            )

        insurance = CoverageEligibilityResponseInsurance(coverage=Reference(reference=f"urn:uuid:{coverage.id}"))
        insurance.inforce = inforce
        if items:
            insurance.item = items
        response.insurance = [insurance]

        resources: list[Resource] = [payer, provider, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.extend([coverage, response])

        return assemble_bundle(bht, *resources, recorder=recorder)
