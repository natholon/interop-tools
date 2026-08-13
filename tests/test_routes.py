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
