import xml.etree.ElementTree as ET

from app.cda.social_history import build_social_history_resources
from app.provenance.recorder import ProvenanceRecorder

_NS = "urn:hl7-org:v3"
_XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def _section(body: str) -> ET.Element:
    return ET.fromstring(f'<section xmlns="{_NS}" {_XSI}>{body}</section>')


_HEADER = (
    '<code code="29762-2" codeSystem="2.16.840.1.113883.6.1" displayName="Social History"/>'
    "<title>Social History</title><text>Ex-smoker.</text>"
)


def test_build_social_history_resources_always_includes_narrative_pair():
    section = _section(_HEADER)
    resources = build_social_history_resources(section, "p1")
    types = [r.get_resource_type() for r in resources]
    assert types == ["DocumentReference", "Binary"]


def test_smoking_status_entry_produces_observation_with_correct_fields():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.78"/>'
        '<code code="72166-2" codeSystem="2.16.840.1.113883.6.1" displayName="Tobacco smoking status NHIS"/>'
        '<statusCode code="completed"/>'
        '<effectiveTime value="20120910"/>'
        '<value xsi:type="CD" code="8517006" displayName="Ex-smoker" codeSystem="2.16.840.1.113883.6.96"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    observation = next(r for r in resources if r.get_resource_type() == "Observation")
    assert observation.code.coding[0].code == "72166-2"
    assert observation.code.coding[0].display == "Tobacco smoking status NHIS"
    assert observation.category[0].coding[0].system == "http://terminology.hl7.org/CodeSystem/observation-category"
    assert observation.category[0].coding[0].code == "social-history"
    assert observation.status == "final"
    assert observation.effectiveDateTime.isoformat() == "2012-09-10"
    assert observation.valueCodeableConcept.coding[0].code == "8517006"
    assert observation.subject.reference == "urn:uuid:p1"


def test_generic_social_history_observation_entry_produces_observation():
    # The base Social History Observation templateId (not the Smoking-
    # Status-specific one) - both are recognized.
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.38"/>'
        '<code code="160573003" codeSystem="2.16.840.1.113883.6.96" displayName="Alcohol intake"/>'
        '<statusCode code="completed"/>'
        '<effectiveTime><low value="20120215"/></effectiveTime>'
        '<value xsi:type="PQ" value="12" unit="/d"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    observation = next(r for r in resources if r.get_resource_type() == "Observation")
    assert observation.code.coding[0].code == "160573003"
    assert float(observation.valueQuantity.value) == 12
    assert observation.valueQuantity.unit == "/d"
    assert observation.effectiveDateTime.isoformat() == "2012-02-15"


def test_unrecognized_entry_templateid_is_skipped():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="9.9.9.9.9"/>'
        '<code code="1" codeSystem="2.16.840.1.113883.6.96"/>'
        '<value xsi:type="INT" value="1"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    assert all(r.get_resource_type() != "Observation" for r in resources)


def test_entry_with_no_resolvable_code_produces_no_observation():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.38"/>'
        '<statusCode code="completed"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    assert all(r.get_resource_type() != "Observation" for r in resources)


def test_value_type_int_maps_to_value_integer():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.38"/>'
        '<code code="1" codeSystem="2.16.840.1.113883.6.96" displayName="Some count"/>'
        '<value xsi:type="INT" value="5"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    observation = next(r for r in resources if r.get_resource_type() == "Observation")
    assert observation.valueInteger == 5


def test_value_type_st_maps_to_value_string():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.38"/>'
        '<code code="1" codeSystem="2.16.840.1.113883.6.96" displayName="Some text field"/>'
        '<value xsi:type="ST">Free text value</value>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    observation = next(r for r in resources if r.get_resource_type() == "Observation")
    assert observation.valueString == "Free text value"


def test_multiple_entries_each_produce_their_own_observation():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.78"/>'
        '<code code="72166-2" codeSystem="2.16.840.1.113883.6.1" displayName="Tobacco smoking status NHIS"/>'
        '<value xsi:type="CD" code="8517006" displayName="Ex-smoker" codeSystem="2.16.840.1.113883.6.96"/>'
        "</observation></entry>"
        '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.38"/>'
        '<code code="160573003" codeSystem="2.16.840.1.113883.6.96" displayName="Alcohol intake"/>'
        '<value xsi:type="PQ" value="3" unit="/wk"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1")
    observations = [r for r in resources if r.get_resource_type() == "Observation"]
    assert len(observations) == 2


def test_records_facts_when_recorder_provided():
    recorder = ProvenanceRecorder(source_format="CDA")
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="EVN">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.78"/>'
        '<code code="72166-2" codeSystem="2.16.840.1.113883.6.1" displayName="Tobacco smoking status NHIS"/>'
        '<statusCode code="completed"/>'
        '<value xsi:type="CD" code="8517006" displayName="Ex-smoker" codeSystem="2.16.840.1.113883.6.96"/>'
        "</observation></entry>"
    )
    resources = build_social_history_resources(section, "p1", recorder=recorder)
    observation = next(r for r in resources if r.get_resource_type() == "Observation")
    facts = {(f.resource_id, f.relative_path): f for f in recorder.facts}
    assert facts[(observation.id, "code.coding[0].code")].value == "72166-2"
    assert facts[(observation.id, "category[0].coding[0].code")].derivation == "inferred"
    assert facts[(observation.id, "status")].derivation == "inferred"
    assert facts[(observation.id, "valueCodeableConcept.coding[0].code")].value == "8517006"


def test_without_recorder_still_works():
    section = _section(_HEADER)
    resources = build_social_history_resources(section, "p1")
    assert len(resources) == 2
