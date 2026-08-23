"""Tests for the computed mapping-decision register.

The register is only useful to a reviewer if it is both complete (no
silent decision) and accurate (no false positive) - a register that cries
wolf gets ignored, and one that misses a decision defeats the sign-off it
exists to support. These tests pin both properties.
"""

from app.provenance.decisions import compute_decisions, scan_populated_components
from app.provenance.dispatch import convert_with_provenance

_MSH = "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|M1|P|2.5"


def _message(pid: str, pv1: str) -> str:
    return "\r".join([_MSH, "EVN|A01|20260101120000", pid, pv1])


def _decisions(message: str):
    _, report, _ = convert_with_provenance(message)
    return compute_decisions(report, scan_populated_components(message))


def _by_location(decisions):
    return {d.source_location: d for d in decisions if d.source_location}


def test_pl_components_are_no_longer_dropped():
    # PV1-3 = C100^^A^GENHOSP used to lose Bed and Facility, because
    # location_display read only components 1-2. Now every populated
    # component becomes its own Location in the IG's partOf chain, so the
    # register must report nothing for PV1-3 at all - the register acting
    # as a regression signal for the mapper fix.
    decisions = _decisions(
        _message(
            "PID|1||578324^^^MRN||Doe^Jane||19620305|F",
            "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1",
        )
    )
    assert not [loc for loc in _by_location(decisions) if loc.startswith("PV1-3")]


def test_dropped_components_are_still_reported_for_unmapped_fields():
    # The detection itself must still work - PID-5's Suffix/Prefix/Degree
    # remain genuinely unmapped.
    decisions = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane^Q^Jr^Dr||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    by_location = _by_location(decisions)
    assert by_location["PID-5.4"].field_label == "Suffix"
    assert by_location["PID-5.4"].lost_value == "Jr"


def test_component_that_is_mapped_is_not_reported_as_dropped():
    # PID-3.4 drives Identifier.system. It was previously mapped but never
    # *recorded*, which made it look dropped - a false positive this
    # register cannot afford. Regression test for that fix.
    decisions = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    assert "PID-3.4" not in _by_location(decisions)


def test_wholly_unmapped_field_reports_once_not_per_component():
    # PV1-8 (Referring Doctor) is not mapped at all. One decision saying
    # so is informative; five component-level rows are noise that buries
    # the partially-dropped fields.
    decisions = _decisions(
        _message(
            "PID|1||578324^^^MRN||Doe^Jane||19620305|F",
            "PV1|1|I|C100|||||1234^Smith^John^A^MD|||||||||||V1",
        )
    )
    by_location = _by_location(decisions)
    assert by_location["PV1-8"].lost_value == "1234^Smith^John^A^MD"
    assert not any(loc.startswith("PV1-8.") for loc in by_location)


def test_inferred_mappings_are_reported_with_their_reason():
    decisions = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    inferred = [d for d in decisions if d.kind == "inferred"]
    assert inferred, "an ADT^A01 always infers Encounter.status"
    status = next(d for d in inferred if d.fhir_path.endswith(".status"))
    assert "A01" in (status.detail or "")
    assert status.lost_value == "in-progress"


def test_a_drop_does_not_claim_unverified_ig_backing():
    # The v2-to-FHIR IG existing does not mean it *confirms* a given drop.
    # Whether the IG defines a target this app fails to implement, or none
    # at all, is a per-component question nobody has checked - so the
    # citation must say that rather than imply standards backing.
    decisions = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane^Q^Jr||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    drop = _by_location(decisions)["PID-5.4"]
    assert drop.citation.authoritative is False
    assert "not yet checked" in drop.citation.title.lower()


def test_inferred_decisions_cite_the_governing_ig():
    decisions = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    inferred = next(d for d in decisions if d.kind == "inferred")
    assert inferred.citation.authoritative is True
    assert inferred.citation.url


def test_decision_ids_are_stable_across_runs_and_values():
    # A reviewer's accept/reject is keyed by id, so the id must survive a
    # re-run - and must not embed the field's value, or editing an
    # unrelated part of the message would silently discard the review.
    first = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1")
    )
    again = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1")
    )
    assert [d.id for d in first] == [d.id for d in again]

    different_suffix = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane^^Sr||19620305|F", "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1")
    )
    baseline = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane^^Jr||19620305|F", "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1")
    )
    assert _by_location(baseline)["PID-5.4"].id == _by_location(different_suffix)["PID-5.4"].id


def test_scan_ignores_msh_and_non_composite_fields():
    populated = scan_populated_components(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    assert not any(segment == "MSH" for segment, _, _ in populated)
    # PV1-3 here has no "^" at all, so there is nothing to drop from it.
    assert ("PV1", 3, 0) not in populated


# --- rejection -------------------------------------------------------

import json

from app.provenance.decisions import (
    DATA_ABSENT_REASON_URL,
    apply_rejections,
    compute_decisions as _compute,
)
from app.generators.registry import generate


def _converted(message: str):
    bundle, report, _ = convert_with_provenance(message)
    decisions = _compute(report, scan_populated_components(message))
    return json.loads(bundle.model_dump_json(exclude_none=True)), decisions


def _entry_index(fhir_path: str) -> int:
    return int(fhir_path[len("Bundle.entry[") :].split("]")[0])


def test_rejecting_a_status_with_a_null_code_emits_that_code():
    # Tier 2: Encounter.status's own value set includes "unknown", so the
    # rejection is expressible as a normal code and the resource stays a
    # fully valid FHIR model.
    bundle, decisions = _converted(generate("ADT", "A01", seed=3))
    target = next(d for d in decisions if d.kind == "inferred" and d.fhir_path.endswith(".status"))
    index = _entry_index(target.fhir_path)
    assert bundle["entry"][index]["resource"]["status"] != "unknown"

    result, outcomes = apply_rejections(bundle, decisions, {target.id})
    resource = result["entry"][index]["resource"]
    assert resource["status"] == "unknown"
    assert "_status" not in resource
    assert outcomes[0].applied is True and outcomes[0].strategy == "code"


def test_rejecting_a_status_with_no_null_code_uses_data_absent_reason():
    # Tier 3: Appointment.status has no null-flavour code and a Required
    # binding, so the only conformant option is value-absent plus the
    # data-absent-reason extension on the primitive.
    bundle, decisions = _converted(generate("SIU", "S12", seed=3))
    target = next(d for d in decisions if d.kind == "inferred" and d.fhir_path.endswith(".status"))
    index = _entry_index(target.fhir_path)

    result, outcomes = apply_rejections(bundle, decisions, {target.id})
    resource = result["entry"][index]["resource"]
    assert "status" not in resource
    assert resource["_status"]["extension"][0]["url"] == DATA_ABSENT_REASON_URL
    assert resource["_status"]["extension"][0]["valueCode"] == "unknown"
    assert outcomes[0].applied is True and outcomes[0].strategy == "absent"


def test_rejecting_a_dropped_field_is_recorded_but_not_applied():
    # Rejecting a *drop* means "this should have been mapped" - a gap in
    # the mapper, not something conversion can act on. It must be
    # reported, never silently ignored.
    message = _message(
        "PID|1||578324^^^MRN||Doe^Jane^Q^Jr||19620305|F", "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1"
    )
    bundle, decisions = _converted(message)
    drop = next(d for d in decisions if d.source_location == "PID-5.4")

    _, outcomes = apply_rejections(bundle, decisions, {drop.id})
    assert outcomes[0].applied is False
    assert "cannot supply a mapping" in (outcomes[0].note or "")


def test_unrejected_decisions_leave_the_bundle_untouched():
    bundle, decisions = _converted(generate("ADT", "A01", seed=3))
    before = json.dumps(bundle, sort_keys=True)
    result, outcomes = apply_rejections(bundle, decisions, set())
    assert json.dumps(result, sort_keys=True) == before
    assert outcomes == []
