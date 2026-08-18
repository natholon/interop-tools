"""Unit tests for app/provenance/json_locator.py - the pretty-JSON path ->
character-span resolver behind the Data Specification page's correlated
highlighting. Covers both small hand-built strings (to nail exact edge
cases - escaped quotes, special characters, empty containers) and a real
converted Bundle (to prove it works against this app's own actual
model_dump_json(indent=2, exclude_none=True) output, not just idealized
JSON)."""

import json

from app.hl7.pipeline import convert_hl7_to_bundle
from app.provenance.json_locator import locate_json_paths


def test_simple_object_leaf_spans():
    text = json.dumps({"resourceType": "Bundle", "id": "abc"}, indent=2)
    locations = locate_json_paths(text)
    id_span = locations["Bundle.id"]
    assert text[id_span.start : id_span.end] == '"abc"'
    assert id_span.token_type == "string"
    type_span = locations["Bundle.resourceType"]
    assert text[type_span.start : type_span.end] == '"Bundle"'


def test_array_indices_are_zero_based():
    text = json.dumps({"entry": [{"a": 1}, {"a": 2}, {"a": 3}]}, indent=2)
    locations = locate_json_paths(text)
    for i, expected in enumerate([1, 2, 3]):
        span = locations[f"Bundle.entry[{i}].a"]
        assert text[span.start : span.end] == str(expected)
        assert span.token_type == "number"


def test_nested_object_and_array_path_composition():
    text = json.dumps({"entry": [{"resource": {"name": [{"family": "Doe", "given": ["Jane", "Q"]}]}}]}, indent=2)
    locations = locate_json_paths(text)
    family_span = locations["Bundle.entry[0].resource.name[0].family"]
    assert text[family_span.start : family_span.end] == '"Doe"'
    given0_span = locations["Bundle.entry[0].resource.name[0].given[0]"]
    assert text[given0_span.start : given0_span.end] == '"Jane"'
    given1_span = locations["Bundle.entry[0].resource.name[0].given[1]"]
    assert text[given1_span.start : given1_span.end] == '"Q"'


def test_string_containing_special_json_characters_is_not_mistaken_for_structure():
    # A clinical note or URI can legitimately contain {, [, or an escaped
    # quote - a line-based/regex approach would misparse this; the
    # recursive-descent parser must not.
    value = 'Note: {see "attached"} [ref: 1]'
    text = json.dumps({"note": value}, indent=2)
    locations = locate_json_paths(text)
    span = locations["Bundle.note"]
    assert json.loads(text[span.start : span.end]) == value


def test_escaped_backslash_before_quote_does_not_break_string_scanning():
    # A trailing literal backslash right before the closing quote (e.g.
    # "C:\\") must not be misread as escaping the closing quote itself.
    value = "path ends in a backslash\\"
    text = json.dumps({"note": value}, indent=2)
    locations = locate_json_paths(text)
    span = locations["Bundle.note"]
    assert json.loads(text[span.start : span.end]) == value


def test_booleans_and_null_recorded_as_literal_type():
    text = json.dumps({"active": True, "deceased": False, "multipleBirth": None}, indent=2)
    locations = locate_json_paths(text)
    assert locations["Bundle.active"].token_type == "literal"
    assert text[locations["Bundle.active"].start : locations["Bundle.active"].end] == "true"
    assert text[locations["Bundle.deceased"].start : locations["Bundle.deceased"].end] == "false"
    assert text[locations["Bundle.multipleBirth"].start : locations["Bundle.multipleBirth"].end] == "null"


def test_negative_and_decimal_numbers():
    # Built as a literal string (not via json.dumps(float)) so the
    # exponent-notation form survives verbatim - json.dumps would
    # otherwise normalize 1.5e10 to 15000000000.0 before this test ever
    # sees it, testing Python's own float repr instead of the parser.
    text = '{\n  "a": -5,\n  "b": 3.14,\n  "c": 1.5e10\n}'
    locations = locate_json_paths(text)
    assert text[locations["Bundle.a"].start : locations["Bundle.a"].end] == "-5"
    assert text[locations["Bundle.b"].start : locations["Bundle.b"].end] == "3.14"
    assert text[locations["Bundle.c"].start : locations["Bundle.c"].end] == "1.5e10"


def test_empty_object_and_array_do_not_crash():
    text = json.dumps({"entry": [], "meta": {}}, indent=2)
    locations = locate_json_paths(text)
    # Neither produces a leaf fact of its own - just confirming no crash
    # and no spurious entries.
    assert not any(path.startswith("Bundle.entry[") for path in locations)


def test_malformed_json_returns_empty_dict_not_a_crash():
    assert locate_json_paths("{not: valid json") == {}
    assert locate_json_paths("") == {}


def test_real_converted_bundle_every_string_span_is_well_formed():
    with open("tests/fixtures/adt_a01_basic.hl7", encoding="utf-8") as f:
        raw_text = f.read()
    bundle = convert_hl7_to_bundle(raw_text)
    json_text = bundle.model_dump_json(indent=2, exclude_none=True)
    locations = locate_json_paths(json_text)
    assert len(locations) > 30

    family_span = locations["Bundle.entry[0].resource.name[0].family"]
    assert json_text[family_span.start : family_span.end] == '"Doe"'

    identifier_span = locations["Bundle.identifier.value"]
    assert json.loads(json_text[identifier_span.start : identifier_span.end])

    # Every recorded string span must itself be valid, self-contained JSON
    # (round-trips through json.loads) - proves span boundaries are exact,
    # not off-by-one.
    for path, span in locations.items():
        snippet = json_text[span.start : span.end]
        if span.token_type == "string":
            assert snippet.startswith('"') and snippet.endswith('"'), (path, snippet)
            json.loads(snippet)  # raises if the span is malformed
