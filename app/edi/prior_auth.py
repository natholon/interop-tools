"""X12 278 (Health Care Services Review - Request for Review and Response)
-> FHIR `Claim` (`use="preauthorization"`) for the request, plus a
`ClaimResponse` referencing that same `Claim` when the message is a
response and carries a certification decision.

**A genuinely different transaction-pairing shape from every other EDI
family in this app**: 270/271, 276/277 each have distinct `ST01` values
for request vs. response. **278 does not** - both directions share the
literal `ST01="278"` (verified directly against real X12.org-published
examples: `x12.org/examples/005010x217/example-04-request-home-health-care`
for the request, `x12.org/examples/005010x217/example-1b-response-request-review`
for the response), distinguished instead by `BHT02` (`"13"`=request,
`"11"`=response). This is why `Edi278Builder` is a single class registered
once under `"278"` - unlike `Edi270Builder`/`Edi271Builder` or
`Edi276Builder`/`Edi277Builder`, there is no second `ST01` to register a
second builder under; the request/response branch happens *inside*
`build_bundle()` by reading `BHT02` itself.

Loop shape (HL-hierarchy, HL03 level codes) - verified against the same
real X12.org examples, not assumed to carry over from another family:
  2000A (HL03="20", UMO/Payer)              NM1*X3      -> payer Organization
  2000B (HL03="21", Requester)              NM1*1P      -> requester Org/Practitioner
  2000C (HL03="22", Subscriber)             NM1*IL, DMG -> subscriber Patient
  2000D (HL03="23", Dependent, optional)    NM1*QC, DMG -> dependent Patient
  2000E (HL03="EV", Patient Event - a CHILD of whichever loop is "the
         patient", subscriber or dependent, not a sibling)
                                             UM, HI, DTP, and - response
                                             only - HCR

2000A-2000D's HL03 codes (`"20"`/`"21"`/`"22"`/`"23"`) are identical to
270/271's own table - genuinely, not coincidentally, confirmed by the same
real examples above - so this module reuses `app.edi.common`'s public
`HL_INFORMATION_SOURCE`/`HL_INFORMATION_RECEIVER`/`HL_SUBSCRIBER`/
`HL_DEPENDENT` constants directly rather than re-declaring them (unlike
276/277, which needed its own table since its chain genuinely diverges).
Past 2000D, 278 continues one level deeper than `resolve_eligibility_parties`
returns (into 2000E, a child of "the patient" loop specifically) - so this
module has its own `resolve_prior_auth_loops()` rather than reusing that
function's return shape unchanged, the same "extract on second use, but
only as far as the second use actually needs" discipline `resolve_claim_status_loops`
already established relative to it.

`UM03` (Service Type Code) is the same X12 external code list as 270's
`EQ01`/271's `EB03` - reuses `app.edi.common.build_service_type_category`
directly for `Claim.item[].productOrService`, rather than a third disclosed
local-system table for the identical list.

`HI` (diagnosis codes) is the first `IVL`-style *repeating* composite
element this app has parsed - up to 12 diagnosis-code composites can occur
positionally within one `HI` segment (`HI*ABK:1831*ABF:2630~` = two
diagnoses, not two `HI` segments) - each read via `component()` the same
way `STC01` already established, iterated across `element(hi, N)` for
N=1..12 (the 5010 IG's own repetition cap for this field), stopping at the
first empty position. This parsing loop (and its qualifier table) was
promoted to `app.edi.common.build_diagnosis_codeable_concepts`/
`HI_QUALIFIER_SYSTEM` once `claim_837p.py` became a second real consumer
of the identical HI composite shape - see that module's own bullet for a
real qualifier-table bug this promotion caught along the way.

`HCR01` (Action Code, e.g. `"A1"`=Certified in Total, `"A2"`=Certified -
Partial, `"A3"`=Not Certified, `"A4"`=Pended - verified against a real RFI
answer plus companion-guide text, no single authoritative published table
found the way STC's Claim Status Category Codes are on x12.org) drives
`ClaimResponse.outcome` via a small disclosed crosswalk, and is also
carried as the `ClaimResponseItemAdjudication.category` coding (a
disclosed local system, same category as `_STC_CATEGORY_SYSTEM`).
`HCR02` (Reference Identification - the actual authorization/certification
number, e.g. `"AUTH0001"`) -> `ClaimResponse.preAuthRef`, a plain string
field confirmed by inspecting `model_fields` directly.

Disclosed Phase-3 scope limits, decided up front: the `2000F` Service-level
sub-loop (per-service-line `UM`/`HCR`/`HI`, used when a review covers
multiple distinct services independently) is not modeled - only one
patient-event-level `UM`/`HI`/`HCR` set is read, the same "claim-level
only, not the further-nested sub-loop" scope limit `claim_status.py`
already applies to `SVC`-nested `STC`. `UM01` (Request Category Code, e.g.
`"HS"`/`"SC"`/`"AR"`) has no clean target in base FHIR `Claim` (its
`.type` is bound to a fixed institutional|oral|pharmacy|professional|vision
set that UM01 doesn't map to at all) and is left unmapped rather than
forcing a wrong code into a field with different semantics; `Claim.type`
defaults to `"professional"`, the dominant real-world prior-authorization
case, the same "default to the most common real value when no fitting
code exists" precedent as 270's `DEFAULT_PURPOSE`. `Claim.priority` has no
source field in 278 at all and defaults to `"normal"` for the same reason.
`HCR03` (Reason Code, external code source 886) is carried as a second
disclosed-local-system adjudication entry rather than dropped, since -
unlike `AAA03`/TXA-17's genuinely unverifiable crosswalks - X12 publishes
code source 886 by name even though this app doesn't have a verified free
copy of its contents to build a real crosswalk from."""

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

TRANSACTION_SET_ID = "278"

_HL_PATIENT_EVENT = "EV"

BHT02_REQUEST = "13"
BHT02_RESPONSE = "11"

_HCR_ACTION_SYSTEM = "urn:interop-tools:x12-hcr-action-code"
_HCR_REASON_SYSTEM = "urn:interop-tools:x12-hcr-reason-code"

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


def _build_diagnoses(hi: Segment | None, delimiters: Delimiters) -> list[ClaimDiagnosis]:
    concepts = build_diagnosis_codeable_concepts(hi, delimiters)
    return [ClaimDiagnosis(sequence=i, diagnosisCodeableConcept=concept) for i, concept in enumerate(concepts, start=1)]


def _build_claim(
    loops: ResolvedPriorAuthLoops,
    bht: Segment,
    patient: Resource,
    payer: Resource,
    requester: Resource,
    coverage: Coverage,
    delimiters: Delimiters,
) -> Claim:
    um = find_segment(loops.patient_event_loop.member_segments, "UM")
    hi = find_segment(loops.patient_event_loop.member_segments, "HI")

    created = parse_x12_datetime(element(bht, 4), element(bht, 5))
    if created is None:
        raise MissingSegmentError(f"{TRANSACTION_SET_ID} transaction set's BHT segment has no resolvable date (BHT04)")

    item_category = build_service_type_category(element(um, 3)) if um is not None else None
    item = ClaimItem(sequence=1, productOrService=item_category or CodeableConcept(text="Unspecified service"))

    insurance = ClaimInsurance(sequence=1, focal=True, coverage=Reference(reference=f"urn:uuid:{coverage.id}"))

    claim = Claim(
        id=str(uuid.uuid4()),
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
    diagnoses = _build_diagnoses(hi, delimiters)
    if diagnoses:
        claim.diagnosis = diagnoses
    return claim


def _build_claim_response(
    loops: ResolvedPriorAuthLoops, claim: Claim, patient: Resource, payer: Resource, coverage: Coverage
) -> ClaimResponse | None:
    hcr = find_segment(loops.patient_event_loop.member_segments, "HCR")
    if hcr is None:
        return None

    # claim.created was already resolved (and MissingSegmentError-checked)
    # from this same BHT segment in _build_claim - reused directly rather
    # than re-parsing BHT04/05 a second time for the same transaction set.
    created = claim.created

    # Normalized the same way NM108/EB01 already are elsewhere in this
    # package - a lowercase HCR01 must still resolve to the correct
    # outcome rather than silently falling back to the "unrecognized"
    # default and firing a spurious edi.278-unrecognized-hcr-action-code
    # finding for a code that's actually recognized.
    action_code = element(hcr, 1).strip().upper()
    outcome = HCR01_TO_OUTCOME.get(action_code, _DEFAULT_OUTCOME)
    auth_number = element(hcr, 2)
    reason_code = element(hcr, 3)

    response = ClaimResponse(
        id=str(uuid.uuid4()),
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

    adjudications = []
    if action_code:
        adjudications.append(
            ClaimResponseItemAdjudication(category=CodeableConcept(coding=[Coding(system=_HCR_ACTION_SYSTEM, code=action_code)]))
        )
    if reason_code:
        adjudications.append(
            ClaimResponseItemAdjudication(category=CodeableConcept(coding=[Coding(system=_HCR_REASON_SYSTEM, code=reason_code)]))
        )
    if adjudications:
        response.item = [ClaimResponseItem(itemSequence=1, adjudication=adjudications)]

    response.insurance = [
        ClaimResponseInsurance(sequence=1, focal=True, coverage=Reference(reference=f"urn:uuid:{coverage.id}"))
    ]
    return response


class Edi278Builder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_prior_auth_loops(transaction_set.segments, self.transaction_set_id)

        payer = build_organization_from_nm1(loops.payer_nm1)
        requester: Resource = (
            build_practitioner_from_nm1(loops.receiver_nm1)
            if is_person_entity(loops.receiver_nm1)
            else build_organization_from_nm1(loops.receiver_nm1)
        )
        subscriber = build_patient_from_nm1_dmg(loops.subscriber_nm1, loops.subscriber_dmg)
        patient = (
            build_patient_from_nm1_dmg(loops.patient_nm1, loops.patient_dmg) if loops.patient_is_dependent else subscriber
        )

        coverage = build_coverage(patient, payer, subscriber)
        claim = _build_claim(loops, bht, patient, payer, requester, coverage, delimiters)

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
            response = _build_claim_response(loops, claim, patient, payer, coverage)
            if response is not None:
                resources.append(response)

        return assemble_bundle(bht, *resources)
