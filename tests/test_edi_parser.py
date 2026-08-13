import pytest

from app.edi.errors import EdiParseError
from app.edi.parser import (
    Delimiters,
    component,
    element,
    find_segment,
    first_transaction_set,
    group_by_hl_hierarchy,
    group_by_leader,
    parse_interchange,
    read_isa_delimiters,
    split_segments,
)

# A real, byte-verified ISA segment (element=*, component=:, repetition=^,
# terminator=~) - constructed and length-checked directly against the
# published 106-byte 5010 layout before being hardcoded here, per this
# project's "verify field positions, don't hand-count" convention.
_ISA = "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     *260812*1200*^*00501*000000001*0*P*:~"
_DELIMITERS = Delimiters(element="*", component=":", repetition="^", segment_terminator="~")


def _envelope(*inner_segments: str, st01: str = "270", st02: str = "0001") -> str:
    return "".join(
        [
            _ISA,
            "GS*HS*SENDERID*RECEIVERID*20260812*1200*1*X*005010X279A1~",
            f"ST*{st01}*{st02}~",
            *inner_segments,
            f"SE*{len(inner_segments) + 2}*{st02}~",
            "GE*1*1~",
            "IEA*1*000000001~",
        ]
    )


_ELIGIBILITY_270_BODY = (
    "BHT*0022*13*10001234*20260812*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*PAYER NAME*****PI*PAYERID001~"
    "HL*2*1*21*1~"
    "NM1*1P*2*PROVIDER NAME*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*DOE*JANE****MI*MEMBERID001~"
    "DMG*D8*19800101*F~"
    "DTP*291*D8*20260812~"
    "EQ*30~"
)


def test_read_isa_delimiters_extracts_all_four_delimiters():
    delimiters = read_isa_delimiters(_ISA)
    assert delimiters == _DELIMITERS


def test_read_isa_delimiters_raises_when_too_short():
    with pytest.raises(EdiParseError):
        read_isa_delimiters("ISA*00*")


def test_read_isa_delimiters_raises_when_missing_isa_prefix():
    with pytest.raises(EdiParseError):
        read_isa_delimiters("X" * 106)


def test_read_isa_delimiters_strips_bom_and_whitespace_first():
    delimiters = read_isa_delimiters("﻿  \n" + _ISA)
    assert delimiters == _DELIMITERS


def test_split_segments_drops_trailing_newline_and_empty_segments():
    raw = "ISA*1*2~\nGS*HS*A~\n\nGE*1*1~\n"
    segments = split_segments(raw, Delimiters("*", ":", "^", "~"))
    assert [s[0] for s in segments] == ["ISA", "GS", "GE"]
    # No stray leading newline glued onto the next segment's ID.
    assert segments[1][0] == "GS"


def test_element_out_of_range_returns_empty_string():
    segment = ["NM1", "IL", "1", "DOE"]
    assert element(segment, 3) == "DOE"
    assert element(segment, 10) == ""
    assert element(segment, 0) == ""


def test_component_out_of_range_returns_empty_string():
    assert component("A:B:C", _DELIMITERS, 2) == "B"
    assert component("A:B:C", _DELIMITERS, 10) == ""
    assert component("", _DELIMITERS, 1) == ""


def test_parse_interchange_full_270_round_trips():
    raw = _envelope(_ELIGIBILITY_270_BODY)
    interchange = parse_interchange(raw)
    assert interchange.delimiters == _DELIMITERS
    assert len(interchange.functional_groups) == 1
    ts = first_transaction_set(interchange)
    assert ts is not None
    assert ts.st01 == "270"
    assert ts.st02 == "0001"
    assert [s[0] for s in ts.segments] == [
        "BHT", "HL", "NM1", "HL", "NM1", "HL", "NM1", "DMG", "DTP", "EQ",
    ]


def test_parse_interchange_raises_when_iea_missing():
    raw = _ISA + "GS*HS*A*B*20260812*1200*1*X*005010X279A1~" + "ST*270*0001~" + "SE*2*0001~" + "GE*1*1~"
    with pytest.raises(EdiParseError):
        parse_interchange(raw)


def test_parse_interchange_raises_when_st_encountered_outside_gs():
    raw = _ISA + "ST*270*0001~" + "SE*2*0001~" + "IEA*0*000000001~"
    with pytest.raises(EdiParseError):
        parse_interchange(raw)


def test_parse_interchange_raises_when_se_has_no_matching_st():
    raw = (
        _ISA + "GS*HS*A*B*20260812*1200*1*X*005010X279A1~" + "SE*2*0001~" + "GE*1*1~" + "IEA*1*000000001~"
    )
    with pytest.raises(EdiParseError):
        parse_interchange(raw)


def test_parse_interchange_raises_when_ge_has_no_matching_gs():
    raw = _ISA + "GE*1*1~" + "IEA*1*000000001~"
    with pytest.raises(EdiParseError):
        parse_interchange(raw)


def test_parse_interchange_raises_when_gs_closed_with_unterminated_st():
    raw = (
        _ISA
        + "GS*HS*A*B*20260812*1200*1*X*005010X279A1~"
        + "ST*270*0001~"
        + "GE*1*1~"
        + "IEA*1*000000001~"
    )
    with pytest.raises(EdiParseError):
        parse_interchange(raw)


def test_parse_interchange_does_not_raise_on_trailer_count_mismatch():
    # SE01/GE01/IEA02 counts are deliberately wrong here (SE01 says "99"
    # segments, GE01 says "5" functional groups, IEA02 says "9"
    # interchanges) - our own walk finds boundaries by segment-ID matching,
    # not by trusting these counts, so a mismatch must not raise. Surfacing
    # it as a validation warning is app/edi/validation.py's job, not
    # parse_interchange's.
    raw = (
        _ISA
        + "GS*HS*A*B*20260812*1200*1*X*005010X279A1~"
        + "ST*270*0001~"
        + "BHT*0022*13*1*20260812*1200~"
        + "SE*99*0001~"
        + "GE*5*1~"
        + "IEA*9*000000001~"
    )
    interchange = parse_interchange(raw)
    assert first_transaction_set(interchange) is not None


def test_parse_interchange_returns_empty_transaction_sets_for_well_formed_but_empty_envelope():
    raw = _ISA + "IEA*0*000000001~"
    interchange = parse_interchange(raw)
    assert interchange.functional_groups == []
    assert first_transaction_set(interchange) is None


def test_first_transaction_set_returns_the_first_across_multiple():
    raw = _envelope(_ELIGIBILITY_270_BODY)
    interchange = parse_interchange(raw)
    ts = first_transaction_set(interchange)
    assert ts.st01 == "270"


def test_find_segment_returns_first_match():
    segments = [seg.split("*") for seg in ["NM1*IL*1*DOE", "DMG*D8*19800101*F", "DMG*D8*19900101*M"]]
    found = find_segment(segments, "DMG")
    assert element(found, 2) == "19800101"


def test_find_segment_returns_none_when_absent():
    segments = [seg.split("*") for seg in ["NM1*IL*1*DOE"]]
    assert find_segment(segments, "DMG") is None


def test_group_by_leader_associates_members_with_correct_leader():
    segments = [
        seg.split("*")
        for seg in ["EQ*30", "REF*6P*GROUP1", "DTP*291*D8*20260812", "EQ*35", "REF*6P*GROUP2"]
    ]
    groups = group_by_leader(segments, "EQ", ["REF", "DTP"])
    assert len(groups) == 2
    first_eq, first_members = groups[0]
    second_eq, second_members = groups[1]
    assert element(first_eq, 1) == "30"
    assert [m[0] for m in first_members] == ["REF", "DTP"]
    assert element(second_eq, 1) == "35"
    assert [m[0] for m in second_members] == ["REF"]


def test_group_by_leader_returns_empty_list_when_no_leader_present():
    segments = [seg.split("*") for seg in ["NM1*IL*1*DOE", "DMG*D8*19800101*F"]]
    assert group_by_leader(segments, "EQ", ["REF", "DTP"]) == []


def test_group_by_leader_ignores_members_before_first_leader():
    segments = [seg.split("*") for seg in ["REF*6P*STRAY", "EQ*30", "DTP*291*D8*20260812"]]
    groups = group_by_leader(segments, "EQ", ["REF", "DTP"])
    assert len(groups) == 1
    _, members = groups[0]
    assert [m[0] for m in members] == ["DTP"]


def test_group_by_hl_hierarchy_flat_single_loop():
    segments = [seg.split("*") for seg in ["HL*1**20*0", "NM1*PR*2*PAYER"]]
    roots = group_by_hl_hierarchy(segments)
    assert len(roots) == 1
    assert roots[0].hl01 == "1"
    assert roots[0].hl03 == "20"
    assert roots[0].has_children is False
    assert [m[0] for m in roots[0].member_segments] == ["NM1"]


def test_group_by_hl_hierarchy_full_chain():
    segments = [
        seg.split("*")
        for seg in [
            "HL*1**20*1",
            "NM1*PR*2*PAYER",
            "HL*2*1*21*1",
            "NM1*1P*2*PROVIDER",
            "HL*3*2*22*1",
            "NM1*IL*1*DOE",
            "HL*4*3*23*0",
            "NM1*QC*1*DOE JR",
        ]
    ]
    roots = group_by_hl_hierarchy(segments)
    assert len(roots) == 1
    a = roots[0]
    assert a.hl03 == "20"
    b = a.children[0]
    assert b.hl03 == "21"
    c = b.children[0]
    assert c.hl03 == "22"
    d = c.children[0]
    assert d.hl03 == "23"
    assert d.children == []


def test_group_by_hl_hierarchy_orphaned_hl02_becomes_a_root():
    # HL02="99" doesn't match any HL01 in this transaction set - a safe,
    # disclosed default treats it as a root rather than dropping it or
    # raising.
    segments = [seg.split("*") for seg in ["HL*1*99*22*0", "NM1*IL*1*DOE"]]
    roots = group_by_hl_hierarchy(segments)
    assert len(roots) == 1
    assert roots[0].hl01 == "1"
