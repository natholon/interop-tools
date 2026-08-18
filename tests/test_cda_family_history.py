import xml.etree.ElementTree as ET

from app.cda.family_history import build_family_history_resources
from app.provenance.recorder import ProvenanceRecorder

_NS = "urn:hl7-org:v3"
_XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
_SDTC = 'xmlns:sdtc="urn:hl7-org:sdtc"'


def _section(body: str) -> ET.Element:
    return ET.fromstring(f'<section xmlns="{_NS}" {_XSI} {_SDTC}>{body}</section>')


_HEADER = (
    '<code code="10157-6" codeSystem="2.16.840.1.113883.6.1" displayName="Family History"/>'
    "<title>Family History</title><text>Father: myocardial infarction.</text>"
)


def _organizer(*, subject_extra: str = "", condition_extra: str = "") -> str:
    return (
        '<entry><organizer classCode="CLUSTER" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.45"/>'
        '<statusCode code="completed"/>'
        '<subject><relatedSubject classCode="PRS">'
        '<code code="FTH" displayName="father" codeSystemName="HL7 FamilyMember" codeSystem="2.16.840.1.113883.5.111"/>'
        f"<subject>{subject_extra}</subject>"
        "</relatedSubject></subject>"
        '<component><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.46"/>'
        '<code code="75323-6" codeSystem="2.16.840.1.113883.6.1" displayName="Condition"/>'
        '<statusCode code="completed"/>'
        '<effectiveTime value="1967"/>'
        '<value xsi:type="CD" code="22298006" codeSystem="2.16.840.1.113883.6.96" displayName="Myocardial infarction"/>'
        f"{condition_extra}"
        "</observation></component>"
        "</organizer></entry>"
    )


def test_build_family_history_resources_always_includes_narrative_pair():
    section = _section(_HEADER)
    resources = build_family_history_resources(section, "p1")
    types = [r.get_resource_type() for r in resources]
    assert types == ["DocumentReference", "Binary"]


def test_organizer_with_one_condition_produces_family_member_history():
    section = _section(_HEADER + _organizer())
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    assert history.status == "completed"
    assert history.patient.reference == "urn:uuid:p1"
    assert history.relationship.coding[0].code == "FTH"
    assert history.relationship.coding[0].display == "father"
    assert len(history.condition) == 1
    condition = history.condition[0]
    assert condition.code.coding[0].code == "22298006"
    assert condition.code.coding[0].display == "Myocardial infarction"
    assert condition.contributedToDeath is None
    assert condition.onsetAge is None


def test_organizer_with_no_relatedsubject_produces_no_family_member_history():
    section = _section(
        _HEADER
        + '<entry><organizer classCode="CLUSTER" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.45"/>'
        '<statusCode code="completed"/>'
        '<component><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.46"/>'
        '<code code="75323-6" codeSystem="2.16.840.1.113883.6.1"/>'
        '<value xsi:type="CD" code="22298006" codeSystem="2.16.840.1.113883.6.96" displayName="Myocardial infarction"/>'
        "</observation></component>"
        "</organizer></entry>"
    )
    resources = build_family_history_resources(section, "p1")
    assert all(r.get_resource_type() != "FamilyMemberHistory" for r in resources)


def test_organizer_with_no_resolvable_condition_produces_no_family_member_history():
    section = _section(
        _HEADER
        + '<entry><organizer classCode="CLUSTER" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.45"/>'
        '<statusCode code="completed"/>'
        '<subject><relatedSubject classCode="PRS">'
        '<code code="FTH" displayName="father" codeSystem="2.16.840.1.113883.5.111"/>'
        "<subject/>"
        "</relatedSubject></subject>"
        '<component><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.46"/>'
        '<code code="75323-6" codeSystem="2.16.840.1.113883.6.1"/>'
        "</observation></component>"
        "</organizer></entry>"
    )
    resources = build_family_history_resources(section, "p1")
    assert all(r.get_resource_type() != "FamilyMemberHistory" for r in resources)


def test_age_observation_sets_onset_age():
    condition_extra = (
        '<entryRelationship typeCode="SUBJ" inversionInd="true">'
        '<observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.31"/>'
        '<code code="445518008" codeSystem="2.16.840.1.113883.6.96" displayName="Age At Onset"/>'
        '<value xsi:type="PQ" value="57" unit="a"/>'
        "</observation></entryRelationship>"
    )
    section = _section(_HEADER + _organizer(condition_extra=condition_extra))
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    condition = history.condition[0]
    assert float(condition.onsetAge.value) == 57
    assert condition.onsetAge.unit == "a"


def test_death_observation_sets_contributed_to_death():
    condition_extra = (
        '<entryRelationship typeCode="CAUS">'
        '<observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.47"/>'
        '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/>'
        '<value xsi:type="CD" code="419099009" codeSystem="2.16.840.1.113883.6.96" displayName="Dead"/>'
        "</observation></entryRelationship>"
    )
    section = _section(_HEADER + _organizer(condition_extra=condition_extra))
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    assert history.condition[0].contributedToDeath is True


def test_administrative_gender_code_maps_to_sex():
    section = _section(
        _HEADER
        + _organizer(subject_extra='<administrativeGenderCode code="M" codeSystem="2.16.840.1.113883.5.1"/>')
    )
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    assert history.sex.coding[0].code == "M"


def test_deceased_ind_true_with_no_time_sets_deceased_boolean():
    section = _section(_HEADER + _organizer(subject_extra='<sdtc:deceasedInd value="true"/>'))
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    assert history.deceasedBoolean is True
    assert history.deceasedDate is None


def test_deceased_ind_false_sets_deceased_boolean_false():
    section = _section(_HEADER + _organizer(subject_extra='<sdtc:deceasedInd value="false"/>'))
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    assert history.deceasedBoolean is False
    assert history.deceasedDate is None


def test_deceased_ind_true_with_time_prefers_deceased_date_over_boolean():
    # deceased[x] is a FHIR choice type - only one of deceasedBoolean/
    # deceasedDate may be set at once (see the module's own docstring for
    # why deceasedDate wins when both resolve).
    section = _section(
        _HEADER
        + _organizer(
            subject_extra='<sdtc:deceasedInd value="true"/><sdtc:deceasedTime value="1967"/>'
        )
    )
    resources = build_family_history_resources(section, "p1")
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    assert history.deceasedDate == "1967"
    assert history.deceasedBoolean is None


def test_deceased_time_partial_precision_shapes():
    # A full year-month-day value round-trips through pydantic's own
    # `date` coercion (a real datetime.date, not the bare string) - only
    # the two shorter, genuinely partial-precision shapes stay strings.
    for raw, expected in (("1967", "1967"), ("196703", "1967-03"), ("19670315", "1967-03-15")):
        section = _section(
            _HEADER
            + _organizer(
                subject_extra=f'<sdtc:deceasedInd value="true"/><sdtc:deceasedTime value="{raw}"/>'
            )
        )
        resources = build_family_history_resources(section, "p1")
        history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
        assert str(history.deceasedDate) == expected


def test_multiple_organizers_each_produce_their_own_family_member_history():
    section = _section(_HEADER + _organizer() + _organizer())
    resources = build_family_history_resources(section, "p1")
    histories = [r for r in resources if r.get_resource_type() == "FamilyMemberHistory"]
    assert len(histories) == 2


def test_records_facts_when_recorder_provided():
    recorder = ProvenanceRecorder(source_format="CDA")
    section = _section(_HEADER + _organizer())
    resources = build_family_history_resources(section, "p1", recorder=recorder)
    history = next(r for r in resources if r.get_resource_type() == "FamilyMemberHistory")
    facts = {(f.resource_id, f.relative_path): f for f in recorder.facts}
    assert facts[(history.id, "relationship.coding[0].code")].value == "FTH"
    assert facts[(history.id, "status")].derivation == "inferred"
    assert facts[(history.id, "condition[0].code.coding[0].code")].value == "22298006"


def test_without_recorder_still_works():
    section = _section(_HEADER + _organizer())
    resources = build_family_history_resources(section, "p1")
    assert len(resources) == 3
