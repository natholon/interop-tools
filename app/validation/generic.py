"""Validation rules applicable to every HL7v2 message type - MSH/PID
structural and data-quality checks that don't depend on which message type
this is. Each _rule_* function is independently testable; validate()
composes them by concatenation."""

from datetime import datetime, timezone

from app.hl7.parser import field_str, optional_segment
from app.validation.common import is_before, not_in_future, parse_comparable_datetime
from app.validation.models import ValidationFinding
from app.validation.required_fields import check_required_fields

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
    """MSH-7's *value*. Its presence, and MSH-10's, are the standard's own
    1..1 minimums and are checked by `check_required_fields` against the
    IG's cardinality column, rather than named one at a time here."""
    findings = []
    raw_timestamp = field_str(msh, 7)
    if raw_timestamp and parse_comparable_datetime(raw_timestamp) is None:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.msh-7-unparseable",
                segment="MSH",
                field=7,
                message=f"MSH-7 (message date/time) {raw_timestamp!r} does not parse as a valid HL7 timestamp.",
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


def _rule_pid_death(pid, now: datetime) -> list[ValidationFinding]:
    """PID-29 (death date) against PID-30 (death indicator) and PID-7.

    The two fields are a choice pair the mapper resolves by precedence, so
    a message can state both and mean two different things - which is the
    contradiction worth surfacing, alongside the ordinary ordering checks
    every other date in this app already gets.
    """
    findings: list[ValidationFinding] = []
    raw_death = field_str(pid, 29)
    indicator = field_str(pid, 30).strip().upper()
    if raw_death and indicator == "N":
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-29-contradicts-pid-30",
                segment="PID",
                field=30,
                message="PID-29 carries a death date while PID-30 says the patient is not deceased.",
            )
        )
    if indicator and indicator not in {"Y", "N"}:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-30-unrecognized",
                segment="PID",
                field=30,
                message=f"PID-30 (patient death indicator) {indicator!r} is not a recognized Y/N value.",
            )
        )
    if not raw_death:
        return findings
    death = parse_comparable_datetime(raw_death)
    if death is None:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-29-unparseable",
                segment="PID",
                field=29,
                message=f"PID-29 (death date/time) {raw_death!r} does not parse as a valid HL7 date/time.",
            )
        )
        return findings
    if not_in_future(raw_death, now) is False:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-29-in-future",
                segment="PID",
                field=29,
                message="PID-29 (death date/time) is in the future.",
            )
        )
    raw_birth = field_str(pid, 7)
    birth = parse_comparable_datetime(raw_birth)
    if birth is not None and is_before(raw_death, death, raw_birth, birth):
        findings.append(
            ValidationFinding(
                severity="error",
                rule_id="generic.pid-29-before-birth",
                segment="PID",
                field=29,
                message="PID-29 (death date/time) is before PID-7 (date of birth).",
            )
        )
    return findings


def _rule_pid_multiple_birth(pid) -> list[ValidationFinding]:
    """PID-25 (birth order) against PID-24 (multiple birth indicator).

    A birth order with the indicator explicitly saying "no" states two
    incompatible things about the same birth; the mapper resolves it by
    the IG's precedence and reports multipleBirthInteger, so the
    contradiction would otherwise pass silently.
    """
    findings: list[ValidationFinding] = []
    indicator = field_str(pid, 24).strip().upper()
    birth_order = field_str(pid, 25).strip()
    if indicator and indicator not in {"Y", "N"}:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-24-unrecognized",
                segment="PID",
                field=24,
                message=f"PID-24 (multiple birth indicator) {indicator!r} is not a recognized Y/N value.",
            )
        )
    if birth_order and not birth_order.isdigit():
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-25-not-numeric",
                segment="PID",
                field=25,
                message=f"PID-25 (birth order) {birth_order!r} is not a number.",
            )
        )
    elif birth_order and indicator == "N":
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="generic.pid-25-contradicts-pid-24",
                segment="PID",
                field=24,
                message="PID-25 carries a birth order while PID-24 says this was not a multiple birth.",
            )
        )
    return findings


def validate(message, trigger_event: str) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    now = datetime.now(timezone.utc)

    # Every segment in the message, against the standard's own required
    # fields. Message-type agnostic on purpose: a required field is
    # required wherever its segment appears.
    for segment in message:
        name = str(segment[0][0]) if len(segment) and len(segment[0]) else ""
        findings.extend(check_required_fields(segment, name))

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
        findings.extend(_rule_pid_birth_date(pid, now))
        findings.extend(_rule_pid_sex(pid))
        findings.extend(_rule_pid_death(pid, now))
        findings.extend(_rule_pid_multiple_birth(pid))

    return findings
