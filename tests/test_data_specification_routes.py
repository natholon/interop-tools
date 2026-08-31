from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
client = TestClient(app)


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_crosswalk_form_renders_on_the_index_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "crosswalk-form" in response.text
    assert "hl7_text" in response.text


def test_data_specification_url_redirects_to_the_index_page():
    # The crosswalk moved onto the one page; this route survives only so
    # existing links/bookmarks still land somewhere useful.
    response = client.get("/data-specification", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/"


def test_static_asset_urls_are_cache_busted():
    # See app/routes/static_assets.py - app/routes/convert.py and
    # app/routes/data_specification.py each build their own Jinja2Templates
    # instance, so the global needed registering on both, not just one.
    response = client.get("/")
    assert 'src="/static/app.js?v=' in response.text
    assert 'href="/static/style.css?v=' in response.text


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


def test_api_data_specification_cda_input_is_now_fully_instrumented():
    # Every section registered in app/cda/registry.py::SECTION_BUILDERS is
    # instrumented, so C-CDA document types now graduate to
    # unsupported=False - this previously asserted the opposite, held back
    # by author -> a FHIR Provenance resource, which has since been
    # reclassified as a deliberate scope decision rather than deferred
    # work (Provenance models an audit trail this stateless converter has
    # no record lifecycle for; see app/provenance/dispatch.py's own
    # _INSTRUMENTED_CDA_DOCUMENT_TYPES comment). A deliberate decision not
    # to map something has never counted against coverage here - MDM
    # leaves TXA-13/TXA-17 unmapped and still reports unsupported=False.
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture("ccd_basic.xml")})
    assert response.status_code == 200
    body = response.json()
    assert body["bundle"]["resourceType"] == "Bundle"
    report = body["report"]
    assert report["unsupported"] is False
    assert report["source_format"] == "CDA"
    assert report["message_type"] == "CDA"
    assert report["trigger_event"] == "CCD"
    assert len(report["entries"]) > 0
    # exclude_none=True drops the key entirely when there's no reason.
    assert "unsupported_reason" not in report


@pytest.mark.parametrize("fixture,document_type", [
    ("discharge_summary_basic.xml", "DISCHARGESUMMARY"),
    ("history_and_physical_basic.xml", "HISTORYANDPHYSICAL"),
])
def test_api_data_specification_other_cda_document_types_are_instrumented(fixture, document_type):
    response = client.post("/api/data-specification", json={"hl7_text": read_fixture(fixture)})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["unsupported"] is False
    assert report["trigger_event"] == document_type
    assert len(report["entries"]) > 0


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


def test_rejecting_a_decision_changes_the_displayed_bundle_too():
    """The pane a reviewer looks at is highlighting.fhir_json_text, which
    was built from the model before rejections were applied to the
    serialized dict - so rejecting a value changed the returned bundle but
    not the Bundle on screen, and read as doing nothing."""
    raw = read_fixture("adt_a01_basic.hl7")
    baseline = client.post("/api/data-specification", json={"hl7_text": raw}).json()
    encounter = next(
        e["resource"] for e in baseline["bundle"]["entry"] if e["resource"]["resourceType"] == "Encounter"
    )
    assert encounter["status"] == "in-progress"
    decision = next(
        d for d in baseline["decisions"]
        if d["kind"] == "inferred" and d.get("fhir_path", "").endswith("resource.status")
    )

    rejected = client.post(
        "/api/data-specification",
        json={"hl7_text": raw, "rejected_decision_ids": [decision["id"]]},
    ).json()
    encounter = next(
        e["resource"] for e in rejected["bundle"]["entry"] if e["resource"]["resourceType"] == "Encounter"
    )
    assert encounter["status"] == "unknown"
    assert rejected["rejection_outcomes"][0]["applied"] is True
    # The displayed Bundle must agree with the returned one.
    assert '"status": "unknown"' in rejected["highlighting"]["fhir_json_text"]
    assert '"status": "in-progress"' not in rejected["highlighting"]["fhir_json_text"]


def test_crosswalk_page_has_no_separate_decision_register_block():
    """Inferred decisions render beside the conversion they produced, and
    dropped ones as their own crosswalk rows with the FHIR columns empty -
    so the separate block above the table has nothing left to show, and
    keeping it would list everything twice."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="decision-register"' not in response.text
    assert 'id="decision-list"' not in response.text
    # The table is where all of it lives now.
    assert "Specification Crosswalk" in response.text
    assert "<th>Mapping Decision</th>" in response.text


def test_api_source_index_names_the_field_at_each_offset():
    # Powers the editor's hover hints: no conversion involved, so it works
    # on a message that is still being typed.
    response = client.post(
        "/api/source-index", json={"hl7_text": read_fixture("adt_a01_basic.hl7")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_format"] == "HL7v2"
    by_path = {p["path"]: p for p in body["positions"]}
    visit = by_path["PV1-19"]
    assert visit["label"] == "Visit Number"
    assert body["display_text"][visit["start"]:visit["end"]] == "V0001"


@pytest.mark.parametrize(
    "raw,expected_format",
    [
        (read_fixture("ccd_basic.xml"), "CDA"),
        (read_fixture("edi_270_basic.x12"), "EDI"),
    ],
)
def test_api_source_index_covers_every_input_format(raw, expected_format):
    body = client.post("/api/source-index", json={"hl7_text": raw}).json()
    assert body["source_format"] == expected_format
    assert body["positions"]


def test_api_source_index_still_names_what_a_half_typed_message_has():
    # Editing is exactly when a message is incomplete, so a partial one
    # gets whatever answer it can rather than an error - here the segment
    # itself and the encoding characters, which are all that is typed.
    response = client.post("/api/source-index", json={"hl7_text": "MSH|^~"})
    assert response.status_code == 200
    assert {p["path"] for p in response.json()["positions"]} == {"MSH", "MSH-2[0]"}


def test_api_source_index_has_nothing_to_say_about_empty_text():
    response = client.post("/api/source-index", json={"hl7_text": ""})
    assert response.status_code == 200
    assert response.json()["positions"] == []
