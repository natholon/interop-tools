import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from app.cda.errors import CdaParseError
from app.generators.registry import generate as generate_sample
from app.generators.registry import list_supported_types
from app.hl7.errors import Hl7ParseError, MappingError, MissingSegmentError
from app.hl7.pipeline import validate_hl7
from app.pipeline import convert_to_bundle, is_xml

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_ERROR_STATUS = {
    Hl7ParseError: ("Parse error", 400),
    CdaParseError: ("Parse error", 400),
    MissingSegmentError: ("Missing segment", 400),
    MappingError: ("Mapping error", 422),
    ValidationError: ("FHIR validation error", 422),
}

# validate_hl7() only ever raises these two - every other failure mode is
# caught inside validate_message() and turned into a finding instead.
_VALIDATION_ERROR_STATUS = {
    Hl7ParseError: ("Parse error", 400),
    MissingSegmentError: ("Missing segment", 400),
}


class ConvertResult(BaseModel):
    bundle_json: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


class ValidationResult(BaseModel):
    report_json: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    status_code: int = 200


async def _resolve_raw_text(hl7_text: str, hl7_file: UploadFile | None) -> str:
    raw_text = hl7_text
    if hl7_file is not None and hl7_file.filename:
        content = await hl7_file.read()
        if content:
            raw_text = content.decode("utf-8", errors="replace")
    return raw_text


def _run_conversion(raw_text: str) -> ConvertResult:
    try:
        bundle = convert_to_bundle(raw_text)
    except tuple(_ERROR_STATUS) as exc:
        category, status_code = _ERROR_STATUS[type(exc)]
        return ConvertResult(error_category=category, error_message=str(exc), status_code=status_code)
    return ConvertResult(bundle_json=bundle.model_dump_json(indent=2, exclude_none=True))


def _run_validation(raw_text: str) -> ValidationResult:
    if is_xml(raw_text):
        # validate_hl7() has no CDA counterpart yet - without this guard,
        # pasting XML into Validate would hit a confusing HL7-parse failure
        # instead of an honest "not supported yet" message.
        return ValidationResult(
            error_category="Unsupported",
            error_message="C-CDA validation is not yet supported.",
            status_code=422,
        )
    try:
        report = validate_hl7(raw_text)
    except tuple(_VALIDATION_ERROR_STATUS) as exc:
        category, status_code = _VALIDATION_ERROR_STATUS[type(exc)]
        return ValidationResult(error_category=category, error_message=str(exc), status_code=status_code)
    return ValidationResult(report_json=report.model_dump_json(indent=2, exclude_none=True))


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "hl7_text": "",
            "result": None,
            "error": None,
            "validation_result": None,
            "validation_error": None,
            "supported_types": list_supported_types(),
        },
    )


@router.post("/convert")
async def convert_form(
    request: Request,
    hl7_text: str = Form(""),
    hl7_file: UploadFile | None = File(None),
):
    raw_text = await _resolve_raw_text(hl7_text, hl7_file)

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
            "validation_result": None,
            "validation_error": None,
            "supported_types": list_supported_types(),
        },
    )


@router.post("/validate")
async def validate_form(
    request: Request,
    hl7_text: str = Form(""),
    hl7_file: UploadFile | None = File(None),
):
    raw_text = await _resolve_raw_text(hl7_text, hl7_file)

    outcome = _run_validation(raw_text)
    validation_error = (
        {"category": outcome.error_category, "message": outcome.error_message}
        if outcome.error_category
        else None
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "hl7_text": raw_text,
            "result": None,
            "error": None,
            "validation_result": outcome.report_json,
            "validation_error": validation_error,
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
