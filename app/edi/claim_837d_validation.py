"""X12 837D plausibility rules - the app/edi/ mirror of
claim_837i_validation.py, following the same file-per-family layout
app/edi/validation.py's own docstring describes. `app/edi/validation.py`'s
`validate_interchange` calls `validate_837d` below."""

from datetime import datetime

from app.edi.claim_837d import (
    Resolved837dLoops,
    find_claim_level_segments,
    find_dtp_by_qualifier,
    resolve_837d_loops,
    resolve_line_dtp_raw_date,
)
from app.edi.common import iter_diagnosis_hi_segments
from app.edi.parser import Delimiters, Segment, TransactionSet, element, find_segment, group_by_leader
from app.hl7.errors import MissingSegmentError
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding

_DTP_SERVICE_DATE = "472"


def _find_837d_loops(transaction_set: TransactionSet) -> Resolved837dLoops | None:
    """Delegates to app.edi.claim_837d.resolve_837d_loops - the one real
    "which loops are which, and where does claim data live" implementation,
    shared with the real 837D builder. Returns None when the loops aren't
    strictly resolvable - already surfaced separately via
    edi.would-not-convert."""
    try:
        return resolve_837d_loops(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None


def _rule_837d_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    if not element(loops.subscriber_nm1, 3) and not element(loops.subscriber_nm1, 4):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.837d-missing-subscriber-name",
                segment="2000B/NM1",
                message="The subscriber's NM1 segment has no resolvable name - the converter will still build a Patient, just with no HumanName.",
            )
        ]
    return []


def _rule_837d_missing_diagnosis(transaction_set: TransactionSet, delimiters: Delimiters) -> list[ValidationFinding]:
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    if not iter_diagnosis_hi_segments(loops.claim_loop.member_segments, delimiters):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.837d-missing-diagnosis",
                segment="2300/HI",
                message="This claim has no diagnosis-qualified HI segment - the converter will build a Claim with no diagnosis entries. Dental claims commonly omit diagnosis codes entirely, so this is informational only.",
            )
        ]
    return []


def _iter_837d_service_lines(transaction_set: TransactionSet) -> list[tuple[Segment, list[Segment]]]:
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    return group_by_leader(loops.claim_loop.member_segments, "LX", ["SV3", "TOO", "DTP"])


def _rule_837d_service_date_in_future(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    claim_level_dtp = find_dtp_by_qualifier(find_claim_level_segments(loops.claim_loop.member_segments), _DTP_SERVICE_DATE)
    for _lx, members in _iter_837d_service_lines(transaction_set):
        dtp = find_dtp_by_qualifier(members, _DTP_SERVICE_DATE)
        raw_date = resolve_line_dtp_raw_date(dtp, claim_level_dtp)
        if not raw_date:
            continue
        if not_in_future(raw_date, now) is False:
            return [
                ValidationFinding(
                    severity="warning",
                    rule_id="edi.837d-service-date-in-future",
                    segment="2400/DTP",
                    message="A service line's date (DTP after LX, or the claim-level DTP*472 default when the line has none of its own) is in the future.",
                )
            ]
    return []


def validate_837d(transaction_set: TransactionSet, delimiters: Delimiters, now: datetime) -> list[ValidationFinding]:
    return (
        _rule_837d_missing_subscriber_name(transaction_set)
        + _rule_837d_missing_diagnosis(transaction_set, delimiters)
        + _rule_837d_service_date_in_future(transaction_set, now)
    )
