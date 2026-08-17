import pytest

from app.cda.validation import validate_document
from app.cda.parser import parse_document
from app.hl7.errors import MissingSegmentError

_XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
_CCD_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.1.2"/>'
_PROBLEMS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.5.1"/>'
_MEDICATIONS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.1.1"/>'
_MEDICATION_ACTIVITY_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.16"/>'
_ALLERGIES_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.6.1"/>'
_ALLERGIES_SECTION_TEMPLATE_ENTRIES_OPTIONAL = '<templateId root="2.16.840.1.113883.10.20.22.2.6"/>'
_ALLERGY_CONCERN_ACT_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.30"/>'
_ALLERGY_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.7"/>'
_REACTION_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.9"/>'
_IMMUNIZATIONS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.2.1"/>'
_IMMUNIZATION_ACTIVITY_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.52"/>'
_VITALS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.4.1"/>'
_VITALS_SECTION_TEMPLATE_ENTRIES_OPTIONAL = '<templateId root="2.16.840.1.113883.10.20.22.2.4"/>'
_VITAL_SIGNS_ORGANIZER_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.26"/>'
_VITAL_SIGN_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.27"/>'
_RESULTS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.3.1"/>'
_RESULTS_SECTION_TEMPLATE_ENTRIES_OPTIONAL = '<templateId root="2.16.840.1.113883.10.20.22.2.3"/>'
_RESULT_ORGANIZER_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.1"/>'
_RESULT_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.2"/>'
_PROCEDURES_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.7.1"/>'
_PROCEDURES_SECTION_TEMPLATE_ENTRIES_OPTIONAL = '<templateId root="2.16.840.1.113883.10.20.22.2.7"/>'
_PROCEDURE_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.14"/>'


def _doc(body: str, ccd: bool = True) -> object:
    template = _CCD_TEMPLATE if ccd else ""
    xml = f'<ClinicalDocument xmlns="urn:hl7-org:v3" {_XSI}>{template}{body}</ClinicalDocument>'
    return parse_document(xml)


def _patient(name: str = "<name><given>A</given><family>B</family></name>", extra: str = "") -> str:
    return f"<recordTarget><patientRole><patient>{name}{extra}</patient></patientRole></recordTarget>"


def _problem_entry(effective_time: str, value: str, negation: str = "") -> str:
    return (
        f'<entry><act classCode="ACT" moodCode="EVN"><templateId root="2.16.840.1.113883.10.20.22.4.3"/>'
        '<statusCode code="active"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN"{negation}>'
        '<templateId root="2.16.840.1.113883.10.20.22.4.4"/><statusCode code="completed"/>'
        f"{effective_time}{value}</observation></entryRelationship></act></entry>"
    )


def _problems_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_PROBLEMS_SECTION_TEMPLATE}"
        f"{entries}</section></component></structuredBody></component>"
    )


def _medication_entry(status: str = "active", effective_time: str = "", code: str = "", negation: str = "") -> str:
    consumable_code = code or '<code code="314076" codeSystem="2.16.840.1.113883.6.88" displayName="Lisinopril"/>'
    return (
        f'<entry><substanceAdministration classCode="SBADM" moodCode="EVN"{negation}>'
        f"{_MEDICATION_ACTIVITY_TEMPLATE}"
        f'<statusCode code="{status}"/>{effective_time}'
        f"<consumable><manufacturedProduct><manufacturedMaterial>{consumable_code}"
        "</manufacturedMaterial></manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
    )


def _medications_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_MEDICATIONS_SECTION_TEMPLATE}"
        f"{entries}</section></component></structuredBody></component>"
    )


def _allergy_entry(
    effective_time: str = "",
    allergen: str = "",
    negation: str = "",
    reaction: str = "",
) -> str:
    participant = (
        allergen
        or '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
        '<code code="102263004" codeSystem="2.16.840.1.113883.6.96" displayName="Eggs"/>'
        "</playingEntity></participantRole></participant>"
    )
    return (
        f'<entry><act classCode="ACT" moodCode="EVN">{_ALLERGY_CONCERN_ACT_TEMPLATE}'
        '<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/><statusCode code="active"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN"{negation}>'
        f"{_ALLERGY_OBSERVATION_TEMPLATE}"
        '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/><statusCode code="completed"/>'
        f'{effective_time}<value xsi:type="CD" code="414285001" codeSystem="2.16.840.1.113883.6.96"/>'
        f"{participant}{reaction}"
        "</observation></entryRelationship></act></entry>"
    )


def _reaction(value: str = "") -> str:
    manifestation = value or '<value xsi:type="CD" code="247472004" codeSystem="2.16.840.1.113883.6.96" displayName="Wheal"/>'
    return (
        f'<entryRelationship typeCode="MFST" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
        f"{_REACTION_OBSERVATION_TEMPLATE}"
        f'<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/><statusCode code="completed"/>{manifestation}'
        "</observation></entryRelationship>"
    )


def _allergies_section(entries: str, entries_optional: bool = False) -> str:
    template = _ALLERGIES_SECTION_TEMPLATE_ENTRIES_OPTIONAL if entries_optional else _ALLERGIES_SECTION_TEMPLATE
    return f"<component><structuredBody><component><section>{template}{entries}</section></component></structuredBody></component>"


def _immunization_entry(
    status: str = "completed", effective_time: str = "", code: str = "", mood: str = "EVN", negation: str = ""
) -> str:
    consumable_code = code or '<code code="88" codeSystem="2.16.840.1.113883.12.292" displayName="Influenza"/>'
    return (
        f'<entry><substanceAdministration classCode="SBADM" moodCode="{mood}"{negation}>'
        f"{_IMMUNIZATION_ACTIVITY_TEMPLATE}"
        f'<statusCode code="{status}"/>{effective_time}'
        f"<consumable><manufacturedProduct><manufacturedMaterial>{consumable_code}"
        "</manufacturedMaterial></manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
    )


def _immunizations_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_IMMUNIZATIONS_SECTION_TEMPLATE}"
        f"{entries}</section></component></structuredBody></component>"
    )


def test_clean_document_is_valid_with_no_findings():
    document = _doc(_patient())
    report = validate_document(document)
    assert report.is_valid is True
    assert report.findings == []
    assert report.message_type == "CDA"
    assert report.trigger_event == "CCD"


def test_missing_patient_role_raises_missing_segment_error_from_convertibility():
    document = _doc("")
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_patient_name_missing_is_warning():
    document = _doc(_patient(name=""))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.patient-name-missing")
    assert finding.severity == "warning"


def test_patient_gender_unrecognized_is_warning():
    document = _doc(_patient(extra='<administrativeGenderCode code="X"/>'))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.patient-gender-unrecognized")
    assert finding.severity == "warning"


def test_patient_gender_recognized_produces_no_finding():
    document = _doc(_patient(extra='<administrativeGenderCode code="UN"/>'))
    report = validate_document(document)
    assert "cda.patient-gender-unrecognized" not in [f.rule_id for f in report.findings]


def test_birth_time_in_future_is_error():
    document = _doc(_patient(extra='<birthTime value="20990101"/>'))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.patient-birthtime-in-future")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_birth_time_implausibly_old_is_warning():
    document = _doc(_patient(extra='<birthTime value="18000101"/>'))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.patient-birthtime-implausibly-old")
    assert finding.severity == "warning"


def test_document_effective_time_in_future_is_warning():
    document = _doc(_patient() + '<effectiveTime value="20990101"/>')
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.document-date-in-future")
    assert finding.severity == "warning"


def test_birth_time_unparseable_is_warning():
    document = _doc(_patient(extra='<birthTime value="garbage"/>'))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.patient-birthtime-unparseable")
    assert finding.severity == "warning"


def test_clean_encounter_produces_no_findings():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="AMB" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><low value="20200101"/><high value="20200102"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_encounter_high_only_in_future_is_warning():
    # An IVL_TS with only @high (known end, unknown start) is legal -
    # ivl_ts_bounds() returns (None, high) for it. Nesting the future-check
    # under `if low:` would silently produce zero encounter findings for
    # this shape even when the known end date is implausibly in the future.
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="AMB" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><high value="20990101"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.encounter-period-end-in-future")
    assert finding.severity == "warning"


def test_encounter_high_only_in_past_produces_no_finding():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="AMB" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><high value="20200101"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    assert report.findings == []


def test_encounter_class_unrecognized_and_period_inverted_fire_together():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="ZZZ" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><low value="20200102"/><high value="20200101"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    rule_ids = {f.rule_id for f in report.findings}
    assert rule_ids == {"cda.encounter-class-unrecognized", "cda.encounter-period-end-before-start"}


def test_encounter_period_end_before_start_is_error():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="AMB" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><low value="20260810120000"/><high value="20260809120000"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.encounter-period-end-before-start")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_encounter_period_start_in_future_is_warning():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="AMB" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><low value="20990101120000"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.encounter-period-start-in-future")
    assert finding.severity == "warning"


def test_encounter_class_unrecognized_is_info():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="ZZZ" codeSystem="2.16.840.1.113883.5.4"/>'
        '</encompassingEncounter></componentOf>'
    )
    document = _doc(body)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.encounter-class-unrecognized")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_problem_missing_value_is_info():
    entry = _problem_entry(effective_time="", value="")
    document = _doc(_patient() + _problems_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.problem-missing-value")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_negated_problem_with_no_value_produces_no_finding():
    entry = _problem_entry(effective_time="", value="", negation=' negationInd="true"')
    document = _doc(_patient() + _problems_section(entry))
    report = validate_document(document)
    assert "cda.problem-missing-value" not in [f.rule_id for f in report.findings]


def test_problem_onset_in_future_is_warning():
    value = '<value xsi:type="CD" code="38341003" codeSystem="2.16.840.1.113883.6.96" displayName="Hypertension"/>'
    entry = _problem_entry(effective_time='<effectiveTime value="20990101"/>', value=value)
    document = _doc(_patient() + _problems_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.problem-onset-in-future")
    assert finding.severity == "warning"


def test_problem_onset_before_birth_is_error():
    value = '<value xsi:type="CD" code="38341003" codeSystem="2.16.840.1.113883.6.96" displayName="Hypertension"/>'
    entry = _problem_entry(effective_time='<effectiveTime value="20100101"/>', value=value)
    document = _doc(_patient(extra='<birthTime value="20200101"/>') + _problems_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.problem-onset-before-birth")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_problem_abatement_before_onset_is_error():
    value = '<value xsi:type="CD" code="38341003" codeSystem="2.16.840.1.113883.6.96" displayName="Hypertension"/>'
    effective_time = '<effectiveTime><low value="20260810"/><high value="20260801"/></effectiveTime>'
    entry = _problem_entry(effective_time=effective_time, value=value)
    document = _doc(_patient() + _problems_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.problem-abatement-before-onset")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_clean_medication_produces_no_findings():
    entry = _medication_entry(status="active")
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_medication_missing_code_is_info():
    entry = _medication_entry(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.medication-missing-code")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_negated_medication_with_no_code_produces_no_finding():
    # Negated entries are skipped by the converter entirely - the missing-
    # code rule shouldn't even evaluate them, matching build_medication_
    # requests()'s own negationInd check ordering.
    entry = _medication_entry(code='<code nullFlavor="UNK"/>', negation=' negationInd="true"')
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_medication_status_unrecognized_is_info():
    entry = _medication_entry(status="new")
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.medication-status-unrecognized")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_medication_recognized_status_produces_no_status_finding():
    entry = _medication_entry(status="suspended")
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_medication_period_end_before_start_is_error():
    effective_time = '<effectiveTime><low value="20260810"/><high value="20260801"/></effectiveTime>'
    entry = _medication_entry(effective_time=effective_time)
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.medication-period-end-before-start")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_medication_period_with_only_low_produces_no_finding():
    effective_time = '<effectiveTime><low value="20260801"/></effectiveTime>'
    entry = _medication_entry(effective_time=effective_time)
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_clean_allergy_produces_no_findings():
    entry = _allergy_entry()
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_allergy_missing_allergen_is_info():
    no_allergen = (
        '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
        '<code nullFlavor="UNK"/>'
        "</playingEntity></participantRole></participant>"
    )
    entry = _allergy_entry(allergen=no_allergen)
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.allergy-missing-allergen")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_allergy_rules_also_run_against_entries_optional_section_variant():
    # Regression test: "entries required" (2.16.840.1.113883.10.20.22.2.6.1)
    # and "entries optional" (2.16.840.1.113883.10.20.22.2.6) wrap the
    # identical entry shape - app.cda.registry.SECTION_BUILDERS dispatches
    # both to build_allergy_intolerances for conversion, but
    # _find_allergies_section originally recognized only the "entries
    # required" templateId, so validate_document() silently ran zero
    # allergy rules against a document using the other variant - caught by
    # code review, reproduced directly, not by this test suite originally.
    no_allergen = (
        '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
        '<code nullFlavor="UNK"/>'
        "</playingEntity></participantRole></participant>"
    )
    entry = _allergy_entry(allergen=no_allergen)
    document = _doc(_patient() + _allergies_section(entry, entries_optional=True))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.allergy-missing-allergen")
    assert finding.severity == "info"


def test_negated_allergy_with_no_allergen_produces_no_missing_allergen_finding():
    # Negated entries are handled by the "no known allergy" text fallback,
    # not skipped - the missing-allergen rule shouldn't evaluate them at
    # all, matching build_allergy_intolerances()'s own negation handling.
    no_allergen = (
        '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
        '<code nullFlavor="UNK"/>'
        "</playingEntity></participantRole></participant>"
    )
    entry = _allergy_entry(allergen=no_allergen, negation=' negationInd="true"')
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_allergy_onset_in_future_is_warning():
    entry = _allergy_entry(effective_time='<effectiveTime><low value="20990101"/></effectiveTime>')
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.allergy-onset-in-future")
    assert finding.severity == "warning"


def test_allergy_onset_before_birth_is_error():
    body = _patient(extra='<birthTime value="20200101"/>') + _allergies_section(
        _allergy_entry(effective_time='<effectiveTime><low value="19990101"/></effectiveTime>')
    )
    document = _doc(body)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.allergy-onset-before-birth")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_allergy_reaction_missing_manifestation_is_info():
    entry = _allergy_entry(reaction=_reaction(value='<value xsi:type="CD" nullFlavor="UNK"/>'))
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.allergy-reaction-missing-manifestation")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_allergy_reaction_with_manifestation_produces_no_finding():
    entry = _allergy_entry(reaction=_reaction())
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_clean_immunization_produces_no_findings():
    entry = _immunization_entry()
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_immunization_missing_vaccine_code_is_info():
    entry = _immunization_entry(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.immunization-missing-vaccine-code")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_immunization_status_unrecognized_is_info():
    entry = _immunization_entry(status="draft")
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.immunization-status-unrecognized")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_negated_immunization_with_unrecognized_status_produces_no_status_finding():
    # negationInd="true" forces status="not-done" unconditionally in the
    # mapper (see app/cda/immunizations.py::_resolve_status) - the
    # statusCode value becomes irrelevant, so the unrecognized-status rule
    # must not fire for it either.
    entry = _immunization_entry(status="draft", negation=' negationInd="true"')
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_immunization_occurrence_in_future_is_warning():
    entry = _immunization_entry(effective_time='<effectiveTime value="20990101"/>')
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.immunization-occurrence-in-future")
    assert finding.severity == "warning"


def test_immunization_occurrence_before_birth_is_error():
    entry = _immunization_entry(effective_time='<effectiveTime value="20100101"/>')
    document = _doc(_patient(extra='<birthTime value="20200101"/>') + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.immunization-occurrence-before-birth")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_planned_immunization_int_mood_produces_no_findings():
    # INT-mood entries are out of scope for this slice and excluded from
    # the rule walk entirely - not flagged, same treatment an unrecognized
    # section already gets.
    entry = _immunization_entry(mood="INT", status="draft")
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    assert report.findings == []


def _vital_sign_observation(code: str = "", effective_time: str = "") -> str:
    observation_code = code or '<code code="8867-4" codeSystem="2.16.840.1.113883.6.1" displayName="Heart rate"/>'
    return (
        f'<component><observation classCode="OBS" moodCode="EVN">{_VITAL_SIGN_OBSERVATION_TEMPLATE}'
        f'{observation_code}<statusCode code="completed"/>{effective_time}'
        '<value xsi:type="PQ" value="76" unit="/min"/></observation></component>'
    )


def _vitals_organizer(components: str) -> str:
    return (
        f'<entry><organizer classCode="CLUSTER" moodCode="EVN">{_VITAL_SIGNS_ORGANIZER_TEMPLATE}'
        '<statusCode code="completed"/>'
        f"{components}</organizer></entry>"
    )


def _vitals_section(entries: str, entries_optional: bool = False) -> str:
    template = _VITALS_SECTION_TEMPLATE_ENTRIES_OPTIONAL if entries_optional else _VITALS_SECTION_TEMPLATE
    return f"<component><structuredBody><component><section>{template}{entries}</section></component></structuredBody></component>"


def test_clean_vitals_produces_no_findings():
    entry = _vitals_organizer(_vital_sign_observation())
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_vitals_missing_code_is_info():
    entry = _vitals_organizer(_vital_sign_observation(code='<code nullFlavor="UNK"/>'))
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.vitals-missing-code")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_vitals_effective_time_in_future_is_warning():
    entry = _vitals_organizer(_vital_sign_observation(effective_time='<effectiveTime value="20990101"/>'))
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.vitals-effective-time-in-future")
    assert finding.severity == "warning"


def test_vitals_rules_also_run_against_entries_optional_section_variant():
    # Regression test mirroring test_allergy_rules_also_run_against_entries_
    # optional_section_variant: "entries required" (...2.4.1) and "entries
    # optional" (...2.4) wrap the identical entry shape and both dispatch to
    # build_vital_signs for conversion (app.cda.registry.SECTION_BUILDERS) -
    # _find_vitals_section must recognize both too, or validate_document()
    # would silently run zero vitals rules against the "entries optional"
    # variant, the exact gap a real official HL7 History and Physical
    # example surfaced for this app's Procedures section (see
    # app/cda/procedures.py).
    entry = _vitals_organizer(_vital_sign_observation(code='<code nullFlavor="UNK"/>'))
    document = _doc(_patient() + _vitals_section(entry, entries_optional=True))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.vitals-missing-code")
    assert finding.severity == "info"


def _result_observation(code: str = "", status: str = "completed", effective_time: str = "") -> str:
    observation_code = code or '<code code="6690-2" codeSystem="2.16.840.1.113883.6.1" displayName="Leukocytes"/>'
    return (
        f'<component><observation classCode="OBS" moodCode="EVN">{_RESULT_OBSERVATION_TEMPLATE}'
        f'{observation_code}<statusCode code="{status}"/>{effective_time}'
        '<value xsi:type="PQ" value="6.8" unit="10*3/uL"/></observation></component>'
    )


def _results_organizer(components: str) -> str:
    return (
        f'<entry><organizer classCode="BATTERY" moodCode="EVN">{_RESULT_ORGANIZER_TEMPLATE}'
        '<statusCode code="completed"/>'
        f"{components}</organizer></entry>"
    )


def _results_section(entries: str, entries_optional: bool = False) -> str:
    template = _RESULTS_SECTION_TEMPLATE_ENTRIES_OPTIONAL if entries_optional else _RESULTS_SECTION_TEMPLATE
    return f"<component><structuredBody><component><section>{template}{entries}</section></component></structuredBody></component>"


def test_clean_result_produces_no_findings():
    entry = _results_organizer(_result_observation())
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_result_missing_code_is_info():
    entry = _results_organizer(_result_observation(code='<code nullFlavor="UNK"/>'))
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-missing-code")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_result_status_unrecognized_is_info():
    entry = _results_organizer(_result_observation(status="nullified"))
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-status-unrecognized")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_result_effective_time_in_future_is_warning():
    entry = _results_organizer(_result_observation(effective_time='<effectiveTime value="20990101"/>'))
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-effective-time-in-future")
    assert finding.severity == "warning"


def test_result_rules_also_run_against_entries_optional_section_variant():
    entry = _results_organizer(_result_observation(code='<code nullFlavor="UNK"/>'))
    document = _doc(_patient() + _results_section(entry, entries_optional=True))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-missing-code")
    assert finding.severity == "info"


def _procedure_entry(status: str = "completed", effective_time: str = "", negation: str = "") -> str:
    effective_time = effective_time or '<effectiveTime value="20260615120000-0500"/>'
    return (
        f'<entry><procedure classCode="PROC" moodCode="EVN"{negation}>{_PROCEDURE_TEMPLATE}'
        '<code code="80146002" codeSystem="2.16.840.1.113883.6.96" displayName="Appendectomy"/>'
        f'<statusCode code="{status}"/>{effective_time}'
        "</procedure></entry>"
    )


def _procedures_section(entries: str, entries_optional: bool = False) -> str:
    template = _PROCEDURES_SECTION_TEMPLATE_ENTRIES_OPTIONAL if entries_optional else _PROCEDURES_SECTION_TEMPLATE
    return f"<component><structuredBody><component><section>{template}{entries}</section></component></structuredBody></component>"


def test_clean_procedure_produces_no_findings():
    entry = _procedure_entry()
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert report.findings == []
    assert report.is_valid is True


def test_procedure_status_unrecognized_is_info():
    entry = _procedure_entry(status="held")
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-status-unrecognized")
    assert finding.severity == "info"
    assert report.is_valid is True


def test_negated_procedure_with_unrecognized_status_produces_no_status_finding():
    # negationInd="true" is a semantic override at the mapper level (this
    # procedure did NOT happen) but, unlike Immunizations' negationInd,
    # does not force a fixed status - it maps directly to "not-done"
    # regardless of statusCode, so the unrecognized-status rule must not
    # fire for a negated entry either (see app/cda/procedures.py::_resolve_status).
    entry = _procedure_entry(status="held", negation=' negationInd="true"')
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert report.findings == []


def test_procedure_effective_time_in_future_is_warning():
    entry = _procedure_entry(effective_time='<effectiveTime value="20990101"/>')
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-effective-time-in-future")
    assert finding.severity == "warning"


def test_procedure_rules_also_run_against_entries_optional_section_variant():
    # This is the section a real official HL7 History and Physical example
    # was actually found using ONLY the "entries optional" (...2.7)
    # templateId for - no paired ...2.7.1 declaration - so this regression
    # test covers a genuinely observed real-world shape, not a hypothetical.
    entry = _procedure_entry(status="held")
    document = _doc(_patient() + _procedures_section(entry, entries_optional=True))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-status-unrecognized")
    assert finding.severity == "info"


def test_unregistered_document_type_is_info():
    document = _doc(_patient(), ccd=False)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.unsupported-document-type")
    assert finding.severity == "info"
    assert report.is_valid is True
    assert report.trigger_event is None


def test_unexpected_convertibility_crash_is_absorbed_into_a_finding(monkeypatch):
    document = _doc(_patient())

    class _ExplodingBuilder:
        def build_bundle(self, doc):
            raise RuntimeError("boom")

    def _fake_get_document_builder(doc):
        return _ExplodingBuilder()

    monkeypatch.setattr("app.cda.registry.get_document_builder", _fake_get_document_builder)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.convertibility-check-failed")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_missing_segment_error_from_build_bundle_is_error_finding():
    # A recordTarget/patientRole present but with no <patient> child - passes
    # this module's own None-guarded rules (no crash), but build_bundle()
    # itself raises MissingSegmentError, which must become a finding here,
    # not propagate - mirrors app/validation/engine.py's identical handling
    # for MappingError/MissingSegmentError raised from to_bundle().
    document = _doc("<recordTarget><patientRole></patientRole></recordTarget>")
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.would-not-convert")
    assert finding.severity == "error"
    assert report.is_valid is False


def test_validate_document_never_raises_missing_segment_error_directly():
    # A defensive proof that validate_document() itself never lets
    # MissingSegmentError escape - it must always be turned into a finding.
    document = _doc("")
    try:
        validate_document(document)
    except MissingSegmentError:
        pytest.fail("validate_document() must not raise MissingSegmentError - it should be a finding")
