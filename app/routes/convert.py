import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fhir.resources.R4B.bundle import Bundle
from pydantic import BaseModel, ValidationError

from app.dedup import deduplicate_bundle
from app.generators.registry import generate as generate_sample
from app.hl7.errors import MappingError
from app.pipeline import convert_to_bundle, validate_any
from app.routes.dropdowns import grouped_supported_types
from app.routes.errors import ERROR_STATUS, VALIDATION_ERROR_STATUS, resolve_raw_text
from app.transform.pipeline import build_message_from_bundle
from app.transform.registry import list_supported_targets

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _transform_target_options() -> list[tuple[str, str, str, str]]:
    """(target_format, target_type, target_trigger, label) tuples for the
    reverse-transform target dropdown - mirrors _grouped_supported_types'
    role for the forward-direction sample dropdown, but flat (not grouped)
    since app/transform/registry.py has only a couple of targets so far;
    grouping can be added the same way once there are enough to warrant
    it. The trigger suffix is omitted entirely for a target with no real
    trigger-event concept (e.g. "CDA CCD", not "CDA CCD^") - str.partition
    on a caret-less label still parses back to an empty target_trigger
    correctly (see transform_form/app.js's own submit handler), so this is
    purely a display improvement, not a parsing-format change."""
    return [
        (
            target_format,
            target_type,
            target_trigger,
            f"{target_format} {target_type}^{target_trigger}" if target_trigger else f"{target_format} {target_type}",
        )
        for target_format, target_type, target_trigger in list_supported_targets()
    ]


class ConvertResult(BaseModel):
    bundle_json: str | None = None
    dedup_summary: dict | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


class ValidationResult(BaseModel):
    report_json: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


def _run_conversion(raw_text: str, deduplicate: bool = False) -> ConvertResult:
    try:
        bundle = convert_to_bundle(raw_text)
    except tuple(ERROR_STATUS) as exc:
        category, status_code = ERROR_STATUS[type(exc)]
        return ConvertResult(error_category=category, error_message=str(exc), status_code=status_code)

    dedup_summary = None
    if deduplicate:
        result = deduplicate_bundle(bundle)
        bundle = result.bundle
        dedup_summary = {
            "resources_merged": result.merged_count,
            "merges": [
                {"resource_type": m.resource_type, "kept_id": m.kept_id, "removed_ids": list(m.removed_ids)}
                for m in result.merges
            ],
        }
    return ConvertResult(
        bundle_json=bundle.model_dump_json(indent=2, exclude_none=True),
        dedup_summary=dedup_summary,
    )


def _run_validation(raw_text: str) -> ValidationResult:
    try:
        report = validate_any(raw_text)
    except tuple(VALIDATION_ERROR_STATUS) as exc:
        category, status_code = VALIDATION_ERROR_STATUS[type(exc)]
        return ValidationResult(error_category=category, error_message=str(exc), status_code=status_code)
    return ValidationResult(report_json=report.model_dump_json(indent=2, exclude_none=True))


class TransformResult(BaseModel):
    message_text: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


def _run_transform(bundle_json: str, target_format: str, target_type: str, target_trigger: str) -> TransformResult:
    try:
        bundle_dict = json.loads(bundle_json)
    except json.JSONDecodeError as exc:
        return TransformResult(error_category="Invalid JSON", error_message=str(exc), status_code=400)
    try:
        bundle = Bundle.model_validate(bundle_dict)
    except ValidationError as exc:
        return TransformResult(error_category="Invalid FHIR Bundle", error_message=str(exc), status_code=422)
    try:
        message_text = build_message_from_bundle(bundle, target_format, target_type, target_trigger)
    except MappingError as exc:
        return TransformResult(error_category="Mapping error", error_message=str(exc), status_code=422)
    return TransformResult(message_text=message_text)


def _default_context() -> dict:
    """Shared defaults for every index.html render - each route overrides
    only the keys its own outcome actually changes, so a new context key
    (like the transform_* ones below) only needs to be added here once
    rather than in every route's own dict literal."""
    return {
        "hl7_text": "",
        "result": None,
        "dedup_summary": None,
        "error": None,
        "validation_result": None,
        "validation_error": None,
        "supported_types": grouped_supported_types(),
        "transform_bundle_json": "",
        "transform_target": "",
        "transform_target_options": _transform_target_options(),
        "transform_result": None,
        "transform_error": None,
    }


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _default_context())


@router.post("/convert")
async def convert_form(
    request: Request,
    hl7_text: str = Form(""),
    hl7_file: UploadFile | None = File(None),
    deduplicate: str | None = Form(None),
):
    raw_text = await resolve_raw_text(hl7_text, hl7_file)

    outcome = _run_conversion(raw_text, deduplicate=deduplicate is not None)
    error = (
        {"category": outcome.error_category, "message": outcome.error_message}
        if outcome.error_category
        else None
    )
    context = _default_context()
    context.update(
        {
            "hl7_text": raw_text,
            "result": outcome.bundle_json,
            "dedup_summary": outcome.dedup_summary,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


@router.post("/validate")
async def validate_form(
    request: Request,
    hl7_text: str = Form(""),
    hl7_file: UploadFile | None = File(None),
):
    raw_text = await resolve_raw_text(hl7_text, hl7_file)

    outcome = _run_validation(raw_text)
    validation_error = (
        {"category": outcome.error_category, "message": outcome.error_message}
        if outcome.error_category
        else None
    )
    context = _default_context()
    context.update(
        {
            "hl7_text": raw_text,
            "validation_result": outcome.report_json,
            "validation_error": validation_error,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


@router.post("/transform")
async def transform_form(
    request: Request,
    bundle_json: str = Form(""),
    target: str = Form(""),
):
    target_format, _, rest = target.partition(" ")
    target_type, _, target_trigger = rest.partition("^")
    outcome = _run_transform(bundle_json, target_format, target_type, target_trigger)
    transform_error = (
        {"category": outcome.error_category, "message": outcome.error_message}
        if outcome.error_category
        else None
    )
    # Display-only \r -> \n substitution (see app/static/app.js's
    # showTransformResult for why the JS-driven path does the same thing) -
    # a bare \r doesn't reliably render as a line break inside <pre>, and
    # this app's own HL7v2 parser already normalizes \n back to \r on any
    # subsequent paste, so round-tripping through this app's own forms is
    # unaffected.
    display_text = outcome.message_text.replace("\r\n", "\n").replace("\r", "\n") if outcome.message_text else None
    context = _default_context()
    context.update(
        {
            "transform_bundle_json": bundle_json,
            "transform_target": target,
            "transform_result": display_text,
            "transform_error": transform_error,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


class ConvertApiRequest(BaseModel):
    hl7_text: str
    deduplicate: bool = False


@router.post("/api/convert")
async def convert_api(payload: ConvertApiRequest):
    outcome = _run_conversion(payload.hl7_text, deduplicate=payload.deduplicate)
    if outcome.error_category:
        return JSONResponse(
            status_code=outcome.status_code,
            content={"error": {"category": outcome.error_category, "message": outcome.error_message}},
        )
    content = {"bundle": json.loads(outcome.bundle_json)}
    if outcome.dedup_summary is not None:
        content["deduplication"] = outcome.dedup_summary
    return JSONResponse(content=content)


class ValidateApiRequest(BaseModel):
    hl7_text: str


@router.post("/api/validate")
async def validate_api(payload: ValidateApiRequest):
    outcome = _run_validation(payload.hl7_text)
    if outcome.error_category:
        # Only the two propagated parse-level exceptions land here. A
        # report concluding is_valid=False is itself a *successful*
        # analysis, not an API error - it's returned below with a normal
        # 200, same as an is_valid=True report.
        return JSONResponse(
            status_code=outcome.status_code,
            content={"error": {"category": outcome.error_category, "message": outcome.error_message}},
        )
    return JSONResponse(content={"report": json.loads(outcome.report_json)})


class TransformApiRequest(BaseModel):
    bundle_json: str
    target_format: str
    target_type: str
    target_trigger: str = ""


@router.post("/api/transform")
async def transform_api(payload: TransformApiRequest):
    outcome = _run_transform(payload.bundle_json, payload.target_format, payload.target_type, payload.target_trigger)
    if outcome.error_category:
        return JSONResponse(
            status_code=outcome.status_code,
            content={"error": {"category": outcome.error_category, "message": outcome.error_message}},
        )
    return JSONResponse(content={"message_text": outcome.message_text})


@router.get("/api/generate")
async def generate_sample_api(message_type: str, trigger_event: str, seed: int | None = None):
    try:
        hl7_text = generate_sample(message_type, trigger_event, seed=seed)
    except MappingError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": {"category": "Unknown message type", "message": str(exc)}},
        )
    return JSONResponse(content={"hl7_text": hl7_text})


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}
