"""X12 270/271 plausibility rules - split out of app/edi/validation.py so
each EDI transaction-set family's own rule set lives alongside its sibling
builder/generator files (eligibility_270.py/271.py, and this module),
mirroring the file-per-family split every other phase already established.
`app/edi/validation.py` itself keeps only structural (whole-interchange)
rules, `_check_convertibility`, and the `validate_interchange` orchestrator
that calls `validate_270`/`validate_271` below."""

from datetime import datetime

from app.edi.common import build_missing_subscriber_name_finding, resolve_eligibility_parties
from app.edi.parser import Segment, TransactionSet, element, group_by_leader
from app.hl7.errors import MissingSegmentError
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding


def _find_patient_loop_members(transaction_set: TransactionSet) -> list[Segment] | None:
    """Delegates to app.edi.common.resolve_eligibility_parties - the one
    real "which loop is the patient" implementation eligibility_270.py/
    271.py's own build_bundle() also calls, so validation can never see a
    different entry set than conversion would produce (a code review caught
    that an earlier, independently-re-derived version of this walk had
    already drifted from the builders' actual algorithm). Returns None when
    the loops aren't strictly resolvable - the same case conversion itself
    would raise MissingSegmentError for, already surfaced separately via
    _check_convertibility's edi.would-not-convert finding, so this just
    skips rather than duplicating that error."""
    try:
        parties = resolve_eligibility_parties(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return None
    return parties.patient_loop_members


def _rule_270_missing_subscriber_name(transaction_set: TransactionSet) -> list[ValidationFinding]:
    try:
        parties = resolve_eligibility_parties(transaction_set.segments, transaction_set.st01)
    except MissingSegmentError:
        return []
    finding = build_missing_subscriber_name_finding("edi.270-missing-subscriber-name", "2000C/NM1", parties.subscriber_nm1)
    return [finding] if finding else []


def _rule_270_serviced_date_in_future(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    members = _find_patient_loop_members(transaction_set)
    if not members:
        return []
    for eq, eq_members in group_by_leader(members, "EQ", ["REF", "DTP", "MSG"]):
        for dtp in (m for m in eq_members if m[0] == "DTP"):
            if element(dtp, 2) != "D8":
                continue
            raw_date = element(dtp, 3)
            if not raw_date:
                continue
            if not_in_future(raw_date, now) is False:
                return [
                    ValidationFinding(
                        severity="warning",
                        rule_id="edi.eligibility-servicedate-in-future",
                        segment="2110C/DTP",
                        message="The service date (DTP after EQ) is in the future.",
                    )
                ]
    return []


def _rule_271_no_service_type(transaction_set: TransactionSet) -> list[ValidationFinding]:
    members = _find_patient_loop_members(transaction_set)
    if members is None:
        return []
    eb_groups = group_by_leader(members, "EB", ["REF", "DTP", "MSG"])
    if not eb_groups:
        return []
    if all(not element(eb, 3) for eb, _members in eb_groups):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.271-no-service-type-in-any-eb",
                segment="2110C/EB",
                message="No EB segment has a resolvable Service Type Code (EB03) - the converter will build insurance.item entries with no category.",
            )
        ]
    return []


def validate_270(transaction_set: TransactionSet, now: datetime) -> list[ValidationFinding]:
    return _rule_270_missing_subscriber_name(transaction_set) + _rule_270_serviced_date_in_future(transaction_set, now)


def validate_271(transaction_set: TransactionSet) -> list[ValidationFinding]:
    return _rule_271_no_service_type(transaction_set)
