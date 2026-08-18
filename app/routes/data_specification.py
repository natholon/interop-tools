"""Routes for the Data Specification pillar - the field-level provenance
crosswalk. Mirrors app/routes/convert.py's own established shape exactly
(a `_run_X` helper returning a result BaseModel with error_category/
error_message/status_code, a GET that renders the page, a no-JS POST form
fallback, and a JSON POST API), but as its own router/page rather than a
new pane on index.html - see CLAUDE.md's own Data Specification section
for why a genuinely separate page was chosen over folding this into the
existing single-page template."""

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.provenance.dispatch import convert_with_provenance
from app.provenance.highlighting import build_highlighting_payload
from app.routes.dropdowns import grouped_supported_types
from app.routes.errors import ERROR_STATUS, resolve_raw_text
from app.routes.static_assets import static_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_url"] = static_url


class CrosswalkResult(BaseModel):
    bundle_json: str | None = None
    report_json: str | None = None
    highlighting_json: str | None = None
    dedup_summary: dict | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


def _run_crosswalk(raw_text: str, deduplicate: bool = False) -> CrosswalkResult:
    try:
        bundle, report, dedup_result = convert_with_provenance(raw_text, deduplicate=deduplicate)
    except tuple(ERROR_STATUS) as exc:
        category, status_code = ERROR_STATUS[type(exc)]
        return CrosswalkResult(error_category=category, error_message=str(exc), status_code=status_code)
    # Purely additive over the existing bundle/report shape - the
    # correlated-highlighting payload (see app/provenance/highlighting.py)
    # never raises on its own, so no extra error handling is needed here.
    # It's built from the already-deduplicated bundle/report when
    # deduplicate=True (dedup runs inside convert_with_provenance, before
    # this function ever sees the result), so highlighting needs no
    # dedup-awareness of its own - see app/provenance/dispatch.py's own
    # docstring for why.
    highlighting = build_highlighting_payload(bundle, report, raw_text, report.source_format)
    dedup_summary = None
    if dedup_result is not None:
        # Mirrors app/routes/convert.py::_run_conversion's own dedup_summary
        # shape exactly, so both pages' JS can share one rendering pattern.
        dedup_summary = {
            "resources_merged": dedup_result.merged_count,
            "merges": [
                {"resource_type": m.resource_type, "kept_id": m.kept_id, "removed_ids": list(m.removed_ids)}
                for m in dedup_result.merges
            ],
        }
    return CrosswalkResult(
        bundle_json=bundle.model_dump_json(indent=2, exclude_none=True),
        report_json=report.model_dump_json(indent=2, exclude_none=True),
        highlighting_json=highlighting.model_dump_json(exclude_none=True),
        dedup_summary=dedup_summary,
    )


def _default_context() -> dict:
    return {
        "hl7_text": "",
        "supported_types": grouped_supported_types(),
        "crosswalk_result": None,
        "crosswalk_bundle": None,
        "crosswalk_error": None,
        "dedup_summary": None,
    }


@router.get("/data-specification")
async def data_specification_page(request: Request):
    return templates.TemplateResponse(request, "data_specification.html", _default_context())


@router.post("/data-specification")
async def data_specification_form(
    request: Request,
    hl7_text: str = Form(""),
    hl7_file: UploadFile | None = File(None),
    deduplicate: str | None = Form(None),
):
    raw_text = await resolve_raw_text(hl7_text, hl7_file)
    outcome = _run_crosswalk(raw_text, deduplicate=deduplicate is not None)
    error = (
        {"category": outcome.error_category, "message": outcome.error_message} if outcome.error_category else None
    )
    context = _default_context()
    # The no-JS fallback can't drive the interactive, correlated-
    # highlighting view (that's entirely mark-injection/hover JS - see
    # app/static/data_specification.js) - it keeps this app's own
    # established fallback shape instead: the report table plus both raw
    # JSON blocks, matching every other feature's own no-JS precedent.
    context.update(
        {
            "hl7_text": raw_text,
            "crosswalk_result": outcome.report_json,
            "crosswalk_bundle": outcome.bundle_json,
            "crosswalk_error": error,
            "dedup_summary": outcome.dedup_summary,
        }
    )
    return templates.TemplateResponse(request, "data_specification.html", context)


class CrosswalkApiRequest(BaseModel):
    hl7_text: str
    deduplicate: bool = False


@router.post("/api/data-specification")
async def data_specification_api(payload: CrosswalkApiRequest):
    outcome = _run_crosswalk(payload.hl7_text, deduplicate=payload.deduplicate)
    if outcome.error_category:
        return JSONResponse(
            status_code=outcome.status_code,
            content={"error": {"category": outcome.error_category, "message": outcome.error_message}},
        )
    content = {
        "bundle": json.loads(outcome.bundle_json),
        "report": json.loads(outcome.report_json),
        "highlighting": json.loads(outcome.highlighting_json),
    }
    if outcome.dedup_summary is not None:
        content["deduplication"] = outcome.dedup_summary
    return JSONResponse(content=content)
