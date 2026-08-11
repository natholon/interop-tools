from pathlib import Path

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


def test_index_renders_message_type_dropdown():
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="message-type-select"' in response.text
    assert "ADT^A01 - Admit" in response.text
    assert "SIU^S12 - New Appointment" in response.text


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
