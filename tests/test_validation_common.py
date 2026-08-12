from datetime import datetime, timezone

from app.validation.common import (
    has_time_precision,
    not_in_future,
    parse_comparable_datetime,
    parse_comparable_fhir_datetime,
)


def test_parse_comparable_datetime_handles_full_timestamp():
    assert parse_comparable_datetime("20260812105000") == datetime(2026, 8, 12, 10, 50, 0, tzinfo=timezone.utc)


def test_parse_comparable_datetime_handles_date_only():
    assert parse_comparable_datetime("19620305") == datetime(1962, 3, 5, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_comparable_datetime_returns_none_for_empty_or_garbage():
    assert parse_comparable_datetime("") is None
    assert parse_comparable_datetime("notadate") is None


def test_parse_comparable_datetime_returns_none_for_calendar_invalid_date():
    # Regression test: "20260231" (Feb 31) is numerically well-formed (8
    # digits) but not a real calendar date - parse_hl7_date only checks
    # digit shape, not calendar validity, so this used to raise an
    # uncaught ValueError instead of returning None like every other
    # unparseable value.
    assert parse_comparable_datetime("20260231") is None
    assert parse_comparable_datetime("20261301000000") is None  # month 13, full timestamp form


def test_has_time_precision():
    assert has_time_precision("20260812105000") is True
    assert has_time_precision("19620305") is False
    assert has_time_precision("") is False
    assert has_time_precision("notadate") is False


def test_parse_comparable_fhir_datetime():
    assert parse_comparable_fhir_datetime("2026-08-12T10:50:00Z") == datetime(2026, 8, 12, 10, 50, 0, tzinfo=timezone.utc)
    assert parse_comparable_fhir_datetime(None) is None
    assert parse_comparable_fhir_datetime("") is None


def test_not_in_future():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    assert not_in_future("20200101000000", now) is True
    assert not_in_future("20990101000000", now) is False
    assert not_in_future("", now) is None
    assert not_in_future("notadate", now) is None
