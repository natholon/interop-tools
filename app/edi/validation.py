"""X12 EDI validation - the app/edi/ mirror of app/cda/validation.py /
app/validation/{generic,adt,engine}.py, independent of conversion, never
raising for anything a real X12 sender could plausibly produce.

This module holds only structural (whole-interchange) rules,
`_check_convertibility`, and the `validate_interchange` orchestrator - each
transaction-set family's own plausibility rules live in their own sibling
module instead (`eligibility_validation.py` for 270/271,
`claim_status_validation.py` for 276/277, `prior_auth_validation.py` for
278, `remittance_validation.py` for 835, `claim_837p_validation.py` for
837P, `claim_837i_validation.py` for 837I, `claim_837d_validation.py` for
837D), mirroring the file-per-family split every builder/generator module
already follows. This split is purely internal - every external caller
(`app/edi/pipeline.py`, every `test_*_validation.py` file) only ever calls
`validate_interchange` itself, so moving code between these modules changes
nothing about the public contract.

Reuses app.validation.models.ValidationFinding/ValidationReport as-is (a
finding uses a path-like string for `segment`, e.g. "2000C/NM1"; `field`
left None) and app.validation.common.not_in_future directly - it operates
on raw HL7-TS-shaped strings, and X12's D8-qualified date elements
(CCYYMMDD) use the identical digit shape (see app/edi/common.py::
parse_x12_datetime for the established precedent of this exact reuse).

Structural rules apply to the whole interchange (its own internal
arithmetic doesn't depend on which transaction set gets converted);
transaction-set-specific plausibility rules apply only to
first_transaction_set() - the one transaction each phase actually converts
(see app/edi/parser.py::first_transaction_set's own disclosed batching
scope limit)."""

import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from app.edi.claim_837d_validation import validate_837d
from app.edi.claim_837i_validation import validate_837i
from app.edi.claim_837p_validation import validate_837p
from app.edi.claim_status_validation import validate_276, validate_277
from app.edi.common import resolve_837_variant
from app.edi.eligibility_validation import validate_270, validate_271
from app.edi.parser import Delimiters, Interchange, TransactionSet, element, first_transaction_set
from app.edi.prior_auth_validation import validate_278
from app.edi.remittance_validation import validate_835
from app.hl7.errors import MappingError, MissingSegmentError
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


def _check_convertibility(transaction_set: TransactionSet | None, delimiters: Delimiters) -> list[ValidationFinding]:
    if transaction_set is None:
        return [
            ValidationFinding(
                severity="info",
                rule_id="edi.no-transaction-set",
                message="This interchange contains no ST/SE transaction set - nothing to convert.",
            )
        ]

    # Deferred import: app.edi.registry imports every builder module at
    # module load time; none of those need this module, so there's no real
    # circularity risk here, but the deferred import keeps this module
    # import-order-independent of registry.py the same way
    # app/cda/validation.py already is of app/cda/registry.py.
    from app.edi.registry import get_transaction_builder

    try:
        builder = get_transaction_builder(transaction_set.st01, transaction_set.st03)
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
            findings.extend(validate_270(transaction_set, now))
        elif normalized_st01 == "271":
            findings.extend(validate_271(transaction_set))
        elif normalized_st01 == "276":
            findings.extend(validate_276(transaction_set))
        elif normalized_st01 == "277":
            findings.extend(validate_277(transaction_set, interchange.delimiters, now))
        elif normalized_st01 == "278":
            findings.extend(validate_278(transaction_set))
        elif normalized_st01 == "835":
            findings.extend(validate_835(transaction_set, now))
        elif normalized_st01 == "837":
            # Mirrors app/edi/registry.py::get_transaction_builder's own
            # 837P-vs-837I-vs-837D dispatch exactly, via the shared
            # common.py::resolve_837_variant - the same decision tree, not
            # just the same leaf predicates, so validation can never
            # disagree with conversion about which variant a given ST03
            # value indicates.
            variant = resolve_837_variant(transaction_set.st03)
            if variant == "837I":
                findings.extend(validate_837i(transaction_set, interchange.delimiters, now))
            elif variant == "837D":
                findings.extend(validate_837d(transaction_set, interchange.delimiters, now))
            else:
                findings.extend(validate_837p(transaction_set, interchange.delimiters, now))

    findings.extend(_check_convertibility(transaction_set, interchange.delimiters))

    is_valid = not any(finding.severity == "error" for finding in findings)
    return ValidationReport(
        message_type="EDI",
        trigger_event=transaction_set.st01 if transaction_set is not None else None,
        is_valid=is_valid,
        findings=findings,
    )
