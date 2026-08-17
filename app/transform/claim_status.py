"""FHIR Bundle -> X12 276 (Health Care Claim Status Request) / 277 (Health
Care Claim Status Response) - the eleventh reverse-direction slice, and
the first proof this app's reverse direction generalizes to a THIRD X12
family beyond the 270/271 sibling pair, with a genuinely deeper loop
shape: a five-level HL chain (270/271's own is four levels), and a target
FHIR resource (`Task`) that can carry claim-status entries for BOTH the
subscriber and a dependent within one transaction set, mirroring the
forward direction's own identical structural break from 270/271's single
"one patient per transaction" rule.

Reverses `app/edi/claim_status.py::_build_tasks_for_patient_loop`/
`_build_status_concept`/`_resolve_task_status` field-for-field: one `TRN`
(+ `STC` for 277 only) group per `Task`, built from `Task.identifier`
(trace number) and `Task.businessStatus`/`.status`.

**Two genuinely new cross-cutting resolution problems this slice needed,
neither of which 270/271's own shared resolvers cover**:
- Payer/provider are recovered directly from any `Task`'s own `.owner`/
  `.requester` references - every `Task` this app's forward mapper builds
  carries both, a more direct resolution than 270/271's own Bundle-order
  fallback (`resolve_payer_and_provider`), since the reference is always
  present here.
- The Information Receiver (2000B) has **no FHIR-side reference pointing
  to it at all** - unlike payer/provider, no `Task` field carries it.
  Resolved by exclusion instead: the remaining `Organization`/
  `Practitioner` in the Bundle that's neither the resolved payer nor the
  resolved provider - the same "the one that's left" spirit as 270/271's
  own provider fallback in `resolve_payer_and_provider`, applied here to
  a resource that's never directly referenced from anywhere on the FHIR
  side at all, not just as a fallback when a reference is absent.
- Subscriber vs. dependent `Patient` has no `Coverage` resource to
  disambiguate via - this family never builds one, unlike 270/271/278/
  837P/837I - so it's resolved via Bundle order instead (the forward
  mapper's own append order always puts the subscriber `Patient` before
  an optional dependent `Patient`), the same disclosed fallback
  `resolve_subscriber_and_dependent` itself already falls back to when no
  `Coverage` is present at all.

**Disclosed round-trip fidelity gaps**: `STC02` (status date) and every
`REF`/`DTP` in the claim-status group have no FHIR-side home at all - the
forward mapper's own `group_by_leader` walk captures them as group
members but never reads any of them into a `Task` field (only `STC01`
is read) - so none are regenerated here, the same "no source field,
disclosed omission" precedent every earlier EDI reverse slice already
established for its own unmapped `REF` segments."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.claim_status import STC_CATEGORY_SYSTEM, STC_STATUS_SYSTEM, STC_CATEGORY_PREFIX_TO_TASK_STATUS
from app.edi.generator import format_x12_date, format_x12_time
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resources
from app.transform.edi_common import (
    DEFAULT_ST_CONTROL,
    build_dmg,
    build_envelope_segments,
    build_org_nm1,
    build_person_nm1,
    build_trailer_segments,
    envelope_datetime,
    sanitize_x12_text,
)

# Reverse of app.edi.claim_status.STC_CATEGORY_PREFIX_TO_TASK_STATUS -
# genuinely many-to-one (both "E" and "D" map to Task.status="failed"),
# reversed via one disclosed representative category-code-prefix per
# target status - "E" for "failed" (an exact-match candidate, preferred
# over "D") - rather than whatever a naive dict-comprehension inversion's
# key ordering would pick arbitrarily, the same deliberate-disclosure
# discipline every other many-to-one status reversal in this app follows.
_TASK_STATUS_TO_STC_CATEGORY_CODE = {
    "received": "A1",
    "in-progress": "P1",
    "completed": "F1",
    "on-hold": "R1",
    "failed": "E1",
}
_DEFAULT_STC_CATEGORY_CODE = "A1"

assert set(STC_CATEGORY_PREFIX_TO_TASK_STATUS.values()) == set(_TASK_STATUS_TO_STC_CATEGORY_CODE)


def _resolve_stc_composite(task) -> tuple[str, str]:
    """Reverse of `_build_status_concept`/`_resolve_task_status`:
    `Task.businessStatus`'s own coding (when present) carries the real,
    original `STC01-1`/`STC01-2` pair the forward side actually saw - used
    directly rather than re-derived from `Task.status` alone, which would
    lose precision within a category (e.g. `businessStatus` might carry
    the specific `A3` "Rejected" code, not just the `A` prefix `Task.status`
    itself was derived from). Falls back to a disclosed representative
    category code (no `STC01-2` at all) only when `businessStatus` itself
    is absent - the 276 request side, which never carries one."""
    category_code = ""
    status_code = ""
    if task.businessStatus and task.businessStatus.coding:
        for coding in task.businessStatus.coding:
            if coding.system == STC_CATEGORY_SYSTEM and coding.code:
                category_code = coding.code
            elif coding.system == STC_STATUS_SYSTEM and coding.code:
                status_code = coding.code
    if not category_code:
        category_code = _TASK_STATUS_TO_STC_CATEGORY_CODE.get(task.status, _DEFAULT_STC_CATEGORY_CODE)
    return category_code, status_code


def _resolve_payer_and_provider(bundle: Bundle, tasks: list):
    by_id = {r.id: r for r in find_resources(bundle, "Organization") + find_resources(bundle, "Practitioner")}
    first_task = tasks[0] if tasks else None
    payer = by_id.get(first_task.owner.reference.removeprefix("urn:uuid:")) if first_task and first_task.owner else None
    provider = (
        by_id.get(first_task.requester.reference.removeprefix("urn:uuid:"))
        if first_task and first_task.requester
        else None
    )
    return payer, provider


def _resolve_receiver(bundle: Bundle, payer, provider):
    excluded_ids = {r.id for r in (payer, provider) if r is not None}
    candidates = [
        r
        for r in find_resources(bundle, "Organization") + find_resources(bundle, "Practitioner")
        if r.id not in excluded_ids
    ]
    return candidates[0] if candidates else None


def _resolve_subscriber_and_dependent(bundle: Bundle):
    patients = find_resources(bundle, "Patient")
    if not patients:
        return None, None
    return patients[0], (patients[1] if len(patients) > 1 else None)


def _build_trn_group(task, include_status: bool) -> list[str]:
    trace_number = task.identifier[0].value if task.identifier else "0000000000"
    trn02 = "2" if include_status else "1"
    segments = [f"TRN*{trn02}*{trace_number}*1512345678~"]
    if include_status:
        category_code, status_code = _resolve_stc_composite(task)
        segments.append(f"STC*{category_code}:{status_code}:PR~")
    return segments


def _build_patient_loop_segments(patient, tasks: list, include_status: bool, patient_role_code: str) -> list[str]:
    segments = [build_person_nm1(patient_role_code, patient, include_id=(patient_role_code == "IL"))]
    dmg = build_dmg(patient)
    if dmg:
        segments.append(dmg)
    for task in tasks:
        segments.extend(_build_trn_group(task, include_status))
    return segments


def _org_or_person_nm1(entity_code: str, resource) -> str:
    return (
        build_org_nm1(entity_code, resource)
        if resource.get_resource_type() == "Organization"
        else build_person_nm1(entity_code, resource, include_id=True)
    )


class _BaseClaimStatusBuilder(MessageBuilder):
    transaction_set_id: str
    bht02: str
    include_status: bool

    def build_message(self, bundle: Bundle) -> str:
        tasks = find_resources(bundle, "Task")
        if not tasks:
            raise MappingError(
                f"Bundle has no Task resource - cannot build a {self.transaction_set_id} message"
            )

        payer, provider = _resolve_payer_and_provider(bundle, tasks)
        if payer is None:
            raise MappingError(f"Bundle has no resolvable payer - cannot build a {self.transaction_set_id} message")
        if provider is None:
            raise MappingError(
                f"Bundle has no resolvable provider - cannot build a {self.transaction_set_id} message"
            )
        receiver = _resolve_receiver(bundle, payer, provider)
        if receiver is None:
            raise MappingError(
                f"Bundle has no resolvable information receiver - cannot build a {self.transaction_set_id} message"
            )

        subscriber, dependent = _resolve_subscriber_and_dependent(bundle)
        if subscriber is None:
            raise MappingError(f"Bundle has no Patient resource - cannot build a {self.transaction_set_id} message")

        subscriber_tasks = [
            t for t in tasks if t.for_fhir and t.for_fhir.reference.removeprefix("urn:uuid:") == subscriber.id
        ]
        dependent_tasks = (
            [t for t in tasks if t.for_fhir and t.for_fhir.reference.removeprefix("urn:uuid:") == dependent.id]
            if dependent is not None
            else []
        )

        now = envelope_datetime(bundle.timestamp)
        bht_reference = sanitize_x12_text(bundle.identifier.value) if bundle.identifier and bundle.identifier.value else "REF00000001"

        envelope_segments = build_envelope_segments(now)
        st_to_hl_segments = [
            f"ST*{self.transaction_set_id}*{DEFAULT_ST_CONTROL}~",
            f"BHT*0010*{self.bht02}*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
            "HL*1**20*1~",
            _org_or_person_nm1("PR", payer),
            "HL*2*1*21*1~",
            _org_or_person_nm1("41", receiver),
            "HL*3*2*19*1~",
            _org_or_person_nm1("1P", provider),
            f"HL*4*3*22*{1 if dependent else 0}~",
        ]
        st_to_hl_segments.extend(
            _build_patient_loop_segments(subscriber, subscriber_tasks, self.include_status, "IL")
        )
        if dependent is not None:
            st_to_hl_segments.append("HL*5*4*23*0~")
            st_to_hl_segments.extend(
                _build_patient_loop_segments(dependent, dependent_tasks, self.include_status, "QC")
            )

        trailer_segments = build_trailer_segments(st_to_hl_segments, [])
        return "".join(envelope_segments + st_to_hl_segments + trailer_segments)


class Edi276Builder(_BaseClaimStatusBuilder):
    transaction_set_id = "276"
    bht02 = "13"
    include_status = False


class Edi277Builder(_BaseClaimStatusBuilder):
    transaction_set_id = "277"
    bht02 = "08"
    include_status = True
