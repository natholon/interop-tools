"""The request body ceiling."""

import json

from fastapi.testclient import TestClient

from app.main import app
from app.request_limits import MAX_REQUEST_BYTES

client = TestClient(app)

_MSH = "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|1|P|2.5\r"


def _padded(extra_bytes: int) -> str:
    return _MSH + "X" * extra_bytes


def test_a_normal_message_is_unaffected():
    assert client.post("/api/validate", json={"hl7_text": _MSH}).status_code == 200


def test_a_body_just_under_the_cap_is_accepted():
    body = _padded(MAX_REQUEST_BYTES - len(_MSH) - 200)
    assert len(json.dumps({"hl7_text": body})) < MAX_REQUEST_BYTES
    assert client.post("/api/validate", json={"hl7_text": body}).status_code == 200


def test_a_body_over_the_cap_is_rejected_with_a_readable_error():
    # 413 with a body the caller can read, not a dropped connection -
    # answering before the client has finished writing makes the page
    # report "Network error" instead of what actually happened.
    response = client.post("/api/validate", json={"hl7_text": _padded(MAX_REQUEST_BYTES)})
    assert response.status_code == 413
    assert response.json()["error"]["category"] == "Request too large"
    assert "2MB" in response.json()["error"]["message"]


def test_an_oversized_body_never_reaches_a_route():
    # The point is CPU, not correctness: a 4.5MB message took ~79s to
    # convert. An oversized body must be refused before anything parses
    # it, so the error is the size, never a parse failure.
    response = client.post("/api/convert", json={"hl7_text": _padded(4 * 1024 * 1024)})
    assert response.status_code == 413
    assert "Parse error" not in json.dumps(response.json())


def test_every_body_carrying_endpoint_is_covered():
    oversized = {"hl7_text": _padded(MAX_REQUEST_BYTES)}
    for path in ("/api/convert", "/api/validate", "/api/data-specification", "/api/source-index"):
        assert client.post(path, json=oversized).status_code == 413, path


def test_a_file_upload_over_the_cap_is_rejected_too():
    # The multipart path shares the same body, so it shares the ceiling.
    files = {"hl7_file": ("big.hl7", _padded(MAX_REQUEST_BYTES).encode(), "text/plain")}
    assert client.post("/validate", files=files).status_code == 413


def test_get_requests_are_untouched():
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/generate", params={"message_type": "ADT", "trigger_event": "A01"}).status_code == 200
