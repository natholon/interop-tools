import random

from app.edi.claim_837i_generator import generate_837i
from app.edi.parser import element, find_segment, first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle, validate_edi


def _first_transaction_set(text: str):
    return first_transaction_set(parse_interchange(text))


def test_generated_message_parses_with_correct_st01_and_st03():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_837i(random.Random(seed)))
        assert transaction_set.st01 == "837"
        assert "X223" in transaction_set.st03


def test_required_fields_always_present():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_837i(random.Random(seed)))
        bht = find_segment(transaction_set.segments, "BHT")
        assert bht is not None
        assert element(bht, 3), "BHT03 reference identification must always be present"
        assert element(bht, 4), "BHT04 date must always be present"
        assert element(bht, 5), "BHT05 time must always be present"

        billing_provider_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "85"), None)
        subscriber_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "IL"), None)
        payer_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "PR"), None)
        clm = find_segment(transaction_set.segments, "CLM")
        cl1 = find_segment(transaction_set.segments, "CL1")
        assert billing_provider_nm1 is not None, "billing provider NM1*85 must always be present"
        assert subscriber_nm1 is not None, "subscriber NM1*IL must always be present"
        assert element(subscriber_nm1, 3), "subscriber family name must always be present"
        assert payer_nm1 is not None, "payer NM1*PR must always be present"
        assert clm is not None, "CLM must always be present"
        assert cl1 is not None, "CL1 must always be present"
        assert element(cl1, 3), "CL103 (patient status) must always be present"
        assert any(s[0] == "HI" for s in transaction_set.segments), "at least one HI segment must always be present"
        assert any(s[0] == "LX" for s in transaction_set.segments), "at least one service line must always be present"


def test_billing_provider_and_patient_loop_shape_vary_across_seeds():
    billing_person = billing_org = 0
    patient_loop_present = patient_loop_absent = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837i(random.Random(seed)))
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


def test_attending_provider_presence_varies_across_seeds():
    with_attending = without_attending = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837i(random.Random(seed)))
        has_attending = any(s[0] == "NM1" and element(s, 1) == "71" for s in transaction_set.segments)
        if has_attending:
            with_attending += 1
        else:
            without_attending += 1
    assert with_attending > 0 and without_attending > 0


def test_procedure_code_presence_on_service_lines_varies_across_seeds():
    with_procedure = without_procedure = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837i(random.Random(seed)))
        for seg in transaction_set.segments:
            if seg[0] != "SV2":
                continue
            if element(seg, 2):
                with_procedure += 1
            else:
                without_procedure += 1
    assert with_procedure > 0 and without_procedure > 0


def test_occurrence_value_condition_hi_segments_occur_across_seeds():
    # Fuzz-proving iter_diagnosis_hi_segments's own "skip non-diagnosis HI
    # segments" filter is actually exercised, not just theoretically
    # reachable - mirrors this app's established "a generator that never
    # produces a case leaves the rule/filter untested" precedent.
    saw_occurrence = saw_value = saw_condition = False
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837i(random.Random(seed)))
        for seg in transaction_set.segments:
            if seg[0] != "HI":
                continue
            first_element = element(seg, 1)
            if first_element.startswith("BH:"):
                saw_occurrence = True
            elif first_element.startswith("BE:"):
                saw_value = True
            elif first_element.startswith("BG:"):
                saw_condition = True
    assert saw_occurrence and saw_value and saw_condition


def test_trailer_count_mismatches_occur_and_are_correct_most_of_the_time():
    mismatched = matched = 0
    for seed in range(120):
        text = generate_837i(random.Random(seed))
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
        text = generate_837i(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "Organization", "Coverage", "Claim"} <= resource_types


def test_generated_message_has_no_validation_errors():
    for seed in range(1000, 1020):
        report = validate_edi(generate_837i(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generated_message_dispatches_to_837i_builder_via_st03():
    # Proves the ST03-based registry dispatch actually routes generated
    # 837I text to Edi837iBuilder (not Edi837pBuilder) end to end - a real
    # regression risk this app already hit once, with 837P vs 837I sharing
    # ST01="837".
    for seed in range(5):
        text = generate_837i(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
        assert claim.type.coding[0].code == "institutional"


def test_generate_is_reproducible_with_same_seed():
    assert generate_837i(random.Random(7)) == generate_837i(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_837i(random.Random(1)) != generate_837i(random.Random(2))
