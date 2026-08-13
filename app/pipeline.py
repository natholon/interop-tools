"""Top-level format-sniffing dispatch, sitting above app/hl7/pipeline.py,
app/cda/pipeline.py, and app/edi/pipeline.py - none of the three
format-specific pipelines needs to know the others exist. This keeps a
single unified "paste anything, click Convert" UX: every prior HL7v2
message type plugged into the existing route/form/pipeline via a registry,
never a new route or UI section per type - a new input *format* is the
same kind of extension one level up, not a reason to branch the UI.
"""

from fhir.resources.R4B.bundle import Bundle

from app.cda.pipeline import convert_cda_to_bundle, validate_cda
from app.edi.pipeline import convert_edi_to_bundle, validate_edi
from app.hl7.pipeline import convert_hl7_to_bundle, validate_hl7
from app.validation.models import ValidationReport

_BOM = "﻿"


def is_xml(raw_text: str) -> bool:
    """HL7v2 messages always start with "MSH|..."; XML always starts with
    '<' (after a UTF-8 byte-order-mark some XML editors emit, and/or
    leading whitespace). No XML-parsing attempt is needed just to route -
    this check is deliberately cheap and unambiguous between the formats
    this app supports. The BOM must be stripped BEFORE whitespace, not
    after: since the BOM character isn't itself whitespace, a leading BOM
    stops str.lstrip() before it ever reaches any whitespace that follows
    it."""
    return raw_text.lstrip(_BOM).lstrip().startswith("<")


def is_x12(raw_text: str) -> bool:
    """X12 EDI interchanges always start with the literal 3-character
    segment id "ISA" (spec-fixed at byte positions 0-2 - see
    app/edi/parser.py::read_isa_delimiters) - checking this literal prefix
    rather than "ISA*" deliberately doesn't bake in an assumption about
    ISA04 (the element separator)'s own value, which is discovered
    dynamically and conventionally-but-not-spec-guaranteed to be "*". Same
    BOM-before-whitespace stripping order as is_xml(), for the same
    reason. Deliberately case-sensitive, matching read_isa_delimiters'
    own case-sensitive check downstream (X12 segment IDs are always
    uppercase by spec) - an earlier case-insensitive version here could
    misroute ordinary non-X12 text that merely started with "isa"
    (e.g. a name beginning "Isabella") into the EDI pipeline, where it
    would then fail read_isa_delimiters' case-sensitive check anyway and
    surface a confusing EDI-flavored parse error instead of falling
    through to the default HL7v2 pipeline."""
    return raw_text.lstrip(_BOM).lstrip().startswith("ISA")


def convert_to_bundle(raw_text: str) -> Bundle:
    """Sniff the input format and delegate to the matching pipeline. The
    three format signatures (a literal "ISA" prefix, a leading "<", and
    everything else defaulting to HL7v2) are mutually exclusive by
    construction, so check order doesn't affect correctness - X12 is
    checked first since it's the cheapest, most unambiguous check (a fixed
    literal-prefix comparison, no whitespace/BOM subtlety beyond what
    is_xml already handles).

    Raises the same exception shapes any underlying pipeline raises
    (Hl7ParseError/CdaParseError/EdiParseError, MissingSegmentError,
    MappingError, or pydantic.ValidationError) - app/routes/convert.py
    handles all of them via one shared dispatch table.
    """
    if is_x12(raw_text):
        return convert_edi_to_bundle(raw_text)
    if is_xml(raw_text):
        return convert_cda_to_bundle(raw_text)
    return convert_hl7_to_bundle(raw_text)


def validate_any(raw_text: str) -> ValidationReport:
    """Sniff the input format and delegate to the matching validator,
    mirroring convert_to_bundle's exact sniff-then-dispatch shape.

    Raises the same exception shapes any underlying validator raises
    (CdaParseError/EdiParseError, or Hl7ParseError/MissingSegmentError for
    HL7v2) - app/routes/convert.py handles all of them via one shared
    dispatch table.
    """
    if is_x12(raw_text):
        return validate_edi(raw_text)
    if is_xml(raw_text):
        return validate_cda(raw_text)
    return validate_hl7(raw_text)
