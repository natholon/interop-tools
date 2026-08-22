import random

from app.cda.ccd import CCD_TEMPLATE_ID
from app.cda.discharge_summary import DISCHARGE_SUMMARY_TEMPLATE_ID
from app.cda.generator import generate_ccd, generate_discharge_summary, generate_history_and_physical
from app.cda.history_and_physical import HISTORY_AND_PHYSICAL_TEMPLATE_ID
from app.cda.narrative_sections import (
    ASSESSMENT_TEMPLATE_ID,
    FAMILY_HISTORY_TEMPLATE_ID,
    GENERAL_STATUS_TEMPLATE_ID,
    HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
    HOSPITAL_COURSE_TEMPLATE_ID,
    PHYSICAL_EXAM_TEMPLATE_ID,
    PLAN_OF_TREATMENT_TEMPLATE_ID,
    REASON_FOR_VISIT_TEMPLATE_ID,
    REVIEW_OF_SYSTEMS_TEMPLATE_ID,
    SOCIAL_HISTORY_TEMPLATE_ID,
)
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


_MEDICATIONS_SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.1.1"


def _medication_entries(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        if not has_template_id(section, _MEDICATIONS_SECTION_TEMPLATE_ID):
            continue
        yield from find_all(section, "entry")


def test_medications_section_varies_across_seeds():
    def has_medications_section(document):
        return any(True for _ in _medication_entries(document))

    present, absent = _present_absent(range(60), has_medications_section)
    assert present > 0 and absent > 0


def test_medication_count_varies_across_seeds():
    counts = set()
    for seed in range(60):
        counts.add(sum(1 for _ in _medication_entries(_document(seed))))
    assert {0, 1, 2, 3} & counts, f"expected some medication-entry counts of 0/1/2/3, got {counts}"


def test_medication_mood_code_varies_across_seeds():
    int_count = evn_count = 0
    for seed in range(60):
        for entry in _medication_entries(_document(seed)):
            mood = find_child(entry, "substanceAdministration").get("moodCode")
            if mood == "INT":
                int_count += 1
            elif mood == "EVN":
                evn_count += 1
    assert int_count > 0 and evn_count > 0


def test_medication_status_code_varies_across_seeds():
    recognized = unrecognized = 0
    for seed in range(80):
        for entry in _medication_entries(_document(seed)):
            status = find_child(find_child(entry, "substanceAdministration"), "statusCode").get("code")
            if status in {"active", "suspended", "aborted", "completed", "nullified"}:
                recognized += 1
            else:
                unrecognized += 1
    assert recognized > 0 and unrecognized > 0


def test_medication_dosing_shape_varies_across_seeds():
    # Structured dosing (routeCode), free-text SIG (nested Free Text Sig
    # substanceAdministration), and neither - the three branches
    # _random_medication_entry splits ~45/35/20.
    structured = free_text = neither = 0
    for seed in range(60):
        for entry in _medication_entries(_document(seed)):
            substance_administration = find_child(entry, "substanceAdministration")
            if find_child(substance_administration, "routeCode") is not None:
                structured += 1
            elif find_all(substance_administration, "entryRelationship"):
                free_text += 1
            else:
                neither += 1
    assert structured > 0 and free_text > 0 and neither > 0


def test_medication_negation_occurs_across_seeds():
    negated = asserted = 0
    for seed in range(60):
        for entry in _medication_entries(_document(seed)):
            if find_child(entry, "substanceAdministration").get("negationInd") == "true":
                negated += 1
            else:
                asserted += 1
    assert negated > 0 and asserted > 0


_ALLERGIES_SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.6.1"
_ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.6"
_ALLERGY_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.7"


def _allergy_observations(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        if not has_template_id(section, _ALLERGIES_SECTION_TEMPLATE_ID) and not has_template_id(
            section, _ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
        ):
            continue
        for entry in find_all(section, "entry"):
            act = find_child(entry, "act")
            for relationship in find_all(act, "entryRelationship"):
                observation = find_child(relationship, "observation")
                if observation is not None and has_template_id(observation, _ALLERGY_OBSERVATION_TEMPLATE_ID):
                    yield observation


def test_allergies_section_varies_across_seeds():
    def has_allergies_section(document):
        return any(True for _ in _allergy_observations(document))

    present, absent = _present_absent(range(60), has_allergies_section)
    assert present > 0 and absent > 0


def test_allergy_count_varies_across_seeds():
    counts = set()
    for seed in range(60):
        counts.add(sum(1 for _ in _allergy_observations(_document(seed))))
    assert {0, 1, 2, 3} & counts, f"expected some allergy-entry counts of 0/1/2/3, got {counts}"


def test_allergies_section_templateid_variant_varies_across_seeds():
    # "entries required" and "entries optional" wrap the identical entry
    # shape (see app/cda/allergies.py) but are two distinct templateIds -
    # app/cda/validation.py once recognized only one of them for its rule
    # dispatch (a real bug, caught by code review). Direct fuzz coverage so
    # that gap can't silently reopen: both variants must actually occur.
    entries_required = entries_optional = 0
    for seed in range(60):
        for section in find_all(_document(seed), "component/structuredBody/component/section"):
            if has_template_id(section, _ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL):
                entries_optional += 1
            elif has_template_id(section, _ALLERGIES_SECTION_TEMPLATE_ID):
                entries_required += 1
    assert entries_required > 0 and entries_optional > 0


def test_allergy_negation_and_allergen_shape_vary_across_seeds():
    # Negated entries split further between a still-resolvable allergen
    # ("no known allergy to X") and a nullFlavor one ("no known
    # allergies") - direct fuzz coverage of both negation branches in
    # _resolve_allergen_code, alongside plain asserted allergies.
    asserted = negated_with_code = negated_without_code = 0
    for seed in range(80):
        for observation in _allergy_observations(_document(seed)):
            code_element = find_child(
                find_child(find_child(observation, "participant"), "participantRole"), "playingEntity"
            )
            code_element = find_child(code_element, "code") if code_element is not None else None
            has_code = code_element is not None and code_element.get("nullFlavor") is None
            if observation.get("negationInd") == "true":
                if has_code:
                    negated_with_code += 1
                else:
                    negated_without_code += 1
            else:
                asserted += 1
    assert asserted > 0 and negated_with_code > 0 and negated_without_code > 0


def test_allergy_clinical_status_resolution_paths_occur_across_seeds():
    act_default = with_status_observation = 0
    for seed in range(60):
        for observation in _allergy_observations(_document(seed)):
            if any(r.get("typeCode") == "REFR" for r in find_all(observation, "entryRelationship")):
                with_status_observation += 1
            else:
                act_default += 1
    assert act_default > 0 and with_status_observation > 0


def test_allergy_criticality_and_reaction_vary_across_seeds():
    criticality_present = criticality_absent = 0
    reaction_present = reaction_absent = 0
    for seed in range(60):
        for observation in _allergy_observations(_document(seed)):
            relationships = find_all(observation, "entryRelationship")
            if any(r.get("typeCode") == "SUBJ" and r.get("inversionInd") == "true" for r in relationships):
                criticality_present += 1
            else:
                criticality_absent += 1
            if any(r.get("typeCode") == "MFST" for r in relationships):
                reaction_present += 1
            else:
                reaction_absent += 1
    assert criticality_present > 0 and criticality_absent > 0
    assert reaction_present > 0 and reaction_absent > 0


_IMMUNIZATIONS_SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.2.1"
_IMMUNIZATION_ACTIVITY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.52"


def _immunization_activities(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        if not has_template_id(section, _IMMUNIZATIONS_SECTION_TEMPLATE_ID):
            continue
        for entry in find_all(section, "entry"):
            substance_administration = find_child(entry, "substanceAdministration")
            if substance_administration is not None and has_template_id(
                substance_administration, _IMMUNIZATION_ACTIVITY_TEMPLATE_ID
            ):
                yield substance_administration


def test_immunizations_section_varies_across_seeds():
    def has_immunizations_section(document):
        return any(True for _ in _immunization_activities(document))

    present, absent = _present_absent(range(60), has_immunizations_section)
    assert present > 0 and absent > 0


def test_immunization_count_varies_across_seeds():
    counts = set()
    for seed in range(60):
        counts.add(sum(1 for _ in _immunization_activities(_document(seed))))
    assert {0, 1, 2, 3} & counts, f"expected some immunization-entry counts of 0/1/2/3, got {counts}"


def test_immunization_mood_and_negation_vary_across_seeds():
    evn_asserted = evn_negated = int_mood = 0
    for seed in range(80):
        for substance_administration in _immunization_activities(_document(seed)):
            if substance_administration.get("moodCode") == "INT":
                int_mood += 1
            elif substance_administration.get("negationInd") == "true":
                evn_negated += 1
            else:
                evn_asserted += 1
    assert evn_asserted > 0 and evn_negated > 0 and int_mood > 0


def test_immunization_status_code_varies_across_seeds():
    recognized = unrecognized = 0
    for seed in range(80):
        for substance_administration in _immunization_activities(_document(seed)):
            status = find_child(substance_administration, "statusCode").get("code")
            if status in {"completed", "nullified", "aborted", "cancelled", "held", "new", "obsolete", "suspended"}:
                recognized += 1
            else:
                unrecognized += 1
    assert recognized > 0 and unrecognized > 0


def test_immunization_dosing_and_lot_number_vary_across_seeds():
    dosing_present = dosing_absent = 0
    lot_present = lot_absent = 0
    for seed in range(60):
        for substance_administration in _immunization_activities(_document(seed)):
            if find_child(substance_administration, "routeCode") is not None:
                dosing_present += 1
            else:
                dosing_absent += 1
            manufactured_material = find_child(
                find_child(find_child(substance_administration, "consumable"), "manufacturedProduct"),
                "manufacturedMaterial",
            )
            if manufactured_material is not None and find_child(manufactured_material, "lotNumberText") is not None:
                lot_present += 1
            else:
                lot_absent += 1
    assert dosing_present > 0 and dosing_absent > 0
    assert lot_present > 0 and lot_absent > 0


_VITALS_SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.4.1"
_VITAL_SIGNS_ORGANIZER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.26"
_VITAL_SIGN_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.27"
_RESULTS_SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.3.1"
_RESULT_ORGANIZER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.1"
_RESULT_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.2"
_PROCEDURES_SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.7.1"
_PROCEDURE_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.14"


def _vital_signs_organizers(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        if not has_template_id(section, _VITALS_SECTION_TEMPLATE_ID):
            continue
        for entry in find_all(section, "entry"):
            organizer = find_child(entry, "organizer")
            if organizer is not None and has_template_id(organizer, _VITAL_SIGNS_ORGANIZER_TEMPLATE_ID):
                yield organizer


def _vital_sign_observations(document):
    for organizer in _vital_signs_organizers(document):
        for component in find_all(organizer, "component"):
            observation = find_child(component, "observation")
            if observation is not None and has_template_id(observation, _VITAL_SIGN_OBSERVATION_TEMPLATE_ID):
                yield observation


def test_vitals_section_varies_across_seeds():
    def has_vitals_section(document):
        return any(True for _ in _vital_signs_organizers(document))

    present, absent = _present_absent(range(60), has_vitals_section)
    assert present > 0 and absent > 0


def test_vital_signs_organizer_and_observation_counts_vary_across_seeds():
    organizer_counts = set()
    observation_counts = set()
    for seed in range(60):
        document = _document(seed)
        organizer_counts.add(sum(1 for _ in _vital_signs_organizers(document)))
        observation_counts.add(sum(1 for _ in _vital_sign_observations(document)))
    assert len(organizer_counts) > 1
    assert len(observation_counts) > 1


_VITALS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.4"


def test_vitals_section_templateid_variant_varies_across_seeds():
    # "entries required" (...2.4.1) and "entries optional" (...2.4) wrap the
    # identical entry shape but are two distinct templateIds - mirroring
    # the identical Allergies fuzz test above. Direct fuzz coverage so a
    # future regression in _find_vitals_section's dispatch can't reopen
    # silently: both variants must actually occur.
    entries_required = entries_optional = 0
    for seed in range(60):
        for section in find_all(_document(seed), "component/structuredBody/component/section"):
            if has_template_id(section, _VITALS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL):
                entries_optional += 1
            elif has_template_id(section, _VITALS_SECTION_TEMPLATE_ID):
                entries_required += 1
    assert entries_required > 0 and entries_optional > 0


def test_vital_sign_interpretation_varies_across_seeds():
    present = absent = 0
    for seed in range(80):
        for observation in _vital_sign_observations(_document(seed)):
            if find_child(observation, "interpretationCode") is not None:
                present += 1
            else:
                absent += 1
    assert present > 0 and absent > 0


def _observation_codes(organizer):
    return {
        find_child(observation, "code").get("code")
        for observation in _vital_sign_observations_in_organizer(organizer)
    }


def _vital_sign_observations_in_organizer(organizer):
    for component in find_all(organizer, "component"):
        observation = find_child(component, "observation")
        if observation is not None and has_template_id(observation, _VITAL_SIGN_OBSERVATION_TEMPLATE_ID):
            yield observation


def test_blood_pressure_panel_pair_and_incomplete_fallback_vary_across_seeds():
    # Direct fuzz coverage of app/cda/vitals.py's own Blood Pressure Panel
    # grouping: both a complete systolic+diastolic pair (grouped into one
    # panel) and an incomplete pair (falls back to plain) must occur.
    complete = incomplete = 0
    for seed in range(80):
        for organizer in _vital_signs_organizers(_document(seed)):
            codes = _observation_codes(organizer)
            has_systolic = "8480-6" in codes
            has_diastolic = "8462-4" in codes
            if has_systolic and has_diastolic:
                complete += 1
            elif has_systolic or has_diastolic:
                incomplete += 1
    assert complete > 0 and incomplete > 0


def test_pulse_oximetry_reading_and_optional_components_vary_across_seeds():
    # Direct fuzz coverage of app/cda/vitals.py's own Pulse Oximetry Panel
    # grouping: the primary O2 saturation reading must occur both with and
    # without its own optional concentration/flow-rate siblings.
    with_components = without_components = absent = 0
    for seed in range(80):
        for organizer in _vital_signs_organizers(_document(seed)):
            codes = _observation_codes(organizer)
            if "59408-5" not in codes:
                absent += 1
                continue
            if "3150-0" in codes or "3151-8" in codes:
                with_components += 1
            else:
                without_components += 1
    assert with_components > 0 and without_components > 0 and absent > 0


def _result_organizers(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        if not has_template_id(section, _RESULTS_SECTION_TEMPLATE_ID):
            continue
        for entry in find_all(section, "entry"):
            organizer = find_child(entry, "organizer")
            if organizer is not None and has_template_id(organizer, _RESULT_ORGANIZER_TEMPLATE_ID):
                yield organizer


def _result_observations(document):
    for organizer in _result_organizers(document):
        for component in find_all(organizer, "component"):
            observation = find_child(component, "observation")
            if observation is not None and has_template_id(observation, _RESULT_OBSERVATION_TEMPLATE_ID):
                yield observation


def test_results_section_varies_across_seeds():
    def has_results_section(document):
        return any(True for _ in _result_organizers(document))

    present, absent = _present_absent(range(60), has_results_section)
    assert present > 0 and absent > 0


_RESULTS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.3"


def test_results_section_templateid_variant_varies_across_seeds():
    entries_required = entries_optional = 0
    for seed in range(60):
        for section in find_all(_document(seed), "component/structuredBody/component/section"):
            if has_template_id(section, _RESULTS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL):
                entries_optional += 1
            elif has_template_id(section, _RESULTS_SECTION_TEMPLATE_ID):
                entries_required += 1
    assert entries_required > 0 and entries_optional > 0


def test_result_status_code_varies_across_recognized_and_unrecognized():
    from app.cda.results import STATUS_MAP

    recognized = unrecognized = 0
    for seed in range(80):
        for observation in _result_observations(_document(seed)):
            status = find_child(observation, "statusCode").get("code")
            if status in STATUS_MAP:
                recognized += 1
            else:
                unrecognized += 1
    assert recognized > 0 and unrecognized > 0


def test_result_reference_range_varies_across_seeds():
    present = absent = 0
    for seed in range(80):
        for observation in _result_observations(_document(seed)):
            if find_child(observation, "referenceRange") is not None:
                present += 1
            else:
                absent += 1
    assert present > 0 and absent > 0


def test_result_specimen_at_organizer_and_observation_level_varies_across_seeds():
    # Direct fuzz coverage of app/cda/results.py's own two specimen
    # attachment levels - the organizer-level default and an individual
    # observation's own override.
    organizer_specimen_present = organizer_specimen_absent = 0
    observation_specimen_present = 0
    for seed in range(80):
        for organizer in _result_organizers(_document(seed)):
            if find_child(organizer, "specimen") is not None:
                organizer_specimen_present += 1
            else:
                organizer_specimen_absent += 1
            for observation in _result_observations_in_organizer(organizer):
                if find_child(observation, "specimen") is not None:
                    observation_specimen_present += 1
    assert organizer_specimen_present > 0 and organizer_specimen_absent > 0
    assert observation_specimen_present > 0


def _result_observations_in_organizer(organizer):
    for component in find_all(organizer, "component"):
        observation = find_child(component, "observation")
        if observation is not None and has_template_id(observation, _RESULT_OBSERVATION_TEMPLATE_ID):
            yield observation


def test_result_value_type_varies_across_pq_ivl_pq_and_ed():
    # Direct fuzz coverage of app/cda/results.py's own IVL_PQ/ED value-type
    # branches, not just the pre-existing PQ shape.
    from app.cda.parser import xsi_type

    value_types = set()
    for seed in range(80):
        for observation in _result_observations(_document(seed)):
            value_element = find_child(observation, "value")
            if value_element is not None:
                value_types.add(xsi_type(value_element))
    assert {"PQ", "IVL_PQ", "ED"} <= value_types


def _procedure_entries(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        if not has_template_id(section, _PROCEDURES_SECTION_TEMPLATE_ID):
            continue
        for entry in find_all(section, "entry"):
            procedure = find_child(entry, "procedure")
            if procedure is not None and has_template_id(procedure, _PROCEDURE_TEMPLATE_ID):
                yield procedure


def test_procedures_section_varies_across_seeds():
    def has_procedures_section(document):
        return any(True for _ in _procedure_entries(document))

    present, absent = _present_absent(range(60), has_procedures_section)
    assert present > 0 and absent > 0


_PROCEDURES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.7"


def test_procedures_section_templateid_variant_varies_across_seeds():
    # This is the section a real official HL7 History and Physical example
    # was found using ONLY the "entries optional" templateId for - see
    # app/cda/procedures.py's own docstring.
    entries_required = entries_optional = 0
    for seed in range(60):
        for section in find_all(_document(seed), "component/structuredBody/component/section"):
            if has_template_id(section, _PROCEDURES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL):
                entries_optional += 1
            elif has_template_id(section, _PROCEDURES_SECTION_TEMPLATE_ID):
                entries_required += 1
    assert entries_required > 0 and entries_optional > 0


def test_procedure_negation_varies_across_seeds():
    negated = asserted = 0
    for seed in range(120):
        for procedure in _procedure_entries(_document(seed)):
            if procedure.get("negationInd") == "true":
                negated += 1
            else:
                asserted += 1
    assert negated > 0 and asserted > 0


def test_procedure_status_code_varies_across_recognized_and_unrecognized():
    from app.cda.procedures import STATUS_MAP

    recognized = unrecognized = 0
    for seed in range(80):
        for procedure in _procedure_entries(_document(seed)):
            status = find_child(procedure, "statusCode").get("code")
            if status in STATUS_MAP:
                recognized += 1
            else:
                unrecognized += 1
    assert recognized > 0 and unrecognized > 0


def test_procedure_effective_time_shape_varies_across_seeds():
    point_in_time = period = 0
    for seed in range(80):
        for procedure in _procedure_entries(_document(seed)):
            effective_time = find_child(procedure, "effectiveTime")
            if effective_time.get("value"):
                point_in_time += 1
            else:
                period += 1
    assert point_in_time > 0 and period > 0


def test_procedure_body_site_varies_across_seeds():
    present = absent = 0
    for seed in range(80):
        for procedure in _procedure_entries(_document(seed)):
            if find_child(procedure, "targetSiteCode") is not None:
                present += 1
            else:
                absent += 1
    assert present > 0 and absent > 0


def test_procedure_performer_presence_and_name_vs_id_only_varies_across_seeds():
    # Direct fuzz coverage of app/cda/procedures.py's own performer
    # handling - present/absent, and (when present) a real assignedPerson/
    # name vs. an id-only assignedEntity (the "skip only when neither
    # resolves" presence rule a real fetched example established).
    present = absent = with_name = id_only = 0
    for seed in range(120):
        for procedure in _procedure_entries(_document(seed)):
            performer = find_child(procedure, "performer")
            if performer is None:
                absent += 1
                continue
            present += 1
            assigned_entity = find_child(performer, "assignedEntity")
            if find_child(assigned_entity, "assignedPerson") is not None:
                with_name += 1
            else:
                id_only += 1
    assert present > 0 and absent > 0
    assert with_name > 0 and id_only > 0


def test_procedure_participant_location_varies_across_seeds():
    # Direct fuzz coverage of app/cda/procedures.py's own Service Delivery
    # Location handling.
    present = absent = 0
    for seed in range(120):
        for procedure in _procedure_entries(_document(seed)):
            if find_child(procedure, "participant") is not None:
                present += 1
            else:
                absent += 1
    assert present > 0 and absent > 0


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


def _discharge_summary_document(seed: int):
    return parse_document(generate_discharge_summary(random.Random(seed)))


def test_generated_discharge_summary_parses_and_has_discharge_summary_templateid():
    for seed in range(20):
        document = _discharge_summary_document(seed)
        assert has_template_id(document, DISCHARGE_SUMMARY_TEMPLATE_ID)
        assert not has_template_id(document, CCD_TEMPLATE_ID)


def test_generated_discharge_summary_always_has_an_encounter():
    # Unlike CCD (where encompassingEncounter is genuinely optional),
    # Discharge Summary forces one - see generate_discharge_summary's
    # force_encounter=True.
    for seed in range(20):
        document = _discharge_summary_document(seed)
        assert find_child(document, "componentOf") is not None


_HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.33"
_DISCHARGE_MEDICATION_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.35"


def _discharge_diagnosis_acts(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        for entry in find_all(section, "entry"):
            act = find_child(entry, "act")
            if act is not None and has_template_id(act, _HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID):
                yield act


def _discharge_medication_acts(document):
    for section in find_all(document, "component/structuredBody/component/section"):
        for entry in find_all(section, "entry"):
            act = find_child(entry, "act")
            if act is not None and has_template_id(act, _DISCHARGE_MEDICATION_ACT_TEMPLATE_ID):
                yield act


def test_discharge_specific_sections_vary_present_and_absent_across_seeds():
    diagnosis_present = diagnosis_absent = 0
    medication_present = medication_absent = 0
    for seed in range(60):
        document = _discharge_summary_document(seed)
        if any(True for _ in _discharge_diagnosis_acts(document)):
            diagnosis_present += 1
        else:
            diagnosis_absent += 1
        if any(True for _ in _discharge_medication_acts(document)):
            medication_present += 1
        else:
            medication_absent += 1
    assert diagnosis_present > 0 and diagnosis_absent > 0
    assert medication_present > 0 and medication_absent > 0


def test_discharge_specific_sections_never_occur_on_ccd_or_history_and_physical():
    # include_discharge_specific_sections is scoped to generate_discharge_
    # summary only - see _generate_sectioned_document's own docstring for
    # why generating these on CCD/H&P would produce unrealistic data.
    for seed in range(60):
        ccd_document = _document(seed)
        hp_document = _history_and_physical_document(seed)
        for document in (ccd_document, hp_document):
            assert not any(True for _ in _discharge_diagnosis_acts(document))
            assert not any(True for _ in _discharge_medication_acts(document))


def test_discharge_medication_status_code_varies_across_recognized_and_unrecognized():
    from app.cda.medications import STATUS_MAP

    recognized = unrecognized = 0
    for seed in range(80):
        for act in _discharge_medication_acts(_discharge_summary_document(seed)):
            for relationship in find_all(act, "entryRelationship"):
                substance_administration = find_child(relationship, "substanceAdministration")
                if substance_administration is None:
                    continue
                status = find_child(substance_administration, "statusCode").get("code")
                if status in STATUS_MAP:
                    recognized += 1
                else:
                    unrecognized += 1
    assert recognized > 0 and unrecognized > 0


def test_discharge_summary_round_trips_through_real_converter():
    for seed in range(1000, 1020):
        xml_text = generate_discharge_summary(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert {"Patient", "Encounter"} <= resource_types


def test_generated_discharge_summary_has_no_validation_errors():
    for seed in range(1000, 1020):
        xml_text = generate_discharge_summary(random.Random(seed))
        report = validate_cda(xml_text)
        assert report.is_valid, f"seed={seed} findings={report.findings}"
        assert report.trigger_event == "DISCHARGESUMMARY"


def test_generate_discharge_summary_is_reproducible_with_same_seed():
    assert generate_discharge_summary(random.Random(7)) == generate_discharge_summary(random.Random(7))


def _has_section(document, template_id: str) -> bool:
    return any(
        has_template_id(section, template_id)
        for section in find_all(document, "component/structuredBody/component/section")
    )


def test_discharge_summary_narrative_sections_vary_present_and_absent_across_seeds():
    hospital_course_present = hospital_course_absent = 0
    plan_of_treatment_present = plan_of_treatment_absent = 0
    for seed in range(60):
        document = _discharge_summary_document(seed)
        if _has_section(document, HOSPITAL_COURSE_TEMPLATE_ID):
            hospital_course_present += 1
        else:
            hospital_course_absent += 1
        if _has_section(document, PLAN_OF_TREATMENT_TEMPLATE_ID):
            plan_of_treatment_present += 1
        else:
            plan_of_treatment_absent += 1
    assert hospital_course_present > 0 and hospital_course_absent > 0
    assert plan_of_treatment_present > 0 and plan_of_treatment_absent > 0


def test_discharge_summary_narrative_sections_convert_to_document_reference_and_binary():
    # Direct proof (not just "doesn't crash") that a generated narrative
    # section round-trips through the real converter into a real,
    # non-empty DocumentReference+Binary pair - see
    # app/cda/narrative_sections.py.
    found_one = False
    for seed in range(30):
        xml_text = generate_discharge_summary(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        document_references = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"]
        binaries_by_id = {e.resource.id: e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary"}
        for document_reference in document_references:
            binary_id = document_reference.content[0].attachment.url.removeprefix("urn:uuid:")
            assert binary_id in binaries_by_id
            assert len(binaries_by_id[binary_id].data) > 0
            found_one = True
    assert found_one


def _history_and_physical_document(seed: int):
    return parse_document(generate_history_and_physical(random.Random(seed)))


def test_generated_history_and_physical_parses_and_has_own_templateid():
    for seed in range(20):
        document = _history_and_physical_document(seed)
        assert has_template_id(document, HISTORY_AND_PHYSICAL_TEMPLATE_ID)
        assert not has_template_id(document, CCD_TEMPLATE_ID)
        assert not has_template_id(document, DISCHARGE_SUMMARY_TEMPLATE_ID)


def test_generated_history_and_physical_encounter_varies_across_seeds():
    # Unlike Discharge Summary (force_encounter=True), an H&P's own
    # componentOf/encompassingEncounter is genuinely optional - a real
    # official example can precede any admission (e.g. a pre-op visit).
    present = absent = 0
    for seed in range(60):
        document = _history_and_physical_document(seed)
        if find_child(document, "componentOf") is not None:
            present += 1
        else:
            absent += 1
    assert present > 0 and absent > 0


def test_history_and_physical_round_trips_through_real_converter():
    for seed in range(1000, 1020):
        xml_text = generate_history_and_physical(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        resource_types = {e.resource.get_resource_type() for e in bundle.entry}
        assert "Patient" in resource_types


def test_generated_history_and_physical_has_no_validation_errors():
    for seed in range(1000, 1020):
        xml_text = generate_history_and_physical(random.Random(seed))
        report = validate_cda(xml_text)
        assert report.is_valid, f"seed={seed} findings={report.findings}"
        assert report.trigger_event == "HISTORYANDPHYSICAL"


def test_generate_history_and_physical_is_reproducible_with_same_seed():
    assert generate_history_and_physical(random.Random(7)) == generate_history_and_physical(random.Random(7))


_HP_NARRATIVE_TEMPLATE_IDS = {
    "Reason for Visit": REASON_FOR_VISIT_TEMPLATE_ID,
    "History of Present Illness": HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
    "Review of Systems": REVIEW_OF_SYSTEMS_TEMPLATE_ID,
    "Physical Exam": PHYSICAL_EXAM_TEMPLATE_ID,
    "General Status": GENERAL_STATUS_TEMPLATE_ID,
    "Assessment": ASSESSMENT_TEMPLATE_ID,
    "Social History": SOCIAL_HISTORY_TEMPLATE_ID,
    "Family History": FAMILY_HISTORY_TEMPLATE_ID,
}


def test_history_and_physical_narrative_sections_each_vary_present_and_absent_across_seeds():
    counts = {name: [0, 0] for name in _HP_NARRATIVE_TEMPLATE_IDS}
    for seed in range(80):
        document = _history_and_physical_document(seed)
        for name, template_id in _HP_NARRATIVE_TEMPLATE_IDS.items():
            counts[name][0 if _has_section(document, template_id) else 1] += 1
    for name, (present, absent) in counts.items():
        assert present > 0 and absent > 0, f"{name} did not vary across seeds: present={present} absent={absent}"


def test_history_and_physical_plan_of_treatment_varies_present_and_absent_across_seeds():
    # Plan of Treatment is shared with Discharge Summary (see
    # app/cda/narrative_sections.py's own docstring) - confirmed here too,
    # not just on the Discharge Summary side.
    present = absent = 0
    for seed in range(60):
        document = _history_and_physical_document(seed)
        if _has_section(document, PLAN_OF_TREATMENT_TEMPLATE_ID):
            present += 1
        else:
            absent += 1
    assert present > 0 and absent > 0


def test_narrative_sections_never_occur_on_ccd():
    # include_hospital_course/include_plan_of_treatment/
    # include_hp_narrative_sections are all scoped away from CCD - see
    # _generate_sectioned_document's own docstring for why generating them
    # there would produce unrealistic synthetic data.
    all_narrative_template_ids = [HOSPITAL_COURSE_TEMPLATE_ID, PLAN_OF_TREATMENT_TEMPLATE_ID] + list(
        _HP_NARRATIVE_TEMPLATE_IDS.values()
    )
    for seed in range(60):
        document = _document(seed)
        for template_id in all_narrative_template_ids:
            assert not _has_section(document, template_id)


def test_history_and_physical_narrative_sections_convert_to_document_reference_and_binary():
    # Direct proof (not just "doesn't crash") that a generated narrative
    # section round-trips through the real converter into a real,
    # non-empty DocumentReference+Binary pair, including a table-shaped one
    # (Social History) - confirming the row/column-preserving extraction
    # never produces an empty or garbled body.
    found_table_shaped = False
    for seed in range(30):
        xml_text = generate_history_and_physical(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        document_references = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"]
        binaries_by_id = {e.resource.id: e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary"}
        for document_reference in document_references:
            binary_id = document_reference.content[0].attachment.url.removeprefix("urn:uuid:")
            assert binary_id in binaries_by_id
            text = binaries_by_id[binary_id].data.decode("utf-8")
            assert len(text) > 0
            if document_reference.type and document_reference.type.coding[0].code == "29762-2":
                assert " | " in text  # Social History's own table row shape survived
                found_table_shaped = True
    assert found_table_shaped


def test_social_history_structured_observation_varies_present_and_absent_across_seeds():
    # Social History's own structured entry (see app/cda/social_history.py)
    # is independently maybe()-gated from the section's own presence -
    # direct fuzz coverage that a real, category="social-history"
    # Observation is produced alongside the narrative DocumentReference+
    # Binary, not just theoretically reachable.
    present = absent = 0
    for seed in range(200):
        xml_text = generate_history_and_physical(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        observations = [
            e.resource
            for e in bundle.entry
            if e.resource.get_resource_type() == "Observation"
            and e.resource.category
            and e.resource.category[0].coding[0].code == "social-history"
        ]
        if observations:
            present += 1
        else:
            absent += 1
    assert present > 0 and absent > 0


def test_family_history_structured_organizer_varies_present_and_absent_across_seeds():
    # Family History's own structured entry (see app/cda/family_history.py)
    # is independently maybe()-gated from the section's own presence -
    # direct fuzz coverage that a real FamilyMemberHistory is produced
    # alongside the narrative pair.
    present = absent = 0
    deceased_boolean_seen = deceased_date_seen = False
    contributed_to_death_seen = onset_age_seen = False
    for seed in range(300):
        xml_text = generate_history_and_physical(random.Random(seed))
        bundle = convert_cda_to_bundle(xml_text)
        histories = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "FamilyMemberHistory"]
        if histories:
            present += 1
        else:
            absent += 1
        for history in histories:
            if history.deceasedBoolean is not None:
                deceased_boolean_seen = True
            if history.deceasedDate is not None:
                deceased_date_seen = True
            for condition in history.condition:
                if condition.contributedToDeath:
                    contributed_to_death_seen = True
                if condition.onsetAge is not None:
                    onset_age_seen = True
    assert present > 0 and absent > 0
    # Both branches of the deceased[x] choice-type resolution (see
    # app/cda/family_history.py::_build_family_member_history's own
    # docstring) occur across seeds, not just one.
    assert deceased_boolean_seen and deceased_date_seen
    assert contributed_to_death_seen
    assert onset_age_seen


def test_plan_of_treatment_structured_entry_varies_present_and_absent_and_both_shapes_occur():
    # Plan of Treatment's own structured entry (see
    # app/cda/plan_of_treatment.py) splits between a Planned Observation
    # and a Planned Procedure entry shape - direct fuzz coverage that both
    # occur, plus the deliberately-unrecognized statusCode's own "unknown"
    # fallback.
    present = absent = 0
    statuses_seen = set()
    for seed in range(200):
        for xml_text in (
            generate_discharge_summary(random.Random(seed)),
            generate_history_and_physical(random.Random(seed)),
        ):
            bundle = convert_cda_to_bundle(xml_text)
            care_plans = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "CarePlan"]
            if care_plans:
                present += 1
            else:
                absent += 1
            for care_plan in care_plans:
                assert care_plan.status == "active"
                assert care_plan.intent == "plan"
                for activity in care_plan.activity:
                    assert activity.detail.kind == "ServiceRequest"
                    statuses_seen.add(activity.detail.status)
    assert present > 0 and absent > 0
    assert "unknown" in statuses_seen  # the deliberately-unrecognized statusCode branch
    assert len(statuses_seen) > 1
