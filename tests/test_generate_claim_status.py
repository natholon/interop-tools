import random

import pytest

from app.edi.generator import generate_276, generate_277
from app.edi.parser import element, find_segment, first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle, validate_edi

_GENERATORS = [
    (generate_276, "276"),
    (generate_277, "277"),
]


def _first_transaction_set(text: str):
    return first_transaction_set(parse_interchange(text))


@pytest.mark.parametrize("generator_fn, st01", _GENERATORS)
def test_generated_message_parses_with_correct_transaction_set(generator_fn, st01):
    for seed in range(20):
        transaction_set = _first_transaction_set(generator_fn(random.Random(seed)))
        assert transaction_set.st01 == st01


@pytest.mark.parametrize("generator_fn, st01", _GENERATORS)
def test_required_fields_always_present(generator_fn, st01):
    for seed in range(20):
        transaction_set = _first_transaction_set(generator_fn(random.Random(seed)))
        bht = find_segment(transaction_set.segments, "BHT")
        assert bht is not None
        assert element(bht, 3), "BHT03 reference identification must always be present"
        assert element(bht, 4), "BHT04 date must always be present"
        assert element(bht, 5), "BHT05 time must always be present"

        subscriber_nm1 = None
        found_trn = False
        for seg in transaction_set.segments:
            if seg[0] == "NM1" and element(seg, 1) == "IL":
                subscriber_nm1 = seg
            if seg[0] == "TRN":
                found_trn = True
        assert subscriber_nm1 is not None, "subscriber NM1 must always be present"
        assert element(subscriber_nm1, 3), "subscriber family name must always be present"
        assert found_trn, "at least one TRN-led claim-status group must always be present"


@pytest.mark.parametrize("generator_fn", [g for g, _ in _GENERATORS])
def test_dependent_and_provider_shape_vary_across_seeds(generator_fn):
    dependent_present = dependent_absent = 0
    receiver_person = receiver_org = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generator_fn(random.Random(seed)))
        hl_segments = [s for s in transaction_set.segments if s[0] == "HL"]
        if len(hl_segments) == 5:
            dependent_present += 1
        else:
            dependent_absent += 1
        receiver_nm1 = next(s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "41")
        if element(receiver_nm1, 2) == "1":
            receiver_person += 1
        else:
            receiver_org += 1
    assert dependent_present > 0 and dependent_absent > 0
    assert receiver_person > 0 and receiver_org > 0


def test_277_status_category_varies_across_recognized_and_unrecognized_prefixes():
    categories_seen = set()
    for seed in range(60):
        transaction_set = _first_transaction_set(generate_277(random.Random(seed)))
        for seg in transaction_set.segments:
            if seg[0] == "STC":
                categories_seen.add(element(seg, 1).split(":")[0])
    # Every prefix app.edi.claim_status recognizes (A/P/F/R/E) plus the
    # deliberately-unrecognized "D0" fallback case should appear somewhere
    # across this many seeds.
    assert {"A1", "P1", "F1", "F2", "R3", "E1", "D0"} <= categories_seen


@pytest.mark.parametrize("generator_fn, st01", _GENERATORS)
def test_trailer_count_mismatches_occur_and_are_correct_most_of_the_time(generator_fn, st01):
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
    for gen in (generate_276, generate_277):
        for seed in range(1000, 1020):
            text = gen(random.Random(seed))
            bundle = convert_edi_to_bundle(text)
            resource_types = {e.resource.get_resource_type() for e in bundle.entry}
            assert {"Patient", "Organization", "Task"} <= resource_types


def test_generated_message_has_no_validation_errors():
    for gen in (generate_276, generate_277):
        for seed in range(1000, 1020):
            report = validate_edi(gen(random.Random(seed)))
            assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generate_is_reproducible_with_same_seed():
    assert generate_276(random.Random(7)) == generate_276(random.Random(7))
    assert generate_277(random.Random(7)) == generate_277(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_276(random.Random(1)) != generate_276(random.Random(2))
    assert generate_277(random.Random(1)) != generate_277(random.Random(2))
