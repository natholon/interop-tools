import pytest

from app.edi.claim_837i import Edi837iBuilder
from app.edi.claim_837p import Edi837pBuilder
from app.edi.eligibility_270 import Edi270Builder
from app.edi.eligibility_271 import Edi271Builder
from app.edi.registry import get_transaction_builder
from app.hl7.errors import MappingError


def test_get_transaction_builder_resolves_270():
    assert isinstance(get_transaction_builder("270"), Edi270Builder)


def test_get_transaction_builder_resolves_271():
    assert isinstance(get_transaction_builder("271"), Edi271Builder)


def test_get_transaction_builder_resolves_837_professional_by_st03():
    assert isinstance(get_transaction_builder("837", "005010X222A1"), Edi837pBuilder)


def test_get_transaction_builder_resolves_837_institutional_by_st03():
    assert isinstance(get_transaction_builder("837", "005010X223A2"), Edi837iBuilder)


def test_get_transaction_builder_defaults_837_to_professional_when_st03_absent():
    # ST03 is situational, not required - a real sender can omit it.
    assert isinstance(get_transaction_builder("837"), Edi837pBuilder)
    assert isinstance(get_transaction_builder("837", ""), Edi837pBuilder)


def test_get_transaction_builder_defaults_837_to_professional_when_st03_unrecognized():
    assert isinstance(get_transaction_builder("837", "005010X999A1"), Edi837pBuilder)


def test_get_transaction_builder_raises_for_unregistered():
    with pytest.raises(MappingError):
        get_transaction_builder("999")


def test_get_transaction_builder_strips_whitespace():
    assert isinstance(get_transaction_builder(" 270 "), Edi270Builder)
