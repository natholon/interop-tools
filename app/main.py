from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.request_limits import LimitRequestSize
from app.security_headers import SecurityHeaders
from app.routes.convert import router as convert_router
from app.routes.data_specification import router as data_specification_router
from app.routes.fhir_api import router as fhir_api_router

app = FastAPI(
    title="interop-tools",
    description="Healthcare interoperability toolkit - HL7v2/C-CDA/X12 EDI to FHIR R4 conversion, validation, and synthetic test-data generation.",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(convert_router)
app.include_router(data_specification_router)
app.include_router(fhir_api_router)

# Outermost, so an oversized body is rejected before any route or
# parser sees it - see app/request_limits.py for the measurements
# that set the ceiling.
app.add_middleware(SecurityHeaders)
app.add_middleware(LimitRequestSize)
