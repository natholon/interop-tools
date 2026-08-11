import hl7

from app.hl7.errors import Hl7ParseError, MissingSegmentError


def _normalize_segment_separators(raw_text: str) -> str:
    """hl7.parse() always splits segments on '\\r'. Input pasted into a browser
    textarea or read from a file may use '\\n' or '\\r\\n' instead, so normalize
    to '\\r' and drop blank lines before handing off to the parser."""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return "\r".join(line for line in text.split("\n") if line.strip() != "")


def parse_message(raw_text: str) -> hl7.Message:
    """Parse raw HL7v2 text into an hl7.Message, or raise Hl7ParseError."""
    normalized = _normalize_segment_separators(raw_text)
    try:
        return hl7.parse(normalized)
    except hl7.exceptions.ParseException as exc:
        raise Hl7ParseError(str(exc)) from exc


def require_segment(message: hl7.Message, name: str):
    """Return the first segment with the given name, or raise MissingSegmentError."""
    try:
        return message.segment(name)
    except KeyError as exc:
        raise MissingSegmentError(f"Required segment {name} is missing") from exc


def component_str(repetition_or_str, component: int = 1) -> str:
    """Return a 1-based component from a field repetition.

    When a field/repetition has no component separators, the hl7 library
    collapses it directly to a bare Python str instead of a nested
    [component] list - guard against indexing into that string by character.
    """
    if isinstance(repetition_or_str, str):
        return repetition_or_str if component <= 1 else ""
    try:
        return str(repetition_or_str[component - 1])
    except IndexError:
        return ""


def field_str(segment, field_num: int, repetition: int = 0, component: int = 1) -> str:
    """Return a single component of a segment field as a string, or '' if absent."""
    try:
        rep = segment[field_num][repetition]
    except IndexError:
        return ""
    return component_str(rep, component)


def field_repetitions(segment, field_num: int):
    """Return the list of repetitions for a field, or [] if the field is absent/out of range."""
    try:
        return list(segment[field_num])
    except IndexError:
        return []
