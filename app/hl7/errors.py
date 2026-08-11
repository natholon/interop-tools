class Hl7ParseError(Exception):
    """Raised when raw text cannot be parsed as an HL7v2 message."""


class MissingSegmentError(Exception):
    """Raised when a required segment (MSH, PID, PV1, ...) is absent."""


class MappingError(Exception):
    """Raised when a parsed HL7v2 message cannot be mapped to FHIR (e.g. unsupported message type)."""
