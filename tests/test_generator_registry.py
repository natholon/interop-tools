import pytest

from app.generators.registry import generate, list_supported_types
from app.hl7.errors import MappingError

_EXPECTED_TYPES = {
    ("ADT", "A01"),
    ("ADT", "A02"),
    ("ADT", "A03"),
    ("ADT", "A04"),
    ("ADT", "A05"),
    ("ADT", "A08"),
    ("ADT", "A11"),
    ("ADT", "A13"),
    ("ADT", "A38"),
    ("SIU", "S12"),
    ("SIU", "S13"),
    ("SIU", "S14"),
    ("SIU", "S15"),
    ("SIU", "S17"),
    ("SIU", "S26"),
    ("ORU", "R01"),
    ("ORU", "R30"),
    ("ORU", "R31"),
    ("ORU", "R32"),
    ("ORU", "R40"),
    ("MDM", "T02"),
    ("MDM", "T04"),
    ("MDM", "T06"),
    ("MDM", "T08"),
    ("MDM", "T10"),
    ("MDM", "T11"),
    ("CDA", "CCD"),
    ("CDA", "DISCHARGESUMMARY"),
    ("EDI", "270"),
    ("EDI", "271"),
    ("EDI", "276"),
    ("EDI", "277"),
    ("EDI", "278REQUEST"),
    ("EDI", "278RESPONSE"),
    ("EDI", "835"),
    ("EDI", "837P"),
    ("EDI", "837I"),
}


def test_list_supported_types_returns_every_registered_combination():
    types = {(msg_type, trigger) for msg_type, trigger, _label in list_supported_types()}
    assert types == _EXPECTED_TYPES


def test_list_supported_types_labels_are_non_empty_strings():
    for _msg_type, _trigger, label in list_supported_types():
        assert isinstance(label, str) and label


def test_generate_is_reproducible_with_same_seed():
    assert generate("ADT", "A01", seed=7) == generate("ADT", "A01", seed=7)


def test_generate_differs_across_seeds():
    assert generate("ADT", "A01", seed=1) != generate("ADT", "A01", seed=2)


def test_generate_is_case_insensitive():
    assert generate("adt", "a01", seed=1) == generate("ADT", "A01", seed=1)


def test_generate_raises_for_unsupported_combination():
    with pytest.raises(MappingError):
        generate("ADT", "A99")
