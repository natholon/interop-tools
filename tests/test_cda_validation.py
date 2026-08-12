import pytest

from app.cda.validation import validate_document
from app.cda.parser import parse_document
from app.hl7.errors import MissingSegmentError

_XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
_CCD_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.1.2"/>'
_PROBLEMS_SECTION_TEMPLATE = '<templateId root="2.16.840.1.113883.10.20.22.2.5.1"/>'


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
