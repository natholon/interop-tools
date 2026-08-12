"""Validation rules applicable to every HL7v2 message type - MSH/PID
structural and data-quality checks that don't depend on which message type
this is. Each _rule_* function is independently testable; validate()
composes them by concatenation."""

from datetime import datetime, timezone

from app.hl7.parser import field_str, optional_segment
from app.validation.common import parse_comparable_datetime
from app.validation.models import ValidationFinding

_RECOGNIZED_SEX_CODES = {"M", "F", "O", "U", "A", "N"}
_MAX_PLAUSIBLE_AGE_YEARS = 120


def _rule_msh_encoding(msh) -> list[ValidationFinding]:
    findings = []
    if field_str(msh, 1) != "|":
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.msh-field-separator-unusual",
                segment="MSH",
                field=1,
                message="MSH-1 (field separator) is not the standard '|'.",
            )
        )
    encoding_chars = field_str(msh, 2)
    if len(encoding_chars) != 4:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.msh-encoding-characters-unusual",
                segment="MSH",
                field=2,
                message=f"MSH-2 (encoding characters) is {encoding_chars!r}, not the standard 4-character '^~\\&'.",
            )
        )
    return findings


def _rule_msh_type_and_trigger(msh) -> list[ValidationFinding]:
    message_type = field_str(msh, 9, component=1)
    trigger_event = field_str(msh, 9, component=2)
    if not message_type or not trigger_event:
        return [
            ValidationFinding(
                severity="error",
                rule_id="generic.msh-9-incomplete",
                segment="MSH",
                field=9,
                message="MSH-9 (message type^trigger event) is missing its message type and/or trigger event component.",
            )
        ]
    return []


def _rule_msh_timestamp_and_control_id(msh) -> list[ValidationFinding]:
    findings = []
    raw_timestamp = field_str(msh, 7)
    if not raw_timestamp:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.msh-7-missing",
                segment="MSH",
                field=7,
                message="MSH-7 (message date/time) is missing.",
            )
        )
    elif parse_comparable_datetime(raw_timestamp) is None:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.msh-7-unparseable",
                segment="MSH",
                field=7,
                message=f"MSH-7 (message date/time) {raw_timestamp!r} does not parse as a valid HL7 timestamp.",
            )
        )
    if not field_str(msh, 10):
        findings.append(
            ValidationFinding(
                severity="info",
                rule_id="generic.msh-10-missing",
                segment="MSH",
                field=10,
                message="MSH-10 (message control ID) is missing.",
            )
        )
    return findings


def _rule_pid_presence(pid) -> list[ValidationFinding]:
    findings = []
    if not field_str(pid, 3):
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-3-missing",
                segment="PID",
                field=3,
                message="PID-3 (patient identifier) is missing.",
            )
        )
    if not field_str(pid, 5):
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-5-missing",
                segment="PID",
                field=5,
                message="PID-5 (patient name) is missing.",
            )
        )
    return findings


def _rule_pid_birth_date(pid, now: datetime) -> list[ValidationFinding]:
    raw_birth_date = field_str(pid, 7)
    if not raw_birth_date:
        return []
    birth_date = parse_comparable_datetime(raw_birth_date)
    if birth_date is None:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-7-unparseable",
                segment="PID",
                field=7,
                message=f"PID-7 (date of birth) {raw_birth_date!r} does not parse as a valid HL7 date/time.",
            )
        ]
    if birth_date > now:
        return [
            ValidationFinding(
                severity="error",
                rule_id="generic.pid-7-in-future",
                segment="PID",
                field=7,
                message="PID-7 (date of birth) is in the future.",
            )
        ]
    if (now - birth_date).days > _MAX_PLAUSIBLE_AGE_YEARS * 365:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-7-implausibly-old",
                segment="PID",
                field=7,
                message=f"PID-7 (date of birth) implies an age over {_MAX_PLAUSIBLE_AGE_YEARS} years.",
            )
        ]
    return []


def _rule_pid_sex(pid) -> list[ValidationFinding]:
    sex = field_str(pid, 8).strip().upper()
    if sex and sex not in _RECOGNIZED_SEX_CODES:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-8-unrecognized",
                segment="PID",
                field=8,
                message=f"PID-8 (administrative sex) {sex!r} is not a recognized HL7 table 0001 code.",
            )
        ]
    return []


def validate(message, trigger_event: str) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    now = datetime.now(timezone.utc)

    msh = optional_segment(message, "MSH")
    if msh is not None:
        findings.extend(_rule_msh_encoding(msh))
        findings.extend(_rule_msh_type_and_trigger(msh))
        findings.extend(_rule_msh_timestamp_and_control_id(msh))

    pid = optional_segment(message, "PID")
    if pid is None:
        findings.append(
            ValidationFinding(severity="error", rule_id="generic.pid-missing", segment="PID", message="PID segment is missing.")
        )
    else:
        findings.extend(_rule_pid_presence(pid))
        findings.extend(_rule_pid_birth_date(pid, now))
        findings.extend(_rule_pid_sex(pid))

    return findings
