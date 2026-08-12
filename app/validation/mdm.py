"""MDM-specific validation rules - data-quality checks plus one structural
check for TXA (every MDM mapper requires it)."""

from datetime import datetime, timezone

from app.hl7.parser import field_str, optional_segment
from app.validation.common import not_in_future
from app.validation.models import ValidationFinding

_VERIFIED_AVAILABILITY_CODE = "AV"


def _rule_origination_not_future(txa, now: datetime) -> list[ValidationFinding]:
    origination = field_str(txa, 6)
    if not origination:
        return []
    if not_in_future(origination, now) is False:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="mdm.origination-date-future",
                segment="TXA",
                field=6,
                message="TXA-6 (origination date/time) is in the future.",
            )
        ]
    return []


def _rule_availability_status(txa) -> list[ValidationFinding]:
    availability = field_str(txa, 19).strip().upper()
    if availability and availability != _VERIFIED_AVAILABILITY_CODE:
        return [
            ValidationFinding(
                severity="info",
                rule_id="mdm.availability-status-unverified",
                segment="TXA",
                field=19,
                message=(
                    f"TXA-19 (availability status) {availability!r} has no verified mapping - "
                    "the converter defaults DocumentReference.status to 'current' regardless."
                ),
            )
        ]
    return []


def validate(message, trigger_event: str) -> list[ValidationFinding]:
    txa = optional_segment(message, "TXA")
    if txa is None:
        return [
            ValidationFinding(severity="error", rule_id="mdm.txa-missing", segment="TXA", message="TXA segment is missing.")
        ]

    now = datetime.now(timezone.utc)
    findings: list[ValidationFinding] = []
    findings.extend(_rule_origination_not_future(txa, now))
    findings.extend(_rule_availability_status(txa))
    return findings
