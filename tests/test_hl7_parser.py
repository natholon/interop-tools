from pathlib import Path

import pytest

from app.hl7.errors import Hl7ParseError, MissingSegmentError
from app.hl7.parser import field_str, parse_message, require_segment

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_basic_message():
    msg = parse_message(read_fixture("adt_a01_basic.hl7"))
    pid = require_segment(msg, "PID")
    assert field_str(pid, 3) == "123456"


def test_parse_minimal_message():
    msg = parse_message(read_fixture("adt_a01_minimal.hl7"))
    pv1 = require_segment(msg, "PV1")
    assert field_str(pv1, 2) == "O"


def test_parse_malformed_message_raises():
    with pytest.raises(Hl7ParseError):
        parse_message(read_fixture("adt_a01_malformed.hl7"))


def test_require_segment_missing_raises():
    msg = parse_message(read_fixture("adt_a01_basic.hl7"))
    with pytest.raises(MissingSegmentError):
        require_segment(msg, "OBX")


def test_field_str_missing_field_returns_empty():
    msg = parse_message(read_fixture("adt_a01_minimal.hl7"))
    pid = require_segment(msg, "PID")
    assert field_str(pid, 13) == ""
