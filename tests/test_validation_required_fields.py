"""The HL7v2 required-field table, and the rule driven by it."""

import csv
import io as _io
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

import pytest

from app.hl7.parser import parse_message
from app.pipeline import validate_any
from app.validation.required_fields import REQUIRED_FIELDS, RULE_ID, check_required_fields

FIXTURES = Path(__file__).parent / "fixtures"


def _segment(text: str, name: str):
    return next(s for s in parse_message(text) if str(s[0][0]) == name)


def test_missing_required_field_is_an_error_naming_the_standard_s_own_name():
    message = "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|1|P|2.5\rPID|1||||Doe^Jane\r"
    findings = check_required_fields(_segment(message, "PID"), "PID")
    assert [f.field for f in findings] == [3]
    assert findings[0].severity == "error"
    assert findings[0].rule_id == RULE_ID
    assert "Patient Identifier List" in findings[0].message


def test_a_composite_populating_only_a_later_component_counts_as_present():
    # SCH-11's own start and end are TQ.4 and TQ.5; TQ.1 is empty. Reading
    # component 1 - which `field_str` returns by default - called the field
    # missing on every SIU that timed itself through SCH-11.
    message = (
        "MSH|^~\\&|A|B|C|D|20260101120000||SIU^S12|1|P|2.5\r"
        "SCH|P1|F1|||||||||^^^20260101090000^20260101093000\r"
    )
    findings = check_required_fields(_segment(message, "SCH"), "SCH")
    assert 11 not in {f.field for f in findings}


def test_a_segment_with_no_table_entry_is_not_checked():
    message = "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|1|P|2.5\rZZZ|1\r"
    assert check_required_fields(_segment(message, "ZZZ"), "ZZZ") == []


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("validation_generic_pid_3_5_missing.hl7", {("PID", 3), ("PID", 5)}),
        ("validation_generic_msh7_missing.hl7", {("MSH", 7)}),
        ("validation_generic_msh10_missing.hl7", {("MSH", 10)}),
    ],
)
def test_deliberately_incomplete_fixtures_report_exactly_what_they_omit(fixture, expected):
    report = validate_any((FIXTURES / fixture).read_text())
    assert {(f.segment, f.field) for f in report.findings if f.rule_id == RULE_ID} == expected
    assert report.is_valid is False


def test_every_other_fixture_is_conformant():
    """Every hand-written HL7v2 fixture bar the three above carries the
    fields the standard requires. They did not, before this rule existed -
    which is the same thing it found in the C-CDA corpus."""
    deliberate = {
        "validation_generic_pid_3_5_missing.hl7",
        "validation_generic_msh7_missing.hl7",
        "validation_generic_msh10_missing.hl7",
        "adt_a01_malformed.hl7",
        # An AIP naming no resource at all, which is the point of it. Its
        # AIP-4 is left off too: with no participant built there is nowhere
        # for the resource type to go, and the register would report a
        # value the mapper never had a chance to read.
        "validation_siu_participant_empty.hl7",
    }
    offenders = {}
    for fixture in sorted(FIXTURES.glob("*.hl7")):
        if fixture.name in deliberate:
            continue
        missing = {
            f"{f.segment}-{f.field}"
            for f in validate_any(fixture.read_text()).findings
            if f.rule_id == RULE_ID
        }
        if missing:
            offenders[fixture.name] = sorted(missing)
    assert offenders == {}


@pytest.mark.network
def test_the_table_still_matches_the_published_ig():
    """The table is transcribed from the v2-to-FHIR segment sheets, so it
    can drift from them. Marked `network` because it fetches; run with
    `-m network` rather than on every pass."""
    listing = json.load(
        urllib.request.urlopen("https://api.github.com/repos/HL7/v2-to-fhir/contents/mappings/segments")
    )

    def load(url):
        rows = list(csv.reader(_io.StringIO(urllib.request.urlopen(url).read().decode("utf-8-sig"))))
        header_row = next((i for i, r in enumerate(rows) if "Identifier" in r), None)
        if header_row is None:
            return {}
        header = [c.strip() for c in rows[header_row]]
        # The FIRST occurrence of each name: the sheets repeat them for the
        # FHIR target, and taking the last reports everything as optional.
        ident_i, min_i = header.index("Identifier"), header.index("Cardinality - Min")
        out = {}
        for row in rows[header_row + 1:]:
            if len(row) <= min_i:
                continue
            ident, low = row[ident_i].strip(), row[min_i].strip()
            # A few cells hold the whole cardinality ("0..2"); the leading
            # integer is the minimum either way.
            match = re.match(r"-?\d+", low)
            if re.fullmatch(r"[A-Z0-9]+-\d+", ident) and match:
                out.setdefault(ident, int(match.group(0)))
        return out

    sheets = defaultdict(list)
    for entry in listing:
        m = re.match(r"HL7 Segment - FHIR R4_ ([A-Z0-9]+)\[", entry["name"])
        if m:
            sheets[m.group(1)].append(entry)

    for segment_name, expected in REQUIRED_FIELDS.items():
        maps = [m for m in (load(f["download_url"]) for f in sheets[segment_name]) if m]
        assert maps, segment_name
        published = {
            int(k.split("-")[1])
            for k in set().union(*maps)
            if any(m.get(k, 0) > 0 for m in maps)
        }
        # MSH-1 and MSH-2 are the delimiters themselves - see the module.
        published -= {1, 2} if segment_name == "MSH" else set()
        assert {num for num, _ in expected} == published, segment_name
