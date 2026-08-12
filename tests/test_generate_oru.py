import random

import pytest

from app.generators.oru import generate_oru_r01, generate_oru_r30, generate_oru_r40
from app.hl7.parser import field_str, group_segments_by_leader, parse_message, require_segment
from app.hl7.pipeline import convert_hl7_to_bundle, validate_hl7

_GENERATORS = [
    (generate_oru_r01, "R01"),
    (generate_oru_r30, "R30"),
    (generate_oru_r40, "R40"),
]


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_generated_message_parses_with_correct_trigger(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        msh = require_segment(message, "MSH")
        assert field_str(msh, 9, component=1) == "ORU"
        assert field_str(msh, 9, component=2) == trigger_event


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_required_fields_always_present(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        pid = require_segment(message, "PID")
        assert field_str(pid, 3), "PID-3 identifier must always be present"
        assert field_str(pid, 5), "PID-5 name must always be present"

        groups = group_segments_by_leader(message, "OBR", ["OBX"])
        assert groups, "every generated ORU message must have at least one OBR group"
        for obr, obx_segments in groups:
            assert field_str(obr, 4), "OBR-4 report code must always be present"
            assert field_str(obr, 25), "OBR-25 result status must always be present"
            assert obx_segments, "every OBR group must have at least one OBX"
            for obx in obx_segments:
                assert field_str(obx, 2), "OBX-2 value type must always be present"
                assert field_str(obx, 3), "OBX-3 observation code must always be present"
                assert field_str(obx, 5), "OBX-5 observation value must always be present"
                assert field_str(obx, 11), "OBX-11 result status must always be present"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_pv1_and_optional_obx_fields_vary_across_seeds(generator_fn):
    pv1_present = pv1_absent = 0
    units_present = units_absent = 0
    for seed in range(60):
        message = parse_message(generator_fn(random.Random(seed)))
        try:
            require_segment(message, "PV1")
            pv1_present += 1
        except Exception:
            pv1_absent += 1
        for _obr, obx_segments in group_segments_by_leader(message, "OBR", ["OBX"]):
            for obx in obx_segments:
                if field_str(obx, 6):
                    units_present += 1
                else:
                    units_absent += 1
    assert pv1_present > 0 and pv1_absent > 0, "PV1 (and therefore Encounter) should be both present and absent"
    assert units_present > 0 and units_absent > 0, "OBX-6 units should be both present and absent"


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_round_trips_through_real_converter(generator_fn, trigger_event):
    for seed in range(1000, 1020):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "DiagnosticReport", "Observation"} <= resource_types
        assert resource_types <= {"Patient", "Encounter", "DiagnosticReport", "Observation", "Practitioner"}


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_generated_message_has_no_validation_errors(generator_fn, trigger_event):
    # Not zero *findings*: the generator deliberately produces OBX-5 values
    # outside their OBX-7 reference range ~30% of the time, which is a real
    # (info-severity) finding, not a bug - only error-severity is asserted.
    for seed in range(1000, 1020):
        report = validate_hl7(generator_fn(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_diagnostic_report_result_references_only_its_own_group(generator_fn, trigger_event):
    # Fuzz check for the positional-grouping guarantee: every reference in a
    # DiagnosticReport.result must point to an Observation that actually
    # exists in the Bundle (and, since we control generation, was built
    # from that report's own OBR group - a cross-group leak would produce
    # either a dangling reference or a mismatched count).
    for seed in range(30):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        by_type = {}
        for entry in bundle.entry:
            by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
        observation_ids = {o.id for o in by_type.get("Observation", [])}
        referenced_ids = {
            ref.reference.removeprefix("urn:uuid:")
            for report in by_type.get("DiagnosticReport", [])
            for ref in (report.result or [])
        }
        assert referenced_ids <= observation_ids
        # every Observation must be referenced by exactly one report
        all_result_refs = [
            ref.reference.removeprefix("urn:uuid:")
            for report in by_type.get("DiagnosticReport", [])
            for ref in (report.result or [])
        ]
        assert sorted(all_result_refs) == sorted(observation_ids)
