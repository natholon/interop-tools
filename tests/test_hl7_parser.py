from pathlib import Path

import pytest

from app.hl7.errors import Hl7ParseError, MissingSegmentError
from app.hl7.parser import field_str, group_segments_by_leader, parse_message, require_segment

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


def test_group_segments_by_leader_associates_members_with_correct_leader():
    msg = parse_message(read_fixture("oru_r01_basic.hl7"))
    groups = group_segments_by_leader(msg, "OBR", ["OBX"])

    assert len(groups) == 2
    first_obr, first_members = groups[0]
    second_obr, second_members = groups[1]
    assert field_str(first_obr, 4, component=1) == "CBC"
    assert [field_str(o, 3, component=1) for o in first_members] == ["WBC", "HGB"]
    assert field_str(second_obr, 4, component=1) == "GLU"
    assert [field_str(o, 3, component=1) for o in second_members] == ["GLUCOSE"]


def test_group_segments_by_leader_returns_empty_list_when_no_leader_present():
    msg = parse_message(read_fixture("adt_a01_basic.hl7"))
    assert group_segments_by_leader(msg, "OBR", ["OBX"]) == []


def test_group_segments_by_leader_ignores_members_before_first_leader():
    msg = parse_message(read_fixture("oru_r01_minimal.hl7"))
    # PID segment precedes OBR/OBX and isn't a member name, so it must not
    # leak into any group; a member name appearing before any leader is
    # also correctly dropped (nothing to attach it to).
    groups = group_segments_by_leader(msg, "OBR", ["PID", "OBX"])
    assert len(groups) == 1
    _, members = groups[0]
    assert [str(s[0][0]) for s in members] == ["OBX"]
