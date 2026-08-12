"""ORU-specific validation rules - data-quality checks plus one structural
check for OBR (every ORU mapper requires at least one). Reference-range
parsing is a narrow, disclosed-scope regex (low-high, both non-negative
numbers) - anything else in OBX-7 is silently skipped rather than guessed
at, matching this app's established "only act on a verified subset"
philosophy (see e.g. app/mappings/mdm.py's TXA-3 MIME crosswalk)."""

import re

from app.hl7.parser import field_str, optional_segments
from app.validation.models import ValidationFinding

_REFERENCE_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$")
_NORMAL_FLAG_CODE = "N"


def _resolve_reference_range(raw_range: str) -> tuple[float, float] | None:
    match = _REFERENCE_RANGE_RE.match(raw_range.strip())
    if not match:
        return None
    low, high = float(match.group(1)), float(match.group(2))
    if low > high:
        # A syntactically well-formed but transposed range (e.g. a
        # sending-system field-order bug) isn't a real low-high pair -
        # silently skip it like any other unrecognized format, rather than
        # computing an inverted (and therefore wrong) range membership.
        return None
    return low, high


def _rule_value_vs_reference_range(obx) -> list[ValidationFinding]:
    if field_str(obx, 2).strip().upper() != "NM":
        return []
    raw_value = field_str(obx, 5)
    raw_range = field_str(obx, 7)
    if not raw_value or not raw_range:
        return []
    try:
        value = float(raw_value)
    except ValueError:
        return []
    bounds = _resolve_reference_range(raw_range)
    if bounds is None:
        return []
    low, high = bounds
    out_of_range = value < low or value > high

    findings = []
    if out_of_range:
        findings.append(
            ValidationFinding(
                severity="info",
                rule_id="oru.value-outside-reference-range",
                segment="OBX",
                field=5,
                message=f"OBX-5 value {value} is outside its OBX-7 reference range ({raw_range}).",
            )
        )

    flag = field_str(obx, 8).strip().upper()
    if flag:
        flag_says_abnormal = flag != _NORMAL_FLAG_CODE
        if out_of_range and not flag_says_abnormal:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    rule_id="oru.abnormal-flag-contradicts-range",
                    segment="OBX",
                    field=8,
                    message="OBX-8 (abnormal flag) is 'N' (normal) but OBX-5's value is outside the OBX-7 reference range.",
                )
            )
        elif not out_of_range and flag_says_abnormal:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    rule_id="oru.abnormal-flag-contradicts-range",
                    segment="OBX",
                    field=8,
                    message=f"OBX-8 (abnormal flag) is {flag!r} but OBX-5's value is within the OBX-7 reference range.",
                )
            )
    return findings


def validate(message, trigger_event: str) -> list[ValidationFinding]:
    obr_segments = optional_segments(message, "OBR")
    if not obr_segments:
        return [
            ValidationFinding(severity="error", rule_id="oru.obr-missing", segment="OBR", message="No OBR segment is present.")
        ]

    findings: list[ValidationFinding] = []
    for obx in optional_segments(message, "OBX"):
        findings.extend(_rule_value_vs_reference_range(obx))
    return findings
