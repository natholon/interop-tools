"""X12 278 plausibility rules - split out of app/edi/validation.py, the
same file-per-family reorganization eligibility_validation.py describes.
`app/edi/validation.py`'s `validate_interchange` calls `validate_278`
below."""

from app.edi.parser import TransactionSet, element, find_segment
from app.edi.prior_auth import BHT02_RESPONSE, HCR01_TO_OUTCOME, ResolvedPriorAuthLoops, resolve_prior_auth_loops
from app.hl7.errors import MissingSegmentError
from app.validation.models import ValidationFinding


def _find_prior_auth_loops(transaction_set: TransactionSet) -> ResolvedPriorAuthLoops | None:
    """Delegates to app.edi.prior_auth.resolve_prior_auth_loops - the one
    real "which loops are the patient/patient-event loops" implementation,
    shared with the real 278 builder. Returns None when the loops aren't
    strictly resolvable - already surfaced separately via
    edi.would-not-convert."""
    try:
        return resolve_prior_auth_loops(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None


def _rule_278_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    loops = _find_prior_auth_loops(transaction_set)
    if loops is None:
        return []
    if not element(loops.subscriber_nm1, 3) and not element(loops.subscriber_nm1, 4):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.278-missing-subscriber-name",
                segment="2000C/NM1",
                message="The subscriber's NM1 segment has no resolvable name - the converter will still build a Patient, just with no HumanName.",
            )
        ]
    return []


def _is_prior_auth_response(transaction_set: TransactionSet) -> bool:
    bht = find_segment(transaction_set.segments, "BHT")
    return bht is not None and element(bht, 2).strip() == BHT02_RESPONSE


def _rule_278_response_missing_hcr(transaction_set: TransactionSet) -> list[ValidationFinding]:
    if not _is_prior_auth_response(transaction_set):
        return []
    loops = _find_prior_auth_loops(transaction_set)
    if loops is None:
        return []
    if find_segment(loops.patient_event_loop.member_segments, "HCR") is None:
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.278-response-missing-hcr",
                segment="2000E/HCR",
                message="BHT02 marks this as a response, but the 2000E Patient Event loop has no HCR segment - the converter will build a Claim but no ClaimResponse.",
            )
        ]
    return []


def _rule_278_unrecognized_hcr_action_code(transaction_set: TransactionSet) -> list[ValidationFinding]:
    if not _is_prior_auth_response(transaction_set):
        return []
    loops = _find_prior_auth_loops(transaction_set)
    if loops is None:
        return []
    hcr = find_segment(loops.patient_event_loop.member_segments, "HCR")
    if hcr is None:
        return []
    # Normalized the same way app.edi.prior_auth::_build_claim_response
    # normalizes this same field, so this rule can never disagree with
    # what the real builder actually resolves.
    action_code = element(hcr, 1).strip().upper()
    if action_code and action_code not in HCR01_TO_OUTCOME:
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.278-unrecognized-hcr-action-code",
                segment="2000E/HCR",
                message=f"HCR01 action code {action_code!r} is not recognized - the converter will default this ClaimResponse's outcome to 'complete'.",
            )
        ]
    return []


def validate_278(transaction_set: TransactionSet) -> list[ValidationFinding]:
    # The response-only rules check BHT02 internally rather than needing a
    # separate dispatch branch, since - unlike every other EDI pair - 278
    # has only one ST01 value in the first place (see prior_auth.py's own
    # module docstring).
    return (
        _rule_278_missing_subscriber_name(transaction_set)
        + _rule_278_response_missing_hcr(transaction_set)
        + _rule_278_unrecognized_hcr_action_code(transaction_set)
    )
