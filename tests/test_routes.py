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
