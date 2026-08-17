"""X12 276/277 plausibility rules - split out of app/edi/validation.py, the
same file-per-family reorganization eligibility_validation.py describes.
`app/edi/validation.py`'s `validate_interchange` calls `validate_276`/
`validate_277` below."""

from datetime import datetime

from app.edi.claim_status import STC_CATEGORY_PREFIX_TO_TASK_STATUS, ResolvedClaimStatusLoops, resolve_claim_status_loops
from app.edi.common import build_missing_subscriber_name_finding
from app.edi.parser import Delimiters, Segment, TransactionSet, component, element, find_segment, group_by_leader
from app.hl7.errors import MissingSegmentError
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding


def _find_claim_status_loops(transaction_set: TransactionSet) -> ResolvedClaimStatusLoops | None:
    """Delegates to app.edi.claim_status.resolve_claim_status_loops - the
    one real "which loops are the patient loops, and do their NM1s
    resolve" implementation, shared with the real 276/277 builder so
    validation can never see a different loop/NM1 set than conversion
    would use. Returns None when the loops aren't strictly resolvable -
    already surfaced separately via edi.would-not-convert."""
    try:
        return resolve_claim_status_loops(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None


def _rule_276_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_claim_status_loops(transaction_set)
    if loops is None:
        return []
    # loops.subscriber_nm1 is guaranteed non-None by resolve_claim_status_
    # loops (which raises MissingSegmentError itself otherwise, already
    # caught above) - only the "present but blank name" case reaches here,
    # matching eligibility_validation.py's identical guarantee.
    finding = build_missing_subscriber_name_finding("edi.276-missing-subscriber-name", "2000D/NM1", loops.subscriber_nm1)
    return [finding] if finding else []


def _iter_claim_status_stc(transaction_set: TransactionSet) -> list[Segment]:
    """Every STC segment found within a TRN-led claim-status group across
    both the subscriber and (if present) dependent patient loops - walked
    the identical way app.edi.claim_status's own _build_tasks_for_patient_
    loop reads them, so a rule here can never see a different STC set than
    conversion would."""
    loops = _find_claim_status_loops(transaction_set)
    if loops is None:
        return []
    stc_segments = []
    for patient_loop in (loops.subscriber_loop, loops.dependent_loop):
        if patient_loop is None:
            continue
        for _trn, members in group_by_leader(patient_loop.member_segments, "TRN", ["REF", "STC", "DTP", "SVC"]):
            stc = find_segment(members, "STC")
            if stc is not None:
                stc_segments.append(stc)
    return stc_segments


def _rule_277_unrecognized_status_category(transaction_set: TransactionSet, delimiters: Delimiters) -> list[ValidationFinding]:
    for stc in _iter_claim_status_stc(transaction_set):
        category_code = component(element(stc, 1), delimiters, 1)
        prefix = category_code[:1].upper() if category_code else ""
        if prefix not in STC_CATEGORY_PREFIX_TO_TASK_STATUS:
            return [
                ValidationFinding(
                    severity="info",
                    rule_id="edi.277-unrecognized-status-category",
                    segment="2200D/STC",
                    message=f"STC01-1 category code {category_code!r} is not recognized - the converter will default this claim's Task.status to 'completed'.",
                )
            ]
    return []


def _rule_277_status_date_in_future(
    transaction_set: TransactionSet, delimiters: Delimiters, now: datetime
) -> list[ValidationFinding]:
    for stc in _iter_claim_status_stc(transaction_set):
        raw_date = element(stc, 2)
        if not raw_date:
            continue
        if not_in_future(raw_date, now) is False:
            return [
                ValidationFinding(
                    severity="warning",
                    rule_id="edi.277-status-date-in-future",
                    segment="2200D/STC",
                    message="The claim status effective date (STC02) is in the future.",
                )
            ]
    return []


def validate_276(transaction_set: TransactionSet) -> list[ValidationFinding]:
    return _rule_276_missing_subscriber_name(transaction_set)


def validate_277(transaction_set: TransactionSet, delimiters: Delimiters, now: datetime) -> list[ValidationFinding]:
    return _rule_277_unrecognized_status_category(transaction_set, delimiters) + _rule_277_status_date_in_future(
        transaction_set, delimiters, now
    )
