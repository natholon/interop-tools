"""The validation orchestrator: runs generic rules, then a type-specific
rule set if one is registered, then a "would this actually convert" check.

That last check deliberately does NOT re-derive a parallel "required
segments per trigger" table - this project has already been bitten once by
exactly that kind of duplication (see CLAUDE.md on build_minimal_pv1_fields
/ build_minimal_encounter). Instead it runs the real mapper and turns
whatever it raises into a finding, so it can never drift from what the
mappers actually require. The tradeoff, made explicit in the finding text:
a mapper stops at the FIRST problem it hits, so this can only ever report
one convertibility issue at a time - the per-type rule modules' own direct
segment-presence checks (e.g. adt.py's PV1-missing check) exist precisely
to cover the common case of surfacing more than one structural problem at
once."""

import logging

from pydantic import ValidationError

from app.hl7.errors import MappingError, MissingSegmentError
from app.mappings.registry import get_mapper
from app.validation.generic import validate as validate_generic
from app.validation.models import ValidationFinding, ValidationReport
from app.validation.registry import get_type_validator

logger = logging.getLogger(__name__)


def _check_convertibility(
    message, message_type: str, trigger_event: str, ran_type_specific_checks: bool
) -> list[ValidationFinding]:
    try:
        mapper = get_mapper(message_type, trigger_event)
    except MappingError:
        # A registered TYPE with an unmapped TRIGGER (e.g. ADT^A38, one of
        # the "remaining trigger events" CLAUDE.md lists) already had its
        # type-specific rules run above, by design - `only generic checks
        # were run` would be false in that case, so the message reflects
        # what actually happened rather than assuming "unsupported type"
        # always means "unsupported at every level".
        ran_description = "generic and type-specific checks were" if ran_type_specific_checks else "generic checks were"
        return [
            ValidationFinding(
                severity="info",
                rule_id="engine.unsupported-message-type",
                message=f"No converter is registered for {message_type}^{trigger_event} - only {ran_description} run.",
            )
        ]

    try:
        mapper.to_bundle(message)
    except (MappingError, MissingSegmentError, ValidationError) as exc:
        # MappingError here is NOT "type not registered" (that's the
        # get_mapper() call above, already past by this point) - mappers
        # also raise it directly for business-rule violations, e.g.
        # AdtA03Mapper requiring a discharge time or SiuS12Mapper requiring
        # resolvable timing. Both are entirely expected, well-documented
        # outcomes, not an internal error - they belong here, not in the
        # bare except Exception safety net below.
        return [
            ValidationFinding(
                severity="error",
                rule_id="engine.would-not-convert",
                message=(
                    f"This message would fail to convert to FHIR: {exc}. Conversion stops at the "
                    "first problem it hits - other findings above may indicate additional issues."
                ),
            )
        ]
    except Exception:
        logger.exception("Unexpected error while checking convertibility for %s^%s", message_type, trigger_event)
        return [
            ValidationFinding(
                severity="error",
                rule_id="engine.convertibility-check-failed",
                message="An unexpected internal error occurred while checking whether this message would convert to FHIR.",
            )
        ]
    return []


def validate_message(message, message_type: str, trigger_event: str) -> ValidationReport:
    findings: list[ValidationFinding] = []
    findings.extend(validate_generic(message, trigger_event))

    type_validator = get_type_validator(message_type)
    if type_validator is not None:
        findings.extend(type_validator(message, trigger_event))

    findings.extend(_check_convertibility(message, message_type, trigger_event, ran_type_specific_checks=type_validator is not None))

    is_valid = not any(finding.severity == "error" for finding in findings)
    return ValidationReport(
        message_type=message_type or None,
        trigger_event=trigger_event or None,
        is_valid=is_valid,
        findings=findings,
    )
