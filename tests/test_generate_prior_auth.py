import random

import pytest

from app.edi.generator import generate_278_request, generate_278_response
from app.edi.parser import element, find_segment, first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle, validate_edi

_GENERATORS = [
    (generate_278_request, "13"),
    (generate_278_response, "11"),
]


def _first_transaction_set(text: str):
    return first_transaction_set(parse_interchange(text))


@pytest.mark.parametrize("generator_fn, bht02", _GENERATORS)
def test_generated_message_parses_with_correct_st01_and_bht02(generator_fn, bht02):
    for seed in range(20):
        transaction_set = _first_transaction_set(generator_fn(random.Random(seed)))
        assert transaction_set.st01 == "278"
        bht = find_segment(transaction_set.segments, "BHT")
        assert element(bht, 2) == bht02


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_required_fields_always_present(generator_fn):
    for seed in range(20):
        transaction_set = _first_transaction_set(generator_fn(random.Random(seed)))
        bht = find_segment(transaction_set.segments, "BHT")
        assert bht is not None
        assert element(bht, 3), "BHT03 reference identification must always be present"
        assert element(bht, 4), "BHT04 date must always be present"
        assert element(bht, 5), "BHT05 time must always be present"

        subscriber_nm1 = None
        found_um = False
        found_patient_event = False
        for seg in transaction_set.segments:
            if seg[0] == "NM1" and element(seg, 1) == "IL":
                subscriber_nm1 = seg
            if seg[0] == "UM":
                found_um = True
            if seg[0] == "HL" and element(seg, 3) == "EV":
                found_patient_event = True
        assert subscriber_nm1 is not None, "subscriber NM1 must always be present"
        assert element(subscriber_nm1, 3), "subscriber family name must always be present"
        assert found_um, "the patient event loop's UM segment must always be present"
        assert found_patient_event, "a 2000E Patient Event loop must always be present"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_dependent_and_requester_shape_vary_across_seeds(generator_fn):
    dependent_present = dependent_absent = 0
    requester_person = requester_org = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generator_fn(random.Random(seed)))
        subscriber_count = sum(1 for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) in ("IL", "QC"))
        if subscriber_count == 2:
            dependent_present += 1
        else:
            dependent_absent += 1
        requester_nm1 = next(s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "1P")
        if element(requester_nm1, 2) == "1":
            requester_person += 1
        else:
            requester_org += 1
    assert dependent_present > 0 and dependent_absent > 0
    assert requester_person > 0 and requester_org > 0


def test_response_hcr_presence_varies_across_seeds():
    with_hcr = without_hcr = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_278_response(random.Random(seed)))
        if find_segment(transaction_set.segments, "HCR") is not None:
            with_hcr += 1
        else:
            without_hcr += 1
    # ~85% with HCR is a fuzz-coverage mechanism, not "usually broken" -
    # both branches must occur, and present must remain the large majority.
    assert with_hcr > 0 and without_hcr > 0
    assert with_hcr > without_hcr


def test_request_never_carries_hcr():
    for seed in range(30):
        transaction_set = _first_transaction_set(generate_278_request(random.Random(seed)))
        assert find_segment(transaction_set.segments, "HCR") is None


def test_response_hcr_action_code_varies_across_recognized_and_unrecognized():
    codes_seen = set()
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_278_response(random.Random(seed)))
        hcr = find_segment(transaction_set.segments, "HCR")
        if hcr is not None:
            codes_seen.add(element(hcr, 1))
    assert {"A1", "A2", "A3", "A4", "Z9"} <= codes_seen


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_trailer_count_mismatches_occur_and_are_correct_most_of_the_time(generator_fn):
    mismatched = matched = 0
    for seed in range(120):
        text = generator_fn(random.Random(seed))
        interchange = parse_interchange(text)
        transaction_set = interchange.functional_groups[0].transaction_sets[0]
        declared_se01 = int(element(transaction_set.se, 1))
        actual_se01 = len(transaction_set.segments) + 2
        if declared_se01 == actual_se01:
            matched += 1
        else:
            mismatched += 1
    assert matched > 0 and mismatched > 0
    assert matched > mismatched


def test_round_trips_through_real_converter():
    for seed in range(1000, 1020):
        text = generate_278_request(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "Organization", "Claim"} <= resource_types

    for seed in range(1000, 1020):
        text = generate_278_response(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        # ClaimResponse isn't guaranteed here - the generator deliberately
        # omits HCR ~15% of the time (see test_response_hcr_presence_
        # varies_across_seeds) - only Claim is guaranteed for a response.
        assert {"Patient", "Organization", "Claim"} <= resource_types


def test_generated_message_has_no_validation_errors():
    for gen in (generate_278_request, generate_278_response):
        for seed in range(1000, 1020):
            report = validate_edi(gen(random.Random(seed)))
            assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generate_is_reproducible_with_same_seed():
    assert generate_278_request(random.Random(7)) == generate_278_request(random.Random(7))
    assert generate_278_response(random.Random(7)) == generate_278_response(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_278_request(random.Random(1)) != generate_278_request(random.Random(2))
    assert generate_278_response(random.Random(1)) != generate_278_response(random.Random(2))
