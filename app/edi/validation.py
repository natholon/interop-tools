"""X12 EDI validation - the app/edi/ mirror of app/cda/validation.py /
app/validation/{generic,adt,engine}.py, independent of conversion, never
raising for anything a real X12 sender could plausibly produce.

Reuses app.validation.models.ValidationFinding/ValidationReport as-is (a
finding uses a path-like string for `segment`, e.g. "2000C/NM1"; `field`
left None) and app.validation.common.not_in_future directly - it operates
on raw HL7-TS-shaped strings, and X12's D8-qualified date elements
(CCYYMMDD) use the identical digit shape (see app/edi/common.py::
parse_x12_datetime for the established precedent of this exact reuse).

Structural rules apply to the whole interchange (its own internal
arithmetic doesn't depend on which transaction set gets converted);
270/271-specific plausibility rules apply only to first_transaction_set()
- the one Phase 1 actually converts (see app/edi/parser.py::
first_transaction_set's own disclosed batching scope limit)."""

import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from app.edi.claim_status import STC_CATEGORY_PREFIX_TO_TASK_STATUS, ResolvedClaimStatusLoops, resolve_claim_status_loops
from app.edi.common import resolve_eligibility_parties
from app.edi.prior_auth import BHT02_RESPONSE, HCR01_TO_OUTCOME, ResolvedPriorAuthLoops, resolve_prior_auth_loops
from app.edi.parser import (
    Delimiters,
    Interchange,
    Segment,
    TransactionSet,
    component,
    element,
    find_segment,
    first_transaction_set,
    group_by_leader,
    parse_decimal,
)
from app.hl7.errors import MappingError, MissingSegmentError
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding, ValidationReport

logger = logging.getLogger(__name__)


def _int_or_none(raw: str) -> int | None:
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _rule_isa_iea_count(interchange: Interchange) -> list[ValidationFinding]:
    declared = _int_or_none(element(interchange.iea, 1))
    actual = len(interchange.functional_groups)
    if declared is not None and declared != actual:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="edi.isa-iea-count-mismatch",
                segment="IEA",
                message=f"IEA01 declares {declared} functional group(s), but {actual} were found.",
            )
        ]
    return []


def _rule_isa_usage_indicator(interchange: Interchange) -> list[ValidationFinding]:
    if element(interchange.isa, 15).strip().upper() == "T":
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.isa-usage-indicator-test",
                segment="ISA",
                message="ISA15 marks this interchange as test data (not production).",
            )
        ]
    return []


def _rule_gs_ge_count(interchange: Interchange) -> list[ValidationFinding]:
    findings = []
    for functional_group in interchange.functional_groups:
        declared = _int_or_none(element(functional_group.ge, 1)) if functional_group.ge is not None else None
        actual = len(functional_group.transaction_sets)
        if declared is not None and declared != actual:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    rule_id="edi.gs-ge-count-mismatch",
                    segment="GE",
                    message=f"GE01 declares {declared} transaction set(s), but {actual} were found in this functional group.",
                )
            )
    return findings


def _rule_st_se_count(interchange: Interchange) -> list[ValidationFinding]:
    findings = []
    for functional_group in interchange.functional_groups:
        for transaction_set in functional_group.transaction_sets:
            if transaction_set.se is None:
                continue
            declared = _int_or_none(element(transaction_set.se, 1))
            # SE01 counts every segment from ST through SE inclusive - the
            # parser retains only the segments *between* them, so the
            # actual count is that plus the two boundary segments.
            actual = len(transaction_set.segments) + 2
            if declared is not None and declared != actual:
                findings.append(
                    ValidationFinding(
                        severity="warning",
                        rule_id="edi.st-se-count-mismatch",
                        segment="SE",
                        message=f"SE01 declares {declared} segment(s) (ST through SE inclusive), but {actual} were found.",
                    )
                )
    return findings


def _rule_hl_parent_not_found(transaction_set: TransactionSet) -> list[ValidationFinding]:
    findings = []
    known_ids = {element(seg, 1) for seg in transaction_set.segments if seg[0] == "HL"}
    for seg in transaction_set.segments:
        if seg[0] != "HL":
            continue
        hl02 = element(seg, 2)
        if hl02 and hl02 not in known_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    rule_id="edi.hl-parent-not-found",
                    segment=f"HL*{element(seg, 1)}",
                    message=f"HL02 references parent loop {hl02!r}, which does not exist in this transaction set.",
                )
            )
    return findings


def _find_patient_loop_members(transaction_set: TransactionSet) -> list | None:
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
    nm1 = parties.subscriber_nm1
    if not element(nm1, 3) and not element(nm1, 4):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.270-missing-subscriber-name",
                segment="2000C/NM1",
                message="The subscriber's NM1 segment has no resolvable name - the converter will still build a Patient, just with no HumanName.",
            )
        ]
    return []


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


def _find_claim_status_loops(transaction_set: TransactionSet) -> ResolvedClaimStatusLoops | None:
    """Delegates to app.edi.claim_status.resolve_claim_status_loops - the
    one real "which loops are the patient loops, and do their NM1s
    resolve" implementation, shared with the real 276/277 builder so
    validation can never see a different loop/NM1 set than conversion
    would use (the same discipline _find_patient_loop_members above
    already established for 270/271, after a code review caught what
    happens when that discipline isn't followed - and caught again here,
    for the dependent-loop NM1 gate specifically, before this phase ever
    shipped). Returns None when the loops aren't strictly resolvable -
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
    # matching _rule_270_missing_subscriber_name's identical guarantee.
    if not element(loops.subscriber_nm1, 3) and not element(loops.subscriber_nm1, 4):
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.276-missing-subscriber-name",
                segment="2000D/NM1",
                message="The subscriber's NM1 segment has no resolvable name - the converter will still build a Patient, just with no HumanName.",
            )
        ]
    return []


def _iter_claim_status_stc(transaction_set: TransactionSet) -> list:
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


def _find_prior_auth_loops(transaction_set: TransactionSet) -> ResolvedPriorAuthLoops | None:
    """Delegates to app.edi.prior_auth.resolve_prior_auth_loops - the one
    real "which loops are the patient/patient-event loops" implementation,
    shared with the real 278 builder for the same reason
    _find_claim_status_loops/_find_patient_loop_members already are.
    Returns None when the loops aren't strictly resolvable - already
    surfaced separately via edi.would-not-convert."""
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


def _check_convertibility(transaction_set: TransactionSet | None, delimiters: Delimiters) -> list[ValidationFinding]:
    if transaction_set is None:
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.no-transaction-set",
                message="This interchange contains no ST/SE transaction set - nothing to convert.",
            )
        ]

    # Deferred import: app.edi.registry imports both eligibility_270.py and
    # eligibility_271.py at module load time; neither of those needs this
    # module, so there's no real circularity risk here, but the deferred
    # import keeps this module import-order-independent of registry.py the
    # same way app/cda/validation.py already is of app/cda/registry.py.
    from app.edi.registry import get_transaction_builder

    try:
        builder = get_transaction_builder(transaction_set.st01)
    except MappingError:
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.unsupported-transaction-set",
                message=f"No builder is registered for X12 transaction set {transaction_set.st01!r} - only generic checks were run.",
            )
        ]

    try:
        builder.build_bundle(transaction_set, delimiters)
    except (MappingError, MissingSegmentError, ValidationError) as exc:
        return [
            ValidationFinding(
                severity="error",
                rule_id="edi.would-not-convert",
                message=f"This transaction set would fail to convert to FHIR: {exc}",
            )
        ]
    except Exception:
        logger.exception("Unexpected error while checking convertibility for an X12 transaction set")
        return [
            ValidationFinding(
                severity="error",
                rule_id="edi.convertibility-check-failed",
                message="An unexpected internal error occurred while checking whether this transaction set would convert to FHIR.",
            )
        ]
    return []


def validate_interchange(interchange: Interchange) -> ValidationReport:
    findings: list[ValidationFinding] = []
    now = datetime.now(timezone.utc)

    findings.extend(_rule_isa_iea_count(interchange))
    findings.extend(_rule_isa_usage_indicator(interchange))
    findings.extend(_rule_gs_ge_count(interchange))
    findings.extend(_rule_st_se_count(interchange))

    transaction_set = first_transaction_set(interchange)

    if transaction_set is not None:
        findings.extend(_rule_hl_parent_not_found(transaction_set))
        # Normalized the same way every other format/trigger dispatch point
        # in this codebase is (get_mapper, get_type_validator) - a
        # lowercase ST01 previously shipped silently skipping ADT's A02
        # rule the same way (see CLAUDE.md), so this dispatch is normalized
        # from the start rather than waiting to rediscover that bug here.
        normalized_st01 = transaction_set.st01.strip().upper()
        if normalized_st01 == "270":
            findings.extend(_rule_270_missing_subscriber_name(transaction_set))
            findings.extend(_rule_270_serviced_date_in_future(transaction_set, now))
        elif normalized_st01 == "271":
            findings.extend(_rule_271_no_service_type(transaction_set))
        elif normalized_st01 == "276":
            findings.extend(_rule_276_missing_subscriber_name(transaction_set))
        elif normalized_st01 == "277":
            findings.extend(_rule_277_unrecognized_status_category(transaction_set, interchange.delimiters))
            findings.extend(_rule_277_status_date_in_future(transaction_set, interchange.delimiters, now))
        elif normalized_st01 == "278":
            # 278 has no separate ST01 for request vs. response (see
            # app/edi/prior_auth.py's own module docstring) - the response-
            # only rules check BHT02 internally rather than needing a
            # fourth dispatch branch here.
            findings.extend(_rule_278_missing_subscriber_name(transaction_set))
            findings.extend(_rule_278_response_missing_hcr(transaction_set))
            findings.extend(_rule_278_unrecognized_hcr_action_code(transaction_set))
        elif normalized_st01 == "835":
            findings.extend(_rule_835_bpr02_total_mismatch(transaction_set))
            findings.extend(_rule_835_bpr16_in_future(transaction_set, now))
            findings.extend(_rule_835_missing_payer_name(transaction_set))
            findings.extend(_rule_835_missing_payee_name(transaction_set))
            findings.extend(_rule_835_claim_paid_exceeds_charge(transaction_set))

    findings.extend(_check_convertibility(transaction_set, interchange.delimiters))

    is_valid = not any(finding.severity == "error" for finding in findings)
    return ValidationReport(
        message_type="EDI",
        trigger_event=transaction_set.st01 if transaction_set is not None else None,
        is_valid=is_valid,
        findings=findings,
    )
