import itertools
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.patient import Patient

from app.hl7.parser import parse_message
from app.mappings.adt import (
    AdtA01Mapper,
    AdtA02Mapper,
    AdtA03Mapper,
    AdtA04Mapper,
    AdtA05Mapper,
    AdtA08Mapper,
    AdtA11Mapper,
    AdtA13Mapper,
    AdtA38Mapper,
)
from app.provenance.location import hl7_location
from app.provenance.recorder import ProvenanceRecorder
from app.provenance.resolver import resolve_bundle_paths

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _deterministic_uuids():
    return (uuid.UUID(int=i) for i in itertools.count())


# One representative fixture per ADT trigger (both branches for the two
# triggers - A08, A13 - whose own docstrings disclose a real behavioral
# split), excluding the two malformed/missing-required-field error fixtures
# (adt_a01_malformed.hl7, adt_a03_missing_discharge.hl7) since those never
# reach a Bundle to compare in the first place.
_ADT_FIXTURES = [
    (AdtA01Mapper, "adt_a01_basic.hl7"),
    (AdtA01Mapper, "adt_a01_minimal.hl7"),
    (AdtA02Mapper, "adt_a02_basic.hl7"),
    (AdtA03Mapper, "adt_a03_basic.hl7"),
    (AdtA04Mapper, "adt_a04_basic.hl7"),
    (AdtA05Mapper, "adt_a05_basic.hl7"),
    (AdtA08Mapper, "adt_a08_finished.hl7"),
    (AdtA08Mapper, "adt_a08_in_progress.hl7"),
    (AdtA11Mapper, "adt_a11_basic.hl7"),
    (AdtA13Mapper, "adt_a13_with_discharge.hl7"),
    (AdtA13Mapper, "adt_a13_no_discharge.hl7"),
    (AdtA38Mapper, "adt_a38_basic.hl7"),
]


@pytest.mark.parametrize("mapper_cls,fixture", _ADT_FIXTURES)
def test_provenance_recording_does_not_change_bundle_output(mapper_cls, fixture):
    # The critical regression test: instrumenting a mapper to also record
    # provenance must never change what it actually builds. Resource ids
    # are freshly random (str(uuid.uuid4())) on every call, so a direct
    # Bundle comparison needs uuid4 patched to an identical deterministic
    # sequence for both the untraced and traced runs.
    message = parse_message(read_fixture(fixture))

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        untraced = mapper_cls().to_bundle(message)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        recorder = ProvenanceRecorder(source_format="HL7v2")
        traced = mapper_cls().to_bundle(message, recorder=recorder)

    assert untraced.model_dump(exclude_none=True) == traced.model_dump(exclude_none=True)
    assert len(recorder.facts) > 0

    # Every recorded fact must resolve to a real Bundle-qualified path with
    # no exceptions and no silently-dropped facts (the traced Bundle here
    # is exactly the Bundle the facts were recorded against).
    entries = resolve_bundle_paths(traced, recorder)
    assert len(entries) == len(recorder.facts)


def test_adt_a01_basic_crosswalk_matches_known_field_values():
    # Direct content-correctness check against adt_a01_basic.hl7's own
    # already-known values (test_adt_a01_mapping.py's own assertions).
    message = parse_message(read_fixture("adt_a01_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA01Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    family_entry = by_path["Bundle.entry[0].resource.name[0].family"]
    assert family_entry.source_location == hl7_location("PID", 5, repetition=0, component=1)
    assert family_entry.value == "Doe"
    assert family_entry.derivation == "direct"

    given0 = by_path["Bundle.entry[0].resource.name[0].given[0]"]
    assert given0.value == "Jane"
    given1 = by_path["Bundle.entry[0].resource.name[0].given[1]"]
    assert given1.value == "Q"

    birth_date_entry = by_path["Bundle.entry[0].resource.birthDate"]
    assert birth_date_entry.value == "1962-03-05"
    assert birth_date_entry.source_value == "19620305"

    gender_entry = by_path["Bundle.entry[0].resource.gender"]
    assert gender_entry.value == "female"
    assert gender_entry.source_value == "F"

    city_entry = by_path["Bundle.entry[0].resource.address[0].city"]
    assert city_entry.value == "Springfield"

    telecom_entry = by_path["Bundle.entry[0].resource.telecom[0].value"]
    assert telecom_entry.value == "(555)555-1234"

    status_entry = by_path["Bundle.entry[1].resource.status"]
    assert status_entry.derivation == "inferred"
    assert status_entry.source_location is None
    assert status_entry.reason
    assert status_entry.value == "in-progress"

    class_entry = by_path["Bundle.entry[1].resource.class.code"]
    assert class_entry.value == "IMP"
    assert class_entry.source_value == "I"

    location_entry = by_path["Bundle.entry[1].resource.location[0].location.display"]
    assert location_entry.value == "W123 456"

    participant_entry = by_path["Bundle.entry[1].resource.participant[0].individual.display"]
    assert participant_entry.value == "Smith, John"

    identifier_entry = by_path["Bundle.identifier.value"]
    assert identifier_entry.value == "MSG00001"


def test_adt_a02_transfer_reindexes_location_provenance_after_insert():
    # AdtA02Mapper inserts the prior location at index 0, shifting the
    # current location (already recorded by build_encounter_core at
    # location[0]) to location[1] - both facts must reflect the final,
    # post-insert indices, not the stale pre-insert one.
    message = parse_message(read_fixture("adt_a02_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA02Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    assert by_path["Bundle.entry[1].resource.location[0].location.display"].source_location == hl7_location("PV1", 6)
    assert by_path["Bundle.entry[1].resource.location[1].location.display"].source_location == hl7_location("PV1", 3)
    # No stray fact left over for an index that no longer means what it
    # used to (there must be exactly these two location facts, not three).
    assert sum(1 for path in by_path if "location[" in path) == 2


def test_adt_a11_cancel_admit_never_leaks_evn2_period_start_into_crosswalk():
    # adt_a11_basic.hl7's own EVN-2 is the cancel notification's own
    # timestamp, not a real admission start (see test_adt_a11_mapping.py's
    # identical regression test on the Bundle side) - build_encounter_core
    # initially records period.start from the EVN-2 fallback, and
    # _drop_evn2_period_start_fallback must correct that away entirely
    # (encounter.period ends up None), leaving no stale period.* fact.
    message = parse_message(read_fixture("adt_a11_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA11Mapper().to_bundle(message, recorder=recorder)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.period is None

    entries = resolve_bundle_paths(bundle, recorder)
    assert not any("period" in e.fhir_path for e in entries)


def test_adt_a13_with_discharge_period_start_points_at_pv1_44_not_evn2():
    message = parse_message(read_fixture("adt_a13_with_discharge.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA13Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    period_start_entry = by_path["Bundle.entry[1].resource.period.start"]
    assert period_start_entry.source_location == hl7_location("PV1", 44)


def test_adt_a03_discharge_disposition_recorded_from_pv1_36():
    message = parse_message(read_fixture("adt_a03_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA03Mapper().to_bundle(message, recorder=recorder)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
    if encounter.hospitalization is None:
        pytest.skip("adt_a03_basic.hl7 doesn't carry a PV1-36 discharge disposition")
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}
    disposition_entry = by_path["Bundle.entry[1].resource.hospitalization.dischargeDisposition.coding[0].code"]
    assert disposition_entry.source_location == hl7_location("PV1", 36)
    assert disposition_entry.value == encounter.hospitalization.dischargeDisposition.coding[0].code


def test_resolve_bundle_paths_bundle_level_fact_has_no_entry_prefix():
    patient = Patient(id="p1")
    bundle = Bundle(id="bundle-1", type="collection", entry=[BundleEntry(fullUrl="urn:uuid:p1", resource=patient)])
    recorder = ProvenanceRecorder(source_format="HL7v2")
    recorder.record("bundle-1", "identifier.value", hl7_location("MSH", 10), "MSG001")

    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == 1
    assert entries[0].fhir_path == "Bundle.identifier.value"


def test_resolve_bundle_paths_skips_fact_for_resource_not_in_bundle():
    patient = Patient(id="p1")
    bundle = Bundle(id="bundle-1", type="collection", entry=[BundleEntry(fullUrl="urn:uuid:p1", resource=patient)])
    recorder = ProvenanceRecorder(source_format="HL7v2")
    recorder.record("p1", "name[0].family", hl7_location("PID", 5, repetition=0, component=1), "Doe")
    recorder.record("never-added-to-bundle", "status", hl7_location("PV1", 2), "in-progress")

    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == 1
    assert entries[0].fhir_path == "Bundle.entry[0].resource.name[0].family"


def test_recorder_record_is_last_write_wins_not_append_only():
    recorder = ProvenanceRecorder(source_format="HL7v2")
    recorder.record("p1", "birthDate", hl7_location("PID", 7), "1962-03-05", source_value="19620305")
    recorder.record("p1", "birthDate", hl7_location("PID", 7), "1962-03-06", source_value="19620306")
    assert len(recorder.facts) == 1
    assert recorder.facts[0].value == "1962-03-06"


def test_recorder_forget_removes_a_fact():
    recorder = ProvenanceRecorder(source_format="HL7v2")
    recorder.record("e1", "period.start", hl7_location("EVN", 2), "2026-01-01T00:00:00Z")
    recorder.forget("e1", "period.start")
    assert len(recorder.facts) == 0


def test_recorder_forget_prefix_removes_only_matching_facts():
    recorder = ProvenanceRecorder(source_format="HL7v2")
    recorder.record("e1", "period.start", hl7_location("PV1", 44), "2026-01-01T00:00:00Z")
    recorder.record("e1", "period.end", hl7_location("PV1", 45), "2026-01-02T00:00:00Z")
    recorder.record("e1", "status", None, "in-progress")
    recorder.forget_prefix("e1", "period.")
    remaining_paths = {fact.relative_path for fact in recorder.facts}
    assert remaining_paths == {"status"}
