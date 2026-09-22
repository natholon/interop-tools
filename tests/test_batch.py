"""Converting a file that holds more than one message."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.batch import MAX_BATCH_MESSAGES, count_messages, iter_conversions, split_hl7_messages
from app.main import app

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures"

_ADT = (FIXTURES / "adt_a01_basic.hl7").read_text()
_ORU = (FIXTURES / "oru_r01_basic.hl7").read_text()
# A well-formed message for a type this app has no mapper for.
_UNMAPPABLE = "MSH|^~\\&|A|B|C|D|20260101120000||ZZZ^Q99|BAD|P|2.5\r"


def _lines(response):
    return [json.loads(line) for line in response.text.strip().split("\n")]


# --- splitting -------------------------------------------------------


def test_hl7v2_splits_on_every_msh():
    assert len(split_hl7_messages(_ADT + _ORU + _ADT)) == 3


def test_a_single_message_is_a_batch_of_one():
    assert len(split_hl7_messages(_ADT)) == 1
    assert count_messages(_ADT) == 1


def test_x12_counts_transaction_sets_not_interchanges():
    # Three ST/SE inside one ISA/GS - the shape a real 270 file takes.
    raw = (FIXTURES / "edi_270_batch_three_sets.x12").read_text()
    assert count_messages(raw) == 3


def test_a_cda_document_is_never_a_batch():
    # Several documents are several files; one document is one message.
    assert count_messages((FIXTURES / "ccd_basic.xml").read_text()) == 1


def test_counting_does_not_convert():
    # The cap has to reject an oversized batch before any work is done,
    # so counting must be cheap and must not raise on content it cannot
    # map.
    assert count_messages(_UNMAPPABLE * 3) == 3


# --- per-message outcomes --------------------------------------------


def test_one_unconvertible_message_does_not_lose_the_others():
    items = list(iter_conversions(_ADT + _UNMAPPABLE + _ORU))
    assert [i.index for i in items] == [0, 1, 2]
    assert items[0].bundle is not None
    assert items[1].bundle is None and items[1].error_category == "Mapping error"
    assert items[2].bundle is not None


def test_each_message_converts_independently():
    raw = (FIXTURES / "edi_270_batch_three_sets.x12").read_text()
    members = []
    for item in iter_conversions(raw):
        patients = [e.resource for e in item.bundle.entry if e.resource.get_resource_type() == "Patient"]
        members += [p.identifier[0].value for p in patients if p.identifier]
    assert members == ["MEMBERID001", "MEMBERID002", "MEMBERID003"]


# --- the endpoint ----------------------------------------------------


def test_the_stream_is_ndjson_with_one_object_per_message():
    response = client.post(
        "/api/convert/batch", content=_ADT + _ORU, headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = _lines(response)
    assert [line["index"] for line in lines[:-1]] == [0, 1]
    assert all(line["bundle"]["resourceType"] == "Bundle" for line in lines[:-1])


def test_a_failure_is_a_line_not_a_dropped_request():
    # Bulk Data's bare-resource NDJSON has nowhere to say message 1
    # failed, which is why every line is an envelope.
    lines = _lines(
        client.post("/api/convert/batch", content=_ADT + _UNMAPPABLE, headers={"Content-Type": "text/plain"})
    )
    assert lines[0]["status"] == "ok"
    assert lines[1]["status"] == "error"
    assert lines[1]["error"]["category"] == "Mapping error"


def test_the_last_line_summarises_the_batch():
    lines = _lines(
        client.post(
            "/api/convert/batch",
            content=_ADT + _UNMAPPABLE + _ORU,
            headers={"Content-Type": "text/plain"},
        )
    )
    assert lines[-1]["summary"] == {"messages": 3, "converted": 2, "failed": 1}


@pytest.mark.parametrize(
    "fixture,content_type",
    [
        ("edi_270_batch_three_sets.x12", "application/edi-x12"),
        ("ccd_basic.xml", "application/xml"),
        ("adt_a01_basic.hl7", "text/plain"),
    ],
)
def test_every_format_reaches_the_batch_endpoint(fixture, content_type):
    response = client.post(
        "/api/convert/batch",
        content=(FIXTURES / fixture).read_text(),
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200
    assert _lines(response)[-1]["summary"]["failed"] == 0


def test_a_batch_over_the_cap_is_refused_before_converting_anything():
    response = client.post(
        "/api/convert/batch",
        content=_ADT * (MAX_BATCH_MESSAGES + 1),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["category"] == "Batch too large"
    # The real count, so the caller knows how far over they are.
    assert f"{MAX_BATCH_MESSAGES + 1:,}" in response.json()["error"]["message"]


def test_the_count_can_be_asked_for_without_converting():
    response = client.post(
        "/api/validate/batch", content=_ADT * 12, headers={"Content-Type": "text/plain"}
    )
    assert response.json() == {"messages": 12, "max_messages": MAX_BATCH_MESSAGES, "within_limit": True}


def test_convert_still_takes_only_the_first_message():
    # The batch endpoint is opt-in; /api/convert's contract is unchanged.
    response = client.post("/api/convert", content=_ADT + _ORU, headers={"Content-Type": "text/plain"})
    assert response.status_code == 200
    assert response.json()["bundle"]["resourceType"] == "Bundle"


def test_capabilities_report_the_batch_limit():
    limits = client.get("/api/capabilities").json()["limits"]
    assert limits["max_batch_messages"] == MAX_BATCH_MESSAGES
    assert limits["messages_per_request"] == 1


# --- disclosing a batch to the single-message endpoints ---------------


def test_a_single_message_carries_no_batch_notice():
    # So an ordinary response is byte-for-byte unchanged.
    for path in ("/api/convert", "/api/data-specification"):
        assert "batch" not in client.post(path, json={"hl7_text": _ADT}).json(), path


def test_a_batch_posted_to_convert_says_how_many_were_skipped():
    # One message's Bundle and no sign the other two existed was the one
    # place "disclosed rather than silent" was silent.
    body = client.post("/api/convert", json={"hl7_text": _ADT + _ORU + _ADT}).json()
    assert body["batch"]["messages"] == 3
    assert body["batch"]["converted"] == 1
    assert "/api/convert/batch" in body["batch"]["note"]


def test_the_page_endpoint_discloses_a_batch_too():
    # What the page posts on Convert; it renders body["batch"]["note"].
    body = client.post("/api/data-specification", json={"hl7_text": _ADT + _ORU}).json()
    assert body["batch"]["messages"] == 2


def test_an_x12_batch_is_disclosed_by_transaction_set():
    raw = (FIXTURES / "edi_270_batch_three_sets.x12").read_text()
    assert client.post("/api/convert", json={"hl7_text": raw}).json()["batch"]["messages"] == 3
