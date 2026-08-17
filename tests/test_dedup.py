import uuid
from pathlib import Path

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.reference import Reference

from app.dedup import ResourceMerge, deduplicate_bundle
from app.pipeline import convert_to_bundle


def _entry(resource):
    return BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource)


def _bundle(*resources):
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")
    bundle.entry = [_entry(r) for r in resources]
    return bundle


def _npi(value: str) -> Identifier:
    return Identifier(system="http://hl7.org/fhir/sid/us-npi", value=value)


def test_identifier_matched_practitioners_are_merged():
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1999996666")])
    p2 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1999996666")])
    bundle = _bundle(p1, p2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 1
    assert len(result.bundle.entry) == 1
    assert result.bundle.entry[0].resource.id == p1.id
    assert result.merges == (ResourceMerge(resource_type="Practitioner", kept_id=p1.id, removed_ids=(p2.id,)),)


def test_different_identifiers_are_not_merged():
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1111111111")])
    p2 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("2222222222")])
    bundle = _bundle(p1, p2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 0
    assert len(result.bundle.entry) == 2


def test_name_fallback_merges_practitioners_with_no_identifier():
    p1 = Practitioner(id=str(uuid.uuid4()), name=[{"family": "Kildare", "given": ["Ben"]}])
    p2 = Practitioner(id=str(uuid.uuid4()), name=[{"family": "KILDARE", "given": ["ben"]}])
    bundle = _bundle(p1, p2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 1


def test_name_fallback_merges_organizations_with_no_identifier():
    o1 = Organization(id=str(uuid.uuid4()), name="Premier Billing Service")
    o2 = Organization(id=str(uuid.uuid4()), name="  premier billing service  ")
    bundle = _bundle(o1, o2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 1


def test_name_fallback_merges_locations_with_no_identifier():
    l1 = Location(id=str(uuid.uuid4()), name="Main Clinic")
    l2 = Location(id=str(uuid.uuid4()), name="main clinic")
    bundle = _bundle(l1, l2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 1


def test_resource_with_neither_identifier_nor_name_is_never_merged():
    p1 = Practitioner(id=str(uuid.uuid4()))
    p2 = Practitioner(id=str(uuid.uuid4()))
    bundle = _bundle(p1, p2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 0
    assert len(result.bundle.entry) == 2


def test_identifier_match_takes_priority_over_name_and_never_collides_with_name_only_match():
    # A Practitioner matched by identifier must never be silently folded
    # together with a different Practitioner that merely shares a name -
    # the ("identifier", ...) / ("name", ...) key tagging in _identity_key
    # exists specifically so these two kinds of match can never collide.
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1111111111")], name=[{"family": "Smith"}])
    p2 = Practitioner(id=str(uuid.uuid4()), name=[{"family": "Smith"}])
    bundle = _bundle(p1, p2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 0


def test_condition_and_other_clinical_resources_are_never_deduplicated():
    # Two Conditions with the identical code are not necessarily the same
    # real occurrence - Condition is deliberately out of scope, unlike the
    # four "identity" resource types.
    patient_id = str(uuid.uuid4())
    c1 = Condition(id=str(uuid.uuid4()), subject=Reference(reference=f"urn:uuid:{patient_id}"))
    c2 = Condition(id=str(uuid.uuid4()), subject=Reference(reference=f"urn:uuid:{patient_id}"))
    bundle = _bundle(c1, c2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 0
    assert len(result.bundle.entry) == 2


def test_references_to_removed_duplicates_are_rewritten_to_the_kept_resource():
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1999996666")])
    p2 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1999996666")])
    encounter = Encounter(
        id=str(uuid.uuid4()),
        status="unknown",
        class_fhir={"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        participant=[{"individual": Reference(reference=f"urn:uuid:{p2.id}")}],
    )
    bundle = _bundle(p1, p2, encounter)

    result = deduplicate_bundle(bundle)

    kept_encounter = next(e.resource for e in result.bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert kept_encounter.participant[0].individual.reference == f"urn:uuid:{p1.id}"


def test_original_bundle_is_not_mutated():
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1999996666")])
    p2 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1999996666")])
    bundle = _bundle(p1, p2)

    deduplicate_bundle(bundle)

    assert len(bundle.entry) == 2


def test_multiple_independent_duplicate_groups_are_each_merged():
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1111111111")])
    p2 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1111111111")])
    org1 = Organization(id=str(uuid.uuid4()), identifier=[Identifier(system="urn:oid:1.2.3", value="A")])
    org2 = Organization(id=str(uuid.uuid4()), identifier=[Identifier(system="urn:oid:1.2.3", value="A")])
    bundle = _bundle(p1, p2, org1, org2)

    result = deduplicate_bundle(bundle)

    assert result.merged_count == 2
    assert len(result.bundle.entry) == 2
    assert {m.resource_type for m in result.merges} == {"Practitioner", "Organization"}


def test_entry_order_among_survivors_is_preserved():
    patient = Patient(id=str(uuid.uuid4()))
    p1 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1111111111")])
    p2 = Practitioner(id=str(uuid.uuid4()), identifier=[_npi("1111111111")])
    org = Organization(id=str(uuid.uuid4()), name="Org")
    bundle = _bundle(patient, p1, org, p2)

    result = deduplicate_bundle(bundle)

    resource_types = [e.resource.get_resource_type() for e in result.bundle.entry]
    assert resource_types == ["Patient", "Practitioner", "Organization"]


def test_real_837p_fixture_merges_billing_and_rendering_provider():
    # The real motivating case this module exists for: a solo practitioner
    # billed as both the Billing Provider (NM1*85) and Rendering Provider
    # (NM1*82) under the same NPI - see tests/fixtures/edi_837p_basic.x12,
    # adapted directly from the official X12.org example.
    fixture = (Path(__file__).parent / "fixtures" / "edi_837p_basic.x12").read_text()
    bundle = convert_to_bundle(fixture)
    practitioners_before = [e for e in bundle.entry if e.resource.get_resource_type() == "Practitioner"]
    assert len(practitioners_before) == 2

    result = deduplicate_bundle(bundle)

    practitioners_after = [e for e in result.bundle.entry if e.resource.get_resource_type() == "Practitioner"]
    assert len(practitioners_after) == 1
    kept_id = practitioners_after[0].resource.id

    claim = next(e.resource for e in result.bundle.entry if e.resource.get_resource_type() == "Claim")
    assert claim.provider.reference == f"urn:uuid:{kept_id}"
    assert claim.careTeam[0].provider.reference == f"urn:uuid:{kept_id}"
