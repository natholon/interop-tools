"""X12 837P plausibility rules - split out of app/edi/validation.py, the
same file-per-family reorganization eligibility_validation.py describes.
`app/edi/validation.py`'s `validate_interchange` calls `validate_837p`
below."""

from datetime import datetime

from app.edi.common import (
    DTP_SERVICE_DATE,
    Resolved837Loops,
    build_diagnosis_codeable_concepts,
    build_missing_subscriber_name_finding,
    check_service_lines_for_future_date,
    find_dtp_by_qualifier,
    resolve_837_loops,
)
from app.edi.parser import Delimiters, Segment, TransactionSet, component, element, find_segment, group_by_leader
from app.hl7.errors import MissingSegmentError
from app.validation.models import ValidationFinding


def _find_837p_loops(transaction_set: TransactionSet) -> Resolved837Loops | None:
    """Delegates to app.edi.common.resolve_837_loops - the one real "which
    loops are which, and where does claim data live" implementation, shared
    with the real 837P builder (and, since the third-consumer promotion,
    837I's and 837D's own builders too). Returns None when the loops aren't
    strictly resolvable - already surfaced separately via
    edi.would-not-convert."""
    try:
        return resolve_837_loops(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None


def _rule_837p_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_837p_loops(transaction_set)
    if loops is None:
        return []
    finding = build_missing_subscriber_name_finding("edi.837p-missing-subscriber-name", "2000B/NM1", loops.subscriber_nm1)
    return [finding] if finding else []


def _rule_837p_missing_diagnosis(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_837p_loops(transaction_set)
    if loops is None:
        return []
    hi = find_segment(loops.claim_loop.member_segments, "HI")
    if hi is None:
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.837p-missing-diagnosis",
                segment="2300/HI",
                message="This claim has no HI (diagnosis) segment - the converter will build a Claim with no diagnosis entries.",
            )
        ]
    return []


def _iter_837p_service_lines(transaction_set: TransactionSet) -> list[tuple[Segment, list[Segment]]]:
    """Every LX-led 2400 service-line group within the claim loop, walked
    the identical way Edi837pBuilder.build_bundle() itself does, so a rule
    here can never see a different service-line set than conversion would."""
    loops = _find_837p_loops(transaction_set)
    if loops is None:
        return []
    return group_by_leader(loops.claim_loop.member_segments, "LX", ["SV1", "DTP", "PWK", "CRC"])


def _resolve_837p_raw_date(members: list[Segment]) -> str | None:
    dtp = find_dtp_by_qualifier(members, DTP_SERVICE_DATE)
    if dtp is None or element(dtp, 2) != "D8":
        return None
    return element(dtp, 3) or None


def _rule_837p_service_date_in_future(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    return check_service_lines_for_future_date(
        _iter_837p_service_lines(transaction_set),
        _resolve_837p_raw_date,
        "edi.837p-service-date-in-future",
        "2400/DTP",
        "A service line's date (DTP after LX) is in the future.",
        now,
    )


def _rule_837p_diagnosis_pointer_unresolved(transaction_set: TransactionSet, delimiters: Delimiters) -> list[ValidationFinding]:
    loops = _find_837p_loops(transaction_set)
    if loops is None:
        return []
    # Reuses the same diagnosis-resolution build_bundle() itself calls
    # (rather than re-deriving "how many diagnoses did HI resolve" here),
    # so this rule can never disagree with what SV1-07's pointers actually
    # resolve against on the conversion side.
    hi = find_segment(loops.claim_loop.member_segments, "HI")
    num_diagnoses = len(build_diagnosis_codeable_concepts(hi, delimiters))

    for _lx, members in _iter_837p_service_lines(transaction_set):
        sv1 = find_segment(members, "SV1")
        if sv1 is None:
            continue
        pointer_composite = element(sv1, 7)
        if not pointer_composite:
            continue
        for position in range(1, 5):
            raw = component(pointer_composite, delimiters, position)
            if not raw:
                break
            try:
                pointer = int(raw)
            except ValueError:
                continue
            if pointer < 1 or pointer > num_diagnoses:
                return [
                    ValidationFinding(
                        severity="info",
                        rule_id="edi.837p-diagnosis-pointer-unresolved",
                        segment="2400/SV1",
                        message=f"SV1-07 references diagnosis position {pointer}, which doesn't resolve to any HI entry on this claim ({num_diagnoses} found) - the converter will silently skip this pointer.",
                    )
                ]
    return []


def validate_837p(transaction_set: TransactionSet, delimiters: Delimiters, now: datetime) -> list[ValidationFinding]:
    return (
        _rule_837p_missing_subscriber_name(transaction_set)
        + _rule_837p_missing_diagnosis(transaction_set)
        + _rule_837p_service_date_in_future(transaction_set, now)
        + _rule_837p_diagnosis_pointer_unresolved(transaction_set, delimiters)
    )
