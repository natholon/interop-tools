from pathlib import Path

from app.hl7.parser import parse_message
from app.mappings.adt import AdtA04Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_register_produces_open_outpatient_encounter():
    message = parse_message(read_fixture("adt_a04_basic.hl7"))
    bundle = AdtA04Mapper().to_bundle(message)
    entries = {e.resource.get_resource_type(): e.resource for e in bundle.entry}
    patient = entries["Patient"]
    encounter = entries["Encounter"]

    assert patient.name[0].family == "Green"
    assert encounter.status == "in-progress"
    assert encounter.class_fhir.code == "AMB"
    assert encounter.location[0].location.display == "CLINIC, C100"
