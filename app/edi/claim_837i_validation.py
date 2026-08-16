"""X12 837I plausibility rules - the app/edi/ mirror of
claim_837p_validation.py, following the same file-per-family layout
app/edi/validation.py's own docstring describes. `app/edi/validation.py`'s
`validate_interchange` calls `validate_837i` below."""

from datetime import datetime

from app.edi.claim_837i import DTP_SERVICE_DATE, Resolved837iLoops, find_dtp_by_qualifier, resolve_837i_loops
from app.edi.common import iter_diagnosis_hi_segments
from app.edi.parser import Delimiters, Segment, TransactionSet, element, find_segment, group_by_leader
from app.hl7.errors import MissingSegmentError
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding


def _find_837i_loops(transaction_set: TransactionSet) -> Resolved837iLoops | None:
    """Delegates to app.edi.claim_837i.resolve_837i_loops - the one real
    "which loops are which, and where does claim data live" implementation,
    shared with the real 837I builder. Returns None when the loops aren't
    strictly resolvable - already surfaced separately via
    edi.would-not-convert."""
    try:
        return resolve_837i_loops(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None


def _rule_837i_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_837i_loops(transaction_set)
    if loops is None:
        return []
    if not element(loops.subscriber_nm1, 3) and not element(loops.subscriber_nm1, 4):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.837i-missing-subscriber-name",
                segment="2000B/NM1",
                message="The subscriber's NM1 segment has no resolvable name - the converter will still build a Patient, just with no HumanName.",
            )
        ]
    return []


def _rule_837i_missing_diagnosis(transaction_set: TransactionSet, delimiters: Delimiters) -> list[ValidationFinding]:
    loops = _find_837i_loops(transaction_set)
    if loops is None:
        return []
    if not iter_diagnosis_hi_segments(loops.claim_loop.member_segments, delimiters):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.837i-missing-diagnosis",
                segment="2300/HI",
                message="This claim has no diagnosis-qualified HI segment - the converter will build a Claim with no diagnosis entries.",
            )
        ]
    return []


def _rule_837i_missing_discharge_status(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_837i_loops(transaction_set)
    if loops is None:
        return []
    cl1 = find_segment(loops.claim_loop.member_segments, "CL1")
    if cl1 is None or not element(cl1, 3):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.837i-missing-discharge-status",
                segment="2300/CL1",
                message="This claim has no resolvable CL103 (Patient Status Code) - the converter will build a Claim with no discharge-status supportingInfo entry.",
            )
        ]
    return []


def _iter_837i_service_lines(transaction_set: TransactionSet) -> list[tuple[Segment, list[Segment]]]:
    loops = _find_837i_loops(transaction_set)
    if loops is None:
        return []
    return group_by_leader(loops.claim_loop.member_segments, "LX", ["SV2", "DTP"])


def _rule_837i_service_date_in_future(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    for _lx, members in _iter_837i_service_lines(transaction_set):
        dtp = find_dtp_by_qualifier(members, DTP_SERVICE_DATE)
        if dtp is None or element(dtp, 2) != "D8":
            continue
        raw_date = element(dtp, 3)
        if not raw_date:
            continue
        if not_in_future(raw_date, now) is False:
            return [
                ValidationFinding(
                    severity="warning",
                    rule_id="edi.837i-service-date-in-future",
                    segment="2400/DTP",
                    message="A service line's date (DTP after LX) is in the future.",
                )
            ]
    return []


def validate_837i(transaction_set: TransactionSet, delimiters: Delimiters, now: datetime) -> list[ValidationFinding]:
    return (
        _rule_837i_missing_subscriber_name(transaction_set)
        + _rule_837i_missing_diagnosis(transaction_set, delimiters)
        + _rule_837i_missing_discharge_status(transaction_set)
        + _rule_837i_service_date_in_future(transaction_set, now)
    )
