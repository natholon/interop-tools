from fhir.resources.R4B.bundle import Bundle

from app.hl7.parser import field_str, parse_message, require_segment
from app.mappings.registry import get_mapper


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
