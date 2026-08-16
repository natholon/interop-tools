import pytest

from app.edi.claim_837p import Edi837pBuilder
from app.edi.eligibility_270 import Edi270Builder
from app.edi.eligibility_271 import Edi271Builder
from app.edi.registry import get_transaction_builder
from app.hl7.errors import MappingError


def test_get_transaction_builder_resolves_270():
    assert isinstance(get_transaction_builder("270"), Edi270Builder)


def test_get_transaction_builder_resolves_271():
    assert isinstance(get_transaction_builder("271"), Edi271Builder)


def test_get_transaction_builder_resolves_837():
    assert isinstance(get_transaction_builder("837"), Edi837pBuilder)


def test_get_transaction_builder_raises_for_unregistered():
    with pytest.raises(MappingError):
        get_transaction_builder("999")


def test_get_transaction_builder_strips_whitespace():
    assert isinstance(get_transaction_builder(" 270 "), Edi270Builder)
