class EdiParseError(Exception):
    """Raised when raw text cannot be parsed as an X12 EDI interchange - too
    short to contain a valid 106-character ISA segment, doesn't start with
    "ISA", or has envelope boundary segments (IEA / GS-GE / ST-SE) that
    don't nest correctly. The mirror of app.hl7.errors.Hl7ParseError /
    app.cda.errors.CdaParseError for the X12 input format -
    MissingSegmentError and MappingError are reused as-is from
    app.hl7.errors rather than duplicated here, since their meaning ("a
    required structural piece is absent" / "no builder registered for this
    recognized-but-unhandled type") is format-agnostic. A structurally
    well-formed interchange that simply contains zero transaction sets is
    NOT a parse error - see app/edi/parser.py::first_transaction_set."""
