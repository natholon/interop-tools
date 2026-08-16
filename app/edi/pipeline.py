from fhir.resources.R4B.bundle import Bundle

from app.edi.parser import first_transaction_set, parse_interchange
from app.edi.registry import get_transaction_builder
from app.edi.validation import validate_interchange
from app.hl7.errors import MissingSegmentError
from app.validation.models import ValidationReport


def convert_edi_to_bundle(raw_text: str) -> Bundle:
    """Parse raw X12 text and convert its first transaction set to a FHIR
    Bundle.

    Raises EdiParseError, MissingSegmentError, MappingError, or
    pydantic.ValidationError on failure - the same exception shapes
    convert_hl7_to_bundle/convert_cda_to_bundle raise, so
    app/routes/convert.py's existing error-handling needs only one new
    dict entry (EdiParseError), not a parallel dispatch table."""
    interchange = parse_interchange(raw_text)
    transaction_set = first_transaction_set(interchange)
    if transaction_set is None:
        raise MissingSegmentError("Interchange contains no ST/SE transaction set to convert")
    builder = get_transaction_builder(transaction_set.st01, transaction_set.st03)
    return builder.build_bundle(transaction_set, interchange.delimiters)


def validate_edi(raw_text: str) -> ValidationReport:
    """Parse raw X12 text and validate it, mirroring
    convert_edi_to_bundle's exact parse-then-delegate shape (and
    app.cda.pipeline.validate_cda's shape on the C-CDA side). Raises only
    EdiParseError - unlike validate_hl7, there's no second raise-worthy
    case (an interchange with no transaction sets is a legitimate,
    well-formed input that validate_interchange itself reports as an info
    finding, not a raise)."""
    interchange = parse_interchange(raw_text)
    return validate_interchange(interchange)
