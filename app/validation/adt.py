"""ADT-specific validation rules - data-quality checks the generic rules
can't cover, plus one structural check for PV1 (the segment every ADT
mapper requires). Several of these deliberately surface behavior the
mappers themselves already silently default/skip, per app/mappings/adt.py
and docs/build-history.md - no new guessing, just making an existing judgment call
visible to whoever is looking at this message."""

from datetime import datetime, timezone

from app.hl7.parser import field_str, optional_segment
from app.validation.common import is_before, not_in_future, parse_comparable_datetime
from app.validation.models import ValidationFinding

_RECOGNIZED_PATIENT_CLASSES = {"I", "O", "E", "P"}


def _rule_admit_discharge_order(pv1) -> list[ValidationFinding]:
    admit = field_str(pv1, 44)
    discharge = field_str(pv1, 45)
    if not admit or not discharge:
        return []
    admit_dt = parse_comparable_datetime(admit)
    discharge_dt = parse_comparable_datetime(discharge)
    if admit_dt is not None and discharge_dt is not None and is_before(discharge, discharge_dt, admit, admit_dt):
        return [
            ValidationFinding(
                severity="error",
                rule_id="adt.discharge-before-admit",
                segment="PV1",
                field=45,
                message="PV1-45 (discharge date/time) is before PV1-44 (admit date/time).",
            )
        ]
    return []


def _rule_admit_not_future(pv1, now: datetime) -> list[ValidationFinding]:
    admit = field_str(pv1, 44)
    if not admit:
        return []
    if not_in_future(admit, now) is False:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="adt.admit-in-future",
                segment="PV1",
                field=44,
                message="PV1-44 (admit date/time) is in the future.",
            )
        ]
    return []


def _rule_birth_before_admit(pid, pv1) -> list[ValidationFinding]:
    if pid is None:
        return []
    birth_date = field_str(pid, 7)
    admit = field_str(pv1, 44)
    if not birth_date or not admit:
        return []
    birth_dt = parse_comparable_datetime(birth_date)
    admit_dt = parse_comparable_datetime(admit)
    if birth_dt is not None and admit_dt is not None and is_before(admit, admit_dt, birth_date, birth_dt):
        return [
            ValidationFinding(
                severity="error",
                rule_id="adt.admit-before-birth",
                segment="PV1",
                field=44,
                message="PV1-44 (admit date/time) is before PID-7 (date of birth).",
            )
        ]
    return []


def _rule_patient_class(pv1) -> list[ValidationFinding]:
    patient_class = field_str(pv1, 2).strip().upper()
    if patient_class and patient_class not in _RECOGNIZED_PATIENT_CLASSES:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="adt.patient-class-unrecognized",
                segment="PV1",
                field=2,
                message=(
                    f"PV1-2 (patient class) {patient_class!r} is not one of I/O/E/P - "
                    "the converter will silently map this to AMB (ambulatory)."
                ),
            )
        ]
    return []


def _rule_a02_missing_prior_location(pv1, trigger_event: str) -> list[ValidationFinding]:
    if trigger_event == "A02" and not field_str(pv1, 6):
        return [
            ValidationFinding(
                severity="info",
                rule_id="adt.a02-missing-prior-location",
                segment="PV1",
                field=6,
                message="PV1-6 (prior location) is missing on an A02 transfer - the resulting Encounter's location history will be incomplete.",
            )
        ]
    return []


def validate(message, trigger_event: str) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    now = datetime.now(timezone.utc)
    # Normalized once here (matching app/mappings/registry.py::get_mapper's
    # and app/validation/registry.py::get_type_validator's .strip().upper()
    # convention) so trigger-specific rules below don't each need their own
    # case-insensitive comparison - MSH-9's trigger component reaches this
    # function exactly as the sender wrote it, lowercase and all.
    trigger_event = trigger_event.strip().upper()

    pv1 = optional_segment(message, "PV1")
    if pv1 is None:
        return [
            ValidationFinding(severity="error", rule_id="adt.pv1-missing", segment="PV1", message="PV1 segment is missing.")
        ]

    pid = optional_segment(message, "PID")

    findings.extend(_rule_admit_discharge_order(pv1))
    findings.extend(_rule_admit_not_future(pv1, now))
    findings.extend(_rule_birth_before_admit(pid, pv1))
    findings.extend(_rule_patient_class(pv1))
    findings.extend(_rule_a02_missing_prior_location(pv1, trigger_event))
    return findings
