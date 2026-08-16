import random
from decimal import Decimal

import pytest

from app.edi.remittance_generator import generate_835
from app.edi.parser import element, find_segment, first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle, validate_edi


def _first_transaction_set(text: str):
    return first_transaction_set(parse_interchange(text))


def test_generated_message_parses_with_correct_st01():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_835(random.Random(seed)))
        assert transaction_set.st01 == "835"


def test_required_fields_always_present():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_835(random.Random(seed)))
        bpr = find_segment(transaction_set.segments, "BPR")
        assert bpr is not None
        assert element(bpr, 2), "BPR02 payment amount must always be present"
        assert element(bpr, 16), "BPR16 payment date must always be present"

        trn = find_segment(transaction_set.segments, "TRN")
        assert trn is not None
        assert element(trn, 2), "TRN02 trace number must always be present"

        payer_n1 = next(s for s in transaction_set.segments if s[0] == "N1" and element(s, 1) == "PR")
        payee_n1 = next(s for s in transaction_set.segments if s[0] == "N1" and element(s, 1) == "PE")
        assert element(payer_n1, 2), "payer name must always be present"
        assert element(payee_n1, 2), "payee name must always be present"

        assert any(s[0] == "CLP" for s in transaction_set.segments), "at least one CLP claim must always be present"


def test_claim_count_varies_across_seeds():
    counts = set()
    for seed in range(60):
        transaction_set = _first_transaction_set(generate_835(random.Random(seed)))
        counts.add(sum(1 for s in transaction_set.segments if s[0] == "CLP"))
    assert counts == {1, 2, 3}


def test_cas_presence_varies_across_seeds():
    with_cas = without_cas = 0
    for seed in range(60):
        transaction_set = _first_transaction_set(generate_835(random.Random(seed)))
        for i, seg in enumerate(transaction_set.segments):
            if seg[0] != "CLP":
                continue
            has_cas = i + 1 < len(transaction_set.segments) and transaction_set.segments[i + 1][0] == "CAS"
            if has_cas:
                with_cas += 1
            else:
                without_cas += 1
    assert with_cas > 0 and without_cas > 0
    assert with_cas > without_cas


def test_bpr02_equals_sum_of_clp04_paid_amounts():
    for seed in range(30):
        transaction_set = _first_transaction_set(generate_835(random.Random(seed)))
        bpr = find_segment(transaction_set.segments, "BPR")
        declared_total = Decimal(element(bpr, 2))
        actual_total = sum(Decimal(element(s, 4)) for s in transaction_set.segments if s[0] == "CLP")
        assert declared_total == actual_total, f"seed={seed}"


def test_trailer_count_mismatches_occur_and_are_correct_most_of_the_time():
    mismatched = matched = 0
    for seed in range(120):
        text = generate_835(random.Random(seed))
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
        text = generate_835(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Organization", "PaymentReconciliation"} <= resource_types


def test_generated_message_has_no_validation_errors():
    for seed in range(1000, 1020):
        report = validate_edi(generate_835(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generate_is_reproducible_with_same_seed():
    assert generate_835(random.Random(7)) == generate_835(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_835(random.Random(1)) != generate_835(random.Random(2))
