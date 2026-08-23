"""Tests for the computed mapping-decision register.

The register is only useful to a reviewer if it is both complete (no
silent decision) and accurate (no false positive) - a register that cries
wolf gets ignored, and one that misses a decision defeats the sign-off it
exists to support. These tests pin both properties.
"""

from pathlib import Path

from app.provenance.decisions import (
    compute_decisions,
    scan_populated_components,
    scan_populated_edi_elements,
)
from app.provenance.dispatch import convert_with_provenance

_MSH = "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|M1|P|2.5"


def _message(pid: str, pv1: str) -> str:
    return "\r".join([_MSH, "EVN|A01|20260101120000", pid, pv1])


def _segment(segment_id: str, fields: dict[int, str]) -> str:
    """Build a segment from {field_number: value}, so a test never depends
    on hand-counting pipes - the same discipline tests/fixtures/*.hl7 are
    built with, and it already caught one test asserting on PV1-8 while
    meaning PV1-10."""
    out = [segment_id] + [""] * max(fields)
    for number, value in fields.items():
        out[number] = value
    return "|".join(out)


def _decisions(message: str):
    _, report, _ = convert_with_provenance(message)
    return compute_decisions(report, message)


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


def test_repeating_field_components_get_distinct_decision_ids():
    # PID-3 carrying two identifiers used to collapse both repetitions onto
    # one location ("PID-3.5") and so one id: apply_rejections keys a dict
    # by id, so one decision became unreachable, and the UI shared a single
    # review state across both rows.
    decisions = _decisions(
        _message(
            "PID|1||578324^^^MRN^MR~ALT999^^^OTHER^PI||Doe^Jane||19620305|F",
            "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1",
        )
    )
    dropped = [d for d in decisions if d.kind == "dropped" and (d.source_location or "").startswith("PID-3")]
    assert len(dropped) == 2, [d.source_location for d in dropped]
    assert len({d.id for d in dropped}) == 2
    assert {d.source_location for d in dropped} == {"PID-3.5", "PID-3[1].5"}
    assert {d.lost_value for d in dropped} == {"MR", "PI"}


def test_unmapped_simple_field_is_reported_as_dropped():
    # A field with no "^" is still real data. PV1-10 (Hospital Service) is
    # genuinely unmapped by this app and must show up - the register claims
    # completeness, and skipping non-composite fields quietly broke it.
    decisions = _decisions(
        _message(
            "PID|1||578324^^^MRN||Doe^Jane||19620305|F",
            _segment("PV1", {1: "1", 2: "I", 3: "C100^^A^GENHOSP", 10: "CAR", 19: "V1"}),
        )
    )
    dropped = _by_location([d for d in decisions if d.kind == "dropped"])
    assert "PV1-10" in dropped
    assert dropped["PV1-10"].lost_value == "CAR"


def test_set_id_and_control_fields_are_not_reported_as_drops():
    # Reporting these would be noise, not loss: a Set ID is sequencing, and
    # OBX-2 is fully consumed to pick which Observation.value[x] gets OBX-5.
    decisions = _decisions(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1")
    )
    reported = set(_by_location(decisions))
    assert "PID-1" not in reported
    assert "PV1-1" not in reported
    assert "EVN-1" not in reported


def test_multi_segment_marker_location_counts_the_field_as_mapped():
    # An MDM document body is recorded against the disclosed marker
    # "OBX-5 (xN segments)", which is not a parseable SEG-N. Reading only
    # the leading prefix keeps it mapped; without that the register
    # reported the document text itself as dropped data.
    raw = open("tests/fixtures/mdm_t02_basic.hl7").read()
    _, report, _ = convert_with_provenance(raw)
    decisions = compute_decisions(report, raw)
    assert any(e.source_location and e.source_location.startswith("OBX-5 (") for e in report.entries)
    assert not [d for d in decisions if (d.source_location or "").startswith("OBX-5")]


def test_scan_ignores_msh_but_sees_non_composite_fields():
    populated = scan_populated_components(
        _message("PID|1||578324^^^MRN||Doe^Jane||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    )
    assert not any(segment == "MSH" for segment, _, _, _ in populated)
    # A field with no "^" is one component, not "nothing to drop" - skipping
    # it made every non-composite field invisible to the register no matter
    # what it carried.
    assert populated[("PV1", 0, 3, 0)] == {1: "C100"}


def test_repeated_segments_each_report_their_own_drops():
    # HL7v2 repeats whole segments as well as fields - an ORU carries one
    # OBX per result. Keying only by (segment, field, repetition) let the
    # last OBX overwrite the earlier ones, so a component dropped by an
    # earlier segment was invisible no matter what it carried.
    raw = (Path(__file__).parent / "fixtures" / "oru_r01_basic.hl7").read_text()
    lines = raw.replace("\n", "\r").split("\r")
    marked = 0
    for i, line in enumerate(lines):
        if line.startswith("OBX") and marked < 2:
            fields = line.split("|")
            fields[3] = fields[3] + f"^^^ONLYON{marked}"
            lines[i] = "|".join(fields)
            marked += 1
    assert marked == 2, "the fixture must carry at least two OBX segments"
    patched = "\r".join(lines)

    _, report, _ = convert_with_provenance(patched)
    decisions = compute_decisions(report, patched)
    dropped = _by_location([d for d in decisions if d.kind == "dropped"])

    # Both are reported, each against its own physical segment. Marking two
    # segments rather than one is deliberate: an earlier version proved the
    # point using OBX-3.3, which was itself a false positive (the CWE coding
    # system is mapped to Coding.system), so the test passed for the wrong
    # reason and broke as soon as that was fixed.
    assert dropped["OBX-3.6"].lost_value == "ONLYON0"
    assert dropped["OBX[1]-3.6"].lost_value == "ONLYON1"
    assert dropped["OBX-3.6"].id != dropped["OBX[1]-3.6"].id


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
    decisions = _compute(report, message)
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


# --- X12 EDI ---------------------------------------------------------

_EDI_FIXTURES = Path(__file__).parent / "fixtures"


def _edi_decisions(fixture: str):
    raw = (_EDI_FIXTURES / fixture).read_text()
    _, report, _ = convert_with_provenance(raw)
    return compute_decisions(report, raw)


def test_edi_reports_dropped_elements():
    # 837P leaves CLM06-CLM09 and the REF segments unmapped by design; the
    # register must say so rather than reporting nothing at all, which is
    # what every EDI message did before the dropped half covered X12.
    decisions = _edi_decisions("edi_837p_basic.x12")
    dropped = _by_location([d for d in decisions if d.kind == "dropped"])
    assert dropped, "an 837P drops real elements"
    assert "CLM-7" in dropped
    assert dropped["CLM-7"].lost_value == "A"


def test_edi_mapped_elements_are_not_reported_as_dropped():
    decisions = _edi_decisions("edi_837p_basic.x12")
    reported = set(_by_location(decisions))
    # Each of these is genuinely read by app/edi/claim_837p.py.
    for location in ("CLM-1", "CLM-2", "CLM-5.1", "SV1-2", "SV1-7", "NM1-3", "NM1-9"):
        assert location not in reported, location


def test_edi_compound_marker_location_counts_both_elements_as_mapped():
    # Bundle.timestamp is recorded against "BHT-4+BHT-5", which
    # parse_edi_location cannot read - so neither element looked mapped and
    # both were reported as dropped.
    raw = (_EDI_FIXTURES / "edi_270_basic.x12").read_text()
    _, report, _ = convert_with_provenance(raw)
    assert any(e.source_location == "BHT-4+BHT-5" for e in report.entries)
    decisions = compute_decisions(report, raw)
    reported = set(_by_location(decisions))
    assert "BHT-4" not in reported
    assert "BHT-5" not in reported


def test_edi_qualifier_elements_are_not_reported_as_drops():
    # Qualifiers select how the value beside them is read - consumed, not
    # lost. HI's sits inside a composite repeated across HI01, HI02, ...
    decisions = _edi_decisions("edi_837p_basic.x12")
    reported = set(_by_location(decisions))
    assert "HI-1.1" not in reported
    assert "HI-2.1" not in reported
    assert "CLM-5.2" not in reported
    assert "SV1-1.1" not in reported


def test_edi_repeated_segments_get_distinct_locations_and_ids():
    # X12 repeats whole segments where HL7v2 repeats fields - an 837P
    # carries several REF/N3/N4 segments, and each must report its own drop
    # rather than colliding onto one id.
    decisions = _edi_decisions("edi_837p_basic.x12")
    n4 = [d for d in decisions if (d.source_location or "").startswith("N4")]
    assert len(n4) > 3
    assert len({d.id for d in n4}) == len(n4)
    assert any("[1]" in (d.source_location or "") for d in n4)


def test_edi_envelope_segments_are_never_reported():
    decisions = _edi_decisions("edi_835_basic.x12")
    reported = set(_by_location(decisions))
    assert not [loc for loc in reported if loc.split("[")[0].split("-")[0] in {"ISA", "GS", "ST", "SE", "GE", "IEA"}]


def test_edi_scan_skips_envelope_and_indexes_repeated_segments():
    raw = (_EDI_FIXTURES / "edi_837p_basic.x12").read_text()
    populated = scan_populated_edi_elements(raw)
    assert not [k for k in populated if k[0] in {"ISA", "GS", "ST", "SE", "GE", "IEA"}]
    occurrences = {occ for sid, occ, _, _ in populated if sid == "NM1"}
    assert occurrences == set(range(len(occurrences))) and len(occurrences) > 1


def test_edi_qualifier_is_reported_when_its_target_is_unmapped():
    # A qualifier is only "consumed" if the element it qualifies was
    # actually mapped. 837I never reads CLM05, so its CLM05-2 qualifies
    # nothing - suppressing it unconditionally hid real data while the
    # CLM05-1 it supposedly qualified was itself reported as dropped.
    reported = set(_by_location(_edi_decisions("edi_837i_basic.x12")))
    assert "CLM-5.1" in reported
    assert "CLM-5.2" in reported

    # 837P does read CLM05-1, so there the qualifier really is consumed.
    reported_837p = set(_by_location(_edi_decisions("edi_837p_basic.x12")))
    assert "CLM-5.1" not in reported_837p
    assert "CLM-5.2" not in reported_837p


def test_joined_field_allowance_applies_only_where_something_read_the_field():
    # JOINED_FIELDS exists so a mapper that collapses several components
    # into one value does not look like it dropped the rest. Where nothing
    # read the field it collapsed nothing: MDM never reads PV1-3, so
    # suppressing its point-of-care and room hid real drops.
    raw = (Path(__file__).parent / "fixtures" / "mdm_t02_basic.hl7").read_text()
    _, report, _ = convert_with_provenance(raw)
    assert not any((e.source_location or "").startswith("PV1-3") for e in report.entries)
    reported = _by_location(compute_decisions(report, raw))
    assert "PV1-3" in reported
    assert reported["PV1-3"].lost_value.startswith("W789^105")

    # ADT does read every PL component, so nothing is reported there.
    adt = (Path(__file__).parent / "fixtures" / "adt_a01_basic.hl7").read_text()
    _, adt_report, _ = convert_with_provenance(adt)
    adt_reported = _by_location(compute_decisions(adt_report, adt))
    assert not [loc for loc in adt_reported if loc.startswith("PV1-3")]


# --- C-CDA -----------------------------------------------------------


def _cda_decisions(fixture: str):
    """C-CDA needs the resolved source spans - a recorded XML location has
    no coordinate inverse, so spans are how a transformed value is told
    from an unread one."""
    from app.provenance.highlighting import build_highlighting_payload

    raw = (Path(__file__).parent / "fixtures" / fixture).read_text()
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, "CDA")
    spans = {tuple(m.source_span) for m in payload.matches if m.source_span}
    return compute_decisions(report, raw, spans)


def test_cda_reports_dropped_values():
    decisions = _cda_decisions("ccd_basic.xml")
    dropped = _by_location([d for d in decisions if d.kind == "dropped"])
    assert dropped, "a CCD drops real values - languageCode is never mapped"
    assert any(loc.endswith("languageCode/@code") for loc in dropped)


def test_cda_transformed_values_are_not_reported_as_dropped():
    # The mapper rewrites most values it reads - a date reformatted, an OID
    # turned into a urn:oid: URI. Comparing values alone called nearly half
    # the document lost; matching the resolved source span does not.
    dropped = set(_by_location(_cda_decisions("ccd_basic.xml")))
    for suffix in (
        "patient/birthTime/@value",                 # -> Patient.birthDate, reformatted
        "patientRole/patient/name/family",          # -> HumanName.family
        "recordTarget/patientRole/id[0]/@root",     # -> Identifier.system, rewritten
    ):
        assert not [loc for loc in dropped if loc.endswith(suffix)], suffix


def test_cda_structural_xml_is_not_reported_as_dropped():
    dropped = set(_by_location(_cda_decisions("ccd_basic.xml")))
    for fragment in ("templateId", "typeId", "@classCode", "@moodCode", "@xmlns"):
        assert not [loc for loc in dropped if fragment in loc], fragment


def test_cda_code_system_is_reported_only_when_its_code_is_unmapped():
    # codeSystem qualifies the code beside it, so it is consumed only when
    # that code was actually read - the same conditional rule the other two
    # formats use.
    dropped = set(_by_location(_cda_decisions("ccd_basic.xml")))
    # The Problem Observation's value/@code IS mapped, so its codeSystem is not a drop.
    assert not [loc for loc in dropped if loc.endswith("observation/value/@codeSystem")]
    # ClinicalDocument/code is not mapped, so its codeSystem is reported with it.
    assert any(loc.endswith("ClinicalDocument/code/@codeSystem") for loc in dropped)


def test_cda_narrative_block_reports_once_not_per_paragraph():
    # C-CDA requires a section's narrative to restate its entries, so
    # reporting every paragraph would bury the real findings under a
    # duplicate of them.
    decisions = _cda_decisions("ccd_basic.xml")
    narrative = [d for d in decisions if (d.source_location or "").endswith("/text()")
                 and "section" in (d.source_location or "")]
    assert len({d.source_location for d in narrative}) == len(narrative)


def test_cda_display_and_unit_are_not_reported_when_the_mapper_carried_them():
    """A caller that recorded only `.coding[0].code` left `@displayName`
    looking unread, and build_quantity_from_pq reads `@unit` that several
    callers never recorded - so the register called all of them lost data
    while the mapper had in fact carried them through."""
    vitals = set(_by_location(_cda_decisions("ccd_vitals_basic.xml")))
    assert not [loc for loc in vitals if loc.endswith("interpretationCode/@displayName")]

    procedures = set(_by_location(_cda_decisions("ccd_procedures_basic.xml")))
    assert not [loc for loc in procedures if loc.endswith("procedure/targetSiteCode/@displayName")]

    medications = set(_by_location(_cda_decisions("ccd_medications_basic.xml")))
    assert not [loc for loc in medications if loc.endswith("routeCode/@displayName")]
    assert not [loc for loc in medications if loc.endswith("doseQuantity/@unit")]
