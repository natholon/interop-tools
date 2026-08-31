"""Routes for the Data Specification pillar - the field-level provenance
crosswalk. Mirrors app/routes/convert.py's own established shape exactly
(a `_run_X` helper returning a result BaseModel with error_category/
error_message/status_code, a no-JS POST form fallback, and a JSON POST
API). The crosswalk is now the app's Message -> FHIR conversion view -
converting and showing which source field produced each FHIR field are one
action, so both live on the single `index.html` page this router shares
with app/routes/convert.py; GET /data-specification survives only as a
redirect for existing links."""

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.provenance.decisions import apply_rejections, compute_decisions
from app.provenance.dispatch import convert_with_provenance
from app.provenance.highlighting import build_highlighting_payload
from app.provenance.source_index import build_source_index
from app.routes.errors import ERROR_STATUS, resolve_raw_text
from app.routes.page_context import default_page_context
from app.routes.static_assets import static_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_url"] = static_url


class CrosswalkResult(BaseModel):
    bundle_json: str | None = None
    report_json: str | None = None
    decisions_json: str | None = None
    rejection_outcomes_json: str | None = None
    highlighting_json: str | None = None
    dedup_summary: dict | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


def _run_crosswalk(
    raw_text: str, deduplicate: bool = False, rejected_decision_ids: set[str] | None = None
) -> CrosswalkResult:
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
    # The reviewable decision register: everything this conversion
    # inferred or dropped, computed rather than declared (see
    # app/provenance/decisions.py).
    # The resolved source spans are what lets the C-CDA drop scan tell a
    # transformed value from an unread one - see decisions.py.
    source_spans = {tuple(m.source_span) for m in highlighting.matches if m.source_span}
    decisions = compute_decisions(report, raw_text, source_spans)

    bundle_dict = json.loads(bundle.model_dump_json(exclude_none=True))
    outcomes = []
    if rejected_decision_ids:
        bundle_dict, outcomes = apply_rejections(bundle_dict, decisions, rejected_decision_ids)

    bundle_json = json.dumps(bundle_dict, indent=2)
    if rejected_decision_ids:
        # Rebuilt from the post-rejection JSON so the pane a reviewer looks
        # at is the Bundle their rejections actually produced. The payload
        # above is built from the model, i.e. pre-rejection, which made
        # rejecting a value look like it had done nothing. Only rebuilt when
        # something was actually rejected - the source-side spans it feeds
        # to compute_decisions are unaffected either way, since those are
        # offsets into the raw message rather than into the Bundle.
        highlighting = build_highlighting_payload(
            bundle, report, raw_text, report.source_format, fhir_json_text=bundle_json
        )

    return CrosswalkResult(
        bundle_json=bundle_json,
        report_json=report.model_dump_json(indent=2, exclude_none=True),
        decisions_json=json.dumps([d.model_dump(exclude_none=True) for d in decisions]),
        rejection_outcomes_json=json.dumps([o.model_dump(exclude_none=True) for o in outcomes]),
        highlighting_json=highlighting.model_dump_json(exclude_none=True),
        dedup_summary=dedup_summary,
    )


@router.get("/data-specification")
async def data_specification_page():
    """Kept so bookmarks and existing links still land somewhere useful now
    that the crosswalk lives on the one page."""
    return RedirectResponse(url="/", status_code=308)


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
    context = default_page_context()
    # The no-JS fallback can't drive the interactive, correlated-
    # highlighting view (that's entirely mark-injection/hover JS - see
    # app/static/app.js) - it keeps this app's own established fallback
    # shape instead: the report table plus both raw JSON blocks, matching
    # every other feature's own no-JS precedent.
    context.update(
        {
            "hl7_text": raw_text,
            "crosswalk_result": outcome.report_json,
            "crosswalk_bundle": outcome.bundle_json,
            "crosswalk_error": error,
            "dedup_summary": outcome.dedup_summary,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


class CrosswalkApiRequest(BaseModel):
    hl7_text: str
    deduplicate: bool = False
    # Decision ids the reviewer rejected for THIS conversion. Stateless by
    # design - nothing is stored server-side; the browser holds the review
    # and replays it on each request.
    rejected_decision_ids: list[str] = []


class SourceIndexRequest(BaseModel):
    hl7_text: str = ""


@router.post("/api/source-index")
async def source_index_api(payload: SourceIndexRequest):
    """What field sits at each offset of the pasted text, so the editor can
    name the one under the pointer.

    Deliberately separate from /api/data-specification: this answers a
    question about the *text*, needs no conversion, and has to keep
    working while a message is half-typed. Always 200 - unparseable text
    yields an empty index rather than an error, since "no answer yet" is
    the honest response to an unfinished message.
    """
    index = build_source_index(payload.hl7_text)
    return JSONResponse(
        content={
            "source_format": index.source_format,
            "display_text": index.display_text,
            "positions": [
                {"start": start, "end": end, "path": path, "label": label}
                for start, end, path, label in index.entries
            ],
        }
    )


@router.post("/api/data-specification")
async def data_specification_api(payload: CrosswalkApiRequest):
    outcome = _run_crosswalk(
        payload.hl7_text,
        deduplicate=payload.deduplicate,
        rejected_decision_ids=set(payload.rejected_decision_ids),
    )
    if outcome.error_category:
        return JSONResponse(
            status_code=outcome.status_code,
            content={"error": {"category": outcome.error_category, "message": outcome.error_message}},
        )
    content = {
        "bundle": json.loads(outcome.bundle_json),
        "report": json.loads(outcome.report_json),
        "highlighting": json.loads(outcome.highlighting_json),
        "decisions": json.loads(outcome.decisions_json),
        "rejection_outcomes": json.loads(outcome.rejection_outcomes_json),
    }
    if outcome.dedup_summary is not None:
        content["deduplication"] = outcome.dedup_summary
    return JSONResponse(content=content)
