"""Data Specification provenance tests for C-CDA - the app/cda/ mirror of
tests/test_provenance_recorder.py, kept in its own file since C-CDA is a
genuinely different input format (XML, not HL7v2 pipe-delimited) with its
own parsing/dispatch layer, the same "own file per format" discipline
test_cda_validation.py already established relative to the HL7v2
test_validation_*.py files.

This slice's own scope: the document header (Patient + optional Encounter)
and the Problems section - see app/provenance/dispatch.py's own
_CDA_UNSUPPORTED_REASON for why no CDA document type is "fully
instrumented" yet despite these two pieces producing real facts."""

import itertools
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.cda.parser import parse_document
from app.cda.registry import get_document_builder
from app.provenance.location import xpath_location
from app.provenance.recorder import ProvenanceRecorder
from app.provenance.resolver import resolve_bundle_paths

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _deterministic_uuids():
    return (uuid.UUID(int=i) for i in itertools.count())


def _build_bundle(fixture_name: str, recorder=None):
    document = parse_document(read_fixture(fixture_name))
    builder = get_document_builder(document)
    return builder.build_bundle(document, recorder=recorder)


# A representative fixture per document type this slice's own header +
# Problems instrumentation touches, plus a few fixtures whose own section
# (Medications/Allergies/Vitals/Results/Procedures/Immunizations) is NOT
# instrumented yet - included specifically to prove the "accept recorder,
# no-op" additions to those 8 other SECTION_BUILDERS entries don't alter
# their own output either.
_CDA_FIXTURES = [
    "ccd_basic.xml",
    "ccd_minimal.xml",
    "ccd_header_multiplicities.xml",
    "ccd_effective_time_variants.xml",
    "ccd_problem_negated.xml",
    "ccd_problem_status_observation_overrides_act_status.xml",
    "ccd_problem_edge_cases.xml",
    "ccd_unrecognized_section_present.xml",
    "ccd_medications_basic.xml",
    "ccd_allergies_basic.xml",
    "ccd_vitals_basic.xml",
    "ccd_results_basic.xml",
    "ccd_procedures_basic.xml",
    "ccd_immunizations_basic.xml",
    "discharge_summary_basic.xml",
    "history_and_physical_basic.xml",
]


@pytest.mark.parametrize("fixture", _CDA_FIXTURES)
def test_cda_provenance_recording_does_not_change_bundle_output(fixture):
    # The critical regression test, mirroring test_provenance_recorder.py's
    # own ADT/SIU/ORU/MDM versions exactly: instrumenting a document
    # builder to also record provenance must never change what it actually
    # builds.
    document = parse_document(read_fixture(fixture))
    builder = get_document_builder(document)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        untraced = builder.build_bundle(document)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        recorder = ProvenanceRecorder(source_format="CDA")
        traced = builder.build_bundle(document, recorder=recorder)

    assert untraced.model_dump(exclude_none=True) == traced.model_dump(exclude_none=True)

    entries = resolve_bundle_paths(traced, recorder)
    assert len(entries) == len(recorder.facts)


def test_ccd_basic_crosswalk_matches_known_field_values():
    # Direct content-correctness check against ccd_basic.xml's own
    # already-known values (test_ccd_mapping.py's own assertions).
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    identifier_entry = by_path["Bundle.entry[0].resource.identifier[0].value"]
    assert identifier_entry.value == "998991"
    assert identifier_entry.source_location == xpath_location("recordTarget", "patientRole", "id[0]")

    family_entry = by_path["Bundle.entry[0].resource.name[0].family"]
    assert family_entry.value == "Betterhalf"
    given_entry = by_path["Bundle.entry[0].resource.name[0].given[0]"]
    assert given_entry.value == "Eve"

    gender_entry = by_path["Bundle.entry[0].resource.gender"]
    assert gender_entry.value == "female"
    assert gender_entry.source_value == "F"

    birth_date_entry = by_path["Bundle.entry[0].resource.birthDate"]
    assert birth_date_entry.value == "1975-05-01"
    assert birth_date_entry.source_value == "19750501"

    city_entry = by_path["Bundle.entry[0].resource.address[0].city"]
    assert city_entry.value == "Beaverton"

    telecom_entry = by_path["Bundle.entry[0].resource.telecom[0].value"]
    assert telecom_entry.value == "+1-555-555-2003"
    assert telecom_entry.source_value == "tel:+1-555-555-2003"

    class_entry = by_path["Bundle.entry[1].resource.class.code"]
    assert class_entry.value == "AMB"
    assert class_entry.source_value == "AMB"

    period_start_entry = by_path["Bundle.entry[1].resource.period.start"]
    assert period_start_entry.source_location == xpath_location(
        "componentOf/encompassingEncounter/effectiveTime/low/@value"
    )
    period_end_entry = by_path["Bundle.entry[1].resource.period.end"]
    assert period_end_entry.source_location == xpath_location(
        "componentOf/encompassingEncounter/effectiveTime/high/@value"
    )

    condition0_code = by_path["Bundle.entry[2].resource.code.coding[0].code"]
    assert condition0_code.value == "38341003"
    condition0_display = by_path["Bundle.entry[2].resource.code.coding[0].display"]
    assert condition0_display.value == "Hypertensive disorder"
    condition0_status = by_path["Bundle.entry[2].resource.clinicalStatus.coding[0].code"]
    assert condition0_status.value == "active"
    assert condition0_status.source_location == xpath_location("act", "statusCode", "@code")
    condition0_onset = by_path["Bundle.entry[2].resource.onsetDateTime"]
    assert condition0_onset.value == "2025-01-03"
    assert "Bundle.entry[2].resource.abatementDateTime" not in by_path

    bundle_identifier = by_path["Bundle.identifier.value"]
    assert bundle_identifier.value == "TT988"
    timestamp_entry = by_path["Bundle.timestamp"]
    assert timestamp_entry.source_value == "20260812101500-0500"


def test_ccd_header_multiplicities_records_correct_indices_for_repeating_fields():
    # ccd_header_multiplicities.xml carries two <name>s, two <addr>s, two
    # <telecom>s, and two encounter <id>s - every kept FHIR array index
    # must match the fact recorded for it, and every source_location must
    # point at the correct XML repetition index too.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_header_multiplicities.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    name0 = by_path["Bundle.entry[0].resource.name[0].family"]
    assert name0.value == "Plicity"
    name1 = by_path["Bundle.entry[0].resource.name[1].family"]
    assert name1.value == "Named"
    assert name1.source_location == xpath_location("recordTarget", "patientRole", "patient", "name[1]", "family")

    addr0 = by_path["Bundle.entry[0].resource.address[0].city"]
    assert addr0.value == "Portland"
    addr1 = by_path["Bundle.entry[0].resource.address[1].postalCode"]
    assert addr1.value == "97204"
    assert addr1.source_location == xpath_location("recordTarget", "patientRole", "addr[1]", "postalCode")

    telecom0 = by_path["Bundle.entry[0].resource.telecom[0].value"]
    assert telecom0.value == "+1-555-555-3001"
    telecom1 = by_path["Bundle.entry[0].resource.telecom[1].value"]
    assert telecom1.value == "+1-555-555-3002"

    # A root-only <id> (no @extension) still produces a real fact - the
    # value is the fallback urn:oid: representation, not a skipped index.
    patient_id_entry = by_path["Bundle.entry[0].resource.identifier[0].value"]
    assert patient_id_entry.value.startswith("urn:oid:")

    encounter_id0 = by_path["Bundle.entry[1].resource.identifier[0].value"]
    assert encounter_id0.value == "LOCALENC1"
    encounter_id1 = by_path["Bundle.entry[1].resource.identifier[1].value"]
    assert encounter_id1.value == "NATIONALENC1"
    assert encounter_id1.source_location == xpath_location("componentOf/encompassingEncounter/id[1]")

    # This fixture's own encompassingEncounter/effectiveTime carries only a
    # <low>, no <high> - period.end must never appear.
    assert "Bundle.entry[1].resource.period.end" not in by_path


def test_ccd_effective_time_variants_records_bare_value_vs_low_high_shapes():
    # ccd_effective_time_variants.xml exercises all three IVL_TS shapes
    # across its own Problem entries - the recorded source_location must
    # reflect whichever shape a given entry actually used, not a guessed
    # default.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_effective_time_variants.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    # Bare @value entry (entry[1]) - onset and abatement share the
    # identical bare-value location, not a fabricated low/high one.
    bare_onset = by_path["Bundle.entry[1].resource.onsetDateTime"]
    assert bare_onset.source_location == xpath_location(
        "act/entryRelationship[SUBJ]/observation/effectiveTime/@value"
    )
    bare_abatement = by_path["Bundle.entry[1].resource.abatementDateTime"]
    assert bare_abatement.source_location == bare_onset.source_location

    # low-only entry (entry[2]) - onset present via low/@value, no
    # abatement fact at all (nullFlavor/absent high).
    low_only_onset = by_path["Bundle.entry[2].resource.onsetDateTime"]
    assert low_only_onset.source_location == xpath_location(
        "act/entryRelationship[SUBJ]/observation/effectiveTime/low/@value"
    )
    assert "Bundle.entry[2].resource.abatementDateTime" not in by_path

    # low+high entry (entry[3]) - both bounds present via their own
    # dedicated children.
    both_onset = by_path["Bundle.entry[3].resource.onsetDateTime"]
    assert both_onset.source_location == xpath_location(
        "act/entryRelationship[SUBJ]/observation/effectiveTime/low/@value"
    )
    both_abatement = by_path["Bundle.entry[3].resource.abatementDateTime"]
    assert both_abatement.source_location == xpath_location(
        "act/entryRelationship[SUBJ]/observation/effectiveTime/high/@value"
    )

    # nullFlavor entry (entry[4]) - fully unknown, no onset/abatement fact.
    assert "Bundle.entry[4].resource.onsetDateTime" not in by_path
    assert "Bundle.entry[4].resource.abatementDateTime" not in by_path


def test_ccd_problem_status_observation_overrides_act_status_records_nested_location():
    # When a nested Status Observation resolves, its own location - not the
    # Act's own statusCode - must be what's recorded, mirroring
    # test_ccd_mapping.py's identical Bundle-side assertion that the
    # resolved (not act-level) status wins.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_problem_status_observation_overrides_act_status.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    status_entry = by_path["Bundle.entry[1].resource.clinicalStatus.coding[0].code"]
    assert status_entry.value == "resolved"
    assert status_entry.source_location == xpath_location(
        "act",
        "entryRelationship[SUBJ]",
        "observation",
        "entryRelationship[REFR]",
        "observation",
        "value",
        "@code",
    )


def test_ccd_problem_negated_produces_no_condition_facts():
    # negationInd="true" makes build_condition return None entirely (the
    # "no known problem" pattern) - no Condition-scoped fact should appear
    # anywhere in the resolved crosswalk, only the header's own facts.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_problem_negated.xml", recorder=recorder)
    assert not any(e.resource.get_resource_type() == "Condition" for e in bundle.entry)
    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == len(recorder.facts)
    assert not any("Condition" in e.fhir_path or "code.coding" in e.fhir_path for e in entries)


def test_ccd_medications_section_records_no_facts_yet():
    # Medications isn't instrumented this slice (see
    # app/cda/medications.py::build_medication_requests' own docstring) -
    # a MedicationRequest still gets built (conversion is unaffected), but
    # zero facts should be recorded against it, only the header's own.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_medications_basic.xml", recorder=recorder)
    medication_requests = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "MedicationRequest"]
    assert len(medication_requests) > 0

    entries = resolve_bundle_paths(bundle, recorder)
    recorded_resource_ids = {e.fhir_path.split(".resource.")[0] for e in entries if ".resource." in e.fhir_path}
    medication_entry_indices = {
        i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "MedicationRequest"
    }
    assert not any(f"Bundle.entry[{i}]" in recorded_resource_ids for i in medication_entry_indices)


def test_discharge_summary_hospital_discharge_diagnosis_condition_also_recorded():
    # Hospital Discharge Diagnosis reuses build_condition directly (see
    # app/cda/hospital_discharge_diagnosis.py's own docstring) - its own
    # Condition (category="encounter-diagnosis") must get real facts too,
    # "for free," the same way the plain Problems-sourced Condition in the
    # same document does.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("discharge_summary_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    discharge_diagnosis = next(
        e.resource
        for e in bundle.entry
        if e.resource.get_resource_type() == "Condition" and e.resource.category
    )
    plain_problem = next(
        e.resource
        for e in bundle.entry
        if e.resource.get_resource_type() == "Condition" and not e.resource.category
    )
    discharge_index = next(i for i, e in enumerate(bundle.entry) if e.resource is discharge_diagnosis)
    plain_index = next(i for i, e in enumerate(bundle.entry) if e.resource is plain_problem)

    discharge_code_entry = by_path[f"Bundle.entry[{discharge_index}].resource.code.coding[0].code"]
    assert discharge_code_entry.value == discharge_diagnosis.code.coding[0].code
    plain_code_entry = by_path[f"Bundle.entry[{plain_index}].resource.code.coding[0].code"]
    assert plain_code_entry.value == plain_problem.code.coding[0].code


def test_history_and_physical_header_only_fixture_records_header_facts():
    # history_and_physical_basic.xml carries no Problems section at all (an
    # H&P-specific narrative section this app doesn't recognize instead) -
    # only header facts should appear.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("history_and_physical_basic.xml", recorder=recorder)
    assert not any(e.resource.get_resource_type() == "Condition" for e in bundle.entry)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}
    assert by_path["Bundle.entry[0].resource.name[0].family"].value == "Pilford"
    assert by_path["Bundle.identifier.value"].value == "HP100"
