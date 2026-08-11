import pytest

from app.hl7.errors import MappingError
from app.mappings.adt import (
    AdtA01Mapper,
    AdtA02Mapper,
    AdtA03Mapper,
    AdtA04Mapper,
    AdtA08Mapper,
)
from app.mappings.registry import get_mapper


@pytest.mark.parametrize(
    "trigger_event, expected_type",
    [
        ("A01", AdtA01Mapper),
        ("A02", AdtA02Mapper),
        ("A03", AdtA03Mapper),
        ("A04", AdtA04Mapper),
        ("A08", AdtA08Mapper),
    ],
)
def test_get_mapper_resolves_registered_adt_triggers(trigger_event, expected_type):
    mapper = get_mapper("ADT", trigger_event)
    assert isinstance(mapper, expected_type)


def test_get_mapper_is_case_insensitive():
    mapper = get_mapper("adt", "a01")
    assert isinstance(mapper, AdtA01Mapper)


def test_get_mapper_raises_for_unregistered_trigger():
    with pytest.raises(MappingError):
        get_mapper("ADT", "A99")
