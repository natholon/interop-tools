"""Data Specification provenance tests for X12 EDI - the app/edi/ mirror
of tests/test_provenance_recorder.py/test_provenance_recorder_cda.py, kept
in its own file since X12 is a genuinely different input format (delimited
text with self-describing delimiters, not HL7v2 pipe-delimited or XML) with
its own parsing/dispatch layer, the same "own file per format" discipline
test_provenance_recorder_cda.py already established.

Scope so far: 270 (Eligibility Inquiry), 271 (Eligibility Response), 276
(Claim Status Request), and 277 (Claim Status Response) - see
app/provenance/dispatch.py's own _INSTRUMENTED_TRANSACTION_SETS for why
every other EDI family still reports unsupported=True."""

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


# 270/271/276/277's own real fixtures, plus a handful of already-shipped
# fixtures from other, still-uninstrumented EDI families - included
# specifically to prove the no-op `recorder=None` additions to those 4
# remaining sibling builder files (and the "free" Bundle.identifier/
# .timestamp facts every one of them now gets via the shared
# assemble_bundle) don't alter their own output either.
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
    "edi_835_basic.x12",
    "edi_837p_basic.x12",
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


def test_edi_278_non_instrumented_family_still_gets_free_bundle_level_facts():
    # 278 isn't instrumented this slice (see app/edi/prior_auth.py's own
    # recorder docstring), but the shared assemble_bundle() still records
    # Bundle.identifier/.timestamp "for free" via the BHT segment every EDI
    # family shares - the identical "some real facts, still not fully
    # instrumented" shape ORU/MDM's own recorder already exhibited before
    # their own slices shipped.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_278_request_basic.x12", recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    assert len(entries) > 0
    assert all(e.fhir_path.startswith("Bundle.identifier") or e.fhir_path.startswith("Bundle.timestamp") for e in entries)


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


def test_edi_835_non_instrumented_family_with_no_bht_gets_zero_facts():
    # 835 has no BHT segment at all (see app/edi/remittance_835.py's own
    # recorder docstring) - unlike every other still-uninstrumented family,
    # it doesn't even get free Bundle-level facts, since its own bundle
    # assembly never threads recorder into anything.
    recorder = ProvenanceRecorder(source_format="EDI")
    bundle = _build_bundle("edi_835_basic.x12", recorder=recorder)
    assert len(bundle.entry) > 0
    entries = resolve_bundle_paths(bundle, recorder)
    assert entries == []
