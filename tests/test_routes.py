import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
client = TestClient(app)


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_index_renders_form():
    response = client.get("/")
    assert response.status_code == 200
    assert "hl7_text" in response.text


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_convert_success():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    assert len(body["bundle"]["entry"]) == 2


def test_api_convert_without_deduplicate_omits_deduplication_key():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    assert "deduplication" not in response.json()


def test_api_convert_with_deduplicate_reports_zero_merges_when_nothing_to_merge():
    response = client.post(
        "/api/convert", json={"hl7_text": read_fixture("adt_a01_basic.hl7"), "deduplicate": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deduplication"]["resources_merged"] == 0
    assert body["deduplication"]["merges"] == []


def test_api_convert_with_deduplicate_merges_837p_billing_and_rendering_provider():
    # The real motivating case for this feature - see tests/test_dedup.py's
    # equivalent direct-module test for the full assertion detail.
    response = client.post(
        "/api/convert", json={"hl7_text": read_fixture("edi_837p_basic.x12"), "deduplicate": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deduplication"]["resources_merged"] == 1
    practitioners = [e for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "Practitioner"]
    assert len(practitioners) == 1


def test_form_convert_with_deduplicate_checkbox_renders_summary():
    response = client.post(
        "/convert", data={"hl7_text": read_fixture("edi_837p_basic.x12"), "deduplicate": "on"}
    )
    assert response.status_code == 200
    assert "Merged 1 duplicate resource" in response.text


def test_api_convert_malformed_returns_error_category():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a01_malformed.hl7")})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["category"] == "Parse error"


def test_form_convert_renders_bundle_in_page():
    response = client.post("/convert", data={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    # Jinja2 autoescapes the JSON in <pre><code>, so check for unescaped words rather than quotes.
    assert "resourceType" in response.text
    assert "Bundle" in response.text


def test_form_convert_renders_error_in_page():
    response = client.post("/convert", data={"hl7_text": read_fixture("adt_a01_malformed.hl7")})
    assert response.status_code == 200
    assert "Parse error" in response.text


def test_api_convert_resolves_non_a01_trigger_end_to_end():
    # Proves the parse -> pipeline -> route stack resolves a non-A01 ADT trigger
    # via the registry, not just the mapper in isolation.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a03_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    encounter = next(e["resource"] for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "Encounter")
    assert encounter["status"] == "finished"


def test_api_convert_resolves_siu_message_end_to_end():
    # Proves the parse -> pipeline -> route stack resolves a second HL7 message
    # *type* (SIU, not just another ADT trigger) via the registry.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("siu_s12_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    appointment = next(
        e["resource"] for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "Appointment"
    )
    assert appointment["status"] == "booked"


def test_api_convert_resolves_oru_message_end_to_end():
    # Proves the parse -> pipeline -> route stack resolves a third HL7 message
    # *type* (ORU, with its OBR/OBX positional grouping) via the registry.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("oru_r01_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    report_count = sum(1 for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
    assert report_count == 2


def test_api_convert_resolves_mdm_message_end_to_end():
    # Proves the parse -> pipeline -> route stack resolves a fourth HL7
    # message *type* (MDM, TXA -> DocumentReference + Binary) via the registry.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("mdm_t02_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    document_reference = next(
        e["resource"] for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "DocumentReference"
    )
    assert document_reference["status"] == "current"


def test_index_renders_message_type_dropdown():
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="message-type-select"' in response.text
    assert "ADT^A01 - Admit" in response.text
    assert "SIU^S12 - New Appointment" in response.text
    assert "ORU^R01" in response.text


def test_api_generate_returns_convertible_message():
    # Full-stack smoke test: generated text must itself round-trip through /api/convert.
    response = client.get("/api/generate", params={"message_type": "ADT", "trigger_event": "A01"})
    assert response.status_code == 200
    hl7_text = response.json()["hl7_text"]
    assert hl7_text

    convert_response = client.post("/api/convert", json={"hl7_text": hl7_text})
    assert convert_response.status_code == 200
    assert convert_response.json()["bundle"]["resourceType"] == "Bundle"


def test_api_generate_is_reproducible_with_seed():
    first = client.get("/api/generate", params={"message_type": "SIU", "trigger_event": "S12", "seed": 5})
    second = client.get("/api/generate", params={"message_type": "SIU", "trigger_event": "S12", "seed": 5})
    assert first.json()["hl7_text"] == second.json()["hl7_text"]


def test_api_generate_unsupported_combination_returns_404():
    response = client.get("/api/generate", params={"message_type": "ADT", "trigger_event": "A99"})
    assert response.status_code == 404
    assert response.json()["error"]["category"] == "Unknown message type"


def test_api_validate_clean_message_returns_200_with_is_valid_true():
    response = client.post("/api/validate", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["is_valid"] is True
    assert body["report"]["findings"] == []


def test_api_validate_message_with_error_still_returns_200():
    # A validation report concluding is_valid=false is itself a *successful*
    # analysis, not an API error - this must NOT be a 4xx response, unlike
    # /api/convert's error-status mapping.
    response = client.post(
        "/api/validate", json={"hl7_text": read_fixture("validation_adt_pv1_missing.hl7")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["is_valid"] is False
    assert any(f["severity"] == "error" for f in body["report"]["findings"])


def test_api_validate_malformed_text_returns_400():
    response = client.post("/api/validate", json={"hl7_text": read_fixture("adt_a01_malformed.hl7")})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Parse error"


def test_form_validate_renders_report_in_page():
    response = client.post("/validate", data={"hl7_text": read_fixture("validation_adt_pv1_missing.hl7")})
    assert response.status_code == 200
    assert "adt.pv1-missing" in response.text


def test_form_validate_renders_error_in_page():
    response = client.post("/validate", data={"hl7_text": read_fixture("adt_a01_malformed.hl7")})
    assert response.status_code == 200
    assert "Parse error" in response.text


def test_index_has_validate_button_and_pane():
    response = client.get("/")
    assert response.status_code == 200
    assert 'formaction="/validate"' in response.text
    assert 'id="validation-pane"' in response.text


def test_api_convert_resolves_cda_message_end_to_end():
    # Proves the format-sniff in app/pipeline.py actually routes XML through
    # the same /api/convert endpoint as HL7v2, not just that the CDA
    # pipeline works in isolation.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("ccd_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    condition_count = sum(1 for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "Condition")
    assert condition_count == 2


def test_api_convert_cda_malformed_returns_parse_error():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("ccd_malformed.xml")})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Parse error"


def test_api_validate_resolves_cda_message_end_to_end():
    # Proves the format-sniff in app/pipeline.py::validate_any actually
    # routes XML through the same /api/validate endpoint as HL7v2, not just
    # that the CDA validator works in isolation.
    response = client.post("/api/validate", json={"hl7_text": read_fixture("ccd_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["is_valid"] is True
    assert body["report"]["message_type"] == "CDA"
    assert body["report"]["trigger_event"] == "CCD"


def test_api_validate_cda_message_with_error_still_returns_200():
    response = client.post(
        "/api/validate", json={"hl7_text": read_fixture("ccd_problem_status_observation_overrides_act_status.xml")}
    )
    assert response.status_code == 200
    assert response.json()["report"]["is_valid"] is True


def test_api_validate_cda_malformed_returns_parse_error():
    response = client.post("/api/validate", json={"hl7_text": read_fixture("ccd_malformed.xml")})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Parse error"


def test_form_validate_renders_cda_report_in_page():
    response = client.post("/validate", data={"hl7_text": read_fixture("ccd_basic.xml")})
    assert response.status_code == 200
    # Jinja2 autoescapes the JSON in <pre><code>, so check for unescaped words rather than quotes.
    assert "is_valid" in response.text
    assert "true" in response.text


def test_api_generate_cda_returns_convertible_and_valid_document():
    # Full-stack smoke test for the CDA generator, mirroring
    # test_api_generate_returns_convertible_message for HL7v2: generated
    # text must itself round-trip through /api/convert and /api/validate.
    response = client.get("/api/generate", params={"message_type": "CDA", "trigger_event": "CCD"})
    assert response.status_code == 200
    xml_text = response.json()["hl7_text"]
    assert xml_text

    convert_response = client.post("/api/convert", json={"hl7_text": xml_text})
    assert convert_response.status_code == 200
    assert convert_response.json()["bundle"]["resourceType"] == "Bundle"

    validate_response = client.post("/api/validate", json={"hl7_text": xml_text})
    assert validate_response.status_code == 200
    assert validate_response.json()["report"]["is_valid"] is True


def test_api_generate_cda_is_reproducible_with_seed():
    first = client.get("/api/generate", params={"message_type": "CDA", "trigger_event": "CCD", "seed": 5})
    second = client.get("/api/generate", params={"message_type": "CDA", "trigger_event": "CCD", "seed": 5})
    assert first.json()["hl7_text"] == second.json()["hl7_text"]


def test_index_message_type_dropdown_includes_cda():
    response = client.get("/")
    assert response.status_code == 200
    assert "CDA^CCD - Continuity of Care Document" in response.text
    assert "CDA^DischargeSummary - Discharge Summary" in response.text
    assert "CDA^HistoryAndPhysical - History and Physical Note" in response.text


def test_index_transform_target_dropdown_includes_adt_a01():
    response = client.get("/")
    assert response.status_code == 200
    assert "HL7 ADT^A01" in response.text


def test_index_transform_target_dropdown_includes_all_six_adt_triggers():
    response = client.get("/")
    assert response.status_code == 200
    for trigger in ("A01", "A02", "A03", "A04", "A05", "A08"):
        assert f"HL7 ADT^{trigger}" in response.text


def test_index_transform_target_dropdown_includes_all_three_adt_cancel_triggers():
    response = client.get("/")
    assert response.status_code == 200
    for trigger in ("A11", "A13", "A38"):
        assert f"HL7 ADT^{trigger}" in response.text


def test_api_transform_builds_adt_a11_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a11_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "ADT", "target_trigger": "A11"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert "||ADT^A11|" in message_text


def test_api_transform_builds_adt_a03_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a03_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "ADT", "target_trigger": "A03"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert "||ADT^A03|" in message_text


def test_index_transform_target_dropdown_includes_siu_s12():
    response = client.get("/")
    assert response.status_code == 200
    assert "HL7 SIU^S12" in response.text


def test_index_transform_target_dropdown_includes_all_six_siu_triggers():
    response = client.get("/")
    assert response.status_code == 200
    for trigger in ("S12", "S13", "S14", "S15", "S17", "S26"):
        assert f"HL7 SIU^{trigger}" in response.text


def test_api_transform_builds_siu_s12_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("siu_s12_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "SIU", "target_trigger": "S12"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert "||SIU^S12|" in message_text
    assert "AIP" in message_text


def test_index_transform_target_dropdown_includes_oru_r01():
    response = client.get("/")
    assert response.status_code == 200
    assert "HL7 ORU^R01" in response.text


def test_index_transform_target_dropdown_includes_all_five_oru_triggers():
    response = client.get("/")
    assert response.status_code == 200
    for trigger in ("R01", "R30", "R31", "R32", "R40"):
        assert f"HL7 ORU^{trigger}" in response.text


def test_api_transform_builds_oru_r30_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("oru_r30_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "ORU", "target_trigger": "R30"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert "||ORU^R30|" in message_text


def test_api_transform_builds_oru_r01_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("oru_r01_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "ORU", "target_trigger": "R01"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert "||ORU^R01|" in message_text
    assert "OBR" in message_text
    assert "OBX" in message_text


def test_index_transform_target_dropdown_includes_mdm_t02():
    response = client.get("/")
    assert response.status_code == 200
    assert "HL7 MDM^T02" in response.text


def test_index_transform_target_dropdown_includes_all_six_mdm_triggers():
    response = client.get("/")
    assert response.status_code == 200
    for trigger in ("T02", "T04", "T06", "T08", "T10", "T11"):
        assert f"HL7 MDM^{trigger}" in response.text


def test_api_transform_builds_mdm_t02_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("mdm_t02_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "MDM", "target_trigger": "T02"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert "||MDM^T02|" in message_text
    assert "TXA" in message_text


def test_index_transform_target_dropdown_includes_ccd_without_stray_caret():
    # A target with no real trigger-event concept must render as "CDA CCD",
    # not "CDA CCD^" - see _transform_target_options' own docstring.
    response = client.get("/")
    assert response.status_code == 200
    assert "CDA CCD" in response.text
    assert "CDA CCD^" not in response.text


def test_api_transform_builds_ccd_document():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("ccd_basic.xml")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "CDA", "target_type": "CCD", "target_trigger": ""},
    )
    assert response.status_code == 200
    document_text = response.json()["message_text"]
    assert document_text.startswith('<?xml version="1.0"')
    assert "Betterhalf" in document_text


def test_index_transform_target_dropdown_includes_discharge_summary():
    response = client.get("/")
    assert response.status_code == 200
    assert "CDA DISCHARGESUMMARY" in response.text


def test_api_transform_builds_discharge_summary_document():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("discharge_summary_basic.xml")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={
            "bundle_json": bundle_json,
            "target_format": "CDA",
            "target_type": "DISCHARGESUMMARY",
            "target_trigger": "",
        },
    )
    assert response.status_code == 200
    document_text = response.json()["message_text"]
    assert document_text.startswith('<?xml version="1.0"')
    assert "18842-5" in document_text


def test_index_transform_target_dropdown_includes_history_and_physical():
    response = client.get("/")
    assert response.status_code == 200
    assert "CDA HISTORYANDPHYSICAL" in response.text


def test_api_transform_builds_history_and_physical_document():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("history_and_physical_basic.xml")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={
            "bundle_json": bundle_json,
            "target_format": "CDA",
            "target_type": "HISTORYANDPHYSICAL",
            "target_trigger": "",
        },
    )
    assert response.status_code == 200
    document_text = response.json()["message_text"]
    assert document_text.startswith('<?xml version="1.0"')
    assert "34117-2" in document_text


def test_index_transform_target_dropdown_includes_270():
    response = client.get("/")
    assert response.status_code == 200
    assert "EDI 270" in response.text


def test_api_transform_builds_270_interchange():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_270_basic.x12")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "EDI", "target_type": "270", "target_trigger": ""},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert message_text.startswith("ISA*")
    assert "ACME HEALTH PLAN" in message_text


def test_index_transform_target_dropdown_includes_271():
    response = client.get("/")
    assert response.status_code == 200
    assert "EDI 271" in response.text


def test_api_transform_builds_271_interchange():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_271_basic.x12")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "EDI", "target_type": "271", "target_trigger": ""},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert message_text.startswith("ISA*")
    assert "ST*271*" in message_text


def test_index_transform_target_dropdown_includes_276_and_277():
    response = client.get("/")
    assert response.status_code == 200
    assert "EDI 276" in response.text
    assert "EDI 277" in response.text


def test_api_transform_builds_277_interchange():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_277_basic.x12")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "EDI", "target_type": "277", "target_trigger": ""},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert message_text.startswith("ISA*")
    assert "ST*277*" in message_text


def test_api_transform_builds_adt_a01_message():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "ADT", "target_trigger": "A01"},
    )
    assert response.status_code == 200
    message_text = response.json()["message_text"]
    assert message_text.startswith("MSH|^~\\&|")
    assert "ADT^A01" in message_text


def test_api_transform_invalid_json_returns_400():
    response = client.post(
        "/api/transform",
        json={"bundle_json": "not json", "target_format": "HL7", "target_type": "ADT", "target_trigger": "A01"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Invalid JSON"


def test_api_transform_invalid_bundle_returns_422():
    response = client.post(
        "/api/transform",
        json={"bundle_json": "{}", "target_format": "HL7", "target_type": "ADT", "target_trigger": "A01"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "Invalid FHIR Bundle"


def test_api_transform_missing_patient_returns_422():
    empty_bundle = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": []})
    response = client.post(
        "/api/transform",
        json={"bundle_json": empty_bundle, "target_format": "HL7", "target_type": "ADT", "target_trigger": "A01"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "Mapping error"


def test_api_transform_unregistered_target_returns_422():
    empty_bundle = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": []})
    response = client.post(
        "/api/transform",
        json={"bundle_json": empty_bundle, "target_format": "HL7", "target_type": "ADT", "target_trigger": "A99"},
    )
    assert response.status_code == 422


def test_form_transform_renders_generated_message_in_page():
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    response = client.post("/transform", data={"bundle_json": bundle_json, "target": "HL7 ADT^A01"})
    assert response.status_code == 200
    assert "Generated Message" in response.text
    assert "ADT" in response.text


def test_form_transform_renders_error_in_page():
    response = client.post("/transform", data={"bundle_json": "not json", "target": "HL7 ADT^A01"})
    assert response.status_code == 200
    assert "Invalid JSON" in response.text


def test_convert_then_transform_round_trips_end_to_end():
    # The full realistic user flow: convert a real message to a Bundle,
    # then transform that exact Bundle back out, and confirm the result
    # converts successfully a second time.
    convert_response = client.post("/api/convert", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    bundle_json = json.dumps(convert_response.json()["bundle"])

    transform_response = client.post(
        "/api/transform",
        json={"bundle_json": bundle_json, "target_format": "HL7", "target_type": "ADT", "target_trigger": "A01"},
    )
    message_text = transform_response.json()["message_text"]

    round_trip_response = client.post("/api/convert", json={"hl7_text": message_text})
    assert round_trip_response.status_code == 200
    assert round_trip_response.json()["bundle"]["resourceType"] == "Bundle"


def test_api_validate_resolves_discharge_summary_end_to_end():
    response = client.post("/api/validate", json={"hl7_text": read_fixture("discharge_summary_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["is_valid"] is True
    assert body["report"]["message_type"] == "CDA"
    assert body["report"]["trigger_event"] == "DISCHARGESUMMARY"


def test_api_generate_discharge_summary_returns_convertible_and_valid_document():
    response = client.get("/api/generate", params={"message_type": "CDA", "trigger_event": "DischargeSummary"})
    assert response.status_code == 200
    xml_text = response.json()["hl7_text"]
    assert xml_text

    convert_response = client.post("/api/convert", json={"hl7_text": xml_text})
    assert convert_response.status_code == 200
    assert convert_response.json()["bundle"]["resourceType"] == "Bundle"

    validate_response = client.post("/api/validate", json={"hl7_text": xml_text})
    assert validate_response.status_code == 200
    assert validate_response.json()["report"]["is_valid"] is True


def test_api_validate_resolves_history_and_physical_end_to_end():
    response = client.post("/api/validate", json={"hl7_text": read_fixture("history_and_physical_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["is_valid"] is True
    assert body["report"]["message_type"] == "CDA"
    assert body["report"]["trigger_event"] == "HISTORYANDPHYSICAL"


def test_api_generate_history_and_physical_returns_convertible_and_valid_document():
    response = client.get("/api/generate", params={"message_type": "CDA", "trigger_event": "HistoryAndPhysical"})
    assert response.status_code == 200
    xml_text = response.json()["hl7_text"]
    assert xml_text

    convert_response = client.post("/api/convert", json={"hl7_text": xml_text})
    assert convert_response.status_code == 200
    assert convert_response.json()["bundle"]["resourceType"] == "Bundle"

    validate_response = client.post("/api/validate", json={"hl7_text": xml_text})
    assert validate_response.status_code == 200
    assert validate_response.json()["report"]["is_valid"] is True


def test_api_convert_resolves_edi_270_message_end_to_end():
    # Proves the format-sniff in app/pipeline.py actually routes a
    # literal-"ISA"-prefixed X12 interchange through the same /api/convert
    # endpoint as HL7v2/CDA, not just that the EDI pipeline works in
    # isolation.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_270_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    resource_types = {e["resource"]["resourceType"] for e in body["bundle"]["entry"]}
    assert "CoverageEligibilityRequest" in resource_types


def test_api_convert_resolves_edi_271_message_end_to_end():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_271_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    resource_types = {e["resource"]["resourceType"] for e in body["bundle"]["entry"]}
    assert "CoverageEligibilityResponse" in resource_types


def test_api_convert_resolves_edi_837p_message_end_to_end():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_837p_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    resource_types = {e["resource"]["resourceType"] for e in body["bundle"]["entry"]}
    assert "Claim" in resource_types


def test_api_convert_resolves_edi_837i_message_end_to_end():
    # Proves the ST03-based dispatch (not just ST01) actually routes an
    # 837I sample to Edi837iBuilder through the real /api/convert endpoint,
    # not just in unit tests calling the builder directly.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_837i_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    resource_types = {e["resource"]["resourceType"] for e in body["bundle"]["entry"]}
    assert "Claim" in resource_types


def test_api_convert_resolves_edi_837d_message_end_to_end():
    # Proves the ST03-based dispatch actually routes an 837D sample to
    # Edi837dBuilder through the real /api/convert endpoint, not just in
    # unit tests calling the builder directly.
    response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_837d_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    resource_types = {e["resource"]["resourceType"] for e in body["bundle"]["entry"]}
    assert "Claim" in resource_types


def test_api_convert_edi_malformed_returns_parse_error():
    response = client.post("/api/convert", json={"hl7_text": read_fixture("edi_malformed.x12")})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Parse error"


def test_api_validate_resolves_edi_message_end_to_end():
    # Proves the format-sniff in app/pipeline.py::validate_any actually
    # routes X12 through the same /api/validate endpoint as HL7v2/CDA, not
    # just that the EDI validator works in isolation.
    response = client.post("/api/validate", json={"hl7_text": read_fixture("edi_270_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["is_valid"] is True
    assert body["report"]["message_type"] == "EDI"
    assert body["report"]["trigger_event"] == "270"


def test_api_validate_edi_malformed_returns_parse_error():
    response = client.post("/api/validate", json={"hl7_text": read_fixture("edi_malformed.x12")})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Parse error"


def test_form_validate_renders_edi_report_in_page():
    response = client.post("/validate", data={"hl7_text": read_fixture("edi_270_basic.x12")})
    assert response.status_code == 200
    assert "is_valid" in response.text
    assert "true" in response.text


def test_index_message_type_dropdown_includes_edi():
    response = client.get("/")
    assert response.status_code == 200
    assert "EDI^270 - Eligibility Inquiry" in response.text
    assert "EDI^271 - Eligibility Response" in response.text
    assert "EDI^276 - Claim Status Request" in response.text
    assert "EDI^277 - Claim Status Response" in response.text
    assert "EDI^278 - Prior Authorization Request" in response.text
    assert "EDI^278 - Prior Authorization Response" in response.text
    assert "EDI^835 - Remittance Advice" in response.text
    assert "EDI^837P - Professional Claim" in response.text
    assert "EDI^837I - Institutional Claim" in response.text
    assert "EDI^837D - Dental Claim" in response.text


@pytest.mark.parametrize(
    "trigger_event, expected_resource_type",
    [
        ("270", "CoverageEligibilityRequest"),
        ("271", "CoverageEligibilityResponse"),
        ("276", "Task"),
        ("277", "Task"),
        ("278REQUEST", "Claim"),
        # Not "278RESPONSE" -> ClaimResponse here: the generator
        # deliberately omits HCR ~15% of the time (see
        # tests/test_generate_prior_auth.py), so ClaimResponse isn't
        # guaranteed for a single unseeded sample - only Claim is.
        ("278RESPONSE", "Claim"),
        ("835", "PaymentReconciliation"),
        ("837P", "Claim"),
        ("837I", "Claim"),
        ("837D", "Claim"),
    ],
)
def test_api_generate_edi_returns_convertible_and_valid_message(trigger_event, expected_resource_type):
    # Full-stack smoke test for the EDI generators, mirroring
    # test_api_generate_cda_returns_convertible_and_valid_document: generated
    # text must itself round-trip through /api/convert and /api/validate.
    response = client.get("/api/generate", params={"message_type": "EDI", "trigger_event": trigger_event})
    assert response.status_code == 200
    x12_text = response.json()["hl7_text"]
    assert x12_text

    convert_response = client.post("/api/convert", json={"hl7_text": x12_text})
    assert convert_response.status_code == 200
    resource_types = {e["resource"]["resourceType"] for e in convert_response.json()["bundle"]["entry"]}
    assert expected_resource_type in resource_types

    validate_response = client.post("/api/validate", json={"hl7_text": x12_text})
    assert validate_response.status_code == 200
    assert validate_response.json()["report"]["is_valid"] is True


def test_api_generate_edi_is_reproducible_with_seed():
    first = client.get("/api/generate", params={"message_type": "EDI", "trigger_event": "270", "seed": 5})
    second = client.get("/api/generate", params={"message_type": "EDI", "trigger_event": "270", "seed": 5})
    assert first.json()["hl7_text"] == second.json()["hl7_text"]
