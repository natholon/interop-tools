"""X12 276 (Health Care Claim Status Request) / 277 (Health Care Claim
Status Response) -> FHIR Task.

No official, free X12-to-FHIR ConceptMap IG exists for this transaction
pair (same disclosed gap as every EDI transaction set in this app - see
CLAUDE.md's EDI section). Unlike 270/271, base FHIR R4 has no purpose-built
resource for "the status of a previously submitted claim" either -
Claim.use is a closed claim|preauthorization|predetermination set with no
status-inquiry concept, confirmed directly against hl7.org/fhir/R4/
claim.html rather than assumed. Task is the documented general mechanism
for tracking the status of an activity/request (hl7.org/fhir/R4/task.html):
Task.businessStatus is an unbound CodeableConcept explicitly intended to
carry "business-specific nuances of the business state" - the disclosed
target for STC's category/status code pair, same category as 270/271's
SERVICE_TYPE_CODE_SYSTEM local-system fallback.

Loop shape (HL-hierarchy, HL03 level codes - a DIFFERENT code table than
270/271's, verified against a real 276 segment example rather than assumed
to carry over - HL03's numeric-code MEANING is defined per-TR3, not a
universal table):
  2000A (HL03="20", Payer)                 NM1*PR      -> payer Organization
  2000B (HL03="21", Information Receiver)  NM1         -> receiver Org/Practitioner
  2000C (HL03="19", Provider)              NM1         -> provider Org/Practitioner
  2000D (HL03="22", Subscriber)            NM1*IL, DMG -> subscriber Patient
  2000E (HL03="23", Dependent, optional)   NM1*QC, DMG -> dependent Patient

Unlike 270/271's single "one patient per transaction, dependent wins when
present" rule, a 276/277 can carry claim-status entries for BOTH the
subscriber and a dependent within the same transaction set (each has its
own repeating TRN-led "claim status tracking" loop) - so this module walks
BOTH 2000D and 2000E (when present) rather than picking one "the patient".
One Task is built per TRN-led claim-status group, referencing whichever
patient (subscriber or dependent) that group's loop belongs to.

Within each patient loop, TRN (trace/reference number, echoed
request->response, verified via hl7.org/fhir - no, via Stedi's X12
reference and Blue Cross NC's 276/277 companion guide) leads a repeating
claim-status group whose members include REF (payer claim control number,
patient control number, etc. - captured by the leader/member walk but not
yet mapped to any FHIR field this phase, the same disclosed-and-deferred
treatment 270/271 already gives REF), DTP, and - 277 only - STC (Health
Care Claim Status). A claim-status group may itself nest a further SVC-led
service-line-level sub-group with its own STC/REF/DTP; this module treats
every STC found within a TRN group as claim-level status without
distinguishing service-line-level STC nested under SVC, and only reads
STC01 (not the additional STC10/STC11 composites, used when more than one
status applies at once) - both disclosed Phase-2 scope limits, the same
category as 270's deferred RD8 date-range qualifier.

276 vs. 277 are distinguished by BHT02 ("13" for 276, "08" for 277,
verified against multiple real companion guides) - not read by this
module directly (registry dispatch is by ST01, not BHT02), but documented
here since it's the field a real sender uses to tell them apart."""

import uuid
from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource
from fhir.resources.R4B.task import Task

from app.edi.base import EdiTransactionBuilder
from app.edi.common import (
    assemble_bundle,
    build_organization_from_nm1,
    build_patient_from_nm1_dmg,
    build_practitioner_from_nm1,
    find_child_loop,
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
)
from app.hl7.errors import MissingSegmentError
from app.provenance.location import edi_location

_HL_PAYER = "20"
_HL_RECEIVER = "21"
_HL_PROVIDER = "19"
_HL_SUBSCRIBER = "22"
_HL_DEPENDENT = "23"

# X12's own Claim Status Category Code and Claim Status Code external code
# lists (STC01-1/STC01-2) have no FHIR-canonical system URI - disclosed
# local systems, same category as 270/271's SERVICE_TYPE_CODE_SYSTEM.
# Public (not module-private) - app/transform/claim_status.py became a
# real reverse-direction consumer, reading Task.businessStatus's own
# coding systems back apart into STC01-1/STC01-2 rather than guessing.
STC_CATEGORY_SYSTEM = "urn:interop-tools:x12-claim-status-category-code"
STC_STATUS_SYSTEM = "urn:interop-tools:x12-claim-status-code"
TRACE_NUMBER_SYSTEM = "urn:interop-tools:x12-claim-status-trace-number"

# Claim Status Category Code (STC01-1) -> Task.status, keyed by the
# category code's own leading letter (A=Acknowledgement, P=Pending,
# F=Finalized, R=Request for additional information, E=Response Error,
# D=Data Search Unsuccessful - verified against x12.org's own published
# Claim Status Category Codes page: x12.org/codes/claim-status-category-codes).
# A prefix-level mapping, not a per-exact-code crosswalk - X12's own list
# has finer distinctions within each prefix (e.g. A1 Received vs. A3
# Rejected) that FHIR's fixed task-status value set has no equivalent
# granularity for without guessing at a fuller crosswalk; unrecognized/
# absent falls back to "completed", the most common real-world outcome,
# matching this project's established "default to the most common real
# value when no unknown option exists" precedent (Medications' moodCode,
# Immunization.status).
STC_CATEGORY_PREFIX_TO_TASK_STATUS = {
    "A": "received",
    "P": "in-progress",
    "F": "completed",
    "R": "on-hold",
    "E": "failed",
    "D": "failed",
}
_DEFAULT_TASK_STATUS = "completed"


@dataclass
class ResolvedClaimStatusLoops:
    """Everything build_bundle() and validation.py's 276/277 rules need
    from the 2000A-2000E loop walk - the NM1 fields are guaranteed
    resolved (never None) for payer/receiver/provider/subscriber, since
    resolve_claim_status_loops raises MissingSegmentError itself rather
    than deferring that check to callers. dependent_loop is None whenever
    no 2000E loop exists OR it exists but has no resolvable NM1 - see
    resolve_claim_status_loops' own docstring for why that gate matters."""

    payer_loop: HlLoop
    receiver_loop: HlLoop
    provider_loop: HlLoop
    subscriber_loop: HlLoop
    dependent_loop: HlLoop | None
    payer_nm1: Segment
    receiver_nm1: Segment
    provider_nm1: Segment
    subscriber_nm1: Segment


def resolve_claim_status_loops(segments: list[Segment], transaction_set_id: str) -> ResolvedClaimStatusLoops:
    """Walk the strict 2000A(20)->2000B(21)->2000C(19)->2000D(22)->
    2000E(23) parent chain this transaction pair requires, and resolve
    each loop's own NM1 - the claim-status-specific analogue of
    app.edi.common::resolve_eligibility_parties, kept local to this module
    rather than forced into a shared helper since the HL03 code table
    genuinely differs (270/271 has no 2000C provider loop at all, and
    reuses "20"/"21"/"22"/"23" for a shallower 4-level chain).

    Mirrors resolve_eligibility_parties' own "raise here, not in the
    caller" discipline for every required loop's NM1 (payer/receiver/
    provider/subscriber) - a code review caught that an earlier version
    left NM1 presence unchecked here, deferring it to build_bundle, which
    meant validation.py's own rules (reading only the loop objects, not
    NM1 presence) could see a subscriber loop with no NM1 at all and
    report a misleading "will still build a Patient, just with no
    HumanName" finding for a case that actually raises MissingSegmentError
    and converts to nothing. The dependent loop gets the same "only counts
    as real once its own NM1 resolves" gate resolve_eligibility_parties
    already applies to 270/271's dependent loop, for the same reason:
    _build_tasks_for_patient_loop below applies that identical check
    before building any Task for the dependent, so validation must see the
    same gate or it can report on STC content the real builder silently
    drops."""
    roots = group_by_hl_hierarchy(segments)
    payer_loop = next((loop for loop in roots if loop.hl03 == _HL_PAYER), None)
    if payer_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000A Payer loop")
    receiver_loop = find_child_loop(payer_loop, _HL_RECEIVER)
    if receiver_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000B Information Receiver loop")
    provider_loop = find_child_loop(receiver_loop, _HL_PROVIDER)
    if provider_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000C Provider loop")
    subscriber_loop = find_child_loop(provider_loop, _HL_SUBSCRIBER)
    if subscriber_loop is None:
        raise MissingSegmentError(f"{transaction_set_id} transaction set is missing its 2000D Subscriber loop")

    payer_nm1 = find_segment(payer_loop.member_segments, "NM1")
    if payer_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000A loop is missing its NM1 (payer) segment")
    receiver_nm1 = find_segment(receiver_loop.member_segments, "NM1")
    if receiver_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000B loop is missing its NM1 (information receiver) segment")
    provider_nm1 = find_segment(provider_loop.member_segments, "NM1")
    if provider_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000C loop is missing its NM1 (provider) segment")
    subscriber_nm1 = find_segment(subscriber_loop.member_segments, "NM1")
    if subscriber_nm1 is None:
        raise MissingSegmentError(f"{transaction_set_id} 2000D loop is missing its NM1 (subscriber) segment")

    dependent_loop = find_child_loop(subscriber_loop, _HL_DEPENDENT)
    if dependent_loop is not None and find_segment(dependent_loop.member_segments, "NM1") is None:
        dependent_loop = None

    return ResolvedClaimStatusLoops(
        payer_loop=payer_loop,
        receiver_loop=receiver_loop,
        provider_loop=provider_loop,
        subscriber_loop=subscriber_loop,
        dependent_loop=dependent_loop,
        payer_nm1=payer_nm1,
        receiver_nm1=receiver_nm1,
        provider_nm1=provider_nm1,
        subscriber_nm1=subscriber_nm1,
    )


def _build_status_concept(
    stc: Segment, delimiters: Delimiters, resource_id: str | None = None, recorder=None
) -> CodeableConcept | None:
    stc01 = element(stc, 1)
    category_code = component(stc01, delimiters, 1)
    status_code = component(stc01, delimiters, 2)
    codings = []
    if category_code:
        codings.append(Coding(system=STC_CATEGORY_SYSTEM, code=category_code))
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"businessStatus.coding[{len(codings) - 1}].code",
                edi_location("STC", 1, component=1),
                category_code,
            )
    if status_code:
        codings.append(Coding(system=STC_STATUS_SYSTEM, code=status_code))
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"businessStatus.coding[{len(codings) - 1}].code",
                edi_location("STC", 1, component=2),
                status_code,
            )
    if not codings:
        return None
    return CodeableConcept(coding=codings)


def _resolve_task_status(
    stc: Segment | None, delimiters: Delimiters, resource_id: str | None = None, recorder=None
) -> str:
    if stc is None:
        if recorder and resource_id:
            recorder.record_inferred(
                resource_id,
                "status",
                'This claim-status group has no STC segment - Task.status defaults to "completed", the most common real-world outcome.',
                _DEFAULT_TASK_STATUS,
            )
        return _DEFAULT_TASK_STATUS
    category_code = component(element(stc, 1), delimiters, 1)
    prefix = category_code[:1].upper() if category_code else ""
    status = STC_CATEGORY_PREFIX_TO_TASK_STATUS.get(prefix, _DEFAULT_TASK_STATUS)
    if recorder and resource_id:
        recorder.record(resource_id, "status", edi_location("STC", 1, component=1), status, source_value=category_code)
    return status


def _build_tasks_for_patient_loop(
    patient_loop: HlLoop,
    patient: Resource,
    payer: Resource,
    provider: Resource,
    delimiters: Delimiters,
    include_status: bool,
    authored_on: str | None,
    recorder=None,
) -> list[Task]:
    """One Task per TRN-led claim-status group within `patient_loop`'s own
    member segments. `include_status` is False for 276 (request - no STC
    exists yet) and True for 277 (response)."""
    tasks: list[Task] = []
    trn_groups = group_by_leader(patient_loop.member_segments, "TRN", ["REF", "STC", "DTP", "SVC"])
    for trn, members in trn_groups:
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            status="requested",
            intent="order",
            code=CodeableConcept(text="Claim Status"),
            for_fhir=Reference(reference=f"urn:uuid:{patient.id}"),
            owner=Reference(reference=f"urn:uuid:{payer.id}"),
            requester=Reference(reference=f"urn:uuid:{provider.id}"),
        )
        if recorder:
            recorder.record_inferred(
                task_id,
                "intent",
                "Every Task this app builds from a 276/277 transaction set has intent=\"order\" - a claim-status check is fundamentally a tracked order/request, not read from any X12 field.",
                "order",
            )
            recorder.record_inferred(
                task_id,
                "code.text",
                'Task.code is a fixed, disclosed literal ("Claim Status") for every Task this app builds from a 276/277 transaction set - not read from any X12 field.',
                "Claim Status",
            )
        if authored_on:
            task.authoredOn = authored_on
            if recorder:
                recorder.record(
                    task_id, "authoredOn", f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}", authored_on
                )

        trace_number = element(trn, 2)
        if trace_number:
            task.identifier = [Identifier(system=TRACE_NUMBER_SYSTEM, value=trace_number)]
            if recorder:
                recorder.record(task_id, "identifier[0].value", edi_location("TRN", 2), trace_number)

        if include_status:
            stc = find_segment(members, "STC")
            task.status = _resolve_task_status(stc, delimiters, resource_id=task_id, recorder=recorder)
            if stc is not None:
                status_concept = _build_status_concept(stc, delimiters, resource_id=task_id, recorder=recorder)
                if status_concept is not None:
                    task.businessStatus = status_concept
        else:
            if recorder:
                recorder.record_inferred(
                    task_id,
                    "status",
                    '276 requests carry no STC segment at all (STC is response-only, per this module\'s own docstring) - Task.status always starts, and stays, "requested".',
                    "requested",
                )

        tasks.append(task)
    return tasks


class _BaseClaimStatusBuilder(EdiTransactionBuilder):
    """Shared build_bundle for 276/277 - both walk the identical
    2000A-2000E HL chain and TRN-led claim-status groups, differing only
    in whether STC/businessStatus is populated (277 only). The v2-to-FHIR-
    style Base*+subclass pattern fits here (unlike 270/271's two sibling
    files), since - unlike CoverageEligibilityRequest vs. Response -
    both transaction sets target the identical FHIR resource (Task) and
    differ by exactly one toggle, not by target-resource shape."""

    include_status: bool

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        bht = find_segment(transaction_set.segments, "BHT")
        if bht is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BHT segment")

        loops = resolve_claim_status_loops(transaction_set.segments, self.transaction_set_id)

        payer = build_organization_from_nm1(loops.payer_nm1, recorder=recorder)
        receiver: Resource = (
            build_practitioner_from_nm1(loops.receiver_nm1, recorder=recorder)
            if is_person_entity(loops.receiver_nm1)
            else build_organization_from_nm1(loops.receiver_nm1, recorder=recorder)
        )
        provider: Resource = (
            build_practitioner_from_nm1(loops.provider_nm1, recorder=recorder)
            if is_person_entity(loops.provider_nm1)
            else build_organization_from_nm1(loops.provider_nm1, recorder=recorder)
        )
        subscriber_dmg = find_segment(loops.subscriber_loop.member_segments, "DMG")
        subscriber = build_patient_from_nm1_dmg(loops.subscriber_nm1, subscriber_dmg, recorder=recorder)

        authored_on = parse_x12_datetime(element(bht, 4), element(bht, 5))

        resources: list[Resource] = [payer, receiver, provider, subscriber]
        tasks = _build_tasks_for_patient_loop(
            loops.subscriber_loop,
            subscriber,
            payer,
            provider,
            delimiters,
            self.include_status,
            authored_on,
            recorder=recorder,
        )

        if loops.dependent_loop is not None:
            # resolve_claim_status_loops already guarantees a resolvable
            # NM1 whenever it returns a non-None dependent_loop.
            dependent_nm1 = find_segment(loops.dependent_loop.member_segments, "NM1")
            dependent_dmg = find_segment(loops.dependent_loop.member_segments, "DMG")
            dependent = build_patient_from_nm1_dmg(dependent_nm1, dependent_dmg, recorder=recorder)
            resources.append(dependent)
            tasks.extend(
                _build_tasks_for_patient_loop(
                    loops.dependent_loop,
                    dependent,
                    payer,
                    provider,
                    delimiters,
                    self.include_status,
                    authored_on,
                    recorder=recorder,
                )
            )

        if not tasks:
            raise MissingSegmentError(
                f"{self.transaction_set_id} transaction set has no TRN-led claim-status entry to report"
            )

        resources.extend(tasks)
        return assemble_bundle(bht, *resources, recorder=recorder)


class Edi276Builder(_BaseClaimStatusBuilder):
    transaction_set_id = "276"
    include_status = False


class Edi277Builder(_BaseClaimStatusBuilder):
    transaction_set_id = "277"
    include_status = True
