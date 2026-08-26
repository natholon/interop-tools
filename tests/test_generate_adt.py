import random

import pytest

from app.generators.adt import (
    generate_adt_a01,
    generate_adt_a02,
    generate_adt_a03,
    generate_adt_a04,
    generate_adt_a05,
    generate_adt_a08,
    generate_adt_a11,
    generate_adt_a13,
    generate_adt_a38,
)
from app.hl7.parser import field_str, parse_message, require_segment
from app.hl7.pipeline import convert_hl7_to_bundle, validate_hl7

_GENERATORS = [
    (generate_adt_a01, "A01"),
    (generate_adt_a02, "A02"),
    (generate_adt_a03, "A03"),
    (generate_adt_a04, "A04"),
    (generate_adt_a05, "A05"),
    (generate_adt_a08, "A08"),
    (generate_adt_a11, "A11"),
    (generate_adt_a13, "A13"),
    (generate_adt_a38, "A38"),
]


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_generated_message_parses_with_correct_trigger(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        msh = require_segment(message, "MSH")
        assert field_str(msh, 9, component=1) == "ADT"
        assert field_str(msh, 9, component=2) == trigger_event


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_required_fields_always_present(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        pid = require_segment(message, "PID")
        pv1 = require_segment(message, "PV1")
        assert field_str(pid, 3), "PID-3 identifier must always be present"
        assert field_str(pid, 5), "PID-5 name must always be present"
        assert field_str(pv1, 2), "PV1-2 patient class must always be present"
        if trigger_event == "A02":
            assert field_str(pv1, 6), "A02 must always include PV1-6 prior location"
        if trigger_event == "A03":
            assert field_str(pv1, 44), "A03 must always include PV1-44 admit time"
            assert field_str(pv1, 45), "A03 must always include PV1-45 discharge time"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_pid_address_varies_across_seeds(generator_fn):
    present = absent = 0
    for seed in range(60):
        message = parse_message(generator_fn(random.Random(seed)))
        pid = require_segment(message, "PID")
        if field_str(pid, 11):
            present += 1
        else:
            absent += 1
    assert present > 0, "optional PID-11 should be present in at least some generated messages"
    assert absent > 0, "optional PID-11 should be absent in at least some generated messages"


def test_a03_discharge_disposition_varies_across_seeds():
    present = absent = 0
    for seed in range(60):
        message = parse_message(generate_adt_a03(random.Random(seed)))
        pv1 = require_segment(message, "PV1")
        if field_str(pv1, 36):
            present += 1
        else:
            absent += 1
    assert present > 0
    assert absent > 0


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_round_trips_through_real_converter(generator_fn, trigger_event):
    for seed in range(1000, 1020):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        # PV1-3/PV1-6 now materialize a chain of real Location resources
        # (one per populated PL component, per the v2-to-FHIR PL[Location]
        # map), so a generated message yields Locations too - how many
        # depends on which components that seed populated.
        assert {"Patient", "Encounter"} <= resource_types
        assert resource_types <= {"Patient", "Encounter", "Location", "Practitioner"}


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_generated_message_has_no_validation_errors(generator_fn, trigger_event):
    for seed in range(1000, 1020):
        report = validate_hl7(generator_fn(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_a08_status_hits_both_branches_across_seeds():
    statuses = set()
    for seed in range(40):
        bundle = convert_hl7_to_bundle(generate_adt_a08(random.Random(seed)))
        encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
        statuses.add(encounter.status)
    assert statuses == {"in-progress", "finished"}


def test_a05_always_produces_planned_status():
    for seed in range(20):
        bundle = convert_hl7_to_bundle(generate_adt_a05(random.Random(seed)))
        encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
        assert encounter.status == "planned"


@pytest.mark.parametrize(
    "generator_fn, trigger_event",
    [(generate_adt_a11, "A11"), (generate_adt_a13, "A13"), (generate_adt_a38, "A38")],
)
def test_cancel_triggers_always_produce_entered_in_error_status(generator_fn, trigger_event):
    for seed in range(20):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
        assert encounter.status == "entered-in-error"


def test_a13_discharge_fields_vary_across_seeds():
    # A13 doesn't require discharge fields (unlike A03) - the generator
    # deliberately exercises both the populated and absent branches of
    # AdtA13Mapper's optional discharge period/disposition handling.
    present = absent = 0
    for seed in range(60):
        message = parse_message(generate_adt_a13(random.Random(seed)))
        pv1 = require_segment(message, "PV1")
        if field_str(pv1, 45):
            present += 1
        else:
            absent += 1
    assert present > 0
    assert absent > 0


def _field(raw: str, segment_id: str, field_num: int) -> str:
    line = next((l for l in raw.split("\r") if l.startswith(segment_id + "|")), "")
    parts = line.split("|")
    return parts[field_num] if len(parts) > field_num else ""


def test_pv1_hospitalization_fields_vary_across_seeds():
    # Every one of these has a real FHIR target, so each needs to occur
    # both present and absent for the mapping to be fuzzed at all.
    for field_num in (4, 5, 10, 13, 14, 15, 16, 38):
        values = {bool(_field(generate_adt_a01(random.Random(seed)), "PV1", field_num)) for seed in range(40)}
        assert values == {True, False}, f"PV1-{field_num} never varies"


def test_pid_demographic_fields_vary_across_seeds():
    for field_num in (15, 16):
        values = {bool(_field(generate_adt_a01(random.Random(seed)), "PID", field_num)) for seed in range(40)}
        assert values == {True, False}, f"PID-{field_num} never varies"


def test_both_branches_of_each_demographic_choice_pair_occur():
    # PID-24/25 and PID-29/30 are choice pairs the mapper resolves by
    # precedence - generating only the indicator would leave
    # multipleBirthInteger and deceasedDateTime untested.
    seen = set()
    for seed in range(120):
        raw = generate_adt_a01(random.Random(seed))
        for field_num in (24, 25, 29, 30):
            if _field(raw, "PID", field_num):
                seen.add(field_num)
    assert seen == {24, 25, 29, 30}


def test_repeating_fields_sometimes_carry_a_second_repetition():
    # PID-3, PID-13 and the four PV1 doctor fields are all 0..-1.
    for segment_id, field_num in (("PID", 3), ("PID", 13), ("PV1", 7), ("PV1", 8), ("PV1", 9), ("PV1", 17)):
        assert any(
            "~" in _field(generate_adt_a01(random.Random(seed)), segment_id, field_num) for seed in range(120)
        ), f"{segment_id}-{field_num} never repeats"

