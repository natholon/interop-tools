from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
client = TestClient(app)


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_data_specification_page_renders():
    response = client.get("/data-specification")
    assert response.status_code == 200
    assert "crosswalk-form" in response.text
    assert "hl7_text" in response.text


def test_index_page_has_nav_linking_to_data_specification():
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/data-specification"' in response.text


def test_data_specification_page_has_nav_linking_back_to_index():
    response = client.get("/data-specification")
    assert response.status_code == 200
    assert 'href="/"' in response.text


def test_api_data_specification_adt_a01_returns_supported_report_with_entries():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    report = body["report"]
    assert report["unsupported"] is False
    assert report["message_type"] == "ADT"
    assert report["trigger_event"] == "A01"
    assert report["source_format"] == "HL7v2"
    assert len(report["entries"]) > 0

    family_entry = next(e for e in report["entries"] if e["fhir_path"] == "Bundle.entry[0].resource.name[0].family")
    assert family_entry["value"] == "Doe"
    assert family_entry["source_location"] == "PID-5[0].1"
    assert family_entry["derivation"] == "direct"

    status_entry = next(e for e in report["entries"] if e["fhir_path"] == "Bundle.entry[1].resource.status")
    assert status_entry["derivation"] == "inferred"
    # exclude_none=True on the JSON response omits null fields entirely
    # rather than serializing them as null.
    assert "source_location" not in status_entry
    assert status_entry["reason"]


def test_api_data_specification_edi_input_converts_but_is_unsupported():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_270_basic.x12")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    report = body["report"]
    assert report["unsupported"] is True
    assert report["source_format"] == "EDI"
    assert report["entries"] == []
    assert "X12 EDI" in report["unsupported_reason"]


def test_api_data_specification_cda_input_converts_but_is_unsupported():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("ccd_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    report = body["report"]
    assert report["unsupported"] is True
    assert report["source_format"] == "CDA"
    assert report["entries"] == []


def test_api_data_specification_siu_type_is_now_instrumented():
    # SIU is instrumented as of this slice - a regression test proving it
    # stays that way (mirrors the ADT^A01 assertions in the sibling test
    # above, at lighter depth since the full field-by-field crosswalk is
    # already covered by tests/test_provenance_recorder.py).
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("siu_s12_basic.hl7")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["message_type"] == "SIU"
    assert report["trigger_event"] == "S12"
    assert len(report["entries"]) > 0


def test_api_data_specification_oru_type_is_now_instrumented():
    # ORU is instrumented as of this slice - a regression test proving it
    # stays that way, mirroring the SIU regression test above.
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("oru_r01_basic.hl7")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["message_type"] == "ORU"
    assert report["trigger_event"] == "R01"
    assert len(report["entries"]) > 0


def test_api_data_specification_mdm_type_is_now_instrumented():
    # MDM is instrumented as of this slice - the fourth and last HL7v2
    # message type this app converts, completing full HL7v2 breadth for the
    # Data Specification pillar (see app/provenance/dispatch.py's own
    # _INSTRUMENTED_MESSAGE_TYPES - every registered HL7v2 message_type is
    # now a member, so there is no longer a "converts but unsupported"
    # HL7v2 example left to test against; only EDI/C-CDA input still
    # exercises that path, see the two tests above).
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("mdm_t02_basic.hl7")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["message_type"] == "MDM"
    assert report["trigger_event"] == "T02"
    assert len(report["entries"]) > 0


def test_api_data_specification_malformed_input_returns_400():
    response = client.post("/api/data-specification", json={"hl7_text": "not a real message"})
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "Parse error"


def test_data_specification_form_post_no_js_fallback_renders_result():
    response = client.post(
        "/data-specification",
        data={"hl7_text": read_fixture("adt_a01_basic.hl7")},
    )
    assert response.status_code == 200
    assert "Doe" in response.text


def test_data_specification_form_post_no_js_fallback_renders_error():
    response = client.post("/data-specification", data={"hl7_text": "not a real message"})
    assert response.status_code == 200
    assert "Parse error" in response.text
