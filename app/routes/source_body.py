"""Accepting a source message as a raw request body, not only as JSON.

A caller holding `message.hl7` on disk should be able to post it:

    curl --data-binary @message.hl7 -H 'Content-Type: text/plain' .../api/convert

Requiring `{"hl7_text": "..."}` means JSON-encoding the file first, which
is friction on the very first request anyone makes - and HL7v2 is
especially awkward, since every segment separator has to be escaped.

The JSON shape stays the primary one: it is what the page itself posts,
what `/docs` documents, and the only shape that carries options like
`deduplicate`. A raw body takes its options from the query string
instead.

**Content type decides, not sniffing.** `application/json` is parsed as
the documented model; anything else is taken as the message verbatim.
Guessing from the bytes would collide with the format sniffing
`app/pipeline.py` already does - a JSON body and an X12 interchange are
both plausible text, and only the caller knows which they meant.
"""

import json

from fastapi import Request


class SourceBodyError(ValueError):
    """The body could not be read as a source message."""


async def read_source_text(request: Request, json_field: str = "hl7_text") -> tuple[str, dict]:
    """`(message text, the JSON body if there was one)`.

    The second half lets a caller still read options like `deduplicate`
    from a JSON body, while a raw body falls back to query parameters.
    """
    raw = await request.body()
    content_type = request.headers.get("content-type", "")

    if content_type.split(";")[0].strip() == "application/json":
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as exc:
            raise SourceBodyError(f"Body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceBodyError("Body must be a JSON object.")
        text = payload.get(json_field)
        if not isinstance(text, str):
            raise SourceBodyError(f"Body must carry a {json_field!r} string.")
        return text, payload

    # Anything else is the message itself. utf-8 with replacement rather
    # than a hard failure: a mis-encoded byte should surface as a parse
    # error naming the segment, not as a 400 about encoding.
    return raw.decode("utf-8", errors="replace"), {}


def query_flag(request: Request, name: str, payload: dict) -> bool:
    """A boolean option from the JSON body, else the query string."""
    if name in payload:
        return bool(payload[name])
    return request.query_params.get(name, "").lower() in ("1", "true", "yes")
