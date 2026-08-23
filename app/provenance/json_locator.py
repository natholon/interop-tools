"""Maps a FHIR JSON path - the exact `fhir_path` shape `resolve_bundle_
paths()` already produces, e.g. `"Bundle.entry[2].resource.name[0].
family"` - to its own leaf value's exact character span in a pretty-
printed JSON text (`bundle.model_dump_json(indent=2, exclude_none=True)`),
for the Data Specification page's correlated highlighting.

A hand-rolled recursive-descent parser, not the stdlib `json` module -
`json.loads`/`json.JSONDecoder` don't expose token positions, and a FHIR
value can legitimately contain `{`, `[`, or an escaped quote (a clinical
note, a URI, a free-text SIG), so a line-based/regex approach over the
pretty text isn't safe. This module is entirely independent of `app/
provenance/{models,recorder,resolver,dispatch}.py` - a pure text-in,
spans-out utility with no FHIR-model awareness at all, reused identically
regardless of which of the three input formats produced the Bundle."""

from dataclasses import dataclass

_WHITESPACE = " \t\n\r"


@dataclass(frozen=True)
class JsonSpan:
    start: int
    end: int
    token_type: str  # "string" | "number" | "literal" - matches app.js::highlightJson's own CSS class names


class _JsonSyntaxError(Exception):
    """Raised internally when the text doesn't parse as valid JSON at the
    expected position - never escapes `locate_json_paths()` itself (see
    its own docstring for why callers get an empty dict instead)."""


def _skip_whitespace(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _WHITESPACE:
        pos += 1
    return pos


def _parse_string(text: str, pos: int) -> int:
    """`text[pos]` must be the opening `"`. Returns the position just past
    the closing (unescaped) `"`."""
    if text[pos] != '"':
        raise _JsonSyntaxError(f"expected '\"' at {pos}")
    pos += 1
    while True:
        if pos >= len(text):
            raise _JsonSyntaxError("unterminated string")
        char = text[pos]
        if char == "\\":
            pos += 2  # skip the escaped character itself - correct even for \", \\, \n, etc.
            continue
        if char == '"':
            return pos + 1
        pos += 1


def _parse_number(text: str, pos: int) -> int:
    start = pos
    if pos < len(text) and text[pos] == "-":
        pos += 1
    while pos < len(text) and text[pos].isdigit():
        pos += 1
    if pos < len(text) and text[pos] == ".":
        pos += 1
        while pos < len(text) and text[pos].isdigit():
            pos += 1
    if pos < len(text) and text[pos] in "eE":
        pos += 1
        if pos < len(text) and text[pos] in "+-":
            pos += 1
        while pos < len(text) and text[pos].isdigit():
            pos += 1
    if pos == start:
        raise _JsonSyntaxError(f"expected a number at {pos}")
    return pos


def _parse_value(
    text: str, pos: int, path: str, out: dict[str, JsonSpan],
    containers: list[tuple[str, JsonSpan]] | None = None,
) -> int:
    pos = _skip_whitespace(text, pos)
    if pos >= len(text):
        raise _JsonSyntaxError("unexpected end of input")
    char = text[pos]

    if char == '"':
        end = _parse_string(text, pos)
        out[path] = JsonSpan(pos, end, "string")
        return end

    if char == "{":
        end = _parse_object(text, pos, path, out, containers)
        if containers is not None:
            containers.append((path, JsonSpan(pos, end, "container")))
        return end

    if char == "[":
        end = _parse_array(text, pos, path, out, containers)
        if containers is not None:
            containers.append((path, JsonSpan(pos, end, "container")))
        return end

    if char in "-0123456789":
        end = _parse_number(text, pos)
        out[path] = JsonSpan(pos, end, "number")
        return end

    for literal in ("true", "false", "null"):
        if text.startswith(literal, pos):
            end = pos + len(literal)
            out[path] = JsonSpan(pos, end, "literal")
            return end

    raise _JsonSyntaxError(f"unexpected character {char!r} at {pos}")


def _parse_object(
    text: str, pos: int, path: str, out: dict[str, JsonSpan],
    containers: list[tuple[str, JsonSpan]] | None = None,
) -> int:
    assert text[pos] == "{"
    pos = _skip_whitespace(text, pos + 1)
    if pos < len(text) and text[pos] == "}":
        return pos + 1
    while True:
        pos = _skip_whitespace(text, pos)
        key_start = pos
        key_end = _parse_string(text, pos)
        key = text[key_start + 1 : key_end - 1]  # strip the surrounding quotes for use as a path segment
        if containers is not None:
            # The key names the field, so clicking it should say so rather
            # than naming the object the key sits in.
            containers.append((f"{path}.{key}", JsonSpan(key_start, key_end, "container")))
        pos = _skip_whitespace(text, key_end)
        if pos >= len(text) or text[pos] != ":":
            raise _JsonSyntaxError(f"expected ':' at {pos}")
        pos = _skip_whitespace(text, pos + 1)
        pos = _parse_value(text, pos, f"{path}.{key}", out, containers)
        pos = _skip_whitespace(text, pos)
        if pos >= len(text):
            raise _JsonSyntaxError("unterminated object")
        if text[pos] == ",":
            pos = _skip_whitespace(text, pos + 1)
            continue
        if text[pos] == "}":
            return pos + 1
        raise _JsonSyntaxError(f"expected ',' or '}}' at {pos}")


def _parse_array(
    text: str, pos: int, path: str, out: dict[str, JsonSpan],
    containers: list[tuple[str, JsonSpan]] | None = None,
) -> int:
    assert text[pos] == "["
    pos = _skip_whitespace(text, pos + 1)
    if pos < len(text) and text[pos] == "]":
        return pos + 1
    index = 0
    while True:
        pos = _skip_whitespace(text, pos)
        pos = _parse_value(text, pos, f"{path}[{index}]", out, containers)
        index += 1
        pos = _skip_whitespace(text, pos)
        if pos >= len(text):
            raise _JsonSyntaxError("unterminated array")
        if text[pos] == ",":
            pos = _skip_whitespace(text, pos + 1)
            continue
        if text[pos] == "]":
            return pos + 1
        raise _JsonSyntaxError(f"expected ',' or ']' at {pos}")


def locate_json_paths(text: str, root_path: str = "Bundle") -> dict[str, JsonSpan]:
    """Walk `text` (JSON produced with `indent=2`) once, returning every
    leaf value's own path -> its exact `JsonSpan`. `path` matches
    `resolve_bundle_paths`'s own convention exactly (dotted object keys,
    0-based `[i]` array indices), rooted at `root_path` - the caller passes
    the default `"Bundle"` for a serialized `Bundle` resource, matching
    every `fhir_path` this app's provenance pillar ever produces. A string
    span includes its surrounding quotes (so the whole JSON token
    highlights, matching how `app.js::highlightJson` already wraps a
    string value including its quotes).

    Never raises - `text` is always this app's own `model_dump_json(...)`
    output, which is well-formed JSON by construction, but a malformed or
    truncated text (defensive, not currently reachable) degrades to an
    empty dict rather than crashing the Data Specification page's own
    highlighting feature over a rendering concern unrelated to the
    conversion itself already having succeeded."""
    out: dict[str, JsonSpan] = {}
    try:
        _parse_value(text, 0, root_path, out)
    except _JsonSyntaxError:
        return {}
    return out


def locate_json_paths_with_containers(
    text: str, root_path: str = "Bundle"
) -> list[tuple[str, JsonSpan]]:
    """Every leaf *and* every object/array, for the caret position readout.

    `locate_json_paths` deliberately records leaves only - a highlight
    should cover the value, not the whole object around it. The position
    readout needs the opposite: clicking a `{`, a key, or the whitespace
    between fields should still name the thing being clicked, so containers
    are included and the caller picks the most specific span containing the
    offset.

    Returns a list, not a dict: a key and the value it labels share a path
    but sit in two different places in the text, and both are worth
    clicking. Never raises - a parse failure degrades to an empty index
    rather than a broken page.
    """
    leaves: dict[str, JsonSpan] = {}
    containers: list[tuple[str, JsonSpan]] = []
    try:
        pos = _parse_value(text, 0, root_path, leaves, containers)
        _skip_whitespace(text, pos)
    except (_JsonSyntaxError, IndexError, RecursionError):
        return []
    return containers + [(path, span) for path, span in leaves.items()]
