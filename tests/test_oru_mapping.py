from pathlib import Path

import pytest

from app.hl7.errors import MissingSegmentError
from app.hl7.parser import parse_message
from app.mappings.oru import OruR01Mapper, OruR30Mapper, OruR31Mapper, OruR32Mapper, OruR40Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _entries_by_type(bundle) -> dict:
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_groups_observations_under_correct_report():
    # Two OBR groups: CBC (WBC + HGB) and Glucose Panel (Glucose only). Each
    # DiagnosticReport.result must reference only its own group's Observations.
    message = parse_message(read_fixture("oru_r01_basic.hl7"))
    bundle = OruR01Mapper().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert len(by_type["DiagnosticReport"]) == 2
    assert len(by_type["Observation"]) == 3
    assert len(by_type["Practitioner"]) == 1  # OBX-16 responsible observer on the WBC result

    cbc_report = next(r for r in by_type["DiagnosticReport"] if r.code.coding[0].code == "CBC")
    glucose_report = next(r for r in by_type["DiagnosticReport"] if r.code.coding[0].code == "GLU")

    observations_by_id = {o.id: o for o in by_type["Observation"]}
    cbc_result_codes = {observations_by_id[ref.reference.removeprefix("urn:uuid:")].code.coding[0].code for ref in cbc_report.result}
    glucose_result_codes = {
        observations_by_id[ref.reference.removeprefix("urn:uuid:")].code.coding[0].code for ref in glucose_report.result
    }
    assert cbc_result_codes == {"WBC", "HGB"}
    assert glucose_result_codes == {"GLUCOSE"}

    assert cbc_report.status == "final"
    assert glucose_report.status == "preliminary"
    assert cbc_report.effectiveDateTime.isoformat() == "2026-08-11T10:00:00+00:00"
    assert cbc_report.issued.isoformat() == "2026-08-11T11:00:00+00:00"

    wbc = next(o for o in by_type["Observation"] if o.code.coding[0].code == "WBC")
    assert wbc.status == "final"
    assert float(wbc.valueQuantity.value) == 7.2
    assert wbc.valueQuantity.unit == "10*3/uL"
    assert wbc.interpretation[0].coding[0].code == "N"
    assert wbc.referenceRange[0].text == "4.0-11.0"
    assert wbc.performer[0].reference == f"urn:uuid:{by_type['Practitioner'][0].id}"

    # Every Observation and DiagnosticReport should reference the same Patient and Encounter.
    patient = by_type["Patient"][0]
    encounter = by_type["Encounter"][0]
    for resource in by_type["DiagnosticReport"] + by_type["Observation"]:
        assert resource.subject.reference == f"urn:uuid:{patient.id}"
        assert resource.encounter.reference == f"urn:uuid:{encounter.id}"


def test_minimal_fixture_without_pv1_omits_encounter():
    message = parse_message(read_fixture("oru_r01_minimal.hl7"))
    bundle = OruR01Mapper().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert "Encounter" not in by_type
    report = by_type["DiagnosticReport"][0]
    observation = by_type["Observation"][0]
    assert report.encounter is None
    assert observation.encounter is None


def test_shared_performer_deduped_across_observations():
    # Two OBX results with the same OBX-16 performer id must produce ONE
    # Practitioner resource, referenced by both Observations - not two
    # near-identical Practitioners for the same real person.
    message = parse_message(read_fixture("oru_r01_shared_performer.hl7"))
    bundle = OruR01Mapper().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert len(by_type["Practitioner"]) == 1
    practitioner = by_type["Practitioner"][0]
    assert practitioner.name[0].family == "Rivera"
    assert len(by_type["Observation"]) == 2
    for observation in by_type["Observation"]:
        assert observation.performer[0].reference == f"urn:uuid:{practitioner.id}"


def test_ft_value_with_caret_is_not_truncated():
    # Regression test: FT/TX/ST are unstructured free text, not HL7
    # composite - a literal '^' in the text used to get silently truncated
    # because _build_observation_value read it via field_str (component 1
    # only) instead of the whole field.
    message = parse_message(read_fixture("oru_r01_ft_with_caret.hl7"))
    bundle = OruR01Mapper().to_bundle(message)
    observation = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation")
    assert observation.valueString == "Grade II^ tear noted; recommend follow-up"


def test_missing_obr_raises_missing_segment_error():
    message = parse_message(read_fixture("oru_r01_missing_obr.hl7"))
    with pytest.raises(MissingSegmentError):
        OruR01Mapper().to_bundle(message)


@pytest.mark.parametrize(
    "mapper_cls, fixture_name",
    [
        (OruR30Mapper, "oru_r30_basic.hl7"),
        (OruR40Mapper, "oru_r40_basic.hl7"),
        (OruR31Mapper, "oru_r31_basic.hl7"),
        (OruR32Mapper, "oru_r32_basic.hl7"),
    ],
)
def test_point_of_care_triggers_produce_same_shape_as_r01(mapper_cls, fixture_name):
    message = parse_message(read_fixture(fixture_name))
    bundle = mapper_cls().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert len(by_type["DiagnosticReport"]) == 1
    assert len(by_type["Observation"]) == 1
    assert by_type["DiagnosticReport"][0].status == "final"
