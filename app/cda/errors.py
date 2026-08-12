class CdaParseError(Exception):
    """Raised when raw text cannot be parsed as a C-CDA XML document (not
    well-formed XML, or the root element isn't ClinicalDocument). The
    mirror of app.hl7.errors.Hl7ParseError for the XML input format -
    MissingSegmentError and MappingError are reused as-is from
    app.hl7.errors rather than duplicated here, since their *meaning* ("a
    required structural piece is absent" / "no builder registered for this
    recognized-but-unhandled type") is format-agnostic."""
