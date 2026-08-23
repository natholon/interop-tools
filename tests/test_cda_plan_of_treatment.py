import xml.etree.ElementTree as ET

from app.cda.plan_of_treatment import build_plan_of_treatment_resources
from app.provenance.recorder import ProvenanceRecorder

_NS = "urn:hl7-org:v3"


def _section(body: str) -> ET.Element:
    return ET.fromstring(f'<section xmlns="{_NS}">{body}</section>')


_HEADER = (
    '<code code="18776-5" codeSystem="2.16.840.1.113883.6.1" displayName="Plan of Treatment"/>'
    "<title>Plan of Care</title><text>Colonoscopy planned.</text>"
)

_PLANNED_OBSERVATION_ENTRY = (
    '<entry><observation classCode="OBS" moodCode="RQO">'
    '<templateId root="2.16.840.1.113883.10.20.22.4.44"/>'
    '<code code="62959-2" codeSystem="2.16.840.1.113883.6.1" displayName="Colonoscopy"/>'
    '<statusCode code="active"/>'
    '<effectiveTime><center value="20141012"/></effectiveTime>'
    "</observation></entry>"
)

_PLANNED_PROCEDURE_ENTRY = (
    '<entry><procedure classCode="PROC" moodCode="RQO">'
    '<templateId root="2.16.840.1.113883.10.20.22.4.41"/>'
    '<code code="73761001" codeSystem="2.16.840.1.113883.6.96" displayName="Knee arthroscopy"/>'
    '<statusCode code="active"/>'
    '<effectiveTime value="20130613"/>'
    "</procedure></entry>"
)


def test_build_plan_of_treatment_resources_always_includes_narrative_pair():
    section = _section(_HEADER)
    resources = build_plan_of_treatment_resources(section, "p1")
    types = [r.get_resource_type() for r in resources]
    assert types == ["DocumentReference", "Binary"]


def test_planned_observation_entry_produces_care_plan_activity():
    section = _section(_HEADER + _PLANNED_OBSERVATION_ENTRY)
    resources = build_plan_of_treatment_resources(section, "p1")
    care_plan = next(r for r in resources if r.get_resource_type() == "CarePlan")
    assert care_plan.status == "active"
    assert care_plan.intent == "plan"
    assert care_plan.subject.reference == "urn:uuid:p1"
    assert len(care_plan.activity) == 1
    detail = care_plan.activity[0].detail
    assert detail.code.coding[0].code == "62959-2"
    assert detail.code.coding[0].display == "Colonoscopy"
    assert detail.status == "scheduled"
    assert detail.kind == "ServiceRequest"
    # <center> is the fourth IVL_TS shape (see ivl_ts_bounds' own
    # docstring) - a single point, so it resolves to scheduledString, not
    # scheduledPeriod.
    assert detail.scheduledString == "2014-10-12"
    assert detail.scheduledPeriod is None


def test_planned_procedure_entry_produces_care_plan_activity():
    section = _section(_HEADER + _PLANNED_PROCEDURE_ENTRY)
    resources = build_plan_of_treatment_resources(section, "p1")
    care_plan = next(r for r in resources if r.get_resource_type() == "CarePlan")
    assert len(care_plan.activity) == 1
    detail = care_plan.activity[0].detail
    assert detail.code.coding[0].code == "73761001"
    assert detail.status == "scheduled"
    assert detail.scheduledString == "2013-06-13"


def test_no_recognized_entries_produces_no_care_plan():
    section = _section(_HEADER)
    resources = build_plan_of_treatment_resources(section, "p1")
    assert all(r.get_resource_type() != "CarePlan" for r in resources)


def test_unrecognized_entry_templateid_is_skipped():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="RQO">'
        '<templateId root="9.9.9.9.9"/>'
        '<code code="1" codeSystem="2.16.840.1.113883.6.96"/>'
        "</observation></entry>"
    )
    resources = build_plan_of_treatment_resources(section, "p1")
    assert all(r.get_resource_type() != "CarePlan" for r in resources)


def test_entry_with_no_resolvable_code_produces_no_activity():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="RQO">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.44"/>'
        '<statusCode code="active"/>'
        "</observation></entry>"
    )
    resources = build_plan_of_treatment_resources(section, "p1")
    assert all(r.get_resource_type() != "CarePlan" for r in resources)


def test_low_high_period_maps_to_scheduled_period():
    section = _section(
        _HEADER
        + '<entry><procedure classCode="PROC" moodCode="RQO">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.41"/>'
        '<code code="73761001" codeSystem="2.16.840.1.113883.6.96" displayName="Knee arthroscopy"/>'
        '<statusCode code="active"/>'
        '<effectiveTime><low value="20260101"/><high value="20260115"/></effectiveTime>'
        "</procedure></entry>"
    )
    resources = build_plan_of_treatment_resources(section, "p1")
    care_plan = next(r for r in resources if r.get_resource_type() == "CarePlan")
    detail = care_plan.activity[0].detail
    assert detail.scheduledPeriod.start.isoformat() == "2026-01-01"
    assert detail.scheduledPeriod.end.isoformat() == "2026-01-15"
    assert detail.scheduledString is None


def test_unrecognized_status_code_falls_back_to_unknown():
    section = _section(
        _HEADER
        + '<entry><observation classCode="OBS" moodCode="RQO">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.44"/>'
        '<code code="62959-2" codeSystem="2.16.840.1.113883.6.1" displayName="Colonoscopy"/>'
        '<statusCode code="new"/>'
        "</observation></entry>"
    )
    resources = build_plan_of_treatment_resources(section, "p1")
    care_plan = next(r for r in resources if r.get_resource_type() == "CarePlan")
    assert care_plan.activity[0].detail.status == "unknown"


def test_multiple_entries_each_produce_their_own_activity():
    section = _section(_HEADER + _PLANNED_OBSERVATION_ENTRY + _PLANNED_PROCEDURE_ENTRY)
    resources = build_plan_of_treatment_resources(section, "p1")
    care_plan = next(r for r in resources if r.get_resource_type() == "CarePlan")
    assert len(care_plan.activity) == 2


def test_records_facts_when_recorder_provided():
    recorder = ProvenanceRecorder(source_format="CDA")
    section = _section(_HEADER + _PLANNED_OBSERVATION_ENTRY)
    resources = build_plan_of_treatment_resources(section, "p1", recorder=recorder)
    care_plan = next(r for r in resources if r.get_resource_type() == "CarePlan")
    facts = {(f.resource_id, f.relative_path): f for f in recorder.facts}
    assert facts[(care_plan.id, "activity[0].detail.code.coding[0].code")].value == "62959-2"
    assert facts[(care_plan.id, "activity[0].detail.status")].derivation == "direct"
    assert facts[(care_plan.id, "activity[0].detail.kind")].derivation == "inferred"
    assert facts[(care_plan.id, "status")].derivation == "inferred"
    assert facts[(care_plan.id, "intent")].derivation == "inferred"


def test_without_recorder_still_works():
    section = _section(_HEADER + _PLANNED_OBSERVATION_ENTRY)
    resources = build_plan_of_treatment_resources(section, "p1")
    assert len(resources) == 3


def test_every_recognized_entry_shape_maps_to_its_own_kind():
    # All eight shapes carry the same code/statusCode/effectiveTime, so one
    # extraction serves them all; what differs is CarePlanActivityDetail
    # .kind. Two shapes share the "act" tag and two share
    # "substanceAdministration", so this also proves the dispatch
    # disambiguates by templateId rather than tag alone.
    from app.cda.plan_of_treatment import _RECOGNIZED_ENTRY_SHAPES

    entries = "".join(
        f'<entry><{tag} classCode="ACT" moodCode="RQO"><templateId root="{template_id}"/>'
        f'<code code="C{i}" codeSystem="2.16.840.1.113883.6.96" displayName="Planned {i}"/>'
        f'<statusCode code="active"/>'
        f'<effectiveTime value="20270101"/>'
        f"</{tag}></entry>"
        for i, (tag, template_id, _kind) in enumerate(_RECOGNIZED_ENTRY_SHAPES)
    )
    section = _section(entries)
    care_plan = next(
        r for r in build_plan_of_treatment_resources(section, "pat-1")
        if r.get_resource_type() == "CarePlan"
    )

    assert len(care_plan.activity) == len(_RECOGNIZED_ENTRY_SHAPES)
    assert [a.detail.kind for a in care_plan.activity] == [
        kind for _tag, _tid, kind in _RECOGNIZED_ENTRY_SHAPES
    ]
    # Both act-tagged shapes resolved, to different kinds.
    assert {"ServiceRequest", "Appointment", "MedicationRequest", "CommunicationRequest"} <= {
        a.detail.kind for a in care_plan.activity
    }
