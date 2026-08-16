"""X12 835 plausibility rules - split out of app/edi/validation.py, the
same file-per-family reorganization eligibility_validation.py describes.
Unlike every other family's own validation module, these rules don't need
a `_find_*_loops` delegation helper - 835 has no HL hierarchy/loop resolver
to delegate to in the first place (see remittance_835.py's own module
docstring), so they walk `transaction_set.segments` directly, the same flat
style `Edi835Builder.build_bundle()` itself uses. `app/edi/validation.py`'s
`validate_interchange` calls `validate_835` below."""

from datetime import datetime

from app.edi.parser import Segment, TransactionSet, element, find_segment, parse_decimal
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding


def _rule_835_bpr02_total_mismatch(transaction_set: TransactionSet) -> list[ValidationFinding]:
    bpr = find_segment(transaction_set.segments, "BPR")
    if bpr is None:
        return []
    declared = parse_decimal(element(bpr, 2))
    if declared is None:
        return []
    claim_amounts = [parse_decimal(element(seg, 4)) for seg in transaction_set.segments if seg[0] == "CLP"]
    if not claim_amounts or any(amount is None for amount in claim_amounts):
        return []
    actual = sum(claim_amounts)
    if declared != actual:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="edi.835-bpr02-total-mismatch",
                segment="BPR",
                message=f"BPR02 declares a total payment of {declared}, but the CLP04 paid amounts across all claims sum to {actual}.",
            )
        ]
    return []


def _rule_835_bpr16_in_future(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    bpr = find_segment(transaction_set.segments, "BPR")
    if bpr is None:
        return []
    raw_date = element(bpr, 16)
    if not raw_date:
        return []
    if not_in_future(raw_date, now) is False:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="edi.835-bpr16-in-future",
                segment="BPR",
                message="The payment effective date (BPR16) is in the future.",
            )
        ]
    return []


def _find_n1_by_entity_code(transaction_set: TransactionSet, entity_code: str) -> Segment | None:
    return next((seg for seg in transaction_set.segments if seg[0] == "N1" and element(seg, 1) == entity_code), None)


def _rule_835_missing_payer_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    payer_n1 = _find_n1_by_entity_code(transaction_set, "PR")
    if payer_n1 is None:
        return []
    if not element(payer_n1, 2):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.835-missing-payer-name",
                segment="1000A/N1",
                message="The payer's N1 segment has no resolvable name (N102) - the converter will still build an Organization, just with no name.",
            )
        ]
    return []


def _rule_835_missing_payee_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    payee_n1 = _find_n1_by_entity_code(transaction_set, "PE")
    if payee_n1 is None:
        return []
    if not element(payee_n1, 2):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.835-missing-payee-name",
                segment="1000B/N1",
                message="The payee's N1 segment has no resolvable name (N102) - the converter will still build an Organization, just with no name.",
            )
        ]
    return []


def _rule_835_claim_paid_exceeds_charge(transaction_set: TransactionSet) -> list[ValidationFinding]:
    for seg in transaction_set.segments:
        if seg[0] != "CLP":
            continue
        charge = parse_decimal(element(seg, 3))
        paid = parse_decimal(element(seg, 4))
        if charge is None or paid is None:
            continue
        if paid > charge:
            return [
                ValidationFinding(
                    severity="warning",
                    rule_id="edi.835-claim-paid-exceeds-charge",
                    segment="2100/CLP",
                    message=f"CLP04 (paid amount, {paid}) exceeds CLP03 (submitted charge, {charge}) for claim {element(seg, 1)!r}.",
                )
            ]
    return []


def validate_835(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    return (
        _rule_835_bpr02_total_mismatch(transaction_set)
        + _rule_835_bpr16_in_future(transaction_set, now)
        + _rule_835_missing_payer_name(transaction_set)
        + _rule_835_missing_payee_name(transaction_set)
        + _rule_835_claim_paid_exceeds_charge(transaction_set)
    )
