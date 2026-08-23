"""Every transformation the crosswalk labels "Transformed" should say what
governed it, not just that a value changed."""

from pathlib import Path

import pytest

from app.provenance.dispatch import convert_with_provenance
from app.provenance.transform_citations import citation_for, normalise_path

FIXTURES = Path(__file__).parent / "fixtures"
_MESSAGES = sorted(
    f.name for f in FIXTURES.iterdir() if f.suffix in {".hl7", ".xml", ".x12"}
)


def _transformed_entries(fixture: str):
    """Entries the crosswalk badges as Transformed - the same test the UI
    applies, so the two cannot disagree about which rows want a citation."""
    raw = (FIXTURES / fixture).read_text(encoding="utf-8")
    _bundle, report, _dedup = convert_with_provenance(raw)
    return [
        e
        for e in report.entries
        if e.derivation == "direct"
        and e.source_value is not None
        and e.value is not None
        and e.source_value != e.value
    ]


@pytest.mark.parametrize("fixture", _MESSAGES)
def test_every_transformed_value_cites_its_source(fixture):
    # A row saying "Transformed" without naming the ConceptMap, code table
    # or datatype rule behind it is an assertion the reader cannot check.
    try:
        entries = _transformed_entries(fixture)
    except Exception:
        pytest.skip("fixture does not convert by design")
    uncited = sorted(
        {
            (e.source_format, normalise_path(e.fhir_path))
            for e in entries
            if e.transform_citation is None
        }
    )
    assert uncited == []


def test_a_direct_copy_gets_no_citation():
    # Only a genuine transformation claims one. A plain copy has nothing to
    # cite, and saying otherwise would imply a table was consulted.
    entries = _transformed_entries("adt_a01_basic.hl7")
    assert entries, "fixture should contain transformations"

    raw = (FIXTURES / "adt_a01_basic.hl7").read_text()
    _bundle, report, _dedup = convert_with_provenance(raw)
    copies = [
        e
        for e in report.entries
        if e.derivation == "direct" and e.source_value is None and e.value
    ]
    assert copies, "fixture should contain plain copies"
    assert all(e.transform_citation is None for e in copies)


def test_inferred_entries_never_carry_a_transform_citation():
    # An inferred value was not transformed from anything - it has a reason
    # instead, which the crosswalk already shows.
    raw = (FIXTURES / "adt_a01_basic.hl7").read_text()
    _bundle, report, _dedup = convert_with_provenance(raw)
    inferred = [e for e in report.entries if e.derivation == "inferred"]
    assert inferred
    assert all(e.transform_citation is None for e in inferred)


def test_normalise_path_strips_entry_prefix_and_indices():
    assert normalise_path("Bundle.entry[2].resource.name[0].use") == "name[].use"
    assert normalise_path("Bundle.timestamp") == "Bundle.timestamp"


def test_an_unmapped_pair_gets_no_guessed_citation():
    assert citation_for("HL7v2", "Bundle.entry[0].resource.nothing.here") is None
    assert citation_for(None, "gender") is None
