import pytest

from app.cda.validation import validate_document
from app.cda.parser import find_child, parse_document
from app.hl7.errors import MissingSegmentError

_XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
# Both, as a real CCD declares and as this app's own generator and
# reverse builder emit - US Realm Header is what tightens title and
# languageCode from 0..1 to 1..1.
_CCD_TEMPLATE = (
    '<templateId root="2.16.840.1.113883.10.20.22.1.1"/>'
    '<templateId root="2.16.840.1.113883.10.20.22.1.2"/>'
)
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
_HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.24"/>'
_HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.33"/>'
_DISCHARGE_MEDICATIONS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.11.1"/>'
_DISCHARGE_MEDICATION_ACT_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.35"/>'
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
_HOSPITAL_COURSE_SECTION_TEMPLATE = '<templateId root="1.3.6.1.4.1.19376.1.5.3.1.3.5"/>'
_SOCIAL_HISTORY_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.17"/>'
_SOCIAL_HISTORY_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.38"/>'
_FAMILY_HISTORY_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.15"/>'
_FAMILY_HISTORY_ORGANIZER_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.45"/>'
_FAMILY_HISTORY_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.46"/>'
_PLAN_OF_TREATMENT_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.10"/>'
_PLANNED_OBSERVATION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.4.44"/>'


# The header elements the CDA StructureDefinition requires of every
# document, in CDA's own sequence order. Without them each of these inline
# documents trips cda.header-missing-required-element seven times over,
# burying the rule the test is actually about.
#
# Only gaps are filled: an element the body already supplies is left
# alone (so a test can put its own effectiveTime in the future), and
# `omit` drops one entirely (so a test can be about its absence).
_HEADER_ELEMENTS = (
    ("id", '<id root="2.16.840.1.113883.19.5" extension="TT001"/>'),
    ("code", '<code code="34133-9" codeSystem="2.16.840.1.113883.6.1"/>'),
    ("title", "<title>Test Document</title>"),
    ("effectiveTime", '<effectiveTime value="20260101120000+0000"/>'),
    ("confidentialityCode", '<confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>'),
    ("languageCode", '<languageCode code="en-US"/>'),
)
_HEADER_PARTICIPATIONS = (
    (
        "author",
        '<author><time value="20260101120000+0000"/><assignedAuthor>'
        '<id root="2.16.840.1.113883.19.5" extension="A1"/>'
        "<assignedPerson><name><given>Ada</given><family>Byron</family></name></assignedPerson>"
        "</assignedAuthor></author>",
    ),
    (
        "custodian",
        "<custodian><assignedCustodian><representedCustodianOrganization>"
        "<name>Test Custodian</name>"
        "</representedCustodianOrganization></assignedCustodian></custodian>",
    ),
)


# The two cardinality rules, plus the one finding a conformant document
# always produces (confidentialityCode is 1..1 in CDA, and this app
# reports that the FHIR US Realm Header profile constrains
# Composition.confidentiality to 0..0).
#
# These tests build the smallest entry that exercises one rule, so a
# template's full 1..1 set is noise here - the corpus-wide check that
# every *fixture* satisfies it lives in test_required_elements.py. A test
# asserting "no findings" means no findings about the thing it is testing.
_STRUCTURAL_RULE_IDS = frozenset(
    {
        "cda.header-missing-required-element",
        "cda.entry-missing-required-element",
        "cda.composition-confidentiality-not-us-realm-conformant",
    }
)


def _body_findings(report) -> list:
    return [f for f in report.findings if f.rule_id not in _STRUCTURAL_RULE_IDS]


def _body_is_valid(report) -> bool:
    """Whether the rule under test judged the entry valid, ignoring the
    cardinality rules - these entries are minimal by design."""
    return not any(f.severity == "error" for f in _body_findings(report))


def _doc(body: str, ccd: bool = True, omit: tuple[str, ...] = ()) -> object:
    # Which header elements the body already supplies is decided by
    # parsing it and looking at the document's own direct children - a
    # substring scan cannot tell ClinicalDocument/code from the <code>
    # inside every section and entry below it.
    provisional = parse_document(
        f'<ClinicalDocument xmlns="urn:hl7-org:v3" {_XSI}>{body}</ClinicalDocument>'
    )

    def fill(elements):
        return "".join(
            xml
            for tag, xml in elements
            if tag not in omit and find_child(provisional, tag) is None
        )

    template = _CCD_TEMPLATE if ccd else ""
    # CDA's header is a sequence: the plain elements, then recordTarget,
    # then author and custodian.
    patient = (
        ""
        if "recordTarget" in omit or find_child(provisional, "recordTarget") is not None
        else _patient()
    )
    xml = (
        f'<ClinicalDocument xmlns="urn:hl7-org:v3" {_XSI}>'
        f"{template}{fill(_HEADER_ELEMENTS)}{patient}{fill(_HEADER_PARTICIPATIONS)}{body}"
        "</ClinicalDocument>"
    )
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
    assert _body_is_valid(report)
    assert _body_findings(report) == []
    assert report.message_type == "CDA"
    assert report.trigger_event == "CCD"


def test_missing_patient_role_raises_missing_segment_error_from_convertibility():
    document = _doc("", omit=("recordTarget",))
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
    assert _body_findings(report) == []
    assert _body_is_valid(report)


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
    assert _body_findings(report) == []


def test_encounter_class_unrecognized_and_period_inverted_fire_together():
    body = _patient() + (
        '<componentOf><encompassingEncounter><code code="ZZZ" codeSystem="2.16.840.1.113883.5.4"/>'
        '<effectiveTime><low value="20200102"/><high value="20200101"/></effectiveTime>'
        "</encompassingEncounter></componentOf>"
    )
    document = _doc(body)
    report = validate_document(document)
    rule_ids = {f.rule_id for f in _body_findings(report)}
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
    assert _body_is_valid(report)


def test_problem_missing_value_is_info():
    entry = _problem_entry(effective_time="", value="")
    document = _doc(_patient() + _problems_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.problem-missing-value")
    assert finding.severity == "info"
    assert _body_is_valid(report)


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
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_medication_missing_code_is_info():
    entry = _medication_entry(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.medication-missing-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_negated_medication_with_no_code_produces_no_finding():
    # Negated entries are skipped by the converter entirely - the missing-
    # code rule shouldn't even evaluate them, matching build_medication_
    # requests()'s own negationInd check ordering.
    entry = _medication_entry(code='<code nullFlavor="UNK"/>', negation=' negationInd="true"')
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []


def test_medication_status_unrecognized_is_info():
    entry = _medication_entry(status="new")
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.medication-status-unrecognized")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_medication_recognized_status_produces_no_status_finding():
    entry = _medication_entry(status="suspended")
    document = _doc(_patient() + _medications_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []


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
    assert _body_findings(report) == []


def test_clean_allergy_produces_no_findings():
    entry = _allergy_entry()
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


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
    assert _body_is_valid(report)


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
    assert _body_findings(report) == []


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
    assert _body_is_valid(report)


def test_allergy_reaction_with_manifestation_produces_no_finding():
    entry = _allergy_entry(reaction=_reaction())
    document = _doc(_patient() + _allergies_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []


def test_clean_immunization_produces_no_findings():
    entry = _immunization_entry()
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_immunization_missing_vaccine_code_is_info():
    entry = _immunization_entry(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.immunization-missing-vaccine-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_immunization_status_unrecognized_is_info():
    entry = _immunization_entry(status="draft")
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.immunization-status-unrecognized")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_negated_immunization_with_unrecognized_status_produces_no_status_finding():
    # negationInd="true" forces status="not-done" unconditionally in the
    # mapper (see app/cda/immunizations.py::_resolve_status) - the
    # statusCode value becomes irrelevant, so the unrecognized-status rule
    # must not fire for it either.
    entry = _immunization_entry(status="draft", negation=' negationInd="true"')
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []


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


def test_planned_immunization_status_unrecognized_is_info():
    # INT-mood entries convert to MedicationRequest, resolving status
    # through CF_MedStatus rather than CF_ImmunizationStatus - so they get
    # their own rule with its own fallback, and validation walks the same
    # entry set conversion does.
    entry = _immunization_entry(mood="INT", status="draft")
    document = _doc(_patient() + _immunizations_section(entry))
    report = validate_document(document)
    finding = next(
        f for f in report.findings if f.rule_id == "cda.planned-immunization-status-unrecognized"
    )
    assert finding.severity == "info"
    assert "unknown" in finding.message


def test_planned_immunization_with_recognized_status_produces_no_findings():
    entry = _immunization_entry(mood="INT", status="active")
    document = _doc(_patient() + _immunizations_section(entry))
    assert _body_findings(validate_document(document)) == []


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
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_vitals_missing_code_is_info():
    entry = _vitals_organizer(_vital_sign_observation(code='<code nullFlavor="UNK"/>'))
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.vitals-missing-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


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


def test_vitals_incomplete_blood_pressure_pair_is_info():
    # Systolic present without its own diastolic pair - the converter will
    # map it as an ordinary flat Vital Sign Observation, not a grouped
    # Blood Pressure Panel (see app.cda.vitals's own BP_SYSTOLIC_CODE/
    # BP_DIASTOLIC_CODE pairing requirement).
    systolic = _vital_sign_observation(
        code='<code code="8480-6" codeSystem="2.16.840.1.113883.6.1" displayName="Systolic blood pressure"/>'
    )
    entry = _vitals_organizer(systolic)
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.vitals-incomplete-blood-pressure-pair")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_vitals_complete_blood_pressure_pair_produces_no_finding():
    systolic = _vital_sign_observation(
        code='<code code="8480-6" codeSystem="2.16.840.1.113883.6.1" displayName="Systolic blood pressure"/>'
    )
    diastolic = _vital_sign_observation(
        code='<code code="8462-4" codeSystem="2.16.840.1.113883.6.1" displayName="Diastolic blood pressure"/>'
    )
    entry = _vitals_organizer(systolic + diastolic)
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.vitals-incomplete-blood-pressure-pair" for f in report.findings)


def test_vitals_orphaned_pulse_oximetry_component_is_info():
    # An inhaled oxygen flow rate reading present without its own primary
    # O2 saturation reading - nothing for the converter to attach it to as
    # a Pulse Oximetry Panel component.
    flow_rate = _vital_sign_observation(
        code='<code code="3151-8" codeSystem="2.16.840.1.113883.6.1" displayName="Inhaled oxygen flow rate"/>'
    )
    entry = _vitals_organizer(flow_rate)
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.vitals-orphaned-pulse-oximetry-component")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_vitals_pulse_oximetry_with_primary_produces_no_finding():
    primary = _vital_sign_observation(
        code='<code code="59408-5" codeSystem="2.16.840.1.113883.6.1" displayName="Oxygen saturation"/>'
    )
    flow_rate = _vital_sign_observation(
        code='<code code="3151-8" codeSystem="2.16.840.1.113883.6.1" displayName="Inhaled oxygen flow rate"/>'
    )
    entry = _vitals_organizer(primary + flow_rate)
    document = _doc(_patient() + _vitals_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.vitals-orphaned-pulse-oximetry-component" for f in report.findings)


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
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_result_missing_code_is_info():
    entry = _results_organizer(_result_observation(code='<code nullFlavor="UNK"/>'))
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-missing-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_result_status_unrecognized_is_info():
    entry = _results_organizer(_result_observation(status="nullified"))
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-status-unrecognized")
    assert finding.severity == "info"
    assert _body_is_valid(report)


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


def test_result_specimen_missing_role_is_info():
    # Mirrors app.cda.results._build_specimen's own skip condition: a
    # <specimen> with no resolvable specimenRole never materializes a
    # Specimen resource.
    specimen = "<specimen><notSpecimenRole/></specimen>"
    entry = _results_organizer(specimen + _result_observation())
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.result-specimen-missing-role")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_result_specimen_with_role_produces_no_finding():
    specimen = '<specimen><specimenRole><id root="2.16.840.1.113883.19.5.1" extension="S-001"/></specimenRole></specimen>'
    entry = _results_organizer(specimen + _result_observation())
    document = _doc(_patient() + _results_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.result-specimen-missing-role" for f in report.findings)


def _procedure_entry(status: str = "completed", effective_time: str = "", negation: str = "", extra: str = "") -> str:
    effective_time = effective_time or '<effectiveTime value="20260615120000-0500"/>'
    return (
        f'<entry><procedure classCode="PROC" moodCode="EVN"{negation}>{_PROCEDURE_TEMPLATE}'
        '<code code="80146002" codeSystem="2.16.840.1.113883.6.96" displayName="Appendectomy"/>'
        f'<statusCode code="{status}"/>{effective_time}'
        f"{extra}"
        "</procedure></entry>"
    )


def _procedures_section(entries: str, entries_optional: bool = False) -> str:
    template = _PROCEDURES_SECTION_TEMPLATE_ENTRIES_OPTIONAL if entries_optional else _PROCEDURES_SECTION_TEMPLATE
    return f"<component><structuredBody><component><section>{template}{entries}</section></component></structuredBody></component>"


def test_clean_procedure_produces_no_findings():
    entry = _procedure_entry()
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_procedure_status_unrecognized_is_info():
    entry = _procedure_entry(status="held")
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-status-unrecognized")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_negated_procedure_with_unrecognized_status_produces_no_status_finding():
    # negationInd="true" is a semantic override at the mapper level (this
    # procedure did NOT happen) but, unlike Immunizations' negationInd,
    # does not force a fixed status - it maps directly to "not-done"
    # regardless of statusCode, so the unrecognized-status rule must not
    # fire for a negated entry either (see app/cda/procedures.py::_resolve_status).
    entry = _procedure_entry(status="held", negation=' negationInd="true"')
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []


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


def test_procedure_performer_missing_identity_is_info():
    # Mirrors app.cda.procedures._build_practitioner_from_assigned_entity's
    # own skip condition exactly: neither an id nor a resolvable
    # assignedPerson/name means the mapper silently drops this performer.
    performer = '<performer typeCode="PRF"><assignedEntity><addr><city>Portland</city></addr></assignedEntity></performer>'
    entry = _procedure_entry(extra=performer)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-performer-missing-identity")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_procedure_performer_with_id_only_produces_no_finding():
    # The mapper's own "id or family or given" rule means an id-only
    # performer still resolves - no finding should fire for it.
    performer = '<performer typeCode="PRF"><assignedEntity><id root="2.16.840.1.113883.19.5" extension="12345"/></assignedEntity></performer>'
    entry = _procedure_entry(extra=performer)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.procedure-performer-missing-identity" for f in report.findings)


def test_procedure_participant_missing_identity_is_info():
    # Mirrors app.cda.procedures._build_service_delivery_location's own
    # skip condition: a Service Delivery Location participant with
    # neither a resolvable name nor a coded type means the mapper
    # silently drops it (Procedure.location stays unset).
    participant = (
        '<participant typeCode="LOC"><participantRole classCode="SDLOC">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.32"/>'
        "</participantRole></participant>"
    )
    entry = _procedure_entry(extra=participant)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-participant-missing-identity")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_procedure_participant_with_type_only_produces_no_finding():
    # A coded type alone (no name) still resolves per the mapper's own
    # "name or type" rule - no finding should fire for it.
    participant = (
        '<participant typeCode="LOC"><participantRole classCode="SDLOC">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.32"/>'
        '<code code="1060-3" codeSystem="2.16.840.1.113883.6.259" displayName="Emergency Department"/>'
        "</participantRole></participant>"
    )
    entry = _procedure_entry(extra=participant)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.procedure-participant-missing-identity" for f in report.findings)


def test_procedure_participant_wrong_type_code_is_not_checked():
    # Only typeCode="LOC" participants are checked - an unrelated
    # participant typeCode is out of scope for this rule, mirroring the
    # mapper's own identical typeCode gate.
    participant = '<participant typeCode="CON"><participantRole classCode="SDLOC"></participantRole></participant>'
    entry = _procedure_entry(extra=participant)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.procedure-participant-missing-identity" for f in report.findings)


def test_procedure_indication_missing_value_is_info():
    # Mirrors app.cda.procedures._build_reason_codes' own skip condition:
    # an Indication Observation with no resolvable <value> never
    # contributes a Procedure.reasonCode entry.
    indication = (
        '<entryRelationship typeCode="RSON"><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.19"/>'
        "<value nullFlavor=\"UNK\"/>"
        "</observation></entryRelationship>"
    )
    entry = _procedure_entry(extra=indication)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-indication-missing-value")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_procedure_indication_with_value_produces_no_finding():
    indication = (
        '<entryRelationship typeCode="RSON"><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.19"/>'
        '<value xsi:type="CD" code="85189001" codeSystem="2.16.840.1.113883.6.96" displayName="Acute appendicitis"/>'
        "</observation></entryRelationship>"
    )
    entry = _procedure_entry(extra=indication)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.procedure-indication-missing-value" for f in report.findings)


def test_procedure_comment_missing_text_is_info():
    # Mirrors app.cda.procedures._build_notes' own skip condition: a
    # Comment Activity act with no resolvable <text> never contributes a
    # Procedure.note entry.
    comment = (
        '<entryRelationship typeCode="SUBJ" inversionInd="true"><act classCode="ACT" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.64"/>'
        "<text></text>"
        "</act></entryRelationship>"
    )
    entry = _procedure_entry(extra=comment)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-comment-missing-text")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_procedure_comment_with_text_produces_no_finding():
    comment = (
        '<entryRelationship typeCode="SUBJ" inversionInd="true"><act classCode="ACT" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.64"/>'
        "<text>Patient tolerated the procedure well.</text>"
        "</act></entryRelationship>"
    )
    entry = _procedure_entry(extra=comment)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.procedure-comment-missing-text" for f in report.findings)


def test_procedure_recorder_missing_author_is_info():
    # Mirrors app.cda.procedures._build_procedure_recorder's own skip
    # condition: a direct-child <author> with no nested assignedAuthor
    # never contributes Procedure.recorder.
    author = "<author><time value=\"20260615113000-0500\"/></author>"
    entry = _procedure_entry(extra=author)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.procedure-recorder-missing-author")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_procedure_recorder_with_assigned_author_produces_no_finding():
    author = (
        "<author><time value=\"20260615113000-0500\"/><assignedAuthor>"
        '<id root="2.16.840.1.113883.19.5" extension="REC-001"/>'
        "</assignedAuthor></author>"
    )
    entry = _procedure_entry(extra=author)
    document = _doc(_patient() + _procedures_section(entry))
    report = validate_document(document)
    assert not any(f.rule_id == "cda.procedure-recorder-missing-author" for f in report.findings)


def _hospital_discharge_diagnosis_entry(effective_time: str = "", value: str = "") -> str:
    value = value or '<value xsi:type="CD" code="385093006" codeSystem="2.16.840.1.113883.6.96" displayName="CAP"/>'
    return (
        f'<entry><act classCode="ACT" moodCode="EVN">{_HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE}'
        '<statusCode code="active"/>'
        '<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.4"/><statusCode code="completed"/>'
        f"{effective_time}{value}</observation></entryRelationship></act></entry>"
    )


def _hospital_discharge_diagnosis_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE}"
        f"{entries}</section></component></structuredBody></component>"
    )


def test_clean_hospital_discharge_diagnosis_produces_no_findings():
    entry = _hospital_discharge_diagnosis_entry()
    document = _doc(_patient() + _hospital_discharge_diagnosis_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_hospital_discharge_diagnosis_missing_value_is_info():
    entry = _hospital_discharge_diagnosis_entry(value='<value xsi:type="CD" nullFlavor="UNK"/>')
    document = _doc(_patient() + _hospital_discharge_diagnosis_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.hospital-discharge-diagnosis-missing-value")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_hospital_discharge_diagnosis_onset_in_future_is_warning():
    entry = _hospital_discharge_diagnosis_entry(effective_time='<effectiveTime value="20990101"/>')
    document = _doc(_patient() + _hospital_discharge_diagnosis_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.hospital-discharge-diagnosis-onset-in-future")
    assert finding.severity == "warning"


def test_hospital_discharge_diagnosis_onset_before_birth_is_error():
    entry = _hospital_discharge_diagnosis_entry(effective_time='<effectiveTime value="20100101"/>')
    document = _doc(_patient(extra='<birthTime value="20200101"/>') + _hospital_discharge_diagnosis_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.hospital-discharge-diagnosis-onset-before-birth")
    assert finding.severity == "error"
    assert report.is_valid is False


def _discharge_medication_entry(status: str = "active", code: str = "") -> str:
    consumable_code = code or '<code code="308191" codeSystem="2.16.840.1.113883.6.88" displayName="Amoxicillin"/>'
    return (
        f'<entry><act classCode="ACT" moodCode="EVN">{_DISCHARGE_MEDICATION_ACT_TEMPLATE}'
        '<statusCode code="completed"/>'
        '<entryRelationship typeCode="SUBJ"><substanceAdministration classCode="SBADM" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.16"/>'
        f'<statusCode code="{status}"/>'
        f"<consumable><manufacturedProduct><manufacturedMaterial>{consumable_code}"
        "</manufacturedMaterial></manufacturedProduct></consumable>"
        "</substanceAdministration></entryRelationship></act></entry>"
    )


def _discharge_medications_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_DISCHARGE_MEDICATIONS_SECTION_TEMPLATE}"
        f"{entries}</section></component></structuredBody></component>"
    )


def test_clean_discharge_medication_produces_no_findings():
    entry = _discharge_medication_entry()
    document = _doc(_patient() + _discharge_medications_section(entry))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_discharge_medication_missing_code_is_info():
    entry = _discharge_medication_entry(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _discharge_medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.discharge-medication-missing-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_discharge_medication_status_unrecognized_is_info():
    entry = _discharge_medication_entry(status="new")
    document = _doc(_patient() + _discharge_medications_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.discharge-medication-status-unrecognized")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_narrative_section_missing_text_is_info():
    section = (
        f"<component><structuredBody><component><section>{_HOSPITAL_COURSE_SECTION_TEMPLATE}"
        '<code code="8648-8" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital Course"/>'
        "<title>Hospital Course</title><text></text>"
        "</section></component></structuredBody></component>"
    )
    document = _doc(_patient() + section)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.narrative-section-missing-text")
    assert finding.severity == "info"
    assert finding.segment == "Hospital Course/text"
    assert "Hospital Course" in finding.message
    assert _body_is_valid(report)


def test_narrative_section_with_text_produces_no_finding():
    section = (
        f"<component><structuredBody><component><section>{_HOSPITAL_COURSE_SECTION_TEMPLATE}"
        '<code code="8648-8" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital Course"/>'
        "<title>Hospital Course</title><text><paragraph>Uncomplicated course.</paragraph></text>"
        "</section></component></structuredBody></component>"
    )
    document = _doc(_patient() + section)
    report = validate_document(document)
    assert not [f for f in report.findings if f.rule_id == "cda.narrative-section-missing-text"]


def test_narrative_section_rule_applies_generically_across_different_templateids():
    # The rule walks all twelve registered templateIds generically (see
    # _iter_narrative_sections) - confirmed here against a genuinely
    # different one (Social History) than the Hospital Course fixture
    # every other test in this block uses, so this isn't just hardcoded to
    # one specific section type.
    section = (
        f"<component><structuredBody><component><section>{_SOCIAL_HISTORY_SECTION_TEMPLATE}"
        '<code code="29762-2" codeSystem="2.16.840.1.113883.6.1" displayName="Social History"/>'
        "<title>Social History</title><text></text>"
        "</section></component></structuredBody></component>"
    )
    document = _doc(_patient() + section)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.narrative-section-missing-text")
    assert finding.segment == "Social History/text"


def _social_history_observation(code: str = "", status: str = "completed", effective_time: str = "") -> str:
    observation_code = code or '<code code="160573003" codeSystem="2.16.840.1.113883.6.96" displayName="Alcohol intake"/>'
    return (
        f'<entry><observation classCode="OBS" moodCode="EVN">{_SOCIAL_HISTORY_OBSERVATION_TEMPLATE}'
        f'{observation_code}<statusCode code="{status}"/>{effective_time}'
        '<value xsi:type="PQ" value="12" unit="/d"/></observation></entry>'
    )


def _social_history_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_SOCIAL_HISTORY_SECTION_TEMPLATE}"
        '<code code="29762-2" codeSystem="2.16.840.1.113883.6.1" displayName="Social History"/>'
        f"<title>Social History</title><text>Some narrative.</text>{entries}"
        "</section></component></structuredBody></component>"
    )


def test_clean_social_history_produces_no_findings():
    document = _doc(_patient() + _social_history_section(_social_history_observation()))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_social_history_missing_code_is_info():
    entry = _social_history_observation(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _social_history_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.social-history-missing-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_social_history_effective_time_in_future_is_warning():
    entry = _social_history_observation(effective_time='<effectiveTime value="20990101"/>')
    document = _doc(_patient() + _social_history_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.social-history-effective-time-in-future")
    assert finding.severity == "warning"


def _family_history_organizer(relationship: str = "", condition_value: str = "") -> str:
    relationship_code = relationship or (
        '<code code="FTH" displayName="father" codeSystemName="HL7 FamilyMember" codeSystem="2.16.840.1.113883.5.111"/>'
    )
    condition = condition_value or (
        '<value xsi:type="CD" code="22298006" codeSystem="2.16.840.1.113883.6.96" displayName="Myocardial infarction"/>'
    )
    return (
        f'<entry><organizer classCode="CLUSTER" moodCode="EVN">{_FAMILY_HISTORY_ORGANIZER_TEMPLATE}'
        '<statusCode code="completed"/>'
        f"<subject><relatedSubject classCode=\"PRS\">{relationship_code}<subject/></relatedSubject></subject>"
        f'<component><observation classCode="OBS" moodCode="EVN">{_FAMILY_HISTORY_OBSERVATION_TEMPLATE}'
        '<code code="75323-6" codeSystem="2.16.840.1.113883.6.1" displayName="Condition"/>'
        f"<statusCode code=\"completed\"/>{condition}</observation></component>"
        "</organizer></entry>"
    )


def _family_history_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_FAMILY_HISTORY_SECTION_TEMPLATE}"
        '<code code="10157-6" codeSystem="2.16.840.1.113883.6.1" displayName="Family History"/>'
        f"<title>Family History</title><text>Some narrative.</text>{entries}"
        "</section></component></structuredBody></component>"
    )


def test_clean_family_history_produces_no_findings():
    document = _doc(_patient() + _family_history_section(_family_history_organizer()))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_family_history_missing_relationship_is_info():
    entry = _family_history_organizer(relationship='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _family_history_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.family-history-missing-relationship")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_family_history_missing_condition_code_is_info():
    entry = _family_history_organizer(condition_value='<value xsi:type="CD" nullFlavor="UNK"/>')
    document = _doc(_patient() + _family_history_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.family-history-missing-condition-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def _planned_observation_entry(code: str = "", status: str = "active") -> str:
    observation_code = code or '<code code="62959-2" codeSystem="2.16.840.1.113883.6.1" displayName="Colonoscopy"/>'
    return (
        f'<entry><observation classCode="OBS" moodCode="RQO">{_PLANNED_OBSERVATION_TEMPLATE}'
        f'{observation_code}<statusCode code="{status}"/>'
        '<effectiveTime><center value="20260901"/></effectiveTime>'
        "</observation></entry>"
    )


def _plan_of_treatment_section(entries: str) -> str:
    return (
        f"<component><structuredBody><component><section>{_PLAN_OF_TREATMENT_SECTION_TEMPLATE}"
        '<code code="18776-5" codeSystem="2.16.840.1.113883.6.1" displayName="Plan of Treatment"/>'
        f"<title>Plan of Care</title><text>Some narrative.</text>{entries}"
        "</section></component></structuredBody></component>"
    )


def test_clean_plan_of_treatment_produces_no_findings():
    document = _doc(_patient() + _plan_of_treatment_section(_planned_observation_entry()))
    report = validate_document(document)
    assert _body_findings(report) == []
    assert _body_is_valid(report)


def test_plan_of_treatment_missing_code_is_info():
    entry = _planned_observation_entry(code='<code nullFlavor="UNK"/>')
    document = _doc(_patient() + _plan_of_treatment_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.plan-of-treatment-missing-code")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_plan_of_treatment_status_unrecognized_is_info():
    entry = _planned_observation_entry(status="new")
    document = _doc(_patient() + _plan_of_treatment_section(entry))
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.plan-of-treatment-status-unrecognized")
    assert finding.severity == "info"
    assert _body_is_valid(report)


def test_unregistered_document_type_is_info():
    document = _doc(_patient(), ccd=False)
    report = validate_document(document)
    finding = next(f for f in report.findings if f.rule_id == "cda.unsupported-document-type")
    assert finding.severity == "info"
    assert _body_is_valid(report)
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


_US_REALM_RULE = "cda.composition-confidentiality-not-us-realm-conformant"


def test_missing_required_header_element_is_error():
    # author is 1..* in the base CDA StructureDefinition, and its absence
    # costs the whole Composition - Composition.author is 1..* in R4 too,
    # R4 restricts data-absent-reason to primitives, and it forbids
    # fabricating a value. So there is nothing to build.
    report = validate_document(_doc(_patient(), omit=("author",)))
    finding = next(f for f in report.findings if f.rule_id == "cda.header-missing-required-element")
    assert finding.severity == "error"
    assert finding.segment == "ClinicalDocument/author"
    assert "1..*" in finding.message
    assert report.is_valid is False


def test_every_required_header_element_is_checked():
    report = validate_document(
        _doc(
            "",
            omit=("id", "code", "title", "effectiveTime", "confidentialityCode",
                  "languageCode", "recordTarget", "author", "custodian"),
        )
    )
    missing = {
        f.segment.split("/")[-1]
        for f in report.findings
        if f.rule_id == "cda.header-missing-required-element"
    }
    # Seven from base CDA, plus the two US Realm Header tightens to 1..1.
    assert missing == {
        "id", "code", "effectiveTime", "confidentialityCode", "recordTarget",
        "author", "custodian", "title", "languageCode",
    }


def test_us_realm_only_requirements_are_not_checked_without_that_template():
    # title and languageCode are 0..1 in base CDA and 1..1 only under the
    # US Realm Header, so a document not claiming that profile keeps them
    # optional. _doc(ccd=False) emits no templateId at all.
    report = validate_document(_doc(_patient(), ccd=False, omit=("title", "languageCode")))
    missing = {
        f.segment.split("/")[-1]
        for f in report.findings
        if f.rule_id == "cda.header-missing-required-element"
    }
    assert missing == set()


def test_conformant_header_produces_no_missing_element_findings():
    report = validate_document(_doc(_patient()))
    assert [f for f in report.findings if f.rule_id == "cda.header-missing-required-element"] == []


def test_confidentiality_code_reports_the_us_realm_prohibition():
    # The value is carried (the base R4 Composition mapping names the
    # target), but US Realm Header constrains .confidentiality to 0..0 -
    # a reviewer should hear that from the validator rather than from a
    # failed profile validation later.
    document = _doc(
        _patient() + '<confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>'
    )
    matching = [f for f in validate_document(document).findings if f.rule_id == _US_REALM_RULE]
    assert len(matching) == 1
    assert matching[0].severity == "info"
    assert "0..0" in matching[0].message


def test_no_confidentiality_code_produces_no_us_realm_finding():
    document = _doc(_patient(), omit=("confidentialityCode",))
    matching = [f for f in validate_document(document).findings if f.rule_id == _US_REALM_RULE]
    assert matching == []
