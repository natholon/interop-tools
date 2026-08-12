"""Cross-rule-module validation helpers - the app/mappings/common.py
equivalent for app/validation/*.py. Kept intentionally small: only what's
genuinely reused across more than one rule module lives here."""

from datetime import datetime, timezone

from app.fhir_models.builders import parse_hl7_date, parse_hl7_datetime


def _parse_fhir_datetime(dt_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_comparable_datetime(raw_value: str) -> datetime | None:
    """Parse a raw HL7 TS field (e.g. PV1-44, PID-7, TXA-6) into a
    timezone-aware datetime usable for ordering comparisons (admit vs
    discharge, birth vs admit, ...). Tries parse_hl7_datetime first (HL7 TS
    with a time component, 12+ digits); PID-7 date-of-birth and other
    date-only TS fields are very commonly just an 8-digit YYYYMMDD with no
    time at all, which parse_hl7_datetime treats as unparseable (it's built
    for timestamps, not bare dates) - falls back to parse_hl7_date for
    exactly that case, treating a date-only value as UTC midnight (see
    has_time_precision() below for why cross-field comparisons need to
    treat that midnight fallback carefully rather than compare it directly
    against a same-day timestamped value). Re-parsed into a real datetime
    either way (not left as a string), since string comparison of ISO-8601
    timestamps only sorts correctly when every value shares the same
    timezone offset, which HL7 messages don't guarantee. Returns None when
    the field is empty, doesn't parse at all, or is numerically well-formed
    but not a real calendar date (e.g. month 13, or day 31 in February) -
    parse_hl7_date only checks digit count/shape, not calendar validity, so
    this guards the fromisoformat() call the same way _parse_fhir_datetime
    already does for the timestamp path."""
    dt_str = parse_hl7_datetime(raw_value)
    if dt_str:
        return _parse_fhir_datetime(dt_str)
    date_str = parse_hl7_date(raw_value)
    if date_str:
        try:
            return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def has_time_precision(raw_value: str) -> bool:
    """Whether a raw HL7 TS field includes an actual time component (12+
    digits) rather than being date-only (exactly 8 digits, YYYYMMDD, which
    parse_comparable_datetime defaults to UTC midnight). Cross-field
    ordering comparisons (admit vs discharge, birth vs admit, ...) need
    this: a date-only value on the same calendar day as a timestamped value
    is genuinely indeterminate relative to it - "2024-01-15" could be
    before or after "2024-01-15T08:30:00Z" - so comparing the synthetic
    midnight directly against a real time-of-day would produce a false
    "before" whenever the date-only side happens to be the earlier
    argument. Callers should fall back to date-granularity comparison
    (`.date() <` instead of `<`) whenever either side lacks time
    precision."""
    return parse_hl7_datetime(raw_value) is not None


def parse_comparable_fhir_datetime(dt_str: str | None) -> datetime | None:
    """Same as parse_comparable_datetime, but for a value that has already
    been converted to a FHIR dateTime string (e.g. by
    app.mappings.siu.resolve_appointment_timing()) rather than raw HL7 TS
    text - skips the redundant parse_hl7_datetime step."""
    return _parse_fhir_datetime(dt_str) if dt_str else None


def is_before(earlier_raw: str, earlier_dt: datetime, later_raw: str, later_dt: datetime) -> bool:
    """Whether `earlier_dt` is unambiguously before `later_dt`. If either
    side is a date-only value (no time component - see
    has_time_precision()), a same-calendar-day pair is indeterminate rather
    than a violation: a date-only "2024-01-15" could represent any time on
    that day, so it can't be shown to be before a same-day "2024-01-15
    08:30" - only compare at date granularity in that case. With full time
    precision on both sides, compare exactly. Shared by app/validation/adt.py
    and app/cda/validation.py - promoted here once a second real consumer
    needed the exact same date-only-aware ordering logic, mirroring this
    project's established "extract once duplication would otherwise occur"
    pattern (see CLAUDE.md on build_minimal_pv1_fields/build_minimal_encounter)."""
    if not has_time_precision(earlier_raw) or not has_time_precision(later_raw):
        return earlier_dt.date() < later_dt.date()
    return earlier_dt < later_dt


def not_in_future(raw_value: str, now: datetime) -> bool | None:
    """Whether a raw HL7 TS field parses to a datetime that is not after
    `now`. Returns None (not True/False) when the field is empty or
    unparseable, so callers can tell "malformed" apart from "in the
    future" - those warrant different findings."""
    parsed = parse_comparable_datetime(raw_value)
    if parsed is None:
        return None
    return parsed <= now
