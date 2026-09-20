"""Converting a batched file, one streamed result per message.

`/api/convert` keeps its one-input-one-Bundle contract; this is the
opt-in endpoint for a file holding many messages, so nothing about the
existing behaviour changes.

**Streamed, because the response is what binds.** HL7v2 amplifies about
11x into FHIR JSON, so 2MB of input becomes ~22MB of Bundles - measured,
not estimated. Accumulating that to serialize one array puts the peak in
memory and makes the caller wait for the last message before seeing the
first. NDJSON lets each Bundle go out as it is produced, which is why the
cap can be 5,000 messages rather than a few hundred.

**`application/x-ndjson`, not `application/fhir+ndjson`.** Bulk Data's
NDJSON is bare resources, one per line, which has nowhere to say that
message 300 failed. A batch of real traffic contains messages this app
cannot map, so every line is an envelope carrying either a Bundle or the
error, and the last line is a summary.

The generator is deliberately synchronous: Starlette runs a sync iterator
in a threadpool, so CPU-bound conversion does not block the event loop.
"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.batch import MAX_BATCH_MESSAGES, count_messages, iter_conversions
from app.routes.source_body import SourceBodyError, read_source_text

router = APIRouter()

NDJSON_MEDIA_TYPE = "application/x-ndjson"


@router.post("/api/convert/batch")
async def convert_batch_api(request: Request):
    """Convert every message in the body, streaming one JSON object per
    line: `{"index", "status", "bundle"}` or `{"index", "status",
    "error"}`, then a final `{"summary": ...}`.

    Accepts the same two body shapes as `/api/convert` - the documented
    JSON object, or the file itself as a raw body.
    """
    try:
        text, _payload = await read_source_text(request)
    except SourceBodyError as exc:
        return JSONResponse(
            status_code=400, content={"error": {"category": "Bad request", "message": str(exc)}}
        )

    # Counted before any conversion, so an oversized batch costs nothing
    # and the caller is told the real number rather than being cut off
    # partway through a stream.
    total = count_messages(text)
    if total > MAX_BATCH_MESSAGES:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "category": "Batch too large",
                    "message": (
                        f"The body holds {total:,} messages; the limit is "
                        f"{MAX_BATCH_MESSAGES:,} per request. Split the file."
                    ),
                }
            },
        )

    def stream():
        converted = failed = 0
        for item in iter_conversions(text):
            if item.bundle is not None:
                converted += 1
                line = {
                    "index": item.index,
                    "status": "ok",
                    "bundle": json.loads(item.bundle.model_dump_json(exclude_none=True)),
                }
            else:
                failed += 1
                line = {
                    "index": item.index,
                    "status": "error",
                    "error": {"category": item.error_category, "message": item.error_message},
                }
            yield json.dumps(line) + "\n"
        yield json.dumps({"summary": {"messages": converted + failed, "converted": converted, "failed": failed}}) + "\n"

    return StreamingResponse(stream(), media_type=NDJSON_MEDIA_TYPE)


@router.post("/api/validate/batch")
async def validate_batch_api(request: Request):
    """How many messages the body holds, without converting any.

    The cheap question a caller asks before posting a large file: is this
    within the limit, and how many messages does this app think it has?
    """
    try:
        text, _payload = await read_source_text(request)
    except SourceBodyError as exc:
        return JSONResponse(
            status_code=400, content={"error": {"category": "Bad request", "message": str(exc)}}
        )
    total = count_messages(text)
    return JSONResponse(
        content={
            "messages": total,
            "max_messages": MAX_BATCH_MESSAGES,
            "within_limit": total <= MAX_BATCH_MESSAGES,
        }
    )
