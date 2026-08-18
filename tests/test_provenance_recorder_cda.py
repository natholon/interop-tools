"""Data Specification provenance tests for C-CDA - the app/cda/ mirror of
tests/test_provenance_recorder.py, kept in its own file since C-CDA is a
genuinely different input format (XML, not HL7v2 pipe-delimited) with its
own parsing/dispatch layer, the same "own file per format" discipline
test_cda_validation.py already established relative to the HL7v2
test_validation_*.py files.

Scope: the document header (Patient + optional Encounter), all seven
general-purpose sections (Problems, Medications, Allergies, Immunizations,
Vital Signs, Results, Procedures), Discharge Summary's own Hospital
Discharge Diagnosis/Discharge Medications sections, and now all twelve
narrative-section templateIds app/cda/narrative_sections.py registers
(Discharge Summary's Hospital Course/Plan of Treatment, History and
Physical's own nine) - see app/provenance/dispatch.py's own
_CDA_UNSUPPORTED_REASON for why no CDA document type is "fully
instrumented" yet despite all of this producing real facts: the still-
deferred structured-entry parsing for Plan of Treatment/Social History/
Family History (see app/cda/narrative_sections.py's own docstring) has
nothing to instrument in the first place, and a handful of narrower
per-field gaps disclosed elsewhere remain."""

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


# A representative fixture per document type this slice's own header + all
# seven general-purpose sections' instrumentation touches.
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
    "ccd_medications_negated.xml",
    "ccd_allergies_basic.xml",
    "ccd_allergies_no_known.xml",
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


def test_ccd_medications_basic_crosswalk_matches_known_field_values():
    # ccd_medications_basic.xml's own two entries exercise both dosage
    # representations in one fixture: structured dosing (route/doseQuantity/
    # effectiveTime bounds, moodCode="INT") and the free-text SIG
    # entryRelationship (moodCode="EVN") - the same fixture
    # test_ccd_mapping.py itself asserts against.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_medications_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    medication_indices = [i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "MedicationRequest"]
    assert len(medication_indices) == 2
    structured_index, free_text_index = medication_indices

    code_entry = by_path[f"Bundle.entry[{structured_index}].resource.medicationCodeableConcept.coding[0].code"]
    assert code_entry.value == "314076"
    assert code_entry.derivation == "direct"
    assert code_entry.source_location == xpath_location(
        "substanceAdministration", "consumable", "manufacturedProduct", "manufacturedMaterial", "code", "@code"
    )
    display_entry = by_path[f"Bundle.entry[{structured_index}].resource.medicationCodeableConcept.coding[0].display"]
    assert display_entry.value == "Lisinopril 10 MG Oral Tablet"

    status_entry = by_path[f"Bundle.entry[{structured_index}].resource.status"]
    assert status_entry.value == "active"
    assert status_entry.derivation == "direct"
    assert status_entry.source_location == xpath_location("substanceAdministration", "statusCode", "@code")

    intent_entry = by_path[f"Bundle.entry[{structured_index}].resource.intent"]
    assert intent_entry.value == "order"
    assert intent_entry.derivation == "direct"
    assert intent_entry.source_location == xpath_location("substanceAdministration", "@moodCode")

    route_entry = by_path[f"Bundle.entry[{structured_index}].resource.dosageInstruction[0].route.coding[0].code"]
    assert route_entry.value == "C38288"
    dose_entry = by_path[f"Bundle.entry[{structured_index}].resource.dosageInstruction[0].doseAndRate[0].doseQuantity.value"]
    assert dose_entry.value == "10"
    start_entry = by_path[f"Bundle.entry[{structured_index}].resource.dosageInstruction[0].timing.repeat.boundsPeriod.start"]
    assert start_entry.value == "2026-07-01"
    end_entry = by_path[f"Bundle.entry[{structured_index}].resource.dosageInstruction[0].timing.repeat.boundsPeriod.end"]
    assert end_entry.value == "2026-10-01"

    # The second entry uses the free-text SIG path instead of structured
    # dosing - proving both of _build_dosage's own recorded branches.
    instruction_entry = by_path[f"Bundle.entry[{free_text_index}].resource.dosageInstruction[0].patientInstruction"]
    assert instruction_entry.value == "Take one capsule by mouth three times daily until gone"
    assert f"Bundle.entry[{free_text_index}].resource.dosageInstruction[0].route.coding[0].code" not in by_path

    intent_entry_2 = by_path[f"Bundle.entry[{free_text_index}].resource.intent"]
    assert intent_entry_2.value == "plan"


def test_ccd_medications_negated_produces_no_medication_request_facts():
    # A negated entry produces no MedicationRequest at all (see
    # app/cda/medications.py's own negationInd handling) - so no
    # MedicationRequest-scoped facts should exist anywhere in the resolved
    # crosswalk, mirroring test_ccd_problem_negated_produces_no_condition_facts.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_medications_negated.xml", recorder=recorder)
    assert not any(e.get_resource_type() == "MedicationRequest" for e in bundle.entry)

    entries = resolve_bundle_paths(bundle, recorder)
    assert not any("medicationCodeableConcept" in e.fhir_path or "dosageInstruction" in e.fhir_path for e in entries)


def test_ccd_allergies_basic_crosswalk_matches_known_field_values():
    # ccd_allergies_basic.xml's own single, fully-populated entry exercises
    # every field this section maps in one fixture - allergen, type,
    # category, a real (not fixed-default) clinicalStatus via a Status
    # Observation, criticality, onset, recordedDate, and one reaction with
    # its own manifestation+severity - the same fixture test_ccd_mapping.py
    # itself asserts against.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_allergies_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    allergy_index = next(i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "AllergyIntolerance")
    prefix = f"Bundle.entry[{allergy_index}].resource"

    code_entry = by_path[f"{prefix}.code.coding[0].code"]
    assert code_entry.value == "102263004"
    assert code_entry.derivation == "direct"
    assert code_entry.source_location == xpath_location(
        "act", "entryRelationship[SUBJ]", "observation", "participant[CSM]", "participantRole", "playingEntity", "code", "@code"
    )

    status_entry = by_path[f"{prefix}.clinicalStatus.coding[0].code"]
    assert status_entry.value == "active"
    assert status_entry.derivation == "direct"  # a real Status Observation resolved it, not the fixed default

    type_entry = by_path[f"{prefix}.type"]
    assert type_entry.value == "allergy"
    category_entry = by_path[f"{prefix}.category[0]"]
    assert category_entry.value == "food"
    # type and category are both sourced from the identical <value> element
    # - the "one source field, two FHIR destinations" case this app has
    # established repeatedly (e.g. SIU's AIP-3, MDM's TXA-9).
    assert type_entry.source_location == category_entry.source_location

    criticality_entry = by_path[f"{prefix}.criticality"]
    assert criticality_entry.value == "high"

    reaction_manifestation = by_path[f"{prefix}.reaction[0].manifestation[0].coding[0].code"]
    assert reaction_manifestation.value == "247472004"
    reaction_severity = by_path[f"{prefix}.reaction[0].severity"]
    assert reaction_severity.value == "moderate"
    # The reaction's own nested location is genuinely deeper than (and
    # distinct from) the allergen's own.
    assert reaction_manifestation.source_location != code_entry.source_location


def test_ccd_allergies_no_known_records_inferred_negation_text_and_status():
    # ccd_allergies_no_known.xml's fully-unresolvable-allergen negation
    # case - no Status Observation and no coded allergen at all - so
    # code.text and clinicalStatus both fall back to the IG's own disclosed
    # fixed defaults, recorded as inferred rather than direct.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_allergies_no_known.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    allergy_index = next(i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "AllergyIntolerance")
    prefix = f"Bundle.entry[{allergy_index}].resource"

    code_entry = by_path[f"{prefix}.code.text"]
    assert code_entry.value == "No known allergies"
    assert code_entry.derivation == "inferred"
    assert code_entry.source_location is None

    status_entry = by_path[f"{prefix}.clinicalStatus.coding[0].code"]
    assert status_entry.value == "active"
    assert status_entry.derivation == "inferred"

    assert f"{prefix}.code.coding[0].code" not in by_path


def test_ccd_immunizations_basic_crosswalk_matches_known_field_values():
    # ccd_immunizations_basic.xml's own EVN-mood entries exercise both
    # branches this section maps: an administered, fully-populated entry
    # (structured dosing/lot number/route) and a refused (negationInd)
    # entry with no resolvable effectiveTime - the same fixture
    # test_ccd_mapping.py itself asserts against. Its third, INT-mood
    # planned entry must never appear in the resolved crosswalk at all.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_immunizations_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    immunization_indices = [i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "Immunization"]
    assert len(immunization_indices) == 2
    administered_index, refused_index = immunization_indices

    code_entry = by_path[f"Bundle.entry[{administered_index}].resource.vaccineCode.coding[0].code"]
    assert code_entry.value == "88"
    assert code_entry.derivation == "direct"
    assert code_entry.source_location == xpath_location(
        "substanceAdministration", "consumable", "manufacturedProduct", "manufacturedMaterial", "code", "@code"
    )

    status_entry = by_path[f"Bundle.entry[{administered_index}].resource.status"]
    assert status_entry.value == "completed"
    assert status_entry.derivation == "direct"
    assert status_entry.source_location == xpath_location("substanceAdministration", "statusCode", "@code")

    occurrence_entry = by_path[f"Bundle.entry[{administered_index}].resource.occurrenceDateTime"]
    assert occurrence_entry.value == "2010-08-15"

    lot_entry = by_path[f"Bundle.entry[{administered_index}].resource.lotNumber"]
    assert lot_entry.value == "1"
    route_entry = by_path[f"Bundle.entry[{administered_index}].resource.route.coding[0].code"]
    assert route_entry.value == "C28161"
    dose_entry = by_path[f"Bundle.entry[{administered_index}].resource.doseQuantity.value"]
    assert dose_entry.value == "0.5"

    # The refused entry: status is driven directly by negationInd (a real
    # source field, not a fabricated inference), while occurrenceString's
    # own "Unknown" fallback genuinely has no source field to point at.
    refused_status_entry = by_path[f"Bundle.entry[{refused_index}].resource.status"]
    assert refused_status_entry.value == "not-done"
    assert refused_status_entry.derivation == "direct"
    assert refused_status_entry.source_location == xpath_location("substanceAdministration", "@negationInd")

    occurrence_string_entry = by_path[f"Bundle.entry[{refused_index}].resource.occurrenceString"]
    assert occurrence_string_entry.value == "Unknown"
    assert occurrence_string_entry.derivation == "inferred"
    assert occurrence_string_entry.source_location is None

    # The INT-mood planned entry never produces an Immunization at all -
    # only two vaccineCode facts should exist anywhere in the crosswalk.
    assert sum(1 for e in entries if "vaccineCode.coding[0].code" in e.fhir_path) == 2


def test_ccd_vitals_basic_crosswalk_matches_known_field_values():
    # ccd_vitals_basic.xml's own one Vital Signs Organizer wraps two Vital
    # Sign Observations - Heart Rate (with an interpretationCode) and Body
    # Temperature (without one) - the same fixture test_ccd_mapping.py
    # itself asserts against. The first real proof (after ORU's own
    # DiagnosticReport/Observation grouping) that this pillar can
    # reconstruct a panel/member grouping relationship, not just a flat
    # entry list.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_vitals_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    panel_index = next(
        i
        for i, e in enumerate(bundle.entry)
        if e.resource.get_resource_type() == "Observation" and e.resource.hasMember
    )
    member_indices = [
        i
        for i, e in enumerate(bundle.entry)
        if e.resource.get_resource_type() == "Observation" and not e.resource.hasMember
    ]
    assert len(member_indices) == 2
    heart_rate_index, temperature_index = (
        (member_indices[0], member_indices[1])
        if bundle.entry[member_indices[0]].resource.code.coding[0].code == "8867-4"
        else (member_indices[1], member_indices[0])
    )

    # The panel's own status/category/code are all fixed, inferred facts -
    # never read from the organizer's own /code.
    panel_prefix = f"Bundle.entry[{panel_index}].resource"
    panel_code_entry = by_path[f"{panel_prefix}.code.coding[0].code"]
    assert panel_code_entry.value == "85353-1"
    assert panel_code_entry.derivation == "inferred"
    panel_status_entry = by_path[f"{panel_prefix}.status"]
    assert panel_status_entry.derivation == "inferred"
    panel_effective_entry = by_path[f"{panel_prefix}.effectiveDateTime"]
    assert panel_effective_entry.derivation == "direct"
    assert panel_effective_entry.source_location == xpath_location("organizer", "effectiveTime", "@value")

    heart_rate_prefix = f"Bundle.entry[{heart_rate_index}].resource"
    hr_code_entry = by_path[f"{heart_rate_prefix}.code.coding[0].code"]
    assert hr_code_entry.value == "8867-4"
    assert hr_code_entry.derivation == "direct"
    assert hr_code_entry.source_location == xpath_location("organizer", "component[0]", "observation", "code", "@code")
    hr_value_entry = by_path[f"{heart_rate_prefix}.valueQuantity.value"]
    assert hr_value_entry.value == "76"
    hr_unit_entry = by_path[f"{heart_rate_prefix}.valueQuantity.unit"]
    assert hr_unit_entry.value == "/min"
    hr_interpretation_entry = by_path[f"{heart_rate_prefix}.interpretation[0].coding[0].code"]
    assert hr_interpretation_entry.value == "N"

    temperature_prefix = f"Bundle.entry[{temperature_index}].resource"
    temp_code_entry = by_path[f"{temperature_prefix}.code.coding[0].code"]
    assert temp_code_entry.value == "8310-5"
    assert temp_code_entry.source_location == xpath_location("organizer", "component[1]", "observation", "code", "@code")
    # The two members' source locations must genuinely differ - the
    # disambiguation this section's own multiple-member shape needs, the
    # same collision class Allergies'/837I's own fixes already established.
    assert temp_code_entry.source_location != hr_code_entry.source_location
    assert f"{temperature_prefix}.interpretation[0].coding[0].code" not in by_path


def test_ccd_results_basic_crosswalk_matches_known_field_values():
    # ccd_results_basic.xml's own one Result Organizer wraps two Result
    # Observations - a PQ-valued one with a referenceRange, and a free-text
    # ST-valued one - exercising two of _build_observation_value's own
    # xsi:type branches in one fixture, plus a real (not fallback) report
    # code, the same fixture test_ccd_mapping.py itself asserts against.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_results_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    report_index = next(i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "DiagnosticReport")
    report_prefix = f"Bundle.entry[{report_index}].resource"

    report_code_entry = by_path[f"{report_prefix}.code.coding[0].code"]
    assert report_code_entry.value == "58410-2"
    assert report_code_entry.derivation == "direct"
    assert report_code_entry.source_location == xpath_location("organizer", "code", "@code")
    report_status_entry = by_path[f"{report_prefix}.status"]
    assert report_status_entry.value == "final"
    assert report_status_entry.derivation == "direct"
    assert report_status_entry.source_location == xpath_location("organizer", "statusCode", "@code")

    pq_index = next(
        i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "Observation" and e.resource.code.coding[0].code == "6690-2"
    )
    st_index = next(
        i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "Observation" and e.resource.code.coding[0].code == "33747-0"
    )

    pq_prefix = f"Bundle.entry[{pq_index}].resource"
    value_entry = by_path[f"{pq_prefix}.valueQuantity.value"]
    assert value_entry.value == "6.8"
    unit_entry = by_path[f"{pq_prefix}.valueQuantity.unit"]
    assert unit_entry.value == "10*3/uL"
    range_low_entry = by_path[f"{pq_prefix}.referenceRange[0].low.value"]
    assert range_low_entry.value == "4.5"
    range_high_entry = by_path[f"{pq_prefix}.referenceRange[0].high.value"]
    assert range_high_entry.value == "11.0"

    st_prefix = f"Bundle.entry[{st_index}].resource"
    string_entry = by_path[f"{st_prefix}.valueString"]
    assert string_entry.value == "No growth after 48 hours"
    assert string_entry.derivation == "direct"
    assert f"{st_prefix}.valueQuantity.value" not in by_path

    # The two members' own source locations must genuinely differ, the
    # same disambiguation Vital Signs' own multiple-member fix established.
    pq_code_entry = by_path[f"{pq_prefix}.code.coding[0].code"]
    st_code_entry = by_path[f"{st_prefix}.code.coding[0].code"]
    assert pq_code_entry.source_location != st_code_entry.source_location


def test_ccd_procedures_basic_crosswalk_matches_known_field_values():
    # ccd_procedures_basic.xml's own two entries exercise both
    # performedDateTime-vs-performedPeriod branches and both identifier
    # shapes in one fixture - a completed, point-in-time Appendectomy with
    # a root+extension id, and a negated Colonoscopy (a low+high period,
    # status driven by negationInd) with a root-only id - the same fixture
    # test_ccd_mapping.py itself asserts against. The last general-purpose
    # C-CDA section instrumented, completing full section breadth for this
    # pillar.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("ccd_procedures_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    procedure_indices = [i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "Procedure"]
    assert len(procedure_indices) == 2
    completed_index, negated_index = (
        procedure_indices
        if bundle.entry[procedure_indices[0]].resource.status == "completed"
        else procedure_indices[::-1]
    )

    completed_prefix = f"Bundle.entry[{completed_index}].resource"
    status_entry = by_path[f"{completed_prefix}.status"]
    assert status_entry.value == "completed"
    assert status_entry.derivation == "direct"
    assert status_entry.source_location == xpath_location("procedure", "statusCode", "@code")
    code_entry = by_path[f"{completed_prefix}.code.coding[0].code"]
    assert code_entry.value == "80146002"
    identifier_entry = by_path[f"{completed_prefix}.identifier[0].value"]
    assert identifier_entry.value == "PROC001"
    performed_entry = by_path[f"{completed_prefix}.performedDateTime"]
    assert performed_entry.value == "2026-06-15T12:00:00-05:00"
    assert performed_entry.source_location == xpath_location("procedure", "effectiveTime", "@value")
    body_site_entry = by_path[f"{completed_prefix}.bodySite[0].coding[0].code"]
    assert body_site_entry.value == "66754008"

    negated_prefix = f"Bundle.entry[{negated_index}].resource"
    negated_status_entry = by_path[f"{negated_prefix}.status"]
    assert negated_status_entry.value == "not-done"
    assert negated_status_entry.derivation == "direct"
    assert negated_status_entry.source_location == xpath_location("procedure", "@negationInd")
    negated_identifier_entry = by_path[f"{negated_prefix}.identifier[0].value"]
    # The root-only identifier shape - _reverse_generic_identifier's own
    # fuller three-shape resolution, this fixture's own real exercise of it.
    assert negated_identifier_entry.value.startswith("urn:oid:")
    start_entry = by_path[f"{negated_prefix}.performedPeriod.start"]
    assert start_entry.value == "2026-02-01"
    end_entry = by_path[f"{negated_prefix}.performedPeriod.end"]
    assert end_entry.value == "2026-02-01"
    assert f"{negated_prefix}.performedDateTime" not in by_path


def test_discharge_medications_reuses_medications_own_recorder_instrumentation():
    # build_discharge_medication_requests reuses build_medication_request
    # directly (see app/cda/discharge_medications.py's own docstring) - its
    # own real fixture's Discharge Medications-sourced MedicationRequest
    # must carry the identical recorded facts a plain-Medications-sourced
    # one would, not silently stay uninstrumented just because a different
    # outer Act template wraps it.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("discharge_summary_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    medication_index = next(
        i for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "MedicationRequest"
    )
    code_entry = by_path[f"Bundle.entry[{medication_index}].resource.medicationCodeableConcept.coding[0].code"]
    assert code_entry.derivation == "direct"


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
    # H&P-specific narrative section instead) - only header facts should
    # appear from that gap specifically.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("history_and_physical_basic.xml", recorder=recorder)
    assert not any(e.resource.get_resource_type() == "Condition" for e in bundle.entry)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}
    assert by_path["Bundle.entry[0].resource.name[0].family"].value == "Pilford"
    assert by_path["Bundle.identifier.value"].value == "HP100"


def test_discharge_summary_hospital_course_records_precise_location_for_unwrapped_text():
    # The real fixture's own Hospital Course section has no <paragraph>
    # wrapper at all - the one shape narrative-section provenance can point
    # at a precise location for (see build_narrative_document_reference's
    # own docstring).
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("discharge_summary_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    document_references = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"]
    hospital_course = next(dr for dr in document_references if dr.type.coding[0].code == "8648-8")
    index = next(i for i, e in enumerate(bundle.entry) if e.resource is hospital_course)

    code_entry = by_path[f"Bundle.entry[{index}].resource.type.coding[0].code"]
    assert code_entry.derivation == "direct"
    assert code_entry.source_location == "code/@code"
    assert code_entry.value == "8648-8"

    description_entry = by_path[f"Bundle.entry[{index}].resource.description"]
    assert description_entry.source_location == "title"
    assert description_entry.value == "Hospital Course"

    binary_id = hospital_course.content[0].attachment.url.removeprefix("urn:uuid:")
    binary_index = next(i for i, e in enumerate(bundle.entry) if e.resource.id == binary_id)
    data_entry = by_path[f"Bundle.entry[{binary_index}].resource.data"]
    assert data_entry.source_location == "text"
    assert "community-acquired pneumonia" in data_entry.value.lower()


def test_discharge_summary_plan_of_treatment_records_disclosed_marker_for_table():
    # The real fixture's own Plan of Treatment section is table-shaped (a
    # header row plus two data rows, three <tr> lines total) - a disclosed
    # block-count marker, not a precise single location, since Binary.data
    # joins multiple rows with no per-row boundary kept.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("discharge_summary_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    document_references = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"]
    plan_of_treatment = next(dr for dr in document_references if dr.type.coding[0].code == "18776-5")
    binary_id = plan_of_treatment.content[0].attachment.url.removeprefix("urn:uuid:")
    binary_index = next(i for i, e in enumerate(bundle.entry) if e.resource.id == binary_id)

    data_entry = by_path[f"Bundle.entry[{binary_index}].resource.data"]
    assert data_entry.source_location == "text (×3 blocks)"
    assert "Planned Activity | Planned Date" in data_entry.value


def test_history_and_physical_all_nine_narrative_sections_recorded():
    # Every one of the real fixture's nine H&P-specific narrative sections
    # (plus the shared Plan of Treatment) must independently produce its
    # own type/description/data facts - none silently skipped, none
    # cross-contaminating another section's own facts.
    recorder = ProvenanceRecorder(source_format="CDA")
    bundle = _build_bundle("history_and_physical_basic.xml", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    document_references = [
        (i, e.resource) for i, e in enumerate(bundle.entry) if e.resource.get_resource_type() == "DocumentReference"
    ]
    assert len(document_references) == 9

    for index, document_reference in document_references:
        code_entry = by_path.get(f"Bundle.entry[{index}].resource.type.coding[0].code")
        assert code_entry is not None, f"no type fact recorded for entry {index}"
        assert code_entry.value == document_reference.type.coding[0].code

        description_entry = by_path.get(f"Bundle.entry[{index}].resource.description")
        assert description_entry is not None
        assert description_entry.value == document_reference.description

        binary_id = document_reference.content[0].attachment.url.removeprefix("urn:uuid:")
        binary_index = next(i for i, e in enumerate(bundle.entry) if e.resource.id == binary_id)
        data_entry = by_path.get(f"Bundle.entry[{binary_index}].resource.data")
        assert data_entry is not None
        assert len(data_entry.value) > 0
