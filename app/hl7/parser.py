import hl7

from app.hl7.errors import Hl7ParseError, MissingSegmentError


def normalize_segment_separators(raw_text: str) -> str:
    """hl7.parse() always splits segments on '\\r'. Input pasted into a browser
    textarea or read from a file may use '\\n' or '\\r\\n' instead, so normalize
    to '\\r' and drop blank lines before handing off to the parser.

    Public (not module-private) - app/provenance/hl7_locator.py became a
    second real consumer, needing the exact same normalized text
    parse_message() itself parses (re-deriving it independently would risk
    the two silently drifting apart)."""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return "\r".join(line for line in text.split("\n") if line.strip() != "")


def truncate_to_first_message(normalized_text: str) -> str:
    """A real HL7v2 file can be a batch - multiple MSH-led messages
    concatenated back to back, with no wrapper segments (BHS/BTS) this app
    recognizes. hl7.parse() has no MSH-boundary awareness at all: it
    happily parses every segment from every concatenated message into one
    single Message object, so a repeating-segment lookup
    (optional_segments/group_segments_by_leader, used for OBX/NTE/AIP/...)
    would silently pull matching segments from every message in the batch
    into one Bundle - a real correctness hazard, not just a batching
    inconvenience, since a caller has no way to tell the result apart from
    a single message with an unusually large number of repeating segments.
    Mirrors this app's own established "process only the first, disclosed
    rather than silent" precedent for batched input (see
    app/edi/parser.py::first_transaction_set's identical scope limit for
    X12) - truncate to the segments before the second MSH, if a second one
    is present, before parsing even begins."""
    segments = normalized_text.split("\r")
    msh_indices = [i for i, seg in enumerate(segments) if seg.startswith("MSH")]
    if len(msh_indices) < 2:
        return normalized_text
    return "\r".join(segments[: msh_indices[1]])


def parse_message(raw_text: str) -> hl7.Message:
    """Parse raw HL7v2 text into an hl7.Message, or raise Hl7ParseError."""
    normalized = truncate_to_first_message(normalize_segment_separators(raw_text))
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


def optional_segments(message: hl7.Message, name: str) -> list:
    """Return every segment with the given name, or [] if none are present.
    For segments that legitimately repeat zero or more times (e.g. NTE, AIP)."""
    try:
        return list(message.segments(name))
    except KeyError:
        return []


def optional_segment(message: hl7.Message, name: str):
    """Return the first segment with the given name, or None if absent - the
    non-raising counterpart to require_segment, for callers that want to
    check for a segment's presence without treating its absence as fatal
    (e.g. app/validation/*.py, which reports a missing segment as a finding
    rather than raising)."""
    try:
        return message.segment(name)
    except KeyError:
        return None


def group_segments_by_leader(message: hl7.Message, leader_name: str, member_names) -> list[tuple]:
    """Walk the message's segments in order and group them by a repeating
    leader-then-members structure (e.g. ORU's OBR followed by its OBX
    result segments; a future ORM's ORC followed by its OBR). Every
    occurrence of `leader_name` starts a new group; each subsequent segment
    whose name is in `member_names` is appended to that group until the next
    leader (or end of message). Segments before the first leader, or whose
    name is neither the leader nor a member, are skipped - `optional_segments`
    (a flat "give me every segment with this name" lookup) is not sufficient
    here because which OBX belongs to which OBR is meaningful: it determines
    which Observations a given DiagnosticReport should reference.

    Returns a list of (leader_segment, [member_segments]) tuples, one per
    leader occurrence, in message order.
    """
    member_names = set(member_names)
    groups: list[tuple] = []
    current_members: list | None = None
    for raw_segment in message:
        name = field_str(raw_segment, 0)
        if name == leader_name:
            current_members = []
            groups.append((raw_segment, current_members))
        elif name in member_names and current_members is not None:
            current_members.append(raw_segment)
    return groups


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


def raw_field_str(segment, field_num: int, repetition: int = 0) -> str:
    """Return a field's full raw text, or '' if absent - unlike field_str,
    this does NOT extract a single component. Use this (not field_str's
    component=1 default) for unstructured free-text field types like TX/FT/
    ST, where a literal '^' in the text is just a character, not an HL7
    component separator: the hl7 library still splits on it the same way it
    would for a genuinely composite field, so field_str(segment, n) would
    silently truncate the text at the first '^'. str() on the raw
    repetition reconstructs the original text in both cases - already a
    bare string when the library didn't split it, and rejoined with '^'
    when it did."""
    try:
        rep = segment[field_num][repetition]
    except IndexError:
        return ""
    return str(rep)
