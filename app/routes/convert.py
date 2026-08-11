import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from app.generators.registry import generate as generate_sample
from app.generators.registry import list_supported_types
from app.hl7.errors import Hl7ParseError, MappingError, MissingSegmentError
from app.hl7.pipeline import convert_hl7_to_bundle

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_ERROR_STATUS = {
    Hl7ParseError: ("Parse error", 400),
    MissingSegmentError: ("Missing segment", 400),
    MappingError: ("Mapping error", 422),
    ValidationError: ("FHIR validation error", 422),
}


class ConvertResult(BaseModel):
    bundle_json: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


def _run_conversion(raw_text: str) -> ConvertResult:
    try:
        bundle = convert_hl7_to_bundle(raw_text)
    except tuple(_ERROR_STATUS) as exc:
        category, status_code = _ERROR_STATUS[type(exc)]
        return ConvertResult(error_category=category, error_message=str(exc), status_code=status_code)
    return ConvertResult(bundle_json=bundle.model_dump_json(indent=2, exclude_none=True))


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "hl7_text": "",
            "result": None,
            "error": None,
            "supported_types": list_supported_types(),
        },
    )


@router.post("/convert")
async def convert_form(
    request: Request,
    hl7_text: str = Form(""),
    hl7_file: UploadFile | None = File(None),
):
    raw_text = hl7_text
    if hl7_file is not None and hl7_file.filename:
        content = await hl7_file.read()
        if content:
            raw_text = content.decode("utf-8", errors="replace")

    outcome = _run_conversion(raw_text)
    error = (
        {"category": outcome.error_category, "message": outcome.error_message}
        if outcome.error_category
        else None
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "hl7_text": raw_text,
            "result": outcome.bundle_json,
            "error": error,
            "supported_types": list_supported_types(),
        },
    )


class ConvertApiRequest(BaseModel):
    hl7_text: str


@router.post("/api/convert")
async def convert_api(payload: ConvertApiRequest):
    outcome = _run_conversion(payload.hl7_text)
    if outcome.error_category:
        return JSONResponse(
            status_code=outcome.status_code,
            content={"error": {"category": outcome.error_category, "message": outcome.error_message}},
        )
    return JSONResponse(content={"bundle": json.loads(outcome.bundle_json)})


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
