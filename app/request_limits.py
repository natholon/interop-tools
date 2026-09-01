"""A ceiling on request body size.

**Why availability, not correctness.** Parsing cost scales with message
size and this app does real work per segment: measured end to end through
`/api/convert`, a 45KB ORU took 0.6s, an 898KB one 11.3s, and a 4.5MB one
**79 seconds** - one request holding one worker for over a minute.
Nothing bounded the body, so a handful of concurrent large-but-perfectly-
valid messages would take the app down with no exploit involved.

2MB is far above any real HL7v2 message, C-CDA document or X12
interchange, and caps the worst case at roughly 35 seconds.

Two things this deliberately does the slower way:

- **The body is counted, not trusted.** `Content-Length` is a client
  claim and can be omitted entirely, so the bytes are counted as they
  arrive.
- **An oversized body is drained before the 413 is sent.** Answering the
  moment the limit is passed leaves the client still writing, and it sees
  a connection abort rather than a response it can read - the page would
  report "Network error" instead of saying what actually happened.
  Draining costs bandwidth, which is far cheaper than the CPU this is
  protecting.

Nothing over the limit ever reaches a route, so the body is never parsed.
"""

from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 2 * 1024 * 1024

_ERROR = {
    "error": {
        "category": "Request too large",
        "message": (
            f"The request body exceeds the {MAX_REQUEST_BYTES // (1024 * 1024)}MB limit. "
            "Real HL7v2 messages, C-CDA documents and X12 interchanges are far smaller; "
            "split a batch file and convert one message at a time."
        ),
    }
}


class LimitRequestSize:
    """Reject a request body over `max_bytes` with HTTP 413."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body = bytearray()
        over_limit = False
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if not over_limit:
                body += message.get("body", b"")
                if len(body) > self.max_bytes:
                    over_limit = True
                    # Stop retaining once the answer is decided; the rest
                    # is read and discarded so the client can still read
                    # the 413.
                    body = bytearray()
            more_body = message.get("more_body", False)

        if over_limit:
            await JSONResponse(_ERROR, status_code=413)(scope, receive, send)
            return

        # The body was consumed here, so hand the route its own copy.
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)
