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
from app.mappings.mdm import (
    MdmT02Mapper,
    MdmT04Mapper,
    MdmT06Mapper,
    MdmT08Mapper,
    MdmT10Mapper,
    MdmT11Mapper,
)
from app.mappings.oru import OruR01Mapper, OruR30Mapper, OruR31Mapper, OruR32Mapper, OruR40Mapper
from app.mappings.siu import SiuS12Mapper, SiuS13Mapper, SiuS14Mapper, SiuS15Mapper, SiuS17Mapper, SiuS26Mapper
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

    # PV1-3 no longer records a display fact - it records one fact per
    # PL component, each against its own Location resource in the chain.
    bed = next(e for e in entries if e.source_location == hl7_location("PV1", 3, component=3))
    assert bed.value == "A"
    facility = next(e for e in entries if e.source_location == hl7_location("PV1", 3, component=4))
    assert facility.value == "HOSP"

    participant_entry = by_path["Bundle.entry[1].resource.participant[0].individual.display"]
    assert participant_entry.value == "Smith, John"

    identifier_entry = by_path["Bundle.identifier.value"]
    assert identifier_entry.value == "MSG00001"


def test_adt_a01_crosswalk_entries_carry_a_human_readable_field_label():
    # app/provenance/hl7_field_names.py's own "source field name" - a
    # component-level label wins over the whole field's own name when a
    # source_location carries one (see that module's own docstring).
    message = parse_message(read_fixture("adt_a01_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA01Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    identifier_entry = by_path["Bundle.entry[0].resource.identifier[0].value"]
    assert identifier_entry.source_location == hl7_location("PID", 3, repetition=0, component=1)
    assert identifier_entry.field_label == "ID"

    family_entry = by_path["Bundle.entry[0].resource.name[0].family"]
    assert family_entry.field_label == "Family Name"

    birth_date_entry = by_path["Bundle.entry[0].resource.birthDate"]
    assert birth_date_entry.field_label == "Date/Time of Birth"

    # An inferred entry has no source_location at all, so it gets no
    # field_label either - never a guess at one.
    status_entry = by_path["Bundle.entry[1].resource.status"]
    assert status_entry.derivation == "inferred"
    assert status_entry.field_label is None


def test_adt_a02_transfer_records_both_location_chains_independently():
    # This previously guarded a reindexing hazard: the prior location was
    # inserted at Encounter.location[0], shifting the already-recorded
    # current location to [1], so both display facts had to be re-recorded
    # at their post-insert indices. That hazard is gone - PV1-3/PV1-6 now
    # record per-component facts against their own Location resources, so
    # no fact is keyed by a mutable Encounter.location index at all.
    message = parse_message(read_fixture("adt_a02_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = AdtA02Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)

    current = [e for e in entries if (e.source_location or "").startswith("PV1-3.")]
    prior = [e for e in entries if (e.source_location or "").startswith("PV1-6.")]
    assert current, "PV1-3's own chain must be recorded"
    assert prior, "PV1-6's own chain must be recorded"

    # Each fact points at a real Location resource, and the two chains
    # never share one - a transfer's prior and current locations are
    # genuinely different places.
    current_entries = {e.fhir_path.split(".resource.")[0] for e in current}
    prior_entries = {e.fhir_path.split(".resource.")[0] for e in prior}
    assert current_entries and prior_entries
    assert not (current_entries & prior_entries)

    # No Encounter.location fact is keyed by index any more.
    assert not [e for e in entries if ".location[" in e.fhir_path and e.fhir_path.endswith(".display")]


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


# ---------------------------------------------------------------------------
# SIU
# ---------------------------------------------------------------------------

_SIU_FIXTURES = [
    (SiuS12Mapper, "siu_s12_basic.hl7"),
    (SiuS12Mapper, "siu_s12_minimal.hl7"),
    (SiuS12Mapper, "siu_s12_aig_location.hl7"),
    (SiuS12Mapper, "siu_s12_aip_id_only.hl7"),
    (SiuS12Mapper, "siu_s12_multiple_nte.hl7"),
    (SiuS12Mapper, "siu_s12_partial_tq1.hl7"),
    (SiuS12Mapper, "siu_s12_sch11_fallback.hl7"),
    (SiuS13Mapper, "siu_s13_basic.hl7"),
    (SiuS13Mapper, "siu_s13_stale_duration.hl7"),
    (SiuS14Mapper, "siu_s14_basic.hl7"),
    (SiuS15Mapper, "siu_s15_basic.hl7"),
    (SiuS17Mapper, "siu_s17_basic.hl7"),
    (SiuS26Mapper, "siu_s26_basic.hl7"),
]


@pytest.mark.parametrize("mapper_cls,fixture", _SIU_FIXTURES)
def test_siu_provenance_recording_does_not_change_bundle_output(mapper_cls, fixture):
    message = parse_message(read_fixture(fixture))

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        untraced = mapper_cls().to_bundle(message)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        recorder = ProvenanceRecorder(source_format="HL7v2")
        traced = mapper_cls().to_bundle(message, recorder=recorder)

    assert untraced.model_dump(exclude_none=True) == traced.model_dump(exclude_none=True)
    assert len(recorder.facts) > 0

    entries = resolve_bundle_paths(traced, recorder)
    assert len(entries) == len(recorder.facts)


def test_siu_s12_basic_crosswalk_matches_known_field_values():
    message = parse_message(read_fixture("siu_s12_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = SiuS12Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    # AIP-3 produces two independent facts on two different resources: the
    # materialized Practitioner's own name, and (via person_display) the
    # Appointment's own participant[1].actor.display - a real "one source
    # field, two FHIR destinations" case.
    practitioner_entry = next(
        (e for e in entries if e.fhir_path.endswith("resource.name[0].family") and e.value == "Smith"), None
    )
    assert practitioner_entry is not None
    assert practitioner_entry.source_location == hl7_location("AIP", 3, repetition=0, component=2)

    # participant[1..3] are SCH-12/-16/-20's contacts, so AIP's is [4].
    participant_entry = by_path["Bundle.entry[1].resource.participant[4].actor.display"]
    assert participant_entry.value == "Smith, John"
    assert participant_entry.source_location == hl7_location("AIP", 3)

    status_entry = by_path["Bundle.entry[1].resource.status"]
    assert status_entry.derivation == "inferred"
    assert status_entry.value == "booked"

    service_type_entry = by_path["Bundle.entry[1].resource.serviceType[0].coding[0].code"]
    assert service_type_entry.source_location == hl7_location("AIS", 3, component=1)

    # MSH-7 produces two independent facts too: Appointment.created and
    # Bundle.timestamp.
    created_entry = by_path["Bundle.entry[1].resource.created"]
    timestamp_entry = by_path["Bundle.timestamp"]
    assert created_entry.value == timestamp_entry.value
    assert created_entry.source_location == hl7_location("MSH", 7)
    assert timestamp_entry.source_location == hl7_location("MSH", 7)


def test_siu_s12_sch11_fallback_timing_points_at_sch_not_tq1():
    # This fixture has no TQ1 segment at all - start/end must resolve from
    # SCH-11's own components, not a nonexistent TQ1-7/TQ1-8.
    message = parse_message(read_fixture("siu_s12_sch11_fallback.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = SiuS12Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    start_entry = by_path["Bundle.entry[1].resource.start"]
    assert start_entry.source_location == hl7_location("SCH", 11, component=4)
    end_entry = by_path["Bundle.entry[1].resource.end"]
    assert end_entry.source_location == hl7_location("SCH", 11, component=5)


def test_siu_s13_duration_prefers_tq1_over_stale_sch9():
    # TQ1-6 must win over SCH-9/10 when both are present - the recorded
    # source_location must reflect TQ1-6, not the stale SCH-9 this fixture
    # deliberately also carries.
    message = parse_message(read_fixture("siu_s13_stale_duration.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = SiuS13Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    duration_entry = by_path["Bundle.entry[1].resource.minutesDuration"]
    assert duration_entry.source_location == hl7_location("TQ1", 6)
    assert duration_entry.value == "60"


def test_siu_s12_aig_device_records_type_and_identifier():
    message = parse_message(read_fixture("siu_s12_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = SiuS12Mapper().to_bundle(message, recorder=recorder)
    device = next((e.resource for e in bundle.entry if e.resource.get_resource_type() == "Device"), None)
    if device is None:
        pytest.skip("siu_s12_basic.hl7 doesn't carry an AIG-derived Device")
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}
    device_index = next(i for i, entry in enumerate(bundle.entry) if entry.resource is device)

    name_entry = by_path[f"Bundle.entry[{device_index}].resource.deviceName[0].name"]
    assert name_entry.source_location in (hl7_location("AIG", 3, component=1), hl7_location("AIG", 3, component=2))
    type_entry = by_path[f"Bundle.entry[{device_index}].resource.type.coding[0].code"]
    assert type_entry.source_location == hl7_location("AIG", 4, component=1)


# ---------------------------------------------------------------------------
# ORU
# ---------------------------------------------------------------------------

_ORU_FIXTURES = [
    (OruR01Mapper, "oru_r01_basic.hl7"),
    (OruR01Mapper, "oru_r01_minimal.hl7"),
    (OruR01Mapper, "oru_r01_shared_performer.hl7"),
    (OruR01Mapper, "oru_r01_ft_with_caret.hl7"),
    (OruR30Mapper, "oru_r30_basic.hl7"),
    (OruR31Mapper, "oru_r31_basic.hl7"),
    (OruR32Mapper, "oru_r32_basic.hl7"),
    (OruR40Mapper, "oru_r40_basic.hl7"),
]


@pytest.mark.parametrize("mapper_cls,fixture", _ORU_FIXTURES)
def test_oru_provenance_recording_does_not_change_bundle_output(mapper_cls, fixture):
    message = parse_message(read_fixture(fixture))

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        untraced = mapper_cls().to_bundle(message)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        recorder = ProvenanceRecorder(source_format="HL7v2")
        traced = mapper_cls().to_bundle(message, recorder=recorder)

    assert untraced.model_dump(exclude_none=True) == traced.model_dump(exclude_none=True)
    assert len(recorder.facts) > 0

    entries = resolve_bundle_paths(traced, recorder)
    assert len(entries) == len(recorder.facts)


def test_oru_r01_basic_crosswalk_matches_known_field_values():
    # oru_r01_basic.hl7 carries two OBR-led groups (CBC with two OBX results,
    # one carrying an OBX-16 performer; GLU with one OBX result) - see
    # test_oru_mapping.py::test_basic_fixture_groups_observations_under_correct_report
    # for the same fixture's own Bundle-shape assertions this mirrors.
    message = parse_message(read_fixture("oru_r01_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = OruR01Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    reports = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "DiagnosticReport"]
    cbc_report = next(r for r in reports if r.code.coding[0].code == "CBC")
    cbc_index = next(i for i, e in enumerate(bundle.entry) if e.resource is cbc_report)
    glu_report = next(r for r in reports if r.code.coding[0].code == "GLU")
    glu_index = next(i for i, e in enumerate(bundle.entry) if e.resource is glu_report)

    cbc_status = by_path[f"Bundle.entry[{cbc_index}].resource.status"]
    assert cbc_status.value == "final"
    assert cbc_status.source_value == "F"
    assert cbc_status.source_location == hl7_location("OBR", 25)

    cbc_code = by_path[f"Bundle.entry[{cbc_index}].resource.code.coding[0].code"]
    assert cbc_code.value == "CBC"
    assert cbc_code.source_location == hl7_location("OBR", 4, component=1)

    cbc_effective = by_path[f"Bundle.entry[{cbc_index}].resource.effectiveDateTime"]
    assert cbc_effective.source_location == hl7_location("OBR", 7)

    cbc_issued = by_path[f"Bundle.entry[{cbc_index}].resource.issued"]
    assert cbc_issued.source_location == hl7_location("OBR", 22)

    glu_status = by_path[f"Bundle.entry[{glu_index}].resource.status"]
    assert glu_status.value == "preliminary"
    assert glu_status.source_value == "P"

    # WBC observation: NM value type -> valueQuantity, plus referenceRange/
    # interpretation/effectiveDateTime and an OBX-16 performer.
    observations = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation"]
    wbc_obs = next(o for o in observations if o.code.coding[0].code == "WBC")
    wbc_index = next(i for i, e in enumerate(bundle.entry) if e.resource is wbc_obs)

    wbc_value = by_path[f"Bundle.entry[{wbc_index}].resource.valueQuantity.value"]
    assert float(wbc_value.value) == 7.2
    assert wbc_value.source_location == hl7_location("OBX", 5)
    wbc_unit = by_path[f"Bundle.entry[{wbc_index}].resource.valueQuantity.unit"]
    assert wbc_unit.value == "10*3/uL"
    assert wbc_unit.source_location == hl7_location("OBX", 6)

    wbc_range = by_path[f"Bundle.entry[{wbc_index}].resource.referenceRange[0].text"]
    assert wbc_range.value == "4.0-11.0"
    assert wbc_range.source_location == hl7_location("OBX", 7)

    wbc_interp = by_path[f"Bundle.entry[{wbc_index}].resource.interpretation[0].coding[0].code"]
    assert wbc_interp.value == "N"
    assert wbc_interp.source_location == hl7_location("OBX", 8)

    wbc_effective = by_path[f"Bundle.entry[{wbc_index}].resource.effectiveDateTime"]
    assert wbc_effective.source_location == hl7_location("OBX", 14)

    performer = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Practitioner")
    performer_index = next(i for i, e in enumerate(bundle.entry) if e.resource is performer)
    performer_family = by_path[f"Bundle.entry[{performer_index}].resource.name[0].family"]
    assert performer_family.value == "Rivera"
    assert performer_family.source_location == hl7_location("OBX", 16, repetition=0, component=2)
    performer_given = by_path[f"Bundle.entry[{performer_index}].resource.name[0].given[0]"]
    assert performer_given.value == "Ana"
    performer_id = by_path[f"Bundle.entry[{performer_index}].resource.identifier[0].value"]
    assert performer_id.value == "5678"
    assert performer_id.source_location == hl7_location("OBX", 16, repetition=0, component=1)


def test_oru_r01_shared_performer_recorded_once_not_once_per_observation():
    # OBX-16's Practitioner is deduped across the message (_resolve_performer)
    # - the crosswalk must reflect exactly one materialized Practitioner's
    # worth of facts, not one per referencing Observation, and the resolved
    # entry count must still match len(recorder.facts) exactly (no stale
    # fact left over from a cache-hit call that skipped recording).
    message = parse_message(read_fixture("oru_r01_shared_performer.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = OruR01Mapper().to_bundle(message, recorder=recorder)
    practitioners = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Practitioner"]
    assert len(practitioners) == 1

    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == len(recorder.facts)
    family_facts = [e for e in entries if e.fhir_path.endswith("resource.name[0].family") and e.value == "Rivera"]
    assert len(family_facts) == 1


def test_oru_r01_free_text_value_with_caret_is_not_truncated_in_crosswalk():
    # _build_observation_value's ST/FT/TX branch reads via raw_field_str, not
    # field_str, specifically so a literal caret in free text isn't mistaken
    # for a component separator - the crosswalk's own recorded value must
    # reflect the same untruncated text the Bundle itself carries (see the
    # `hl7` library gotcha section in CLAUDE.md).
    message = parse_message(read_fixture("oru_r01_ft_with_caret.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = OruR01Mapper().to_bundle(message, recorder=recorder)
    observation = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation")
    assert observation.valueString == "Grade II^ tear noted; recommend follow-up"

    entries = resolve_bundle_paths(bundle, recorder)
    obs_index = next(i for i, e in enumerate(bundle.entry) if e.resource is observation)
    value_entry = next(e for e in entries if e.fhir_path == f"Bundle.entry[{obs_index}].resource.valueString")
    assert value_entry.value == "Grade II^ tear noted; recommend follow-up"
    assert value_entry.source_location == hl7_location("OBX", 5)


def test_oru_minimal_fixture_without_pv1_records_no_encounter_facts():
    # oru_r01_minimal.hl7 has no PV1 segment at all - build_minimal_encounter
    # never runs, so no Encounter-scoped fact should appear anywhere in the
    # resolved crosswalk.
    message = parse_message(read_fixture("oru_r01_minimal.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = OruR01Mapper().to_bundle(message, recorder=recorder)
    assert not any(e.resource.get_resource_type() == "Encounter" for e in bundle.entry)
    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == len(recorder.facts)


# ---------------------------------------------------------------------------
# MDM
# ---------------------------------------------------------------------------

_MDM_FIXTURES = [
    (MdmT02Mapper, "mdm_t02_basic.hl7"),
    (MdmT02Mapper, "mdm_t02_minimal.hl7"),
    (MdmT02Mapper, "mdm_t02_obx_with_caret.hl7"),
    (MdmT02Mapper, "mdm_t02_same_author_authenticator.hl7"),
    (MdmT04Mapper, "mdm_t02_basic.hl7"),
    (MdmT06Mapper, "mdm_t02_basic.hl7"),
    (MdmT08Mapper, "mdm_t02_basic.hl7"),
    (MdmT10Mapper, "mdm_t02_basic.hl7"),
    (MdmT11Mapper, "mdm_t02_basic.hl7"),
]


@pytest.mark.parametrize("mapper_cls,fixture", _MDM_FIXTURES)
def test_mdm_provenance_recording_does_not_change_bundle_output(mapper_cls, fixture):
    message = parse_message(read_fixture(fixture))

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        untraced = mapper_cls().to_bundle(message)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        recorder = ProvenanceRecorder(source_format="HL7v2")
        traced = mapper_cls().to_bundle(message, recorder=recorder)

    assert untraced.model_dump(exclude_none=True) == traced.model_dump(exclude_none=True)
    assert len(recorder.facts) > 0

    entries = resolve_bundle_paths(traced, recorder)
    assert len(entries) == len(recorder.facts)


def test_mdm_t02_basic_crosswalk_matches_known_field_values():
    # mdm_t02_basic.hl7 carries a full TXA (type/content-presentation/
    # origination-date/originator/authenticator/master-id/confidentiality/
    # title) plus two TX-typed OBX lines making up the document body - see
    # test_mdm_mapping.py::test_basic_fixture_maps_every_field for the same
    # fixture's own Bundle-shape assertions this mirrors.
    message = parse_message(read_fixture("mdm_t02_basic.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = MdmT02Mapper().to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    doc_ref = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    doc_index = next(i for i, e in enumerate(bundle.entry) if e.resource is doc_ref)

    # TXA-19 "AV" is a verified mapping to "current", so it is a real read -
    # recording it inferred left the field looking both unread and dropped
    # when it had in fact been mapped. Any other value still defaults to
    # "current" without being read, and stays inferred.
    status_entry = by_path[f"Bundle.entry[{doc_index}].resource.status"]
    assert status_entry.derivation == "direct"
    assert status_entry.source_location == "TXA-19"
    assert status_entry.source_value == "AV"
    assert status_entry.value == "current"

    content_type_entry = by_path[f"Bundle.entry[{doc_index}].resource.content[0].attachment.contentType"]
    assert content_type_entry.value == "text/plain"
    assert content_type_entry.source_location == hl7_location("TXA", 3)
    assert content_type_entry.source_value == "TEXT"

    type_entry = by_path[f"Bundle.entry[{doc_index}].resource.type.coding[0].code"]
    assert type_entry.value == "CN"
    assert type_entry.source_location == hl7_location("TXA", 2, component=1)
    type_display_entry = by_path[f"Bundle.entry[{doc_index}].resource.type.coding[0].display"]
    assert type_display_entry.value == "Consultation Note"

    master_id_entry = by_path[f"Bundle.entry[{doc_index}].resource.masterIdentifier.value"]
    assert master_id_entry.value == "DOC-000123"
    assert master_id_entry.source_location == hl7_location("TXA", 12)

    date_entry = by_path[f"Bundle.entry[{doc_index}].resource.date"]
    assert date_entry.source_location == hl7_location("TXA", 6)
    assert date_entry.source_value == "20260812105000"

    security_entry = by_path[f"Bundle.entry[{doc_index}].resource.securityLabel[0].coding[0].code"]
    assert security_entry.value == "R"
    assert security_entry.source_location == hl7_location("TXA", 18)

    description_entry = by_path[f"Bundle.entry[{doc_index}].resource.description"]
    assert description_entry.value == "Cardiology Consult Note"
    assert description_entry.source_location == hl7_location("TXA", 25)

    # TXA-9 produces two independent facts on two different resources: the
    # materialized originator Practitioner's own name, and (via
    # person_display) this DocumentReference's own author[0].display - the
    # same "one source field, two FHIR destinations" case SIU's AIP-3
    # already established.
    author_display_entry = by_path[f"Bundle.entry[{doc_index}].resource.author[0].display"]
    assert author_display_entry.value == "Chen, Wei"
    assert author_display_entry.source_location == hl7_location("TXA", 9, repetition=0)

    originator = next(
        e.resource
        for e in bundle.entry
        if e.resource.get_resource_type() == "Practitioner" and e.resource.name[0].family == "Chen"
    )
    originator_index = next(i for i, e in enumerate(bundle.entry) if e.resource is originator)
    originator_family_entry = by_path[f"Bundle.entry[{originator_index}].resource.name[0].family"]
    assert originator_family_entry.source_location == hl7_location("TXA", 9, repetition=0, component=2)

    authenticator_display_entry = by_path[f"Bundle.entry[{doc_index}].resource.authenticator.display"]
    assert authenticator_display_entry.value == "Alvarez, Rosa"
    assert authenticator_display_entry.source_location == hl7_location("TXA", 10)

    # Document body: two TX-typed OBX lines joined into one Binary.data fact,
    # disclosed via a "(×2 segments)" location the same way SIU's own NTE
    # join discloses its own multi-segment source.
    binary = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary")
    binary_index = next(i for i, e in enumerate(bundle.entry) if e.resource is binary)
    data_entry = by_path[f"Bundle.entry[{binary_index}].resource.data"]
    assert data_entry.value == (
        "Patient seen for cardiology consult.\nNo acute distress; recommend follow-up in 2 weeks."
    )
    assert data_entry.source_location == "OBX-5 (×2 segments)"


def test_mdm_t02_same_author_authenticator_records_authenticator_display_from_txa10():
    # mdm_t02_same_author_authenticator.hl7's TXA-9/TXA-10 identify the same
    # real person - only one Practitioner is materialized (see
    # test_mdm_mapping.py::test_same_originator_and_authenticator_deduplicates_to_one_practitioner),
    # but authenticator.display must still be recorded against TXA-10 (not
    # TXA-9), since person_display(txa, 10) - not 9 - built the string, even
    # though both point at the identical underlying Practitioner resource.
    message = parse_message(read_fixture("mdm_t02_same_author_authenticator.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = MdmT02Mapper().to_bundle(message, recorder=recorder)
    practitioners = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Practitioner"]
    assert len(practitioners) == 1

    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}
    doc_ref = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    doc_index = next(i for i, e in enumerate(bundle.entry) if e.resource is doc_ref)

    authenticator_entry = by_path[f"Bundle.entry[{doc_index}].resource.authenticator.display"]
    assert authenticator_entry.source_location == hl7_location("TXA", 10)
    author_entry = by_path[f"Bundle.entry[{doc_index}].resource.author[0].display"]
    assert author_entry.source_location == hl7_location("TXA", 9, repetition=0)


def test_mdm_t02_obx_with_caret_binary_data_is_not_truncated_in_crosswalk():
    # mdm_t02_obx_with_caret.hl7's own single OBX-5 contains a literal caret
    # - _build_binary_from_obx reads it via raw_field_str, and the crosswalk
    # must record the same untruncated text the Bundle's own Binary.data
    # actually carries.
    message = parse_message(read_fixture("mdm_t02_obx_with_caret.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = MdmT02Mapper().to_bundle(message, recorder=recorder)
    binary = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary")
    # Binary.data is stored as already-decoded raw bytes (pydantic's
    # Base64Binary type decodes it at construction time) - see CLAUDE.md's
    # own fhir.resources notes for this exact gotcha.
    assert binary.data.decode("utf-8") == "Grade II^ tear noted on exam; recommend follow-up"

    entries = resolve_bundle_paths(bundle, recorder)
    binary_index = next(i for i, e in enumerate(bundle.entry) if e.resource is binary)
    data_entry = next(e for e in entries if e.fhir_path == f"Bundle.entry[{binary_index}].resource.data")
    assert data_entry.value == "Grade II^ tear noted on exam; recommend follow-up"
    assert data_entry.source_location == hl7_location("OBX", 5)


def test_mdm_t02_minimal_fixture_without_pv1_records_no_encounter_facts():
    message = parse_message(read_fixture("mdm_t02_minimal.hl7"))
    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = MdmT02Mapper().to_bundle(message, recorder=recorder)
    assert not any(e.resource.get_resource_type() == "Encounter" for e in bundle.entry)
    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == len(recorder.facts)
