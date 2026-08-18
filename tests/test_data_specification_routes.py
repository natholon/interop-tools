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


def test_api_data_specification_response_includes_highlighting_payload():
    # See app/provenance/highlighting.py - the correlated source<->FHIR
    # span data the Data Specification page's side-by-side view renders.
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    body = response.json()
    highlighting = body["highlighting"]
    assert highlighting["display_source_text"].startswith("MSH|")
    assert highlighting["fhir_json_text"]
    assert len(highlighting["matches"]) == len(body["report"]["entries"])

    matches_by_path = {m["fhir_path"]: m for m in highlighting["matches"]}
    family_match = matches_by_path["Bundle.entry[0].resource.name[0].family"]
    assert family_match["color_index"] is not None
    start, end = family_match["source_span"]
    assert highlighting["display_source_text"][start:end] == "Doe"
    fhir_start, fhir_end = family_match["fhir_span"]
    assert highlighting["fhir_json_text"][fhir_start:fhir_end] == '"Doe"'
    assert family_match["fhir_token_type"] == "string"

    status_match = matches_by_path["Bundle.entry[1].resource.status"]
    # exclude_none omits null fields entirely, matching every other JSON
    # response in this app.
    assert "color_index" not in status_match
    assert "source_span" not in status_match


def test_api_data_specification_837d_type_is_now_instrumented():
    # 837D is instrumented as of this slice - the fifth and last EDI family,
    # completing full "big five" HIPAA EDI breadth for the Data
    # Specification pillar (see app/provenance/dispatch.py's own
    # _INSTRUMENTED_TRANSACTION_SETS - every EDI family this app converts is
    # now a member, so there is no longer a "converts but unsupported" EDI
    # example left to test against; only C-CDA input still exercises that
    # path, see the test below).
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_837d_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "837D"
    assert len(report["entries"]) > 0


def test_api_data_specification_270_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_270_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "270"
    assert len(report["entries"]) > 0


def test_api_data_specification_271_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_271_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "271"
    assert len(report["entries"]) > 0


def test_api_data_specification_278_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_278_request_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "278"
    assert len(report["entries"]) > 0


def test_api_data_specification_835_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_835_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "835"
    assert len(report["entries"]) > 0


def test_api_data_specification_837p_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_837p_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "837P"
    assert len(report["entries"]) > 0


def test_api_data_specification_837i_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_837i_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "837I"
    assert len(report["entries"]) > 0


def test_api_data_specification_276_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_276_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "276"
    assert len(report["entries"]) > 0


def test_api_data_specification_277_type_is_now_instrumented():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("edi_277_basic.x12")})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "EDI"
    assert report["trigger_event"] == "277"
    assert len(report["entries"]) > 0


def test_api_data_specification_cda_input_converts_but_is_unsupported():
    # C-CDA's document header + Problems section are instrumented (see
    # app/cda/common.py/app/cda/problems.py), so ccd_basic.xml (which
    # carries both) now produces real, non-empty partial entries - but the
    # report stays unsupported=True until every CCD section is
    # instrumented, mirroring the identical "some real facts, still
    # unsupported" shape a not-yet-instrumented HL7v2 message type already
    # produces (see app/provenance/dispatch.py's own _CDA_UNSUPPORTED_REASON).
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("ccd_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    report = body["report"]
    assert report["unsupported"] is True
    assert report["source_format"] == "CDA"
    assert report["message_type"] == "CDA"
    assert report["trigger_event"] == "CCD"
    assert len(report["entries"]) > 0
    assert "C-CDA" in report["unsupported_reason"]


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


def test_api_data_specification_without_deduplicate_omits_deduplication_key():
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("adt_a01_basic.hl7")})
    assert response.status_code == 200
    assert "deduplication" not in response.json()


def test_api_data_specification_with_deduplicate_reports_zero_merges_when_nothing_to_merge():
    response = client.post(
        "/api/data-specification", json={"hl7_text": read_fixture("adt_a01_basic.hl7"), "deduplicate": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deduplication"]["resources_merged"] == 0
    assert body["deduplication"]["merges"] == []
    # A genuine no-op: the crosswalk itself is unaffected when nothing
    # merges - see tests/test_highlighting.py's own equivalent module-level
    # assertion for the fuller entry-by-entry proof.
    plain = client.post("/api/data-specification", json={"hl7_text": read_fixture("adt_a01_basic.hl7")}).json()
    assert len(body["report"]["entries"]) == len(plain["report"]["entries"])


def test_api_data_specification_with_deduplicate_merges_837p_billing_and_rendering_provider():
    # The real motivating case for app/dedup.py itself, reused here - see
    # tests/test_highlighting.py's own equivalent test for the fuller
    # provenance-specific assertion detail (which resource's facts survive).
    response = client.post(
        "/api/data-specification", json={"hl7_text": read_fixture("edi_837p_basic.x12"), "deduplicate": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deduplication"]["resources_merged"] == 1
    practitioners = [e for e in body["bundle"]["entry"] if e["resource"]["resourceType"] == "Practitioner"]
    assert len(practitioners) == 1

    # Every crosswalk entry and every highlighting match must still point
    # at a real Bundle.entry[N] - none left dangling at an index that
    # belonged to the now-removed duplicate.
    entry_count = len(body["bundle"]["entry"])
    for entry in body["report"]["entries"]:
        if entry["fhir_path"].startswith("Bundle.entry["):
            index = int(entry["fhir_path"][len("Bundle.entry[") : entry["fhir_path"].index("]")])
            assert index < entry_count


def test_form_data_specification_with_deduplicate_checkbox_renders_summary():
    response = client.post(
        "/data-specification", data={"hl7_text": read_fixture("edi_837p_basic.x12"), "deduplicate": "on"}
    )
    assert response.status_code == 200
    assert "Merged 1 duplicate resource" in response.text


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
