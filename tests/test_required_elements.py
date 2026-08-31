"""The C-CDA entry-template required-element table, and its rule."""

import json
import urllib.request
from pathlib import Path

import pytest

from app.cda.parser import parse_document
from app.cda.required_elements import REQUIRED_ELEMENTS, RULE_ID, check_required_elements
from app.pipeline import validate_any

FIXTURES = Path(__file__).parent / "fixtures"

# The one entry left deliberately incomplete: it exists to prove that a
# Problem Observation with no <value> is skipped rather than crashing.
DELIBERATE = {("ccd_problem_edge_cases.xml", "Problem Observation/value")}

_DOC = (
    '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "{body}</ClinicalDocument>"
)


def test_missing_required_element_is_an_error_naming_the_template():
    document = parse_document(
        _DOC.format(
            body='<entry><observation classCode="OBS" moodCode="EVN">'
            '<templateId root="2.16.840.1.113883.10.20.22.4.4"/>'
            '<code code="55607006" codeSystem="2.16.840.1.113883.6.96"/>'
            "</observation></entry>"
        )
    )
    findings = check_required_elements(document)
    assert {f.segment for f in findings} == {
        "Problem Observation/id",
        "Problem Observation/statusCode",
        "Problem Observation/effectiveTime",
        "Problem Observation/value",
    }
    assert all(f.severity == "error" and f.rule_id == RULE_ID for f in findings)


def test_a_complete_entry_produces_no_findings():
    document = parse_document(
        _DOC.format(
            body='<entry><observation classCode="OBS" moodCode="EVN">'
            '<templateId root="2.16.840.1.113883.10.20.22.4.4"/>'
            '<id root="2.16.840.1.113883.19.5" extension="1"/>'
            '<code code="55607006" codeSystem="2.16.840.1.113883.6.96"/>'
            '<statusCode code="completed"/>'
            '<effectiveTime><low value="20250101"/></effectiveTime>'
            '<value xsi:type="CD" code="38341003" codeSystem="2.16.840.1.113883.6.96"/>'
            "</observation></entry>"
        )
    )
    assert check_required_elements(document) == []


def test_an_unrecognized_template_is_not_checked():
    document = parse_document(
        _DOC.format(
            body='<entry><observation classCode="OBS" moodCode="EVN">'
            '<templateId root="1.2.3.4.5.6.7.8.9"/></observation></entry>'
        )
    )
    assert check_required_elements(document) == []


def test_every_fixture_entry_is_conformant():
    """Every C-CDA fixture's entries carry what their own template
    requires. They did not before this rule existed - the generator's
    medications, immunizations and both Concern Acts were all short of a
    1..1 element, which is what the rule found first."""
    offenders = {}
    for fixture in sorted(FIXTURES.glob("*.xml")):
        try:
            report = validate_any(fixture.read_text(encoding="utf-8"))
        except Exception:
            continue  # ccd_malformed.xml, deliberately unparseable
        missing = sorted(
            f.segment
            for f in report.findings
            if f.rule_id == RULE_ID and (fixture.name, f.segment) not in DELIBERATE
        )
        if missing:
            offenders[fixture.name] = missing
    assert offenders == {}


@pytest.mark.parametrize("document_type", ["CCD", "DischargeSummary", "HistoryAndPhysical"])
def test_generated_documents_are_conformant(document_type):
    from app.generators.registry import generate

    for seed in range(40):
        report = validate_any(generate("CDA", document_type, seed=seed))
        assert [f for f in report.findings if f.rule_id == RULE_ID] == [], f"seed={seed}"


@pytest.mark.network
def test_the_table_still_matches_the_published_snapshots():
    """Transcribed from each template's published StructureDefinition, so
    it can drift. The **snapshot**, not the differential: a differential
    lists only what a template constrains, and reading it reported
    AllergyIntoleranceObservation as requiring nothing at all."""
    # templateId -> the IG's own StructureDefinition name.
    SD_NAMES = {
        "2.16.840.1.113883.10.20.22.4.3": "ProblemConcernAct",
        "2.16.840.1.113883.10.20.22.4.4": "ProblemObservation",
        "2.16.840.1.113883.10.20.22.4.16": "MedicationActivity",
        "2.16.840.1.113883.10.20.22.4.30": "AllergyConcernAct",
        "2.16.840.1.113883.10.20.22.4.7": "AllergyIntoleranceObservation",
        "2.16.840.1.113883.10.20.22.4.52": "ImmunizationActivity",
        "2.16.840.1.113883.10.20.22.4.26": "VitalSignsOrganizer",
        "2.16.840.1.113883.10.20.22.4.27": "VitalSignObservation",
        "2.16.840.1.113883.10.20.22.4.1": "ResultOrganizer",
        "2.16.840.1.113883.10.20.22.4.2": "ResultObservation",
        "2.16.840.1.113883.10.20.22.4.14": "ProcedureActivityProcedure",
        "2.16.840.1.113883.10.20.22.4.38": "SocialHistoryObservation",
        "2.16.840.1.113883.10.20.22.4.45": "FamilyHistoryOrganizer",
        "2.16.840.1.113883.10.20.22.4.46": "FamilyHistoryObservation",
        "2.16.840.1.113883.10.20.22.4.41": "PlannedProcedure",
    }
    assert set(SD_NAMES) == set(REQUIRED_ELEMENTS)
    structure = {"classCode", "moodCode", "typeCode", "templateId", "negationInd", "nullFlavor", "inversionInd"}
    for template_id, sd_name in SD_NAMES.items():
        published = json.load(
            urllib.request.urlopen(f"https://hl7.org/cda/us/ccda/StructureDefinition-{sd_name}.json")
        )
        expected = {
            e["path"].split(".")[-1]
            for e in published["snapshot"]["element"]
            if e.get("min", 0) >= 1 and e["path"].count(".") == 1 and e["path"].split(".")[-1] not in structure
        }
        assert set(REQUIRED_ELEMENTS[template_id][1]) == expected, sd_name
