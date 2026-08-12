from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA05Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_pre_admit_sets_planned_status():
    message = parse_message(read_fixture("adt_a05_basic.hl7"))
    bundle = AdtA05Mapper().to_bundle(message)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    assert encounter.status == "planned"
    assert encounter.class_fhir.code == "IMP"
