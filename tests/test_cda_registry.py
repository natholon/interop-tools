from pathlib import Path

import pytest

from app.cda.ccd import CcdBuilder
from app.cda.parser import parse_document
from app.cda.registry import SECTION_BUILDERS, get_document_builder
from app.cda.problems import SECTION_TEMPLATE_ID, build_conditions
from app.hl7.errors import MappingError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_get_document_builder_resolves_ccd():
    document = parse_document(read_fixture("ccd_basic.xml"))
    builder = get_document_builder(document)
    assert isinstance(builder, CcdBuilder)


def test_get_document_builder_raises_for_unrecognized_template():
    document = parse_document(read_fixture("ccd_unrecognized_document_type.xml"))
    with pytest.raises(MappingError):
        get_document_builder(document)


def test_section_builders_registers_problems_section():
    assert SECTION_BUILDERS[SECTION_TEMPLATE_ID] is build_conditions
