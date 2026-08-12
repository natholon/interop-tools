import pytest

from app.hl7.errors import MappingError
from app.mappings.adt import (
    AdtA01Mapper,
    AdtA02Mapper,
    AdtA03Mapper,
    AdtA04Mapper,
    AdtA05Mapper,
    AdtA08Mapper,
    AdtA11Mapper,
    AdtA13Mapper,
)
from app.mappings.mdm import MdmT02Mapper, MdmT04Mapper, MdmT06Mapper
from app.mappings.oru import OruR01Mapper, OruR30Mapper, OruR40Mapper
from app.mappings.registry import get_mapper
from app.mappings.siu import SiuS12Mapper, SiuS13Mapper, SiuS14Mapper, SiuS15Mapper, SiuS17Mapper, SiuS26Mapper


@pytest.mark.parametrize(
    "trigger_event, expected_type",
    [
        ("A01", AdtA01Mapper),
        ("A02", AdtA02Mapper),
        ("A03", AdtA03Mapper),
        ("A04", AdtA04Mapper),
        ("A05", AdtA05Mapper),
        ("A08", AdtA08Mapper),
        ("A11", AdtA11Mapper),
        ("A13", AdtA13Mapper),
    ],
)
def test_get_mapper_resolves_registered_adt_triggers(trigger_event, expected_type):
    mapper = get_mapper("ADT", trigger_event)
    assert isinstance(mapper, expected_type)


@pytest.mark.parametrize(
    "trigger_event, expected_type",
    [
        ("S12", SiuS12Mapper),
        ("S13", SiuS13Mapper),
        ("S14", SiuS14Mapper),
        ("S15", SiuS15Mapper),
        ("S17", SiuS17Mapper),
        ("S26", SiuS26Mapper),
    ],
)
def test_get_mapper_resolves_registered_siu_triggers(trigger_event, expected_type):
    mapper = get_mapper("SIU", trigger_event)
    assert isinstance(mapper, expected_type)


@pytest.mark.parametrize(
    "trigger_event, expected_type",
    [
        ("R01", OruR01Mapper),
        ("R30", OruR30Mapper),
        ("R40", OruR40Mapper),
    ],
)
def test_get_mapper_resolves_registered_oru_triggers(trigger_event, expected_type):
    mapper = get_mapper("ORU", trigger_event)
    assert isinstance(mapper, expected_type)


@pytest.mark.parametrize(
    "trigger_event, expected_type",
    [
        ("T02", MdmT02Mapper),
        ("T04", MdmT04Mapper),
        ("T06", MdmT06Mapper),
    ],
)
def test_get_mapper_resolves_registered_mdm_triggers(trigger_event, expected_type):
    mapper = get_mapper("MDM", trigger_event)
    assert isinstance(mapper, expected_type)


def test_get_mapper_raises_for_unregistered_mdm_trigger():
    with pytest.raises(MappingError):
        get_mapper("MDM", "T99")


def test_get_mapper_is_case_insensitive():
    mapper = get_mapper("adt", "a01")
    assert isinstance(mapper, AdtA01Mapper)


def test_get_mapper_raises_for_unregistered_trigger():
    with pytest.raises(MappingError):
        get_mapper("ADT", "A99")


def test_get_mapper_raises_for_unregistered_siu_trigger():
    with pytest.raises(MappingError):
        get_mapper("SIU", "S99")


def test_get_mapper_raises_for_unregistered_oru_trigger():
    with pytest.raises(MappingError):
        get_mapper("ORU", "R99")
