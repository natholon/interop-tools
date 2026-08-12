from typing import Literal

from pydantic import BaseModel

Severity = Literal["error", "warning", "info"]


class ValidationFinding(BaseModel):
    """One validation observation. `segment`/`field` point at the offending
    location when there is one (e.g. segment="PID", field=7); both are None
    for message-level findings (e.g. an unrecognized message type)."""

    severity: Severity
    rule_id: str
    segment: str | None = None
    field: int | None = None
    message: str


class ValidationReport(BaseModel):
    """message_type/trigger_event are echoed back (from MSH-9) so a caller
    can show "detected as ADT^A08" even when every other check fails.
    is_valid is true iff no finding has severity="error" - warnings and
    info findings never affect it."""

    message_type: str | None = None
    trigger_event: str | None = None
    is_valid: bool
    findings: list[ValidationFinding]
