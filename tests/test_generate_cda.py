import random

from app.cda.ccd import CCD_TEMPLATE_ID
from app.cda.generator import generate_ccd
from app.cda.parser import find_all, find_child, has_template_id, parse_document
from app.cda.pipeline import convert_cda_to_bundle, validate_cda


def _document(seed: int):
    return parse_document(generate_ccd(random.Random(seed)))


def test_generated_document_parses_and_has_ccd_templateid():
    for seed in range(20):
        document = _document(seed)
        assert has_template_id(document, CCD_TEMPLATE_ID)


def test_patient_name_always_present():
    for seed in range(20):
        document = _document(seed)
        patient = find_child(find_child(find_child(document, "recordTarget"), "patientRole"), "patient")
        assert find_all(patient, "name"), f"seed={seed} missing patient name"


def _present_absent(seeds, check):
    present = absent = 0
    for seed in seeds:
        if check(_document(seed)):
            present += 1
        else:
            absent += 1
    return present, absent


def test_gender_varies_across_seeds():
    def has_gender(document):
        patient = find_child(find_child(find_child(document, "recordTarget"), "patientRole"), "patient")
        return find_child(patient, "administrativeGenderCode") is not None

    present, absent = _present_absent(range(60), has_gender)
    assert present > 0 and absent > 0


def test_birth_time_varies_across_seeds():
    def has_birth_time(document):
        patient = find_child(find_child(find_child(document, "recordTarget"), "patientRole"), "patient")
        return find_child(patient, "birthTime") is not None

    present, absent = _present_absent(range(60), has_birth_time)
    assert present > 0 and absent > 0


def test_address_and_telecom_vary_across_seeds():
    def has_addr(document):
        patient_role = find_child(find_child(document, "recordTarget"), "patientRole")
        return bool(find_all(patient_role, "addr"))

    def has_telecom(document):
        patient_role = find_child(find_child(document, "recordTarget"), "patientRole")
        return bool(find_all(patient_role, "telecom"))

    addr_present, addr_absent = _present_absent(range(60), has_addr)
    telecom_present, telecom_absent = _present_absent(range(60), has_telecom)
    assert addr_present > 0 and addr_absent > 0
    assert telecom_present > 0 and telecom_absent > 0


def test_encounter_and_problems_section_vary_across_seeds():
    def has_encounter(document):
        return find_child(document, "componentOf") is not None

    def has_problems_section(document):
        return any(
            has_template_id(section, "2.16.840.1.113883.10.20.22.2.5.1")
            for section in find_all(document, "component/structuredBody/component/section")
        )

    enc_present, enc_absent = _present_absent(range(60), has_encounter)
    problems_present, problems_absent = _present_absent(range(60), has_problems_section)
    assert enc_present > 0 and enc_absent > 0
    assert problems_present > 0 and problems_absent > 0


def test_both_identifier_shapes_occur_across_seeds():
    root_only = with_extension = 0
    for seed in range(60):
        document = _document(seed)
        patient_role = find_child(find_child(document, "recordTarget"), "patientRole")
        for id_element in find_all(patient_role, "id"):
            if id_element.get("extension"):
                with_extension += 1
            else:
                root_only += 1
    assert root_only > 0 and with_extension > 0


def test_both_document_effective_time_shapes_occur_across_seeds():
    date_only = full_timestamp = 0
    for seed in range(60):
        document = _document(seed)
        value = find_child(document, "effectiveTime").get("value")
        if len(value) == 8:
            date_only += 1
        else:
            full_timestamp += 1
    assert date_only > 0 and full_timestamp > 0


def test_unrecognized_encounter_class_occurs_across_seeds():
    recognized = unrecognized = 0
    for seed in range(120):
        document = _document(seed)
        component_of = find_child(document, "componentOf")
        if component_of is None:
            continue
        code = find_child(find_child(component_of, "encompassingEncounter"), "code").get("code")
        if code in {"AMB", "EMER", "IMP"}:
            recognized += 1
        else:
            unrecognized += 1
    assert recognized > 0 and unrecognized > 0


def test_encounter_and_problem_ivl_ts_shapes_all_occur_across_seeds():
    # Encounter and problem effectiveTime use the 3-way bare/low-only/
    # low+high split (_random_ivl_ts) - a genuinely different code path
    # from the document-level effectiveTime's simpler 2-way split, so it
    # needs its own fuzz-coverage assertion.
    shapes = {"bare": 0, "low_only": 0, "low_and_high": 0}

    def classify(effective_time):
        if effective_time.get("value"):
            shapes["bare"] += 1
        elif find_child(effective_time, "high") is not None:
            shapes["low_and_high"] += 1
        else:
            shapes["low_only"] += 1

    for seed in range(80):
        document = _document(seed)
        component_of = find_child(document, "componentOf")
        if component_of is not None:
            classify(find_child(find_child(component_of, "encompassingEncounter"), "effectiveTime"))
        for section in find_all(document, "component/structuredBody/component/section"):
            if not has_template_id(section, "2.16.840.1.113883.10.20.22.2.5.1"):
                continue
            for entry in find_all(section, "entry"):
                act = find_child(entry, "act")
                for relationship in find_all(act, "entryRelationship"):
                    observation = find_child(relationship, "observation")
                    classify(find_child(observation, "effectiveTime"))

    assert all(count > 0 for count in shapes.values()), shapes


def test_problem_count_varies_across_seeds():
    counts = set()
    for seed in range(60):
        document = _document(seed)
        for section in find_all(document, "component/structuredBody/component/section"):
            if not has_template_id(section, "2.16.840.1.113883.10.20.22.2.5.1"):
                continue
            counts.add(len(find_all(section, "entry")))
    assert {1, 2, 3} & counts, f"expected some problem-entry counts of 1/2/3, got {counts}"


def test_concern_act_status_varies_across_seeds():
    active = other = 0
    for seed in range(60):
        document = _document(seed)
        for section in find_all(document, "component/structuredBody/component/section"):
            if not has_template_id(section, "2.16.840.1.113883.10.20.22.2.5.1"):
                continue
            for entry in find_all(section, "entry"):
                act = find_child(entry, "act")
                status = find_child(act, "statusCode").get("code")
                if status == "active":
                    active += 1
                else:
                    other += 1
    assert active > 0 and other > 0


def test_patient_and_encounter_id_counts_vary_across_seeds():
    patient_counts = set()
    encounter_counts = set()
    for seed in range(60):
        document = _document(seed)
        patient_role = find_child(find_child(document, "recordTarget"), "patientRole")
        patient_counts.add(len(find_all(patient_role, "id")))
        component_of = find_child(document, "componentOf")
        if component_of is not None:
            encounter_counts.add(len(find_all(find_child(component_of, "encompassingEncounter"), "id")))
    assert patient_counts == {1, 2}, patient_counts
    assert encounter_counts == {1, 2}, encounter_counts


def test_both_clinical_status_resolution_paths_occur_across_seeds():
    act_only = with_status_observation = 0
    for seed in range(60):
        document = _document(seed)
        for section in find_all(document, "component/structuredBody/component/section"):
            if not has_template_id(section, "2.16.840.1.113883.10.20.22.2.5.1"):
                continue
            for entry in find_all(section, "entry"):
                act = find_child(entry, "act")
                for relationship in find_all(act, "entryRelationship"):
                    observation = find_child(relationship, "observation")
                    if any(r.get("typeCode") == "REFR" for r in find_all(observation, "entryRelationship")):
                        with_status_observation += 1
                    else:
                        act_only += 1
    assert act_only > 0 and with_status_observation > 0


def test_negation_occurs_across_seeds():
    negated = asserted = 0
    for seed in range(60):
        document = _document(seed)
        for section in find_all(document, "component/structuredBody/component/section"):
            if not has_template_id(section, "2.16.840.1.113883.10.20.22.2.5.1"):
                continue
            for entry in find_all(section, "entry"):
                act = find_child(entry, "act")
                for relationship in find_all(act, "entryRelationship"):
                    observation = find_child(relationship, "observation")
                    if observation.get("negationInd") == "true":
                        negated += 1
                    else:
                        asserted += 1
    assert negated > 0 and asserted > 0


def test_round_trips_through_real_converter():
    for seed in range(1000, 1020):
        xml_text = generate_ccd(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert "Patient" in resource_types


def test_generated_document_has_no_validation_errors():
    for seed in range(1000, 1020):
        xml_text = generate_ccd(random.Random(seed))
        report = validate_cda(xml_text)
        assert report.is_valid, f"seed={seed} findings={report.findings}"


def test_generate_is_reproducible_with_same_seed():
    assert generate_ccd(random.Random(7)) == generate_ccd(random.Random(7))


def test_generate_differs_across_seeds():
    assert generate_ccd(random.Random(1)) != generate_ccd(random.Random(2))
