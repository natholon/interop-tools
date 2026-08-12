import random

import pytest

from app.generators.siu import (
    generate_siu_s12,
    generate_siu_s13,
    generate_siu_s14,
    generate_siu_s15,
    generate_siu_s17,
    generate_siu_s26,
)
from app.hl7.parser import field_str, parse_message, require_segment
from app.hl7.pipeline import convert_hl7_to_bundle, validate_hl7

_BOOKED_GENERATORS = [
    (generate_siu_s12, "S12"),
    (generate_siu_s13, "S13"),
    (generate_siu_s14, "S14"),
]
_TIMING_REQUIRED_GENERATORS = _BOOKED_GENERATORS + [(generate_siu_s26, "S26")]
_UNTIMED_GENERATORS = [(generate_siu_s15, "S15"), (generate_siu_s17, "S17")]
_ALL_GENERATORS = _TIMING_REQUIRED_GENERATORS + _UNTIMED_GENERATORS
_EXPECTED_STATUS = {
    "S12": "booked",
    "S13": "booked",
    "S14": "booked",
    "S15": "cancelled",
    "S17": "entered-in-error",
    "S26": "noshow",
}


@pytest.mark.parametrize("generator_fn, trigger_event", _ALL_GENERATORS)
def test_generated_message_parses_with_correct_trigger(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        msh = require_segment(message, "MSH")
        assert field_str(msh, 9, component=1) == "SIU"
        assert field_str(msh, 9, component=2) == trigger_event


@pytest.mark.parametrize("generator_fn, trigger_event", _ALL_GENERATORS)
def test_required_fields_always_present(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        pid = require_segment(message, "PID")
        sch = require_segment(message, "SCH")
        assert field_str(pid, 3), "PID-3 identifier must always be present"
        assert field_str(pid, 5), "PID-5 name must always be present"
        assert field_str(sch, 1), "SCH-1 placer appointment ID must always be present"


@pytest.mark.parametrize("generator_fn, trigger_event", _TIMING_REQUIRED_GENERATORS)
def test_timing_required_triggers_always_have_resolvable_timing(generator_fn, trigger_event):
    # Would raise MappingError if timing were ever unresolved for these triggers.
    for seed in range(30):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")
        assert appointment.status == _EXPECTED_STATUS[trigger_event]
        assert appointment.start is not None
        assert appointment.end is not None
        assert appointment.extension[0].valueCode == trigger_event


@pytest.mark.parametrize("generator_fn, trigger_event", _UNTIMED_GENERATORS)
def test_untimed_triggers_succeed_with_and_without_timing(generator_fn, trigger_event):
    has_timing = no_timing = 0
    for seed in range(60):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        appointment = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")
        assert appointment.status == _EXPECTED_STATUS[trigger_event]
        if appointment.start is not None:
            has_timing += 1
        else:
            no_timing += 1
    assert has_timing > 0, f"some {trigger_event} messages should include timing"
    assert no_timing > 0, f"some {trigger_event} messages should omit timing entirely"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _ALL_GENERATORS])
def test_sch_filler_id_varies_across_seeds(generator_fn):
    present = absent = 0
    for seed in range(60):
        message = parse_message(generator_fn(random.Random(seed)))
        sch = require_segment(message, "SCH")
        if field_str(sch, 2):
            present += 1
        else:
            absent += 1
    assert present > 0, "optional SCH-2 should be present in at least some generated messages"
    assert absent > 0, "optional SCH-2 should be absent in at least some generated messages"


@pytest.mark.parametrize("generator_fn, trigger_event", _ALL_GENERATORS)
def test_round_trips_through_real_converter(generator_fn, trigger_event):
    allowed_types = {"Patient", "Appointment", "Practitioner", "Location", "Device"}
    for seed in range(1000, 1020):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "Appointment"} <= resource_types
        assert resource_types <= allowed_types


@pytest.mark.parametrize("generator_fn, trigger_event", _ALL_GENERATORS)
def test_generated_message_has_no_validation_errors(generator_fn, trigger_event):
    for seed in range(1000, 1020):
        report = validate_hl7(generator_fn(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _ALL_GENERATORS])
def test_aip_ail_aig_materialize_as_real_resources_across_seeds(generator_fn):
    # AIP/AIL/AIG are each independently ~50% present in the generator, so
    # across enough seeds every one of Practitioner/Location/Device should
    # show up at least once, each referenced from an Appointment.participant
    # by urn:uuid - not left as display-only text.
    seen_types = set()
    for seed in range(60):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        by_type = {e.resource.get_resource_type(): e.resource for e in bundle.entry}
        appointment = by_type["Appointment"]
        for participant in appointment.participant:
            assert participant.actor.reference is not None, "participant actor must be a real reference, not display text"
        seen_types.update(by_type.keys())
    assert {"Practitioner", "Location", "Device"} <= seen_types
