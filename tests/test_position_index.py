"""Offset -> location, for the crosswalk's caret readout.

Each test asserts the path resolved for a *known* offset found by
searching the display text for real content, rather than a hard-coded
number that would silently rot when a fixture changes.
"""

from pathlib import Path

import pytest

from app.provenance.dispatch import convert_with_provenance
from app.provenance.highlighting import build_highlighting_payload
from app.provenance.position_index import (
    build_fhir_position_index,
    build_source_position_index,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _panes(fixture: str):
    raw = (FIXTURES / fixture).read_text(encoding="utf-8")
    bundle, report, _dedup = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    return payload, report.source_format


def _at(entries, offset):
    """The most specific span containing the offset - the same rule the
    client applies, since spans nest."""
    hits = [e for e in entries if e.start <= offset < e.end]
    return min(hits, key=lambda e: e.end - e.start).path if hits else None


def _source_location(fixture: str, needle: str):
    payload, source_format = _panes(fixture)
    index = build_source_position_index(payload.display_source_text, source_format)
    offset = payload.display_source_text.index(needle)
    return _at(index, offset)


def test_hl7v2_offsets_resolve_to_segment_field_and_component():
    assert _source_location("adt_a01_basic.hl7", "Doe") == "PID-5.1"
    assert _source_location("adt_a01_basic.hl7", "19620305") == "PID-7"
    # Clicking the segment id names the segment, not a field within it.
    assert _source_location("adt_a01_basic.hl7", "PID|") == "PID"


def test_hl7v2_msh_field_numbering_is_offset_by_one():
    # MSH-1 is the field separator itself, not a `|`-split token, so every
    # MSH field N>=2 is the (N-1)th token. Getting this wrong shifts every
    # MSH field by one.
    payload, source_format = _panes("adt_a01_basic.hl7")
    text = payload.display_source_text
    index = build_source_position_index(text, source_format)
    msh = text.split("\n")[0].split("|")
    # `|`-split token 2 is the sending application, which is MSH-3.
    assert _at(index, text.index(msh[2])) == "MSH-3"

    # Stronger: agree with the locator that already resolves the other
    # direction. Whatever span Hl7Locator gives MSH-3, this index must name
    # MSH-3 for an offset inside it, or the two disagree about one field.
    from app.provenance.hl7_locator import Hl7Locator

    span = Hl7Locator(text).locate("MSH-3", 0)
    assert span is not None
    assert _at(index, span[0]) == "MSH-3"


def test_edi_offsets_resolve_to_segment_and_element():
    assert _source_location("edi_270_basic.x12", "NM1") == "NM1"


def test_cda_offsets_resolve_to_element_text_and_attributes():
    assert (
        _source_location("ccd_basic.xml", "Betterhalf")
        == "ClinicalDocument/recordTarget/patientRole/patient/name/family"
    )
    assert (
        _source_location("ccd_basic.xml", "Beaverton")
        == "ClinicalDocument/recordTarget/patientRole/addr/city"
    )
    assert _source_location("ccd_basic.xml", "19750501").endswith("birthTime/@value")


def test_fhir_offsets_resolve_to_a_bundle_path():
    payload, _fmt = _panes("adt_a01_basic.hl7")
    text = payload.fhir_json_text
    index = build_fhir_position_index(text)

    # A value resolves to its own leaf path...
    assert _at(index, text.index("Doe")) == "Bundle.entry[0].resource.name[0].family"
    # ...and so does the key that labels it, which is what a reader is
    # pointing at when they click the field name rather than the value.
    assert _at(index, text.index('"family"')) == "Bundle.entry[0].resource.name[0].family"
    # A container resolves to the container.
    assert _at(index, text.index('"name"')) == "Bundle.entry[0].resource.name"


def test_unknown_format_yields_an_empty_index():
    assert build_source_position_index("anything", None) == []
    assert build_source_position_index("anything", "SOMETHING-ELSE") == []


def test_malformed_json_yields_an_empty_index_rather_than_raising():
    assert build_fhir_position_index("{not json") == []


@pytest.mark.parametrize(
    "fixture",
    [f.name for f in sorted(FIXTURES.iterdir()) if f.suffix in {".hl7", ".xml", ".x12"}],
)
def test_every_span_is_within_its_own_text(fixture):
    # A span past the end of the string it indexes would resolve to
    # nothing, or to the wrong text, in the pane.
    try:
        payload, source_format = _panes(fixture)
    except Exception:
        pytest.skip("fixture does not convert by design")

    for index, text in (
        (build_source_position_index(payload.display_source_text, source_format), payload.display_source_text),
        (build_fhir_position_index(payload.fhir_json_text), payload.fhir_json_text),
    ):
        for entry in index:
            assert 0 <= entry.start < entry.end <= len(text), f"{entry} out of range for {fixture}"


def test_an_absolute_path_resolves_without_claiming_an_occurrence():
    # A relative location is resolved by claiming an occurrence of its
    # leading element, which needs a resource-scoped hint to disambiguate.
    # Patient's US Core extensions come from inside a section, where no
    # such hint exists, so they record an absolute path instead - and the
    # document root is unique, so there is exactly one candidate.
    #
    # CdaLocator._candidates only walked *children*, so an absolute path
    # matched nothing at all until the root was made a candidate for its
    # own tag.
    from app.provenance.cda_locator import CdaLocator

    raw = (FIXTURES / "history_and_physical_basic.xml").read_text(encoding="utf-8")
    index = build_source_position_index(raw, "CDA")
    gender_identity = next(
        e for e in index if e.path.endswith("/@code") and raw[e.start : e.end] == "446141000124107"
    )
    assert gender_identity.path.startswith("ClinicalDocument/")

    span = CdaLocator(raw).locate(gender_identity.path, 0)
    assert span is not None
    assert raw[span[0] : span[1]] == "446141000124107"


def test_patient_extension_facts_highlight_their_own_source_element():
    # The regression this exists for: both Birth Sex and Gender Identity
    # recorded a relative entry/observation/value path, so the occurrence
    # counter gave them the same element and Gender Identity highlighted
    # the Birth Sex text.
    payload, _fmt = _panes("history_and_physical_basic.xml")
    raw = (FIXTURES / "history_and_physical_basic.xml").read_text(encoding="utf-8")
    _bundle, report, _dedup = convert_with_provenance(raw)

    spans = {}
    for match, entry in zip(payload.matches, report.entries):
        if entry.fhir_path and "extension" in entry.fhir_path and match.source_span:
            spans[entry.fhir_path.split(".resource.")[-1]] = raw[
                match.source_span[0] : match.source_span[1]
            ]

    assert spans["extension[0].valueCode"] == "F"
    assert spans["extension[1].valueCodeableConcept.coding[0].code"] == "446141000124107"
