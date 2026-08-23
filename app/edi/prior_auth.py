"""X12 278 (Health Care Services Review) -> FHIR `Claim`
(`use="preauthorization"`), plus a `ClaimResponse` referencing that same
`Claim` when the message carries a certification decision.

**278 is the one family here whose request and response share an `ST01`.**
270/271 and 276/277 each get two; 278 uses `"278"` for both, distinguished
by `BHT02` (`"13"`=request, `"11"`=response). So `Edi278Builder` is a
single class registered once, branching inside `build_bundle()`. Verified
against real X12.org examples (`005010x217` example-04 and example-1b).

Loop shape (HL03 level codes, verified against those same examples):

    2000A  "20"  UMO/Payer            NM1*X3      -> payer Organization
    2000B  "21"  Requester            NM1*1P      -> Org/Practitioner
    2000C  "22"  Subscriber           NM1*IL+DMG  -> subscriber Patient
    2000D  "23"  Dependent (optional) NM1*QC+DMG  -> dependent Patient
    2000E  "EV"  Patient Event - a CHILD of whichever loop is the patient,
                 not a sibling. Carries UM, HI, DTP, and (response) HCR.

2000A-2000D's codes are genuinely identical to 270/271's, so this reuses
`common.py`'s `HL_*` constants. It continues one level deeper than
`resolve_eligibility_parties` goes, hence its own
`resolve_prior_auth_loops()`.

Field notes:
- `UM03` is the same X12 code list as 270's `EQ01`/271's `EB03` - reuses
  `build_service_type_category` rather than a third local table.
- `HI` holds up to 12 diagnosis composites positionally within *one*
  segment (`HI*ABK:1831*ABF:2630~` is two diagnoses, not two segments).
  Parsed by the shared `build_diagnosis_codeable_concepts`.
- `HCR01` (Action Code: A1 certified, A2 partial, A3 not certified, A4
  pended) drives `ClaimResponse.outcome` and rides along as the
  adjudication category. No authoritative free table exists for it the way
  x12.org publishes STC's, so the crosswalk is disclosed-local, verified
  against an RFI answer plus companion-guide text.
- `HCR02` -> `ClaimResponse.preAuthRef` (a plain string, per `model_fields`).

Scope limits:
- The `2000F` Service-level sub-loop is not modelled - only one
  patient-event-level UM/HI/HCR set is read, matching the claim-level-only
  limit `claim_status.py` applies to `SVC`-nested `STC`.
- `UM01` (Request Category) has no target: `Claim.type` is bound to
  institutional|oral|pharmacy|professional|vision, which it does not map
  to. `Claim.type` defaults to `"professional"` and `.priority` to
  `"normal"` - neither has a source field here.
- `HCR03` (external code source 886) is carried as a disclosed-local
  adjudication entry: X12 names the code source but publishes no free
  copy to build a real crosswalk from."""

import uuid
from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.claim import Claim, ClaimDiagnosis, ClaimInsurance, ClaimItem
from fhir.resources.R4B.claimresponse import ClaimResponse, ClaimResponseInsurance, ClaimResponseItem, ClaimResponseItemAdjudication
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.coverage import Coverage
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.base import EdiTransactionBuilder
from app.edi.common import (
    HL_DEPENDENT,
    HL_INFORMATION_RECEIVER,
    HL_INFORMATION_SOURCE,
    HL_SUBSCRIBER,
    assemble_bundle,
    build_coverage,
    build_diagnosis_codeable_concepts,
    build_organization_from_nm1,
    build_patient_from_nm1_dmg,
    build_practitioner_from_nm1,
    build_service_type_category,
    find_child_loop,
    is_person_entity,
    parse_x12_datetime,
)
from app.edi.parser import Delimiters, HlLoop, Segment, TransactionSet, element, find_segment, group_by_hl_hierarchy
from app.hl7.errors import MissingSegmentError
from app.provenance.location import edi_location

TRANSACTION_SET_ID = "278"

_HL_PATIENT_EVENT = "EV"

BHT02_REQUEST = "13"
BHT02_RESPONSE = "11"

# Public (not module-private) - app/transform/prior_auth.py became a real
# reverse-direction consumer, reading ClaimResponseItemAdjudication's own
# coding systems back apart into HCR01/HCR03 rather than guessing.
HCR_ACTION_SYSTEM = "urn:interop-tools:x12-hcr-action-code"
HCR_REASON_SYSTEM = "urn:interop-tools:x12-hcr-reason-code"

# HCR01 (Action Code) -> ClaimResponse.outcome. Verified against a real
# X12.org response example (HCR*A1*AUTH0001~) plus a published RFI answer
# describing A2/A3/A4 - no single authoritative full code table was found
# published free, so this is a disclosed subset covering the common
# real-world decisions; unrecognized/absent falls back to "complete", the
# most common real outcome (matching Immunization.status's own precedent).
HCR01_TO_OUTCOME = {
    "A1": "complete",  # Certified in Total
    "A2": "partial",  # Certified - Partial
    "A3": "complete",  # Not Certified - a denial is still a completed decision, not a FHIR "error"
    "A4": "queued",  # Pended (pending additional information)
}
_DEFAULT_OUTCOME = "complete"

DEFAULT_CLAIM_TYPE = "professional"
DEFAULT_PRIORITY = "normal"


@dataclass
class ResolvedPriorAuthLoops:
    """Everything Edi278Builder.build_bundle() needs from the 2000A-2000E
    loop walk. Mirrors app.edi.common.ResolvedEligibilityParties' fields
    for the shared 2000A-2000D portion (same HL03 table), extended with
    patient_event_loop - the one level 270/271 never needed to reach."""

    payer_loop: HlLoop
    receiver_loop: HlLoop
    subscriber_loop: HlLoop
    dependent_loop: HlLoop | None
    patient_event_loop: HlLoop
    payer_nm1: Segment
    receiver_nm1: Segment
    subscriber_nm1: Segment
    subscriber_dmg: Segment | None
    patient_nm1: Segment
    patient_dmg: Segment | None
    patient_is_dependent: bool


def resolve_prior_auth_loops(segments: list[Segment], transaction_set_id: str) -> ResolvedPriorAuthLoops:
    """Walk the strict 2000A(20)->2000B(21)->2000C(22)->[2000D(23)]->
    2000E(EV) parent chain a 278 transaction set requires, raising
    MissingSegmentError for any required loop or NM1 that doesn't resolve
    - the same "raise here, not in the caller" discipline
    resolve_eligibility_parties/resolve_claim_status_loops both already
    established, including the "a dependent loop only counts as real once
    its own NM1 resolves" gate for 2000D."""
    roots = group_by_hl_hierarchy(segments)
    payer_loop = next((loop for loop in roots if loop.hl03 == HL_INFORMATION_SOURCE), None)
    if payer_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000A Payer/UMO loop")
    receiver_loop = find_child_loop(payer_loop, HL_INFORMATION_RECEIVER)
    if receiver_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000B Requester loop")
    subscriber_loop = find_child_loop(receiver_loop, HL_SUBSCRIBER)
    if subscriber_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000C Subscriber loop")

    payer_nm1 = find_segment(payer_loop.member_segments, "NM1")
    if payer_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000A loop is missing its NM1 (payer) segment")
    receiver_nm1 = find_segment(receiver_loop.member_segments, "NM1")
    if receiver_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1 (requester) segment")
    subscriber_nm1 = find_segment(subscriber_loop.member_segments, "NM1")
    if subscriber_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000C loop is missing its NM1 (subscriber) segment")
    subscriber_dmg = find_segment(subscriber_loop.member_segments, "DMG")

    dependent_loop = find_child_loop(subscriber_loop, HL_DEPENDENT)
    if dependent_loop is not None and find_segment(dependent_loop.member_segments, "NM1") is None:
        dependent_loop = None

    patient_loop = subscriber_loop
    patient_nm1 = subscriber_nm1
    patient_dmg = subscriber_dmg
    patient_is_dependent = False
    if dependent_loop is not None:
        patient_loop = dependent_loop
        patient_nm1 = find_segment(dependent_loop.member_segments, "NM1")
        patient_dmg = find_segment(dependent_loop.member_segments, "DMG")
        patient_is_dependent = True

    patient_event_loop = find_child_loop(patient_loop, _HL_PATIENT_EVENT)
    if patient_event_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000E Patient Event loop")

    return ResolvedPriorAuthLoops(
        payer_loop=payer_loop,
        receiver_loop=receiver_loop,
        subscriber_loop=subscriber_loop,
        dependent_loop=dependent_loop,
        patient_event_loop=patient_event_loop,
        payer_nm1=payer_nm1,
        receiver_nm1=receiver_nm1,
        subscriber_nm1=subscriber_nm1,
        subscriber_dmg=subscriber_dmg,
        patient_nm1=patient_nm1,
        patient_dmg=patient_dmg,
        patient_is_dependent=patient_is_dependent,
    )


def _build_diagnoses(hi: Segment | None, delimiters: Delimiters, resource_id: str | None = None, recorder=None) -> list[ClaimDiagnosis]:
    concepts = build_diagnosis_codeable_concepts(
        hi, delimiters, resource_id=resource_id, relative_path_prefix="diagnosis", recorder=recorder
    )
    return [ClaimDiagnosis(sequence=i, diagnosisCodeableConcept=concept) for i, concept in enumerate(concepts, start=1)]


def _build_claim(
    loops: ResolvedPriorAuthLoops,
    bht: Segment,
    patient: Resource,
    payer: Resource,
    requester: Resource,
    coverage: Coverage,
    delimiters: Delimiters,
    recorder=None,
) -> Claim:
    um = find_segment(loops.patient_event_loop.member_segments, "UM")
    hi = find_segment(loops.patient_event_loop.member_segments, "HI")

    bht04_raw = element(bht, 4)
    bht05_raw = element(bht, 5)
    created = parse_x12_datetime(bht04_raw, bht05_raw)
    if created is None:
        raise MissingSegmentError(f"{TRANSACTION_SET_ID} transaction set's BHT segment has no resolvable date (BHT04)")

    claim_id = str(uuid.uuid4())

    item_category = (
        build_service_type_category(
            element(um, 3),
            resource_id=claim_id,
            relative_path="item[0].productOrService",
            source_location=edi_location("UM", 3),
            recorder=recorder,
        )
        if um is not None
        else None
    )
    product_or_service = item_category or CodeableConcept(text="Unspecified service")
    item = ClaimItem(sequence=1, productOrService=product_or_service)
    if recorder and item_category is None:
        recorder.record_inferred(
            claim_id,
            "item[0].productOrService.text",
            "No UM segment was present in the 2000E Patient Event loop, or its own Service Type Code (UM03) was empty - Claim.item[0].productOrService defaults to a generic placeholder text.",
            "Unspecified service",
        )

    insurance = ClaimInsurance(sequence=1, focal=True, coverage=Reference(reference=f"urn:uuid:{coverage.id}"))

    claim = Claim(
        id=claim_id,
        status="active",
        type=CodeableConcept(coding=[Coding(system="http://terminology.hl7.org/CodeSystem/claim-type", code=DEFAULT_CLAIM_TYPE)]),
        use="preauthorization",
        patient=Reference(reference=f"urn:uuid:{patient.id}"),
        created=created,
        provider=Reference(reference=f"urn:uuid:{requester.id}"),
        priority=CodeableConcept(text=DEFAULT_PRIORITY),
        insurance=[insurance],
    )
    claim.insurer = Reference(reference=f"urn:uuid:{payer.id}")
    claim.item = [item]
    if recorder:
        recorder.record_inferred(
            claim_id,
            "status",
            "Every Claim this app builds from a 278 transaction set has status=\"active\" - not read from any X12 field.",
            "active",
        )
        recorder.record_inferred(
            claim_id,
            "type.coding[0].code",
            'UM01 (Request Category Code) has no clean target in base FHIR Claim.type (a fixed institutional|oral|pharmacy|professional|vision set) - always defaults to "professional", the dominant real-world prior-authorization case.',
            DEFAULT_CLAIM_TYPE,
        )
        recorder.record_inferred(
            claim_id,
            "use",
            'Every Claim this app builds from a 278 transaction set has use="preauthorization" - that\'s what a 278 request/response fundamentally is, not read from any field.',
            "preauthorization",
        )
        recorder.record(
            claim_id, "created", f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}", created, source_value=bht04_raw + bht05_raw
        )
        recorder.record_inferred(
            claim_id,
            "priority.text",
            "278 carries no data element for a review's own priority - Claim.priority defaults to \"normal\", the same \"default to the most common real value\" precedent as 270's DEFAULT_PURPOSE.",
            DEFAULT_PRIORITY,
        )
    diagnoses = _build_diagnoses(hi, delimiters, resource_id=claim_id, recorder=recorder)
    if diagnoses:
        claim.diagnosis = diagnoses
    return claim


def _build_claim_response(
    loops: ResolvedPriorAuthLoops, claim: Claim, patient: Resource, payer: Resource, coverage: Coverage, recorder=None
) -> ClaimResponse | None:
    hcr = find_segment(loops.patient_event_loop.member_segments, "HCR")
    if hcr is None:
        return None

    # claim.created was already resolved (and MissingSegmentError-checked)
    # from this same BHT segment in _build_claim - reused directly rather
    # than re-parsing BHT04/05 a second time for the same transaction set.
    # Note this is the resource's own POST-construction attribute, already
    # normalized by pydantic into a real datetime object (not the original
    # FHIR-formatted string _build_claim itself computed) - recorded via
    # created_display below so the crosswalk shows the identical "Z"/
    # "+HH:MM"-suffixed shape parse_hl7_datetime itself produces, not
    # Python's own default `str(datetime)` rendering.
    created = claim.created
    created_display = created.isoformat().replace("+00:00", "Z") if hasattr(created, "isoformat") else created

    # Normalized the same way NM108/EB01 already are elsewhere in this
    # package - a lowercase HCR01 must still resolve to the correct
    # outcome rather than silently falling back to the "unrecognized"
    # default and firing a spurious edi.278-unrecognized-hcr-action-code
    # finding for a code that's actually recognized.
    action_code_raw = element(hcr, 1)
    action_code = action_code_raw.strip().upper()
    outcome = HCR01_TO_OUTCOME.get(action_code, _DEFAULT_OUTCOME)
    auth_number = element(hcr, 2)
    reason_code = element(hcr, 3)

    response_id = str(uuid.uuid4())
    response = ClaimResponse(
        id=response_id,
        status="active",
        type=claim.type,
        use="preauthorization",
        patient=Reference(reference=f"urn:uuid:{patient.id}"),
        created=created,
        insurer=Reference(reference=f"urn:uuid:{payer.id}"),
        outcome=outcome,
    )
    response.request = Reference(reference=f"urn:uuid:{claim.id}")
    if auth_number:
        response.preAuthRef = auth_number

    if recorder:
        recorder.record_inferred(
            response_id,
            "status",
            "Every ClaimResponse this app builds from a 278 transaction set has status=\"active\" - not read from any X12 field.",
            "active",
        )
        recorder.record_inferred(
            response_id,
            "use",
            'Every ClaimResponse this app builds from a 278 transaction set has use="preauthorization", mirroring the Claim it responds to - not read from any field.',
            "preauthorization",
        )
        recorder.record(
            response_id, "created", f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}", created_display
        )
        if action_code:
            recorder.record(response_id, "outcome", edi_location("HCR", 1), outcome, source_value=action_code_raw)
        else:
            recorder.record_inferred(
                response_id,
                "outcome",
                'This 278 response\'s HCR segment has no resolvable HCR01 (Action Code) - outcome defaults to "complete", the most common real-world decision.',
                _DEFAULT_OUTCOME,
            )
        if auth_number:
            recorder.record(response_id, "preAuthRef", edi_location("HCR", 2), auth_number)

    adjudications = []
    if action_code:
        adjudications.append(
            ClaimResponseItemAdjudication(category=CodeableConcept(coding=[Coding(system=HCR_ACTION_SYSTEM, code=action_code)]))
        )
        if recorder:
            recorder.record(
                response_id,
                f"item[0].adjudication[{len(adjudications) - 1}].category.coding[0].code",
                edi_location("HCR", 1),
                action_code,
                source_value=action_code_raw,
            )
    if reason_code:
        adjudications.append(
            ClaimResponseItemAdjudication(category=CodeableConcept(coding=[Coding(system=HCR_REASON_SYSTEM, code=reason_code)]))
        )
        if recorder:
            recorder.record(
                response_id,
                f"item[0].adjudication[{len(adjudications) - 1}].category.coding[0].code",
                edi_location("HCR", 3),
                reason_code,
            )
    if adjudications:
        response.item = [ClaimResponseItem(itemSequence=1, adjudication=adjudications)]

    response.insurance = [
        ClaimResponseInsurance(sequence=1, focal=True, coverage=Reference(reference=f"urn:uuid:{coverage.id}"))
    ]
    return response


class Edi278Builder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_prior_auth_loops(transaction_set.segments, self.transaction_set_id)

        payer = build_organization_from_nm1(loops.payer_nm1, recorder=recorder)
        requester: Resource = (
            build_practitioner_from_nm1(loops.receiver_nm1, recorder=recorder)
            if is_person_entity(loops.receiver_nm1)
            else build_organization_from_nm1(loops.receiver_nm1, recorder=recorder)
        )
        subscriber = build_patient_from_nm1_dmg(loops.subscriber_nm1, loops.subscriber_dmg, recorder=recorder)
        patient = (
            build_patient_from_nm1_dmg(loops.patient_nm1, loops.patient_dmg, recorder=recorder)
            if loops.patient_is_dependent
            else subscriber
        )

        coverage = build_coverage(patient, payer, subscriber, recorder=recorder)
        claim = _build_claim(loops, bht, patient, payer, requester, coverage, delimiters, recorder=recorder)

        resources: list[Resource] = [payer, requester, subscriber]
        if patient is not subscriber:
            resources.append(patient)
        resources.append(coverage)
        resources.append(claim)

        # BHT02 - not ST01 - is what distinguishes a 278 request from a
        # response (see the module docstring); an unrecognized/absent
        # BHT02 is treated as a request (no ClaimResponse attempted),
        # matching every other X12 pair's own "request" default when the
        # purpose code itself doesn't resolve.
        if element(bht, 2).strip() == BHT02_RESPONSE:
            response = _build_claim_response(loops, claim, patient, payer, coverage, recorder=recorder)
            if response is not None:
                resources.append(response)

        return assemble_bundle(bht, *resources, recorder=recorder)
