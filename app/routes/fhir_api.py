"""Endpoints that operate on a FHIR Bundle the caller already has.

Both capabilities existed before this module and neither was reachable on
its own: `check_bundle` ran only as a side effect of `/api/convert`, and
`deduplicate_bundle` only as an opt-in flag on it. That made them useless
to anyone holding a Bundle from somewhere else - another vendor's mapper,
an EHR export, a hand-written test case - which is exactly the audience
for a conformance check.

Plus `/api/capabilities`, so an integrator can discover what this
instance supports rather than reading prose. The registries answer it, so
it cannot drift from what the app actually does the way a documented list
does.
"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fhir.resources.R4B.bundle import Bundle
from pydantic import ValidationError

from app.dedup import deduplicate_bundle
from app.fhir_conformance.checker import check_bundle
from app.fhir_conformance.tables import REQUIRED_BINDINGS, REQUIRED_ELEMENTS, UNCHECKED_BINDINGS
from app.generators.registry import list_supported_types
from app.request_limits import MAX_REQUEST_BYTES
from app.routes.source_body import SourceBodyError, query_flag, read_source_text
from app.transform.registry import list_supported_targets
from app.version import VERSION

router = APIRouter()


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": {"category": "Bad request", "message": message}})


async def _read_bundle(request: Request):
    """The Bundle from a JSON body, either bare or under `bundle`.

    Bare is what a caller piping another tool's output will send; the
    wrapped shape matches what `/api/convert` returns, so a response can
    be fed straight back in.
    """
    try:
        text, payload = await read_source_text(request, json_field="bundle_json")
    except SourceBodyError:
        # Not the {"bundle_json": "..."} shape - try the body as the
        # Bundle itself, which is the more natural call here.
        raw = await request.body()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as exc:
            return None, _bad_request(f"Body is not valid JSON: {exc}")
        document = payload.get("bundle", payload) if isinstance(payload, dict) else payload
    else:
        try:
            document = json.loads(text)
        except ValueError as exc:
            return None, _bad_request(f"bundle_json is not valid JSON: {exc}")

    if not isinstance(document, dict):
        return None, _bad_request("Body must be a FHIR Bundle object.")
    if document.get("resourceType") != "Bundle":
        return None, _bad_request(
            f"Expected a Bundle, got {document.get('resourceType') or 'no resourceType'}."
        )
    try:
        return Bundle(**document), None
    except ValidationError as exc:
        return None, _bad_request(f"Not a valid FHIR R4 Bundle: {exc.error_count()} problem(s). {exc.errors()[:3]}")


@router.post("/api/fhir/conformance")
async def conformance_api(request: Request):
    """Check a Bundle against FHIR R4 required cardinality, required
    bindings and invariants.

    `?narrative=true` also reports `dom-6`, which is off by default
    because it holds for every resource this app builds - see
    `app/fhir_conformance/checker.py`.
    """
    bundle, error = await _read_bundle(request)
    if error is not None:
        return error
    report = check_bundle(bundle, include_narrative_warning=query_flag(request, "narrative", {}))
    return JSONResponse(content={"conformance": json.loads(report.model_dump_json(exclude_none=True))})


@router.post("/api/fhir/deduplicate")
async def deduplicate_api(request: Request):
    """Merge duplicate Patient/Practitioner/Organization/Location
    resources within a Bundle, returning the merged Bundle and what was
    merged into what."""
    bundle, error = await _read_bundle(request)
    if error is not None:
        return error
    result = deduplicate_bundle(bundle)
    return JSONResponse(
        content={
            "bundle": json.loads(result.bundle.model_dump_json(exclude_none=True)),
            "deduplication": {
                "resources_merged": result.merged_count,
                "merges": [
                    {"resource_type": m.resource_type, "kept_id": m.kept_id, "removed_ids": list(m.removed_ids)}
                    for m in result.merges
                ],
            },
        }
    )


@router.get("/api/capabilities")
async def capabilities_api():
    """What this instance supports, answered by the registries themselves.

    A documented list goes stale; this cannot, because it is the same
    source the converter dispatches on.
    """
    return JSONResponse(
        content={
            "version": VERSION,
            "convert": [
                {"message_type": mt, "trigger_event": te, "label": label}
                for mt, te, label in list_supported_types()
            ],
            "transform_targets": [
                {"target_format": fmt, "target_type": typ, "target_trigger": trig}
                for fmt, typ, trig in list_supported_targets()
            ],
            "conformance": {
                "resource_types_checked": sorted(REQUIRED_ELEMENTS),
                "required_bindings_checked": len(REQUIRED_BINDINGS),
                # Named rather than silently skipped: both need a
                # terminology server this app does not have.
                "bindings_not_checked": UNCHECKED_BINDINGS,
            },
            "limits": {
                "max_request_bytes": MAX_REQUEST_BYTES,
                # Disclosed rather than discovered at runtime: a batch
                # file converts its first message only.
                "messages_per_request": 1,
            },
        }
    )
