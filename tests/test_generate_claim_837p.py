import random

from app.edi.claim_837p_generator import generate_837p
from app.edi.parser import element, find_segment, first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle, validate_edi


def _first_transaction_set(text: str):
    return first_transaction_set(parse_interchange(text))


def test_generated_message_parses_with_correct_st01():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_837p(random.Random(seed)))
        assert transaction_set.st01 == "837"


def test_required_fields_always_present():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_837p(random.Random(seed)))
        bht = find_segment(transaction_set.segments, "BHT")
        assert bht is not None
        assert element(bht, 3), "BHT03 reference identification must always be present"
        assert element(bht, 4), "BHT04 date must always be present"
        assert element(bht, 5), "BHT05 time must always be present"

        billing_provider_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "85"), None)
        subscriber_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "IL"), None)
        payer_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "PR"), None)
        clm = find_segment(transaction_set.segments, "CLM")
        hi = find_segment(transaction_set.segments, "HI")
        assert billing_provider_nm1 is not None, "billing provider NM1*85 must always be present"
        assert subscriber_nm1 is not None, "subscriber NM1*IL must always be present"
        assert element(subscriber_nm1, 3), "subscriber family name must always be present"
        assert payer_nm1 is not None, "payer NM1*PR must always be present"
        assert clm is not None, "CLM must always be present"
        assert hi is not None, "HI must always be present (generator always produces at least one diagnosis)"
        assert any(s[0] == "LX" for s in transaction_set.segments), "at least one service line must always be present"


def test_billing_provider_and_patient_loop_shape_vary_across_seeds():
    billing_person = billing_org = 0
    patient_loop_present = patient_loop_absent = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837p(random.Random(seed)))
        billing_nm1 = next(s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "85")
        if element(billing_nm1, 2) == "1":
            billing_person += 1
        else:
            billing_org += 1
        if any(s[0] == "HL" and element(s, 3) == "23" for s in transaction_set.segments):
            patient_loop_present += 1
        else:
            patient_loop_absent += 1
    assert billing_person > 0 and billing_org > 0
    assert patient_loop_present > 0 and patient_loop_absent > 0


def test_rendering_provider_presence_varies_across_seeds():
    with_rendering = without_rendering = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837p(random.Random(seed)))
        has_rendering = any(s[0] == "NM1" and element(s, 1) == "82" for s in transaction_set.segments)
        if has_rendering:
            with_rendering += 1
        else:
            without_rendering += 1
    assert with_rendering > 0 and without_rendering > 0


def test_diagnosis_pointer_out_of_range_occurs_and_is_the_minority():
    # ~10% deliberate out-of-range pointer fuzz - direct proof
    # edi.837p-diagnosis-pointer-unresolved is actually exercised, not just
    # theoretically reachable (mirrors ORU's own out-of-range OBX-5 proof).
    out_of_range = in_range = 0
    for seed in range(200):
        interchange = parse_interchange(generate_837p(random.Random(seed)))
        transaction_set = first_transaction_set(interchange)
        num_diagnoses = 0
        hi = find_segment(transaction_set.segments, "HI")
        for position in range(1, 13):
            composite = element(hi, position)
            if not composite:
                break
            num_diagnoses += 1
        found_unresolved = False
        for seg in transaction_set.segments:
            if seg[0] != "SV1":
                continue
            pointer_composite = element(seg, 7)
            for raw in pointer_composite.split(":"):
                if raw and int(raw) > num_diagnoses:
                    found_unresolved = True
        if found_unresolved:
            out_of_range += 1
        else:
            in_range += 1
    assert out_of_range > 0 and in_range > 0
    assert in_range > out_of_range


def test_trailer_count_mismatches_occur_and_are_correct_most_of_the_time():
    mismatched = matched = 0
    for seed in range(120):
        text = generate_837p(random.Random(seed))
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
        text = generate_837p(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "Organization", "Coverage", "Claim"} <= resource_types


def test_generated_message_has_no_validation_errors():
    for seed in range(1000, 1020):
        report = validate_edi(generate_837p(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generate_is_reproducible_with_same_seed():
    assert generate_837p(random.Random(7)) == generate_837p(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_837p(random.Random(1)) != generate_837p(random.Random(2))
