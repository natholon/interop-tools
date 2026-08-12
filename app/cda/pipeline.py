from fhir.resources.R4B.bundle import Bundle

from app.cda.parser import parse_document
from app.cda.registry import get_document_builder


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
