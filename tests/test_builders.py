from app.fhir_models.builders import parse_hl7_datetime


def test_parse_hl7_datetime_assumes_utc_when_no_offset():
    assert parse_hl7_datetime("20260901090000") == "2026-09-01T09:00:00Z"


def test_parse_hl7_datetime_preserves_negative_offset():
    assert parse_hl7_datetime("20260901090000-0400") == "2026-09-01T09:00:00-04:00"


def test_parse_hl7_datetime_preserves_positive_offset():
    assert parse_hl7_datetime("20260901090000+0530") == "2026-09-01T09:00:00+05:30"


def test_parse_hl7_datetime_handles_offset_without_seconds():
    assert parse_hl7_datetime("202609010900-0400") == "2026-09-01T09:00:00-04:00"


def test_parse_hl7_datetime_returns_none_for_invalid_input():
    assert parse_hl7_datetime("not-a-date") is None
    assert parse_hl7_datetime("") is None
