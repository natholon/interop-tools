"""X12 837D plausibility rules - the app/edi/ mirror of
claim_837i_validation.py, following the same file-per-family layout
app/edi/validation.py's own docstring describes. `app/edi/validation.py`'s
`validate_interchange` calls `validate_837d` below."""

from datetime import datetime

from app.edi.claim_837d import find_claim_level_segments, resolve_line_dtp_raw_date
from app.edi.common import (
    DTP_SERVICE_DATE,
    Resolved837Loops,
    build_diagnosis_codeable_concepts,
    build_missing_subscriber_name_finding,
    check_service_lines_for_future_date,
    find_dtp_by_qualifier,
    iter_diagnosis_hi_segments,
    resolve_837_loops,
)
from app.edi.parser import Delimiters, Segment, TransactionSet, component, element, group_by_leader
from app.hl7.errors import MissingSegmentError
from app.validation.models import ValidationFinding

_MAX_DIAGNOSIS_POINTERS = 4  # mirrors claim_837d.py's own SV3-11 cap


def _find_837d_loops(transaction_set: TransactionSet) -> Resolved837Loops | None:
    """Delegates to app.edi.common.resolve_837_loops - the one real
    "which loops are which, and where does claim data live" implementation,
    shared with the real 837D builder (and, since the third-consumer
    promotion, 837P's and 837I's own builders too). Returns None when the
    loops aren't strictly resolvable - already surfaced separately via
    edi.would-not-convert."""
    try:
        return resolve_837_loops(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None


def _rule_837d_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    finding = build_missing_subscriber_name_finding("edi.837d-missing-subscriber-name", "2000B/NM1", loops.subscriber_nm1)
    return [finding] if finding else []


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


def _rule_837d_service_date_in_future(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    # Resolved once here and threaded directly into group_by_leader, rather
    # than also calling a separate _iter_837d_service_lines() helper that
    # would re-resolve the same loop hierarchy a second time - 837D's own
    # claim-level DTP fallback needs `loops` for its own lookup anyway, so
    # there's no reason to pay for group_by_hl_hierarchy's O(segments) walk
    # twice per rule call the way an earlier version of this rule did.
    claim_level_dtp = find_dtp_by_qualifier(
        find_claim_level_segments(loops.claim_loop.member_segments), DTP_SERVICE_DATE
    )
    service_lines = group_by_leader(loops.claim_loop.member_segments, "LX", ["SV3", "TOO", "DTP"])

    def _resolve_raw_date(members: list[Segment]) -> str | None:
        dtp = find_dtp_by_qualifier(members, DTP_SERVICE_DATE)
        return resolve_line_dtp_raw_date(dtp, claim_level_dtp)

    return check_service_lines_for_future_date(
        service_lines,
        _resolve_raw_date,
        "edi.837d-service-date-in-future",
        "2400/DTP",
        "A service line's date (DTP after LX, or the claim-level DTP*472 default when the line has none of its own) is in the future.",
        now,
    )


def _rule_837d_diagnosis_pointer_unresolved(transaction_set: TransactionSet, delimiters: Delimiters) -> list[ValidationFinding]:
    """Mirrors claim_837p_validation.py's own
    edi.837p-diagnosis-pointer-unresolved rule for 837D's own SV3-11
    composite - 837D's _build_diagnosis_pointers() silently drops an
    out-of-range/unresolvable pointer exactly the way 837P's SV1-07
    handling does, but until this rule was added, 837D had no matching
    validation finding for it even though claim_837d_generator.py's own
    fuzz coverage already assumed one existed (see
    tests/test_generate_claim_837d.py::
    test_diagnosis_pointer_out_of_range_occurs_and_is_the_minority)."""
    loops = _find_837d_loops(transaction_set)
    if loops is None:
        return []
    # Reuses the same diagnosis-resolution build_bundle() itself calls
    # (rather than re-deriving "how many diagnoses did HI resolve" here),
    # so this rule can never disagree with what SV3-11's pointers actually
    # resolve against on the conversion side.
    num_diagnoses = 0
    for hi in iter_diagnosis_hi_segments(loops.claim_loop.member_segments, delimiters):
        num_diagnoses += len(build_diagnosis_codeable_concepts(hi, delimiters))

    for _lx, members in group_by_leader(loops.claim_loop.member_segments, "LX", ["SV3", "TOO", "DTP"]):
        sv3 = next((seg for seg in members if seg[0] == "SV3"), None)
        if sv3 is None:
            continue
        pointer_composite = element(sv3, 11)
        if not pointer_composite:
            continue
        for position in range(1, _MAX_DIAGNOSIS_POINTERS + 1):
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
                        rule_id="edi.837d-diagnosis-pointer-unresolved",
                        segment="2400/SV3",
                        message=f"SV3-11 references diagnosis position {pointer}, which doesn't resolve to any HI entry on this claim ({num_diagnoses} found) - the converter will silently skip this pointer.",
                    )
                ]
    return []


def validate_837d(transaction_set: TransactionSet, delimiters: Delimiters, now: datetime) -> list[ValidationFinding]:
    return (
        _rule_837d_missing_subscriber_name(transaction_set)
        + _rule_837d_missing_diagnosis(transaction_set, delimiters)
        + _rule_837d_service_date_in_future(transaction_set, now)
        + _rule_837d_diagnosis_pointer_unresolved(transaction_set, delimiters)
    )
