from fhir.resources.R4B.bundle import Bundle

from app.cda.parser import parse_document
from app.cda.registry import get_document_builder
from app.cda.validation import validate_document
from app.validation.models import ValidationReport


def convert_cda_to_bundle(raw_xml: str) -> Bundle:
    """Parse raw C-CDA XML and convert it to a FHIR Bundle.

    Raises CdaParseError, MissingSegmentError, MappingError, or
    pydantic.ValidationError on failure - the same exception shapes
    convert_hl7_to_bundle raises, so app/routes/convert.py's existing
    error-handling needs only one new dict entry (CdaParseError), not a
    parallel dispatch table.
    """
    document = parse_document(raw_xml)
    builder = get_document_builder(document)
    return builder.build_bundle(document)


def validate_cda(raw_xml: str) -> ValidationReport:
    """Parse raw C-CDA XML and validate it, mirroring
    convert_cda_to_bundle's exact parse-then-delegate shape (and
    app.hl7.pipeline.validate_hl7's shape on the HL7v2 side). Raises only
    CdaParseError - unlike validate_hl7, there's no second raise-worthy
    case (HL7v2's "MSH itself is absent" maps to a malformed/wrong-root
    document here, which parse_document already turns into CdaParseError).
    """
    document = parse_document(raw_xml)
    return validate_document(document)
