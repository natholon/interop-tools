"""FHIR R4 conformance of the Bundles this app produces."""

import json
import urllib.request

import pytest
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.organization import Organization

from app.fhir_conformance.checker import BINDING_RULE_ID, INVARIANT_RULE_ID, check_bundle
from app.fhir_conformance.invariants import DOM_6, INVARIANTS
from app.fhir_conformance.tables import REQUIRED_BINDINGS, REQUIRED_ELEMENTS, UNCHECKED_BINDINGS
from app.generators.registry import generate, list_supported_types
from app.pipeline import convert_to_bundle

# The violations this app's own output currently has, with why each is
# here rather than fixed. Pinned rather than tolerated: anything NEW fails
# the sweep below immediately, and closing one of these means deleting its
# line here.
KNOWN_VIOLATIONS = {
    # SIU^S17 maps to Appointment.status "entered-in-error", which app-3
    # does not admit without start and end. The status is a deliberate
    # mapping choice (see app/mappings/siu.py - the HL7 standard draws an
    # explicit S17-vs-S15 distinction that "cancelled" would lose), and
    # R4 forbids the resulting shape. Reported rather than silently
    # resolved either way.
    "app-3",
    # A source document can say a problem concern is active while the
    # problem observation carries an end date - the two come from
    # different elements (see app/cda/problems.py). The mapper reflects
    # what the document said; con-4 forbids the combination.
    "con-4",
}


def _bundle_with(*resources, bundle_type="collection"):
    bundle = Bundle(id="b", type=bundle_type)
    bundle.entry = []
    for index, resource in enumerate(resources):
        from fhir.resources.R4B.bundle import BundleEntry

        bundle.entry.append(BundleEntry(fullUrl=f"urn:uuid:{index}", resource=resource))
    return bundle


def _keys(report):
    return {f.message.split(":")[0] for f in report.findings}


def test_a_code_outside_its_required_binding_is_an_error():
    # The gap this exists for: fhir.resources accepts any string here.
    encounter = Encounter(status="banana", class_fhir=Coding(code="IMP"))
    report = check_bundle(_bundle_with(encounter))
    finding = next(f for f in report.findings if f.rule_id == BINDING_RULE_ID)
    assert finding.severity == "error"
    assert "banana" in finding.message
    assert finding.segment == "Bundle.entry[0].resource.status"
    assert report.is_valid is False


def test_a_valid_code_produces_no_binding_finding():
    encounter = Encounter(status="finished", class_fhir=Coding(code="IMP"))
    assert [f for f in check_bundle(_bundle_with(encounter)).findings if f.rule_id == BINDING_RULE_ID] == []


def test_a_reserved_word_element_is_not_reported_as_absent():
    # Encounter.class is class_fhir on the model. Looking up the spec name
    # directly reported it missing on every Encounter this app builds.
    encounter = Encounter(status="finished", class_fhir=Coding(code="IMP"))
    assert "Encounter.class is required by FHIR R4 and is absent." not in {
        f.message for f in check_bundle(_bundle_with(encounter)).findings
    }


def test_an_invariant_violation_is_reported_with_its_key():
    # org-1: an Organization SHALL have a name or an identifier.
    report = check_bundle(_bundle_with(Organization(id="o")))
    finding = next(f for f in report.findings if f.rule_id == INVARIANT_RULE_ID)
    assert finding.message.startswith("org-1:")
    assert finding.severity == "error"


def test_obs_6_catches_a_value_beside_a_data_absent_reason():
    observation = Observation(
        status="final",
        code=CodeableConcept(text="x"),
        valueString="7",
        dataAbsentReason=CodeableConcept(text="unknown"),
    )
    assert "obs-6" in _keys(check_bundle(_bundle_with(observation)))


def test_dom_6_is_off_by_default_and_available_on_request():
    # It holds for every resource this app builds, so on by default it
    # would bury everything else.
    bundle = _bundle_with(Organization(id="o", name="Acme"))
    assert "dom-6" not in _keys(check_bundle(bundle))
    assert "dom-6" in _keys(check_bundle(bundle, include_narrative_warning=True))


@pytest.mark.parametrize("message_type,trigger", [(mt, te) for mt, te, _ in list_supported_types()])
def test_generated_output_has_no_conformance_violation_beyond_the_known_ones(message_type, trigger):
    for seed in range(8):
        report = check_bundle(convert_to_bundle(generate(message_type, trigger, seed=seed)))
        unexpected = [
            f
            for f in report.findings
            if f.message.split(":")[0] not in KNOWN_VIOLATIONS
        ]
        assert unexpected == [], f"seed={seed}: {[f.message for f in unexpected]}"


def test_the_known_violations_are_still_real():
    """If one stops occurring it has been fixed, and its line in
    KNOWN_VIOLATIONS should go - a pin that no longer pins anything hides
    the next regression."""
    seen = set()
    for message_type, trigger, _ in list_supported_types():
        for seed in range(8):
            report = check_bundle(convert_to_bundle(generate(message_type, trigger, seed=seed)))
            seen |= {f.message.split(":")[0] for f in report.findings}
    assert KNOWN_VIOLATIONS <= seen, f"no longer occurring: {KNOWN_VIOLATIONS - seen}"


@pytest.mark.network
def test_the_tables_still_match_the_published_spec():
    """REQUIRED_ELEMENTS and REQUIRED_BINDINGS are transcribed from the
    published R4 StructureDefinitions, so they can drift from them."""
    for resource_type, expected in sorted(REQUIRED_ELEMENTS.items()):
        profile = json.load(
            urllib.request.urlopen(f"https://hl7.org/fhir/R4/{resource_type.lower()}.profile.json")
        )
        published = tuple(
            sorted(
                e["path"].split(".", 1)[1]
                for e in profile["snapshot"]["element"]
                if e.get("min", 0) >= 1 and e["path"].count(".") == 1
            )
        )
        assert expected == published, resource_type


@pytest.mark.network
def test_every_invariant_still_matches_its_published_expression():
    """The rules are hand-written from these expressions. If HL7 changes
    one, the rule has to be re-read rather than silently drifting."""
    published = {}
    types = set(INVARIANTS) | {"Observation"}
    for resource_type in sorted(types):
        profile = json.load(
            urllib.request.urlopen(f"https://hl7.org/fhir/R4/{resource_type.lower()}.profile.json")
        )
        for constraint in profile["snapshot"]["element"][0].get("constraint", []):
            published[constraint["key"]] = (
                constraint["severity"],
                constraint["human"],
                constraint["expression"],
            )

    implemented = [inv for pairs in INVARIANTS.values() for inv, _ in pairs] + [DOM_6[0]]
    for invariant in implemented:
        severity, human, expression = published[invariant.key]
        assert invariant.severity == severity, invariant.key
        assert invariant.human == human, invariant.key
        # Whitespace differs between the JSON and a wrapped Python string.
        assert " ".join(invariant.expression.split()) == " ".join(expression.split()), invariant.key


@pytest.mark.network
def test_the_unchecked_bindings_are_still_the_ones_that_cannot_expand():
    """They are named rather than skipped, so this confirms they are still
    the only two that need a terminology server."""
    assert set(UNCHECKED_BINDINGS) == {"Binary.contentType", "Composition.confidentiality"}
    for path in UNCHECKED_BINDINGS:
        assert path not in REQUIRED_BINDINGS
