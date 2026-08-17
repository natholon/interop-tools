"""Shared HTTP error-response wiring every route module reuses - extracted
from app/routes/convert.py once app/routes/data_specification.py became a
second real consumer of the identical {ExceptionType: (category, http_status)}
dispatch table and raw-text/file-upload resolution helper, the same
"extract on second real consumer" discipline this project applies
everywhere else."""

from fastapi import UploadFile
from pydantic import ValidationError

from app.cda.errors import CdaParseError
from app.edi.errors import EdiParseError
from app.hl7.errors import Hl7ParseError, MappingError, MissingSegmentError

# The full conversion-failure table - every exception convert_to_bundle()/
# convert_with_provenance() can raise.
ERROR_STATUS = {
    Hl7ParseError: ("Parse error", 400),
    CdaParseError: ("Parse error", 400),
    EdiParseError: ("Parse error", 400),
    MissingSegmentError: ("Missing segment", 400),
    MappingError: ("Mapping error", 422),
    ValidationError: ("FHIR validation error", 422),
}

# validate_any() only ever raises a parse-level error - every other failure
# mode is caught inside validate_document()/validate_message()/
# validate_interchange() and turned into a finding instead.
VALIDATION_ERROR_STATUS = {
    Hl7ParseError: ("Parse error", 400),
    CdaParseError: ("Parse error", 400),
    EdiParseError: ("Parse error", 400),
    MissingSegmentError: ("Missing segment", 400),
}


async def resolve_raw_text(hl7_text: str, hl7_file: UploadFile | None) -> str:
    raw_text = hl7_text
    if hl7_file is not None and hl7_file.filename:
        content = await hl7_file.read()
        if content:
            raw_text = content.decode("utf-8", errors="replace")
    return raw_text
