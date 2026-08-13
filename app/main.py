from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.convert import router as convert_router

app = FastAPI(
    title="interop-tools",
    description="Healthcare interoperability toolkit - HL7v2/C-CDA/X12 EDI to FHIR R4 conversion, validation, and synthetic test-data generation.",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(convert_router)
