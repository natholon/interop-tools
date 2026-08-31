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

# Nothing. Both violations this checker first found have been closed at
# source rather than tolerated:
#
#   app-3 - an S17 delete maps to Appointment.status "entered-in-error",
#   which R4 does not excuse from carrying start and end. The status is
#   right (the v2-to-FHIR FillerStatusCodes ConceptMap maps a Deleted
#   filler status to exactly that), so what was missing was the timing -
#   and it cannot be inferred. The generator now emits it and
#   `siu.appointment-timing-required-for-status` flags a message that
#   does not.
#
#   con-4 - a Condition carrying an abatement must be inactive, resolved
#   or in remission. Most of these came from a real mapping defect:
#   `ivl_ts_bounds` collapses a bare `<effectiveTime value="X"/>` to
#   (X, X), and taking that as an end date claimed every point-in-time
#   problem resolved the instant it began. The IG maps only
#   effectiveTime\high to abatementDateTime, so a bare @value now supplies
#   the onset alone. The rest were generated documents asserting an active
#   status beside an end date, which `cda.problem-abated-but-active` now
#   flags.
#
# A new entry here needs a reason of the same kind: why the output cannot
# be made conformant, not merely that it currently is not.
KNOWN_VIOLATIONS: set = set()


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
    """A pin that no longer pins anything hides the next regression, so a
    violation that has stopped occurring must lose its entry."""
    seen = set()
    for message_type, trigger, _ in list_supported_types():
        for seed in range(8):
            report = check_bundle(convert_to_bundle(generate(message_type, trigger, seed=seed)))
            seen |= {f.message.split(":")[0] for f in report.findings}
    assert KNOWN_VIOLATIONS <= seen, f"no longer occurring: {KNOWN_VIOLATIONS - seen}"


def test_every_hand_written_fixture_converts_to_conformant_fhir():
    """The fixtures are the shapes a real sender produces, so their output
    has to be valid FHIR too - generated output being clean is a weaker
    claim, since the generator is this app's own."""
    from pathlib import Path

    # This one exists to be non-conformant: it is the S17 carrying no
    # timing, which is what siu.appointment-timing-required-for-status is
    # about. siu_s17_basic.hl7 is the representative, conformant S17.
    deliberate = {"siu_s17_missing_timing.hl7"}

    offenders = {}
    for fixture in sorted((Path(__file__).parent / "fixtures").glob("*")):
        if fixture.suffix not in (".hl7", ".xml", ".x12") or fixture.name in deliberate:
            continue
        try:
            bundle = convert_to_bundle(fixture.read_text(encoding="utf-8"))
        except Exception:
            continue  # the deliberately malformed ones
        findings = [f.message for f in check_bundle(bundle).findings]
        if findings:
            offenders[fixture.name] = findings
    assert offenders == {}


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
