from fhir.resources.R4B.bundle import Bundle

from app.hl7.parser import field_str, parse_message, require_segment
from app.mappings.registry import get_mapper
from app.validation.engine import validate_message
from app.validation.models import ValidationReport


def convert_hl7_to_bundle(raw_text: str) -> Bundle:
    """Parse raw HL7v2 text and convert it to a FHIR Bundle.

    Raises Hl7ParseError, MissingSegmentError, MappingError, or
    pydantic.ValidationError on failure.
    """
    message = parse_message(raw_text)
    msh = require_segment(message, "MSH")
    message_type = field_str(msh, 9, component=1)
    trigger_event = field_str(msh, 9, component=2)
    mapper = get_mapper(message_type, trigger_event)
    return mapper.to_bundle(message)


def validate_hl7(raw_text: str) -> ValidationReport:
    """Parse raw HL7v2 text and run it through the validation rule set.

    Raises Hl7ParseError (unparseable text) or MissingSegmentError (MSH
    itself absent) - the only two problems severe enough that there's
    nothing meaningful left to validate. Every other failure mode (unknown
    message type, a segment missing elsewhere, a FHIR construction error
    while checking convertibility) is caught inside validate_message and
    turned into a finding - this function always returns a ValidationReport
    in those cases rather than raising, unlike convert_hl7_to_bundle.
    """
    message = parse_message(raw_text)
    msh = require_segment(message, "MSH")
    message_type = field_str(msh, 9, component=1)
    trigger_event = field_str(msh, 9, component=2)
    return validate_message(message, message_type, trigger_event)
