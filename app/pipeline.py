"""Top-level format-sniffing dispatch, sitting above app/hl7/pipeline.py
and app/cda/pipeline.py - neither format-specific pipeline needs to know
the other format exists. This keeps a single unified "paste anything,
click Convert" UX: every prior HL7v2 message type plugged into the
existing route/form/pipeline via a registry, never a new route or UI
section per type - a new input *format* is the same kind of extension one
level up, not a reason to branch the UI.
"""

from fhir.resources.R4B.bundle import Bundle

from app.cda.pipeline import convert_cda_to_bundle, validate_cda
from app.hl7.pipeline import convert_hl7_to_bundle, validate_hl7
from app.validation.models import ValidationReport

_BOM = "﻿"


def is_xml(raw_text: str) -> bool:
    """HL7v2 messages always start with "MSH|..."; XML always starts with
    '<' (after a UTF-8 byte-order-mark some XML editors emit, and/or
    leading whitespace). No XML-parsing attempt is needed just to route -
    this check is deliberately cheap and unambiguous between the two
    formats this app supports. The BOM must be stripped BEFORE whitespace,
    not after: since the BOM character isn't itself whitespace, a leading
    BOM stops str.lstrip() before it ever reaches any whitespace that
    follows it."""
    return raw_text.lstrip(_BOM).lstrip().startswith("<")


def convert_to_bundle(raw_text: str) -> Bundle:
    """Sniff the input format and delegate to the matching pipeline.

    Raises the same exception shapes either underlying pipeline raises
    (Hl7ParseError/CdaParseError, MissingSegmentError, MappingError, or
    pydantic.ValidationError) - app/routes/convert.py handles all of them
    via one shared dispatch table.
    """
    if is_xml(raw_text):
        return convert_cda_to_bundle(raw_text)
    return convert_hl7_to_bundle(raw_text)


def validate_any(raw_text: str) -> ValidationReport:
    """Sniff the input format and delegate to the matching validator,
    mirroring convert_to_bundle's exact sniff-then-dispatch shape.

    Raises the same exception shapes either underlying validator raises
    (CdaParseError, or Hl7ParseError/MissingSegmentError for HL7v2) -
    app/routes/convert.py handles both via one shared dispatch table.
    """
    if is_xml(raw_text):
        return validate_cda(raw_text)
    return validate_hl7(raw_text)
