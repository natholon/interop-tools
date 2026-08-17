"""Data Specification provenance tests for X12 EDI - the app/edi/ mirror
of tests/test_provenance_recorder.py/test_provenance_recorder_cda.py, kept
in its own file since X12 is a genuinely different input format (delimited
text with self-describing delimiters, not HL7v2 pipe-delimited or XML) with
its own parsing/dispatch layer, the same "own file per format" discipline
test_provenance_recorder_cda.py already established.

Scope: 270 (Eligibility Inquiry), 271 (Eligibility Response), 276 (Claim
Status Request), 277 (Claim Status Response), 278 (Health Care Services
Review - Request for Review and Response), 835 (Health Care Claim
Payment/Advice), 837P (Health Care Claim: Professional), 837I (Health Care
Claim: Institutional), and 837D (Health Care Claim: Dental) - the full
"big five" HIPAA EDI transaction-set suite, every EDI family this app
converts to FHIR, is now instrumented (see app/provenance/dispatch.py's own
_INSTRUMENTED_TRANSACTION_SETS - 837P/837I/837D share ST01="837" but are
tracked independently via resolve_837_variant()). There is no longer an
EDI family left to exercise the "converts but unsupported" path with -
only C-CDA's own remaining sections still do (see
test_provenance_recorder_cda.py)."""

import itertools
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.edi.parser import first_transaction_set, parse_interchange
from app.edi.registry import get_transaction_builder
from app.provenance.location import edi_location
from app.provenance.recorder import ProvenanceRecorder
from app.provenance.resolver import resolve_bundle_paths

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _deterministic_uuids():
    return (uuid.UUID(int=i) for i in itertools.count())


def _build_bundle(fixture_name: str, recorder=None):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    builder = get_transaction_builder(transaction_set.st01, transaction_set.st03)
    return builder.build_bundle(transaction_set, interchange.delimiters, recorder=recorder)


# Every instrumented EDI family's own real fixtures - the full "big five"
# HIPAA suite, now including 837D.
_EDI_FIXTURES = [
    "edi_270_basic.x12",
    "edi_270_no_dependent.x12",
    "edi_271_basic.x12",
    "edi_271_rejected.x12",
    "edi_276_basic.x12",
    "edi_276_no_dependent.x12",
    "edi_277_basic.x12",
    "edi_277_error_status.x12",
    "edi_278_request_basic.x12",
    "edi_278_request_with_dependent.x12",
    "edi_278_response_certified.x12",
    "edi_278_response_denied.x12",
    "edi_835_basic.x12",
    "edi_835_multi_claim.x12",
    "edi_837p_basic.x12",
    "edi_837p_with_dependent.x12",
    "edi_837i_basic.x12",
    "edi_837i_with_dependent.x12",
    "edi_837d_basic.x12",
    "edi_837d_no_dependent.x12",
]


@pytest.mark.parametrize("fixture", _EDI_FIXTURES)
def test_edi_provenance_recording_does_not_change_bundle_output(fixture):
    # The critical regression test, mirroring every earlier format's own
    # version exactly: instrumenting a transaction-set builder to also
    # record provenance must never change what it actually builds.
    interchange = parse_interchange(read_fixture(fixture))
    transaction_set = first_transaction_set(interchange)
    builder = get_transaction_builder(transaction_set.st01, transaction_set.st03)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        untraced = builder.build_bundle(transaction_set, interchange.delimiters)

    with patch("uuid.uuid4", side_effect=_deterministic_uuids()):
        recorder = ProvenanceRecorder(source_format="EDI")
        traced = builder.build_bundle(transaction_set, interchange.delimiters, recorder=recorder)

    assert untraced.model_dump(exclude_none=True) == traced.model_dump(exclude_none=True)

    entries = resolve_bundle_paths(traced, recorder)
    assert len(entries) == len(recorder.facts)


def test_edi_270_basic_crosswalk_matches_known_field_values():
    # Direct content-correctness check against edi_270_basic.x12's own
    # already-known values (test_eligibility_270_mapping.py's own
    # assertions) - a payer Organization, a provider Organization, a
    # subscriber Patient, a dependent Patient, a Coverage, and a
    # CoverageEligibilityRequest.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_270_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    payer = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Organization")
    payer_index = next(i for i, e in enumerate(bundle.entry) if e.resource is payer)
    payer_name = by_path[f"Bundle.entry[{payer_index}].resource.name"]
    assert payer_name.value == "ACME HEALTH PLAN"
    assert payer_name.source_location == edi_location("NM1", 3)
    payer_id = by_path[f"Bundle.entry[{payer_index}].resource.identifier[0].value"]
    assert payer_id.value == "PAYERID001"
    assert payer_id.source_location == edi_location("NM1", 9)

    subscriber = next(
        e.resource
        for e in bundle.entry
        if e.resource.get_resource_type() == "Patient" and e.resource.name and e.resource.name[0].given[0] == "JANE"
    )
    subscriber_index = next(i for i, e in enumerate(bundle.entry) if e.resource is subscriber)
    subscriber_family = by_path[f"Bundle.entry[{subscriber_index}].resource.name[0].family"]
    assert subscriber_family.value == "DOE"
    subscriber_birth = by_path[f"Bundle.entry[{subscriber_index}].resource.birthDate"]
    assert subscriber_birth.value == "1980-01-01"
    assert subscriber_birth.source_location == edi_location("DMG", 2)
    subscriber_gender = by_path[f"Bundle.entry[{subscriber_index}].resource.gender"]
    assert subscriber_gender.value == "female"
    assert subscriber_gender.source_location == edi_location("DMG", 3)

    dependent = next(
        e.resource
        for e in bundle.entry
        if e.resource.get_resource_type() == "Patient" and e.resource.name and e.resource.name[0].given[0] == "JIMMY"
    )
    dependent_index = next(i for i, e in enumerate(bundle.entry) if e.resource is dependent)
    dependent_family = by_path[f"Bundle.entry[{dependent_index}].resource.name[0].family"]
    assert dependent_family.value == "DOE"

    request = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityRequest")
    request_index = next(i for i, e in enumerate(bundle.entry) if e.resource is request)

    status_entry = by_path[f"Bundle.entry[{request_index}].resource.status"]
    assert status_entry.derivation == "inferred"
    assert status_entry.value == "active"
    purpose_entry = by_path[f"Bundle.entry[{request_index}].resource.purpose[0]"]
    assert purpose_entry.derivation == "inferred"
    assert purpose_entry.value == "benefits"

    item_entry = by_path[f"Bundle.entry[{request_index}].resource.item[0].coding[0].code"]
    assert item_entry.value == "30"
    assert item_entry.source_location == edi_location("EQ", 1)

    serviced_date_entry = by_path[f"Bundle.entry[{request_index}].resource.servicedDate"]
    assert serviced_date_entry.source_location == edi_location("DTP", 3)

    created_entry = by_path[f"Bundle.entry[{request_index}].resource.created"]
    assert created_entry.source_location == f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}"

    bundle_identifier_entry = by_path["Bundle.identifier.value"]
    assert bundle_identifier_entry.value == "10001234"
    assert bundle_identifier_entry.source_location == edi_location("BHT", 3)

    timestamp_entry = by_path["Bundle.timestamp"]
    assert timestamp_entry.value == created_entry.value


def test_edi_271_basic_crosswalk_matches_known_field_values():
    # edi_271_basic.x12 carries two EB groups (both active/in-network) -
    # both must resolve to their own, independently-indexed insurance items.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_271_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse")
    response_index = next(i for i, e in enumerate(bundle.entry) if e.resource is response)

    item0_code = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].item[0].coding[0].code"]
    assert item0_code.value == "30"
    item0_excluded = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].item[0].excluded"]
    # ProvenanceEntry.value is str | None - a recorded bool is stringified
    # by pydantic, the same "value is always a display string" contract
    # every other recorded fact in this app already follows.
    assert item0_excluded.value == "False"
    item0_description = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].item[0].description"]
    assert item0_description.value == "Gold Plan"
    item0_network = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].item[0].network.text"]
    assert item0_network.value == "In Network"

    item1_code = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].item[1].coding[0].code"]
    assert item1_code.value == "88"
    item1_description = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].item[1].description"]
    assert item1_description.value == "Dental Plan"

    inforce_entry = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].inforce"]
    assert inforce_entry.value == "True"
    assert inforce_entry.derivation == "direct"
    assert inforce_entry.source_location == edi_location("EB", 1)

    outcome_entry = by_path[f"Bundle.entry[{response_index}].resource.outcome"]
    assert outcome_entry.derivation == "inferred"
    assert outcome_entry.value == "complete"
    assert f"Bundle.entry[{response_index}].resource.disposition" not in by_path


def test_edi_271_rejected_records_direct_outcome_and_disposition():
    # edi_271_rejected.x12's own AAA01="N" rejection must flip outcome to a
    # direct (not inferred) fact, with disposition recorded too - and
    # insurance.inforce, with no EB01="1" anywhere, must be the inferred
    # false default.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_271_rejected.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse")
    response_index = next(i for i, e in enumerate(bundle.entry) if e.resource is response)

    outcome_entry = by_path[f"Bundle.entry[{response_index}].resource.outcome"]
    assert outcome_entry.derivation == "direct"
    assert outcome_entry.value == "error"
    assert outcome_entry.source_location == edi_location("AAA", 1)

    disposition_entry = by_path[f"Bundle.entry[{response_index}].resource.disposition"]
    assert disposition_entry.value == "Rejected: 72"
    assert disposition_entry.source_location == edi_location("AAA", 3)

    inforce_entry = by_path[f"Bundle.entry[{response_index}].resource.insurance[0].inforce"]
    assert inforce_entry.derivation == "inferred"
    assert inforce_entry.value == "False"


def test_edi_270_no_dependent_records_only_subscriber_facts():
    # edi_270_no_dependent.x12 has no 2000D loop at all - only one Patient
    # (the subscriber) should carry any facts, and the resolved crosswalk
    # must have exactly one Patient's worth of name/birthDate/gender facts.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_270_no_dependent.x12", recorder=recorder)
    patients = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 1

    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == len(recorder.facts)
    family_facts = [e for e in entries if e.fhir_path.endswith("resource.name[0].family")]
    assert len(family_facts) == 1
    assert family_facts[0].value == "SMITH"


def test_edi_837d_basic_crosswalk_matches_known_field_values():
    # edi_837d_basic.x12's own claim carries two service lines, neither
    # with its own DTP*472 - both fall back to the one claim-level DTP*472
    # (the real regression case resolve_line_dtp_raw_date's own claim-wide
    # default exists for), and the first line alone carries a TOO segment
    # (tooth number + two surfaces).
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_837d_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)

    type_entry = by_path[f"Bundle.entry[{claim_index}].resource.type.coding[0].code"]
    assert type_entry.value == "oral"
    assert type_entry.derivation == "inferred"

    item0_procedure = by_path[f"Bundle.entry[{claim_index}].resource.item[0].productOrService.coding[0].code"]
    assert item0_procedure.value == "D2150"
    assert item0_procedure.source_location == edi_location("SV3", 1, component=2)

    item0_date = by_path[f"Bundle.entry[{claim_index}].resource.item[0].servicedDate"]
    assert item0_date.derivation == "direct"
    # Neither line carries its own DTP*472 in this fixture - both must
    # resolve from the one claim-level default, disambiguated via
    # segment_repetition=0 the same way multi-HI-segment 837I diagnoses are.
    assert item0_date.source_location == edi_location("DTP", 3, segment_repetition=0)
    item1_date = by_path[f"Bundle.entry[{claim_index}].resource.item[1].servicedDate"]
    assert item1_date.source_location == edi_location("DTP", 3, segment_repetition=0)

    item0_body_site = by_path[f"Bundle.entry[{claim_index}].resource.item[0].bodySite.coding[0].code"]
    assert item0_body_site.value == "12"
    assert item0_body_site.source_location == edi_location("TOO", 2)
    sub_site0 = by_path[f"Bundle.entry[{claim_index}].resource.item[0].subSite[0].coding[0].code"]
    assert sub_site0.value == "M"
    sub_site1 = by_path[f"Bundle.entry[{claim_index}].resource.item[0].subSite[1].coding[0].code"]
    assert sub_site1.value == "O"
    # The second line has no TOO segment at all - no bodySite/subSite facts.
    assert f"Bundle.entry[{claim_index}].resource.item[1].bodySite.coding[0].code" not in by_path

    care_team_entry = by_path[f"Bundle.entry[{claim_index}].resource.careTeam[0].role.coding[0].code"]
    assert care_team_entry.derivation == "inferred"
    assert care_team_entry.value == "primary"

    identifier_entry = by_path[f"Bundle.entry[{claim_index}].resource.identifier[0].value"]
    assert identifier_entry.derivation == "direct"
    assert identifier_entry.source_location == edi_location("CLM", 1)


def test_edi_837d_no_dependent_records_diagnosis_and_per_line_date():
    # edi_837d_no_dependent.x12's own single service line has a resolvable
    # diagnosis pointer AND its own per-line DTP*472 - proving the per-line
    # branch of resolve_line_dtp_raw_date's own precedence order, the
    # complement of the basic fixture's claim-level-fallback case above.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_837d_no_dependent.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)

    diagnosis_entry = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[0].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis_entry.value == "K0290"
    assert diagnosis_entry.source_location == edi_location("HI", 1, component=2, segment_repetition=0)

    pointer_entry = by_path[f"Bundle.entry[{claim_index}].resource.item[0].diagnosisSequence"]
    assert pointer_entry.value == "1"
    assert pointer_entry.source_location == edi_location("SV3", 11)

    date_entry = by_path[f"Bundle.entry[{claim_index}].resource.item[0].servicedDate"]
    # No segment_repetition suffix - the per-line DTP fired, not the
    # claim-level fallback.
    assert date_entry.source_location == edi_location("DTP", 3)

    patients = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 1


def test_edi_837i_basic_crosswalk_matches_known_field_values():
    # edi_837i_basic.x12's own claim carries two service lines and its own
    # HI segments split across a Principal (1 diagnosis) and an Other (2
    # diagnoses) HI segment - the real regression case for the
    # segment_repetition disambiguation fix.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_837i_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)

    type_entry = by_path[f"Bundle.entry[{claim_index}].resource.type.coding[0].code"]
    assert type_entry.value == "institutional"
    assert type_entry.derivation == "inferred"

    diagnosis0 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[0].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis0.value == "3669"
    assert diagnosis0.source_location == edi_location("HI", 1, component=2, segment_repetition=0)
    diagnosis1 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[1].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis1.value == "4019"
    assert diagnosis1.source_location == edi_location("HI", 1, component=2, segment_repetition=1)
    diagnosis2 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[2].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis2.value == "79431"
    assert diagnosis2.source_location == edi_location("HI", 2, component=2, segment_repetition=1)
    # The two HI segments' own diagnoses land at distinct source locations
    # even though both include an intra-segment position 1.
    assert diagnosis0.source_location != diagnosis1.source_location

    discharge_category_entry = by_path[f"Bundle.entry[{claim_index}].resource.supportingInfo[0].category.coding[0].code"]
    assert discharge_category_entry.derivation == "inferred"
    assert discharge_category_entry.value == "discharge"
    discharge_code_entry = by_path[f"Bundle.entry[{claim_index}].resource.supportingInfo[0].code.coding[0].code"]
    assert discharge_code_entry.value == "01"
    assert discharge_code_entry.source_location == edi_location("CL1", 3)

    item0_procedure = by_path[f"Bundle.entry[{claim_index}].resource.item[0].productOrService.coding[0].code"]
    assert item0_procedure.value == "85025"
    assert item0_procedure.source_location == edi_location("SV2", 2, component=2)
    item0_revenue = by_path[f"Bundle.entry[{claim_index}].resource.item[0].revenue.coding[0].code"]
    assert item0_revenue.value == "0305"
    assert item0_revenue.source_location == edi_location("SV2", 1)

    care_team_entry = by_path[f"Bundle.entry[{claim_index}].resource.careTeam[0].role.coding[0].code"]
    assert care_team_entry.derivation == "inferred"
    assert care_team_entry.value == "primary"


def test_edi_837i_with_dependent_records_text_fallback_for_procedure_less_line():
    # edi_837i_with_dependent.x12's own single service line has no SV2-02
    # procedure composite, only a revenue code - productOrService must fall
    # back to the disclosed "Revenue code X" text, recorded as a direct
    # fact (the revenue code itself is real), not an inferred placeholder.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_837i_with_dependent.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)

    product_entry = by_path[f"Bundle.entry[{claim_index}].resource.item[0].productOrService.text"]
    assert product_entry.value == "Revenue code 0120"
    assert product_entry.derivation == "direct"
    assert product_entry.source_location == edi_location("SV2", 1)

    patients = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 2


def test_edi_837p_basic_crosswalk_matches_known_field_values():
    # edi_837p_basic.x12's own claim carries two service lines - one with
    # both diagnosis pointers resolved, one with only the first - plus a
    # rendering provider distinct from the billing provider.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_837p_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)

    status_entry = by_path[f"Bundle.entry[{claim_index}].resource.status"]
    assert status_entry.derivation == "inferred"
    type_entry = by_path[f"Bundle.entry[{claim_index}].resource.type.coding[0].code"]
    assert type_entry.value == "professional"
    use_entry = by_path[f"Bundle.entry[{claim_index}].resource.use"]
    assert use_entry.value == "claim"
    priority_entry = by_path[f"Bundle.entry[{claim_index}].resource.priority.text"]
    assert priority_entry.derivation == "inferred"

    identifier_entry = by_path[f"Bundle.entry[{claim_index}].resource.identifier[0].value"]
    assert identifier_entry.value == "26407789"
    assert identifier_entry.source_location == edi_location("CLM", 1)
    total_entry = by_path[f"Bundle.entry[{claim_index}].resource.total.value"]
    assert total_entry.value == "79.04"
    assert total_entry.source_location == edi_location("CLM", 2)

    diagnosis0 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[0].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis0.value == "J209"
    diagnosis1 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[1].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis1.value == "E119"

    item0_procedure = by_path[f"Bundle.entry[{claim_index}].resource.item[0].productOrService.coding[0].code"]
    assert item0_procedure.value == "99213"
    assert item0_procedure.source_location == edi_location("SV1", 1, component=2)
    item0_price = by_path[f"Bundle.entry[{claim_index}].resource.item[0].unitPrice.value"]
    assert item0_price.value == "43"
    item0_pointers = by_path[f"Bundle.entry[{claim_index}].resource.item[0].diagnosisSequence"]
    assert item0_pointers.value == "1,2"
    assert item0_pointers.source_location == edi_location("SV1", 7)
    item0_location = by_path[f"Bundle.entry[{claim_index}].resource.item[0].locationCodeableConcept.coding[0].code"]
    assert item0_location.value == "11"
    assert item0_location.source_location == edi_location("CLM", 5, component=1)

    item1_pointers = by_path[f"Bundle.entry[{claim_index}].resource.item[1].diagnosisSequence"]
    assert item1_pointers.value == "1"

    care_team_entry = by_path[f"Bundle.entry[{claim_index}].resource.careTeam[0].role.coding[0].code"]
    assert care_team_entry.derivation == "inferred"
    assert care_team_entry.value == "primary"

    created_entry = by_path[f"Bundle.entry[{claim_index}].resource.created"]
    assert created_entry.derivation == "direct"
    assert created_entry.source_location == f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}"


def test_edi_837p_with_dependent_records_both_patients():
    # edi_837p_with_dependent.x12's own dependent must resolve as its own
    # Patient's worth of facts, and the claim's own single service line has
    # no rendering provider - no careTeam fact should appear at all.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_837p_with_dependent.x12", recorder=recorder)
    patients = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 2

    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}
    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)
    assert f"Bundle.entry[{claim_index}].resource.careTeam[0].role.coding[0].code" not in by_path

    given_facts = {e.value for e in entries if e.fhir_path.endswith("resource.name[0].given[0]")}
    assert given_facts == {"JANE", "TED"}


def test_edi_278_request_basic_crosswalk_matches_known_field_values():
    # edi_278_request_basic.x12's own Claim carries two diagnoses (from one
    # HI segment's two composites) and no UM segment - proving the
    # productOrService fallback text is used and disclosed as inferred.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_278_request_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)

    status_entry = by_path[f"Bundle.entry[{claim_index}].resource.status"]
    assert status_entry.derivation == "inferred"
    assert status_entry.value == "active"
    use_entry = by_path[f"Bundle.entry[{claim_index}].resource.use"]
    assert use_entry.derivation == "inferred"
    assert use_entry.value == "preauthorization"
    type_entry = by_path[f"Bundle.entry[{claim_index}].resource.type.coding[0].code"]
    assert type_entry.derivation == "inferred"
    assert type_entry.value == "professional"
    priority_entry = by_path[f"Bundle.entry[{claim_index}].resource.priority.text"]
    assert priority_entry.derivation == "inferred"

    product_entry = by_path[f"Bundle.entry[{claim_index}].resource.item[0].productOrService.text"]
    assert product_entry.derivation == "inferred"
    assert product_entry.value == "Unspecified service"

    diagnosis0 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[0].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis0.value == "1831"
    assert diagnosis0.source_location == edi_location("HI", 1, component=2)
    diagnosis1 = by_path[f"Bundle.entry[{claim_index}].resource.diagnosis[1].diagnosisCodeableConcept.coding[0].code"]
    assert diagnosis1.value == "2630"
    assert diagnosis1.source_location == edi_location("HI", 2, component=2)

    created_entry = by_path[f"Bundle.entry[{claim_index}].resource.created"]
    assert created_entry.derivation == "direct"
    assert created_entry.source_location == f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}"


def test_edi_278_response_certified_records_direct_outcome_and_item_category():
    # edi_278_response_certified.x12's own UM segment IS present (unlike
    # the basic request fixture), so productOrService must resolve direct
    # from UM03, and the ClaimResponse's own outcome/preAuthRef/adjudication
    # must all resolve direct from HCR.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_278_response_certified.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    claim_index = next(i for i, e in enumerate(bundle.entry) if e.resource is claim)
    product_entry = by_path[f"Bundle.entry[{claim_index}].resource.item[0].productOrService.coding[0].code"]
    assert product_entry.derivation == "direct"
    assert product_entry.source_location == edi_location("UM", 3)

    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "ClaimResponse")
    response_index = next(i for i, e in enumerate(bundle.entry) if e.resource is response)

    outcome_entry = by_path[f"Bundle.entry[{response_index}].resource.outcome"]
    assert outcome_entry.derivation == "direct"
    assert outcome_entry.value == "complete"
    assert outcome_entry.source_location == edi_location("HCR", 1)
    assert outcome_entry.source_value == "A1"

    preauth_entry = by_path[f"Bundle.entry[{response_index}].resource.preAuthRef"]
    assert preauth_entry.value == "AUTH0001"
    assert preauth_entry.source_location == edi_location("HCR", 2)

    adjudication_entry = by_path[f"Bundle.entry[{response_index}].resource.item[0].adjudication[0].category.coding[0].code"]
    assert adjudication_entry.value == "A1"
    assert adjudication_entry.source_location == edi_location("HCR", 1)

    # ClaimResponse.created mirrors the referenced Claim's own created value
    # exactly - the "created" fact must be recorded consistently on both
    # resources (a regression proof for the datetime-vs-string formatting
    # fix disclosed in prior_auth.py's own _build_claim_response docstring).
    response_created = by_path[f"Bundle.entry[{response_index}].resource.created"]
    claim_created = by_path[f"Bundle.entry[{claim_index}].resource.created"]
    assert response_created.value == claim_created.value


def test_edi_278_response_denied_records_two_adjudication_entries():
    # edi_278_response_denied.x12's own HCR carries both an action code
    # (A3, Not Certified) and a reason code (HCR03) - both must resolve to
    # their own, independently-indexed adjudication[] entries.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_278_response_denied.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "ClaimResponse")
    response_index = next(i for i, e in enumerate(bundle.entry) if e.resource is response)

    outcome_entry = by_path[f"Bundle.entry[{response_index}].resource.outcome"]
    assert outcome_entry.value == "complete"
    assert outcome_entry.source_value == "A3"

    action_entry = by_path[f"Bundle.entry[{response_index}].resource.item[0].adjudication[0].category.coding[0].code"]
    assert action_entry.value == "A3"
    assert action_entry.source_location == edi_location("HCR", 1)
    reason_entry = by_path[f"Bundle.entry[{response_index}].resource.item[0].adjudication[1].category.coding[0].code"]
    assert reason_entry.source_location == edi_location("HCR", 3)

    assert f"Bundle.entry[{response_index}].resource.preAuthRef" not in by_path


def test_edi_278_request_with_dependent_records_both_patients():
    # edi_278_request_with_dependent.x12's own dependent must resolve as
    # its own Patient's worth of facts, distinct from the subscriber - the
    # "dependent, when present, is unconditionally also the patient"
    # resolution 278 shares with 270/271's own dependent-precedence rule.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_278_request_with_dependent.x12", recorder=recorder)
    patients = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 2

    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) == len(recorder.facts)
    family_facts = {e.value for e in entries if e.fhir_path.endswith("resource.name[0].family")}
    assert family_facts == {"DOE"}
    given_facts = {e.value for e in entries if e.fhir_path.endswith("resource.name[0].given[0]")}
    assert given_facts == {"JANE", "JIMMY"}


def test_edi_276_basic_crosswalk_matches_known_field_values():
    # edi_276_basic.x12 carries a subscriber and a dependent, each with
    # their own TRN-led claim-status Task - 276 has no STC at all (request-
    # only), so both Tasks' own status must be the inferred "requested"
    # default, never STC-derived.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_276_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    tasks = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Task"]
    assert len(tasks) == 2
    for i, entry in enumerate(bundle.entry):
        if entry.resource.get_resource_type() != "Task":
            continue
        status_entry = by_path[f"Bundle.entry[{i}].resource.status"]
        assert status_entry.derivation == "inferred"
        assert status_entry.value == "requested"
        intent_entry = by_path[f"Bundle.entry[{i}].resource.intent"]
        assert intent_entry.derivation == "inferred"
        assert intent_entry.value == "order"
        code_entry = by_path[f"Bundle.entry[{i}].resource.code.text"]
        assert code_entry.value == "Claim Status"
        authored_entry = by_path[f"Bundle.entry[{i}].resource.authoredOn"]
        assert authored_entry.source_location == f"{edi_location('BHT', 4)}+{edi_location('BHT', 5)}"
        assert f"Bundle.entry[{i}].resource.businessStatus.coding[0].code" not in by_path

    trace_values = {
        by_path[f"Bundle.entry[{i}].resource.identifier[0].value"].value
        for i, e in enumerate(bundle.entry)
        if e.resource.get_resource_type() == "Task"
    }
    assert trace_values == {"TRACE0001", "TRACE0002"}


def test_edi_277_basic_records_direct_status_and_business_status():
    # edi_277_basic.x12's own two STC-populated Tasks must resolve
    # status/businessStatus directly from STC01's category:status
    # composite, not the inferred 276-style default.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_277_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    finalized_task = next(t for t in bundle.entry if t.resource.get_resource_type() == "Task" and t.resource.status == "completed")
    finalized_index = next(i for i, e in enumerate(bundle.entry) if e.resource is finalized_task.resource)

    status_entry = by_path[f"Bundle.entry[{finalized_index}].resource.status"]
    assert status_entry.derivation == "direct"
    assert status_entry.source_location == edi_location("STC", 1, component=1)
    assert status_entry.source_value == "F1"

    category_entry = by_path[f"Bundle.entry[{finalized_index}].resource.businessStatus.coding[0].code"]
    assert category_entry.value == "F1"
    assert category_entry.source_location == edi_location("STC", 1, component=1)
    status_code_entry = by_path[f"Bundle.entry[{finalized_index}].resource.businessStatus.coding[1].code"]
    assert status_code_entry.value == "1"
    assert status_code_entry.source_location == edi_location("STC", 1, component=2)


def test_edi_277_error_status_records_failed_status():
    # edi_277_error_status.x12's own "E1" category must resolve to
    # Task.status="failed", the STC_CATEGORY_PREFIX_TO_TASK_STATUS mapping's
    # own "E" entry, confirmed direct (not the completed/inferred default).
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_277_error_status.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    task = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Task")
    task_index = next(i for i, e in enumerate(bundle.entry) if e.resource is task)

    status_entry = by_path[f"Bundle.entry[{task_index}].resource.status"]
    assert status_entry.value == "failed"
    assert status_entry.derivation == "direct"
    assert status_entry.source_value == "E1"


def test_edi_835_basic_crosswalk_matches_known_field_values():
    # edi_835_basic.x12 has no BHT segment at all (see
    # app/edi/remittance_835.py's own module docstring) - Bundle.identifier
    # comes from TRN02 instead, and there is no Bundle.timestamp fact at
    # all (835 carries no full date+time field anywhere).
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_835_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    payer = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Organization" and e.resource.name == "ACME HEALTH PLAN")
    payer_index = next(i for i, e in enumerate(bundle.entry) if e.resource is payer)
    payer_name = by_path[f"Bundle.entry[{payer_index}].resource.name"]
    assert payer_name.source_location == edi_location("N1", 2)
    payer_id = by_path[f"Bundle.entry[{payer_index}].resource.identifier[0].value"]
    assert payer_id.value == "PAYERID001"
    assert payer_id.source_location == edi_location("N1", 4)

    payment_reconciliation = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation"
    )
    pr_index = next(i for i, e in enumerate(bundle.entry) if e.resource is payment_reconciliation)

    status_entry = by_path[f"Bundle.entry[{pr_index}].resource.status"]
    assert status_entry.derivation == "inferred"
    assert status_entry.value == "active"

    # BPR16 produces two independent facts (created and paymentDate), the
    # same "one source field, two FHIR destinations" case SIU's AIP-3 and
    # MDM's TXA-9 already established.
    created_entry = by_path[f"Bundle.entry[{pr_index}].resource.created"]
    payment_date_entry = by_path[f"Bundle.entry[{pr_index}].resource.paymentDate"]
    assert created_entry.value == payment_date_entry.value == "2026-08-12"
    assert created_entry.source_location == payment_date_entry.source_location == edi_location("BPR", 16)

    payment_amount_entry = by_path[f"Bundle.entry[{pr_index}].resource.paymentAmount.value"]
    assert payment_amount_entry.value == "150.00"
    assert payment_amount_entry.source_location == edi_location("BPR", 2)

    detail_type_entry = by_path[f"Bundle.entry[{pr_index}].resource.detail[0].type.coding[0].code"]
    assert detail_type_entry.source_location == edi_location("CLP", 2)
    detail_id_entry = by_path[f"Bundle.entry[{pr_index}].resource.detail[0].identifier.value"]
    assert detail_id_entry.value == "PCN12345"
    assert detail_id_entry.source_location == edi_location("CLP", 1)
    detail_amount_entry = by_path[f"Bundle.entry[{pr_index}].resource.detail[0].amount.value"]
    assert detail_amount_entry.value == "150.00"
    assert detail_amount_entry.source_location == edi_location("CLP", 4)

    bundle_identifier_entry = by_path["Bundle.identifier.value"]
    assert bundle_identifier_entry.value == "1512345678"
    assert bundle_identifier_entry.source_location == edi_location("TRN", 2)

    assert "Bundle.timestamp" not in by_path


def test_edi_835_multi_claim_records_independently_indexed_details():
    # edi_835_multi_claim.x12's own two CLP groups (one paid, one denied
    # with a $0.00 CLP04) must resolve to their own, independently-indexed
    # detail[] facts - a $0.00 paid amount is still a real, present value,
    # not skipped the way an empty/unresolvable one would be.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_835_multi_claim.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    by_path = {e.fhir_path: e for e in entries}

    payment_reconciliation = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation"
    )
    pr_index = next(i for i, e in enumerate(bundle.entry) if e.resource is payment_reconciliation)

    detail0_id = by_path[f"Bundle.entry[{pr_index}].resource.detail[0].identifier.value"]
    assert detail0_id.value == "PCN22222"
    detail1_id = by_path[f"Bundle.entry[{pr_index}].resource.detail[1].identifier.value"]
    assert detail1_id.value == "PCN33333"
    detail1_amount = by_path[f"Bundle.entry[{pr_index}].resource.detail[1].amount.value"]
    assert detail1_amount.value == "0.00"
