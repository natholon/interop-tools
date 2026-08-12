import random

import pytest

from app.generators.mdm import (
    generate_mdm_t02,
    generate_mdm_t04,
    generate_mdm_t06,
    generate_mdm_t08,
    generate_mdm_t10,
    generate_mdm_t11,
)
from app.hl7.parser import field_str, parse_message, require_segment
from app.hl7.pipeline import convert_hl7_to_bundle, validate_hl7

_GENERATORS = [
    (generate_mdm_t02, "T02"),
    (generate_mdm_t04, "T04"),
    (generate_mdm_t06, "T06"),
    (generate_mdm_t08, "T08"),
    (generate_mdm_t10, "T10"),
    (generate_mdm_t11, "T11"),
]


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_generated_message_parses_with_correct_trigger(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        msh = require_segment(message, "MSH")
        assert field_str(msh, 9, component=1) == "MDM"
        assert field_str(msh, 9, component=2) == trigger_event


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_required_fields_always_present(generator_fn, trigger_event):
    for seed in range(20):
        message = parse_message(generator_fn(random.Random(seed)))
        pid = require_segment(message, "PID")
        txa = require_segment(message, "TXA")
        assert field_str(pid, 3), "PID-3 identifier must always be present"
        assert field_str(pid, 5), "PID-5 name must always be present"
        assert field_str(txa, 2), "TXA-2 document type must always be present"
        assert field_str(txa, 3), "TXA-3 content presentation must always be present"
        assert field_str(txa, 19), "TXA-19 availability status must always be present"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_pv1_and_optional_txa_fields_vary_across_seeds(generator_fn):
    pv1_present = pv1_absent = 0
    author_present = author_absent = 0
    for seed in range(60):
        message = parse_message(generator_fn(random.Random(seed)))
        try:
            require_segment(message, "PV1")
            pv1_present += 1
        except Exception:
            pv1_absent += 1
        txa = require_segment(message, "TXA")
        if field_str(txa, 9):
            author_present += 1
        else:
            author_absent += 1
    assert pv1_present > 0 and pv1_absent > 0, "PV1 (and therefore Encounter) should be both present and absent"
    assert author_present > 0 and author_absent > 0, "TXA-9 originator should be both present and absent"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_obx_content_varies_across_seeds(generator_fn):
    # OBX content drives whether a Binary gets materialized - the generator
    # should exercise both the with-Binary and without-Binary mapper paths.
    with_content = without_content = 0
    for seed in range(60):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        if "Binary" in resource_types:
            with_content += 1
        else:
            without_content += 1
    assert with_content > 0, "some generated MDM messages should include OBX document content"
    assert without_content > 0, "some generated MDM messages should omit OBX document content"


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_round_trips_through_real_converter(generator_fn, trigger_event):
    allowed_types = {"Patient", "Encounter", "DocumentReference", "Binary", "Practitioner"}
    for seed in range(1000, 1020):
        bundle = convert_hl7_to_bundle(generator_fn(random.Random(seed)))
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "DocumentReference"} <= resource_types
        assert resource_types <= allowed_types


@pytest.mark.parametrize("generator_fn, trigger_event", _GENERATORS)
def test_generated_message_has_no_validation_errors(generator_fn, trigger_event):
    for seed in range(1000, 1020):
        report = validate_hl7(generator_fn(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"
