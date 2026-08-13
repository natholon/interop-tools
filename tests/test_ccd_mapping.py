from pathlib import Path

import pytest
from fhir.resources.R4B.bundle import Bundle

from app.cda.errors import CdaParseError
from app.cda.pipeline import convert_cda_to_bundle
from app.hl7.errors import MappingError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _entries_by_type(bundle):
    entries = {}
    for entry in bundle.entry:
        entries.setdefault(entry.resource.get_resource_type(), []).append(entry)
    return entries


def test_basic_fixture_maps_patient_encounter_and_conditions():
    bundle = convert_cda_to_bundle(read_fixture("ccd_basic.xml"))

    assert bundle.type == "collection"
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    encounter = entries["Encounter"][0].resource
    conditions = [e.resource for e in entries["Condition"]]

    assert patient.identifier[0].value == "998991"
    assert patient.name[0].family == "Betterhalf"
    assert patient.name[0].given == ["Eve"]
    assert patient.gender == "female"
    assert patient.birthDate.isoformat() == "1975-05-01"
    assert patient.address[0].city == "Beaverton"
    assert patient.address[0].state == "OR"
    assert patient.telecom[0].value == "+1-555-555-2003"

    assert encounter.class_fhir.code == "AMB"
    assert encounter.identifier[0].value == "ENC001"
    assert encounter.subject.reference == f"urn:uuid:{patient.id}"
    assert encounter.period.start is not None
    assert encounter.period.end is not None

    assert len(conditions) == 2
    displays = {c.code.coding[0].display for c in conditions}
    assert displays == {"Hypertensive disorder", "Type 2 diabetes mellitus"}
    for condition in conditions:
        assert condition.subject.reference == f"urn:uuid:{patient.id}"
        assert condition.clinicalStatus.coding[0].code == "active"
        assert condition.onsetDateTime is not None

    assert bundle.identifier.value == "TT988"
    assert bundle.timestamp is not None


def test_minimal_fixture_omits_optional_resources_and_fields():
    bundle = convert_cda_to_bundle(read_fixture("ccd_minimal.xml"))

    entries = _entries_by_type(bundle)
    assert list(entries.keys()) == ["Patient"]
    patient = entries["Patient"][0].resource
    assert patient.name[0].family == "Imal"
    assert patient.identifier is None
    assert patient.gender is None
    assert patient.birthDate is None
    assert patient.address is None


def test_malformed_fixture_raises_cda_parse_error():
    with pytest.raises(CdaParseError):
        convert_cda_to_bundle(read_fixture("ccd_malformed.xml"))


def test_unrecognized_document_type_raises_mapping_error():
    with pytest.raises(MappingError):
        convert_cda_to_bundle(read_fixture("ccd_unrecognized_document_type.xml"))


def test_negated_problem_produces_no_condition():
    bundle = convert_cda_to_bundle(read_fixture("ccd_problem_negated.xml"))
    entries = _entries_by_type(bundle)
    assert "Condition" not in entries


def test_status_observation_overrides_act_status():
    bundle = convert_cda_to_bundle(read_fixture("ccd_problem_status_observation_overrides_act_status.xml"))
    entries = _entries_by_type(bundle)
    condition = entries["Condition"][0].resource
    # The Concern Act's own statusCode says "active"; the nested Status
    # Observation says "Resolved" (SNOMED 413322009) - the nested
    # observation must win per the IG.
    assert condition.clinicalStatus.coding[0].code == "resolved"


def test_effective_time_variants_resolve_onset_and_abatement_per_shape():
    bundle = convert_cda_to_bundle(read_fixture("ccd_effective_time_variants.xml"))
    entries = _entries_by_type(bundle)
    conditions = {c.resource.code.coding[0].display: c.resource for c in entries["Condition"]}

    bare_value = conditions["Acute bronchitis"]
    assert bare_value.onsetDateTime.isoformat() == "2022-03-01"
    assert bare_value.abatementDateTime.isoformat() == "2022-03-01"

    low_only = conditions["Asthma"]
    assert low_only.onsetDateTime.isoformat() == "2022-06-15"
    assert low_only.abatementDateTime is None

    low_and_high = conditions["Viral sinusitis"]
    assert low_and_high.onsetDateTime.isoformat() == "2021-01-10"
    assert low_and_high.abatementDateTime.isoformat() == "2021-02-09"

    null_flavor = conditions["Anemia"]
    assert null_flavor.onsetDateTime is None
    assert null_flavor.abatementDateTime is None


def test_unrecognized_section_is_silently_skipped():
    bundle = convert_cda_to_bundle(read_fixture("ccd_unrecognized_section_present.xml"))
    entries = _entries_by_type(bundle)
    # Social History has no registered section builder this slice - its
    # entry must not appear as any resource, but the sibling recognized
    # Problems section must still be processed normally.
    assert set(entries.keys()) == {"Patient", "Condition"}
    assert entries["Condition"][0].resource.code.coding[0].display == "Osteoarthritis"


def test_problem_edge_cases_multi_subj_missing_value_and_wrong_type_code():
    bundle = convert_cda_to_bundle(read_fixture("ccd_problem_edge_cases.xml"))
    entries = _entries_by_type(bundle)
    conditions = entries.get("Condition", [])
    displays = {c.resource.code.coding[0].display for c in conditions}
    # One Concern Act with two entryRelationship[SUBJ] children -> both
    # problems mapped. A Problem Observation with no <value> at all is
    # skipped, not crashed on. A REFR (not SUBJ) relationship wrapping a
    # Problem-Observation-templated element must not be treated as an
    # asserted problem.
    assert displays == {"Asthma", "Anemia"}


def test_header_multiplicities_and_fallbacks():
    bundle = convert_cda_to_bundle(read_fixture("ccd_header_multiplicities.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    encounter = entries["Encounter"][0].resource

    # A root-only <id> (no @extension) is still a complete identifier per
    # the HL7 II datatype - the root itself is the identifier value.
    assert patient.identifier[0].value == "urn:oid:7c2d4e6f-0000-0000-0000-000000000002"
    assert bundle.identifier.value == "urn:oid:8f3c6b2a-0000-0000-0000-000000000001"

    assert [n.use for n in patient.name] == ["official", "old"]
    assert [n.family for n in patient.name] == ["Plicity", "Named"]
    assert len(patient.address) == 2
    assert [t.value for t in patient.telecom] == ["+1-555-555-3001", "+1-555-555-3002"]

    # Both encounter <id> elements must be captured, not just the first.
    assert [i.value for i in encounter.identifier] == ["LOCALENC1", "NATIONALENC1"]
    # An unrecognized encounter class code falls back to the disclosed default.
    assert encounter.class_fhir.code == "AMB"

    # ClinicalDocument/effectiveTime is date-only here - Bundle.timestamp is
    # FHIR "instant" (no date-only form), so it must be omitted rather than
    # crash resource construction.
    assert bundle.timestamp is None


def test_medications_basic_fixture_maps_structured_and_free_text_dosing():
    bundle = convert_cda_to_bundle(read_fixture("ccd_medications_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    requests = {r.resource.medicationCodeableConcept.coding[0].display: r.resource for r in entries["MedicationRequest"]}
    assert len(requests) == 2
    for request in requests.values():
        assert request.subject.reference == f"urn:uuid:{patient.id}"

    structured = requests["Lisinopril 10 MG Oral Tablet"]
    assert structured.status == "active"
    assert structured.intent == "order"  # moodCode INT
    dosage = structured.dosageInstruction[0]
    assert dosage.route.coding[0].display == "ORAL"
    assert float(dosage.doseAndRate[0].doseQuantity.value) == 10
    assert dosage.doseAndRate[0].doseQuantity.unit == "mg"
    assert dosage.timing.repeat.boundsPeriod.start.isoformat() == "2026-07-01"
    assert dosage.timing.repeat.boundsPeriod.end.isoformat() == "2026-10-01"
    assert dosage.patientInstruction is None

    free_text = requests["Amoxicillin 500 MG Oral Capsule"]
    assert free_text.status == "completed"
    assert free_text.intent == "plan"  # moodCode EVN
    assert free_text.dosageInstruction[0].patientInstruction == (
        "Take one capsule by mouth three times daily until gone"
    )
    assert free_text.dosageInstruction[0].route is None


def test_negated_medication_produces_no_medication_request():
    bundle = convert_cda_to_bundle(read_fixture("ccd_medications_negated.xml"))
    entries = _entries_by_type(bundle)
    assert "MedicationRequest" not in entries


def test_allergies_basic_fixture_maps_full_shape():
    bundle = convert_cda_to_bundle(read_fixture("ccd_allergies_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    allergy = entries["AllergyIntolerance"][0].resource

    assert allergy.patient.reference == f"urn:uuid:{patient.id}"
    assert allergy.code.coding[0].display == "Eggs (edible)"
    assert allergy.clinicalStatus.coding[0].code == "active"
    assert allergy.clinicalStatus.coding[0].system == "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
    assert allergy.type == "allergy"
    assert allergy.category == ["food"]
    assert allergy.criticality == "high"
    assert allergy.onsetDateTime.isoformat() == "1998-06-01"
    assert allergy.recordedDate.isoformat() == "2014-01-04"

    assert len(allergy.reaction) == 1
    reaction = allergy.reaction[0]
    assert reaction.manifestation[0].coding[0].display == "Wheal"
    assert reaction.severity == "moderate"
    assert reaction.onset.isoformat() == "1998-06-01"


def test_no_known_allergies_produces_text_only_code():
    bundle = convert_cda_to_bundle(read_fixture("ccd_allergies_no_known.xml"))
    entries = _entries_by_type(bundle)
    allergy = entries["AllergyIntolerance"][0].resource
    assert allergy.code.text == "No known allergies"
    assert allergy.code.coding is None
    assert allergy.clinicalStatus.coding[0].code == "active"


def test_bundle_round_trips_through_json():
    bundle = convert_cda_to_bundle(read_fixture("ccd_basic.xml"))
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
