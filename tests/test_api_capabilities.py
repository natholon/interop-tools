"""The API surface an integrator uses: discovery, raw bodies, and the two
Bundle operations that were previously only side effects of converting."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.fhir_conformance.tables import REQUIRED_BINDINGS, REQUIRED_ELEMENTS
from app.generators.registry import list_supported_types
from app.request_limits import MAX_REQUEST_BYTES
from app.transform.registry import list_supported_targets
from app.main import app
from app.version import VERSION

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures"


def _bundle():
    response = client.post("/api/convert", json={"hl7_text": (FIXTURES / "adt_a01_basic.hl7").read_text()})
    return response.json()["bundle"]


# --- discovery -------------------------------------------------------


def test_healthz_reports_the_running_version():
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"] == VERSION


def test_capabilities_are_answered_by_the_registries_not_a_list():
    # The point of the endpoint: it cannot drift from what the converter
    # dispatches on, because it is the same source.
    caps = client.get("/api/capabilities").json()
    assert len(caps["convert"]) == len(list_supported_types())
    assert len(caps["transform_targets"]) == len(list_supported_targets())
    assert {(c["message_type"], c["trigger_event"]) for c in caps["convert"]} == {
        (mt, te) for mt, te, _ in list_supported_types()
    }


def test_capabilities_disclose_the_limits_that_bite():
    caps = client.get("/api/capabilities").json()
    assert caps["limits"]["max_request_bytes"] == MAX_REQUEST_BYTES
    # A batch file converts its first message only - discoverable rather
    # than found out.
    assert caps["limits"]["messages_per_request"] == 1


def test_capabilities_name_what_conformance_cannot_check():
    conformance = client.get("/api/capabilities").json()["conformance"]
    assert conformance["resource_types_checked"] == sorted(REQUIRED_ELEMENTS)
    assert conformance["required_bindings_checked"] == len(REQUIRED_BINDINGS)
    assert set(conformance["bindings_not_checked"]) == {"Binary.contentType", "Composition.confidentiality"}


# --- raw bodies ------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,content_type",
    [
        ("adt_a01_basic.hl7", "text/plain"),
        ("ccd_basic.xml", "application/xml"),
        ("edi_270_basic.x12", "application/edi-x12"),
    ],
)
def test_a_message_can_be_posted_as_a_raw_body(fixture, content_type):
    # curl --data-binary @message.hl7 is the natural call; requiring
    # {"hl7_text": "..."} means JSON-encoding the file first.
    raw = (FIXTURES / fixture).read_text()
    response = client.post("/api/convert", content=raw, headers={"Content-Type": content_type})
    assert response.status_code == 200
    assert response.json()["bundle"]["entry"]


def test_a_raw_body_and_a_json_body_produce_the_same_bundle():
    raw = (FIXTURES / "adt_a01_basic.hl7").read_text()
    as_json = client.post("/api/convert", json={"hl7_text": raw}).json()["bundle"]
    as_raw = client.post("/api/convert", content=raw, headers={"Content-Type": "text/plain"}).json()["bundle"]
    # Resource ids are fresh uuids per conversion, so compare shape.
    assert [e["resource"]["resourceType"] for e in as_json["entry"]] == [
        e["resource"]["resourceType"] for e in as_raw["entry"]
    ]


def test_options_come_from_the_query_string_for_a_raw_body():
    raw = (FIXTURES / "edi_837p_basic.x12").read_text()
    plain = client.post("/api/convert", content=raw, headers={"Content-Type": "text/plain"})
    deduped = client.post(
        "/api/convert?deduplicate=true", content=raw, headers={"Content-Type": "text/plain"}
    )
    assert "deduplication" not in plain.json()
    assert "deduplication" in deduped.json()


def test_validate_accepts_a_raw_body_too():
    raw = (FIXTURES / "adt_a01_basic.hl7").read_text()
    response = client.post("/api/validate", content=raw, headers={"Content-Type": "text/plain"})
    assert response.status_code == 200
    assert response.json()["report"]["is_valid"] is True


def test_a_malformed_json_body_is_a_bad_request_not_a_parse_error():
    # Content-Type decides how the body is read, so a broken JSON body
    # must not be silently treated as an HL7v2 message.
    response = client.post("/api/convert", content=b"{oops", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Bad request"


# --- Bundle operations -----------------------------------------------


def test_conformance_checks_a_bundle_the_caller_already_has():
    response = client.post("/api/fhir/conformance", json=_bundle())
    assert response.status_code == 200
    assert response.json()["conformance"]["is_valid"] is True


def test_conformance_reports_a_code_outside_its_required_binding():
    bundle = _bundle()
    encounter = next(e for e in bundle["entry"] if e["resource"]["resourceType"] == "Encounter")
    encounter["resource"]["status"] = "banana"
    findings = client.post("/api/fhir/conformance", json=bundle).json()["conformance"]["findings"]
    assert any(f["rule_id"] == "fhir.code-outside-required-binding" for f in findings)


def test_a_convert_response_can_be_fed_straight_back_in():
    # The wrapped shape /api/convert returns, not just a bare Bundle.
    converted = client.post(
        "/api/convert", json={"hl7_text": (FIXTURES / "adt_a01_basic.hl7").read_text()}
    ).json()
    assert client.post("/api/fhir/conformance", json=converted).status_code == 200


def test_deduplicate_operates_on_a_bundle_directly():
    response = client.post("/api/fhir/deduplicate", json=_bundle())
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    assert "resources_merged" in body["deduplication"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"resourceType": "Patient"}, "Expected a Bundle"),
        ({}, "Expected a Bundle"),
        ({"resourceType": "Bundle"}, "Not a valid FHIR R4 Bundle"),
    ],
)
def test_a_body_that_is_not_a_bundle_is_refused_with_a_reason(payload, expected):
    response = client.post("/api/fhir/conformance", json=payload)
    assert response.status_code == 400
    assert expected in response.json()["error"]["message"]


def test_an_invalid_code_is_reported_rather_than_refused():
    # fhir.resources accepts any string in a required-binding field, so a
    # Bundle with a bogus type parses. Refusing it with a 400 would be
    # wrong: reporting exactly this is what the endpoint is for.
    response = client.post(
        "/api/fhir/conformance", json={"resourceType": "Bundle", "type": "not-a-real-type", "id": "x"}
    )
    assert response.status_code == 200
    report = response.json()["conformance"]
    assert report["is_valid"] is False
    assert any(f["rule_id"] == "fhir.code-outside-required-binding" for f in report["findings"])


def test_narrative_warning_is_opt_in_on_the_endpoint_too():
    bundle = _bundle()
    default = client.post("/api/fhir/conformance", json=bundle).json()["conformance"]["findings"]
    with_narrative = client.post("/api/fhir/conformance?narrative=true", json=bundle).json()["conformance"][
        "findings"
    ]
    assert not any("dom-6" in f["message"] for f in default)
    assert any("dom-6" in f["message"] for f in with_narrative)


def test_the_new_endpoints_are_size_capped_like_every_other():
    oversized = json.dumps({"resourceType": "Bundle", "type": "collection", "id": "x" * MAX_REQUEST_BYTES})
    for path in ("/api/fhir/conformance", "/api/fhir/deduplicate"):
        assert client.post(path, content=oversized, headers={"Content-Type": "application/json"}).status_code == 413, path
