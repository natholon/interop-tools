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
