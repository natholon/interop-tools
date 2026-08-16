import random

from app.edi.claim_837d_generator import generate_837d
from app.edi.parser import element, find_segment, first_transaction_set, parse_interchange
from app.edi.pipeline import convert_edi_to_bundle, validate_edi


def _first_transaction_set(text: str):
    return first_transaction_set(parse_interchange(text))


def test_generated_message_parses_with_correct_st01_and_st03():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        assert transaction_set.st01 == "837"
        assert "X224" in transaction_set.st03


def test_required_fields_always_present():
    for seed in range(20):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        bht = find_segment(transaction_set.segments, "BHT")
        assert bht is not None
        assert element(bht, 3), "BHT03 reference identification must always be present"
        assert element(bht, 4), "BHT04 date must always be present"
        assert element(bht, 5), "BHT05 time must always be present"

        billing_provider_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "85"), None)
        subscriber_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "IL"), None)
        payer_nm1 = next((s for s in transaction_set.segments if s[0] == "NM1" and element(s, 1) == "PR"), None)
        clm = find_segment(transaction_set.segments, "CLM")
        assert billing_provider_nm1 is not None, "billing provider NM1*85 must always be present"
        assert subscriber_nm1 is not None, "subscriber NM1*IL must always be present"
        assert element(subscriber_nm1, 3), "subscriber family name must always be present"
        assert payer_nm1 is not None, "payer NM1*PR must always be present"
        assert clm is not None, "CLM must always be present"
        # Unlike 837P/837I, HI (diagnosis) is deliberately NOT always
        # present - dental claims commonly carry none at all (see
        # claim_837d_generator.py's own comment) - so it's not asserted
        # here, only that at least one service line always is.
        assert any(s[0] == "LX" for s in transaction_set.segments), "at least one service line must always be present"
        assert any(s[0] == "SV3" for s in transaction_set.segments), "at least one SV3 dental service line must always be present"


def test_billing_provider_and_patient_loop_shape_vary_across_seeds():
    billing_person = billing_org = 0
    patient_loop_present = patient_loop_absent = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
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
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        has_rendering = any(s[0] == "NM1" and element(s, 1) == "82" for s in transaction_set.segments)
        if has_rendering:
            with_rendering += 1
        else:
            without_rendering += 1
    assert with_rendering > 0 and without_rendering > 0


def test_diagnosis_presence_varies_across_seeds():
    # Direct fuzz coverage of edi.837d-missing-diagnosis's own info
    # finding - a generator that always (or never) included HI would leave
    # one branch of that rule permanently untested.
    with_diagnosis = without_diagnosis = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        if any(s[0] == "HI" for s in transaction_set.segments):
            with_diagnosis += 1
        else:
            without_diagnosis += 1
    assert with_diagnosis > 0 and without_diagnosis > 0


def test_diagnosis_pointer_out_of_range_occurs_and_is_the_minority():
    in_range = out_of_range = 0
    for seed in range(300):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        diagnosis_count = sum(
            1
            for s in transaction_set.segments
            if s[0] == "HI"
            for pos in range(1, 13)
            if element(s, pos)
        )
        for seg in transaction_set.segments:
            if seg[0] != "SV3":
                continue
            pointer_composite = element(seg, 11)
            if not pointer_composite:
                continue
            pointers = [int(p) for p in pointer_composite.split(":") if p]
            if all(1 <= p <= diagnosis_count for p in pointers):
                in_range += 1
            else:
                out_of_range += 1
    assert in_range > 0 and out_of_range > 0
    assert in_range > out_of_range


def test_tooth_information_presence_varies_across_seeds():
    with_too = without_too = 0
    for seed in range(80):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        line_groups = [i for i, s in enumerate(transaction_set.segments) if s[0] == "LX"]
        for idx, lx_index in enumerate(line_groups):
            end = line_groups[idx + 1] if idx + 1 < len(line_groups) else len(transaction_set.segments)
            members = transaction_set.segments[lx_index:end]
            if any(s[0] == "TOO" for s in members):
                with_too += 1
            else:
                without_too += 1
    assert with_too > 0 and without_too > 0


def test_service_date_source_varies_across_seeds():
    # Dental's own structural quirk: a service line's date can come from
    # its own DTP, a claim-level DTP*472 fallback, or be absent entirely -
    # direct fuzz coverage that all three actually occur across seeds, not
    # just the always-per-line shape 837P/837I have.
    line_level = claim_level_only = neither = 0
    for seed in range(150):
        transaction_set = _first_transaction_set(generate_837d(random.Random(seed)))
        clm_index = next(i for i, s in enumerate(transaction_set.segments) if s[0] == "CLM")
        first_lx_index = next((i for i, s in enumerate(transaction_set.segments) if s[0] == "LX"), len(transaction_set.segments))
        claim_level_dtp = any(
            s[0] == "DTP" and element(s, 1) == "472" for s in transaction_set.segments[clm_index:first_lx_index]
        )
        line_groups = [i for i, s in enumerate(transaction_set.segments) if s[0] == "LX"]
        for idx, lx_index in enumerate(line_groups):
            end = line_groups[idx + 1] if idx + 1 < len(line_groups) else len(transaction_set.segments)
            members = transaction_set.segments[lx_index:end]
            has_own_dtp = any(s[0] == "DTP" and element(s, 1) == "472" for s in members)
            if has_own_dtp:
                line_level += 1
            elif claim_level_dtp:
                claim_level_only += 1
            else:
                neither += 1
    assert line_level > 0 and claim_level_only > 0 and neither > 0


def test_trailer_count_mismatches_occur_and_are_correct_most_of_the_time():
    mismatched = matched = 0
    for seed in range(120):
        text = generate_837d(random.Random(seed))
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
        text = generate_837d(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "Organization", "Coverage", "Claim"} <= resource_types


def test_generated_message_has_no_validation_errors():
    for seed in range(1000, 1020):
        report = validate_edi(generate_837d(random.Random(seed)))
        assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generated_message_dispatches_to_837d_builder_via_st03():
    # Proves the ST03-based registry dispatch actually routes generated
    # 837D text to Edi837dBuilder (not Edi837pBuilder) end to end - the
    # same regression risk this app already hit once for 837I vs 837P.
    for seed in range(5):
        text = generate_837d(random.Random(seed))
        bundle = convert_edi_to_bundle(text)
        claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
        assert claim.type.coding[0].code == "oral"


def test_generate_is_reproducible_with_same_seed():
    assert generate_837d(random.Random(7)) == generate_837d(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_837d(random.Random(1)) != generate_837d(random.Random(2))
