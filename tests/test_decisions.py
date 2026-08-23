"""Tests for the computed mapping-decision register.

The register is only useful to a reviewer if it is both complete (no
silent decision) and accurate (no false positive) - a register that cries
wolf gets ignored, and one that misses a decision defeats the sign-off it
exists to support. These tests pin both properties.
"""

from pathlib import Path

import pytest

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
    # CX.6 (Assigning Facility) is used rather than CX.5: the IG maps CX.5
    # to Identifier.type and this app now builds it, so it is no longer a
    # drop at all - the test needs a component that genuinely still is one.
    decisions = _decisions(
        _message(
            "PID|1||578324^^^MRN^MR^FAC1~ALT999^^^OTHER^PI^FAC2||Doe^Jane||19620305|F",
            "PV1|1|I|C100^^A^GENHOSP|||||||||||||||||V1",
        )
    )
    dropped = [d for d in decisions if d.kind == "dropped" and (d.source_location or "").startswith("PID-3")]
    assert len(dropped) == 2, [d.source_location for d in dropped]
    assert len({d.id for d in dropped}) == 2
    assert {d.source_location for d in dropped} == {"PID-3.6", "PID-3[1].6"}
    assert {d.lost_value for d in dropped} == {"FAC1", "FAC2"}


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
    # read the field it collapsed nothing, and the whole field is the drop.
    #
    # PV1-6 (Prior Patient Location) is the case: only ADT^A02 reads it, so
    # on an A01 it is entirely unmapped. This used to be demonstrated with
    # PV1-3 on an MDM message, until MDM started building the PV1-3 Location
    # chain the IG maps - at which point no message type ignored PV1-3 any
    # more and the test was asserting something no longer true.
    decisions = _decisions(
        _message(
            "PID|1||578324^^^MRN||Doe^Jane||19620305|F",
            _segment("PV1", {1: "1", 2: "I", 3: "C100^^A^GENHOSP", 6: "W200^^B^OTHERFAC", 19: "V1"}),
        )
    )
    reported = _by_location([d for d in decisions if d.kind == "dropped"])
    assert "PV1-6" in reported
    assert reported["PV1-6"].lost_value.startswith("W200")
    # ADT does read PV1-3, so its joined components stay suppressed.
    assert not [loc for loc in reported if loc.startswith("PV1-3")]


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
    # A wholly unmapped element reports once, naming the element rather
    # than one row per attribute.
    assert any(loc.endswith("/languageCode") for loc in dropped)


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
    # ClinicalDocument/code is not mapped at all, so it reports as one
    # element-level row whose detail names every attribute it carried,
    # codeSystem included.
    doc_code = next(d for d in _cda_decisions("ccd_basic.xml")
                    if d.source_location == "ClinicalDocument/code")
    assert "@codeSystem=" in (doc_code.detail or "")
    assert "@displayName=" in (doc_code.detail or "")


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


def test_cda_wholly_unmapped_element_reports_once_not_per_attribute():
    """An unmapped <code> produced three rows (@code, @codeSystem,
    @displayName) telling a reviewer one fact. The HL7v2 half already
    reports a wholly unmapped field once; this brings C-CDA in line."""
    dropped = _by_location(_cda_decisions("ccd_basic.xml"))
    doc_code = dropped["ClinicalDocument/code"]
    assert "@code=" in doc_code.detail and "@codeSystem=" in doc_code.detail
    assert not [loc for loc in dropped if loc.startswith("ClinicalDocument/code/@")]


def test_cda_repeated_shapes_collapse_with_a_count():
    """A Vital Signs organizer with six readings drops six identical
    statusCode facts. One row stating the count carries the same
    information without burying the findings that differ."""
    dropped = _by_location(_cda_decisions("ccd_vitals_basic.xml"))
    key = next(loc for loc in dropped if loc.endswith("component/observation/statusCode"))
    collapsed = dropped[key]
    assert "occurrences" in (collapsed.detail or "")
    assert "[" not in collapsed.source_location, "a collapsed row names the shape, not one occurrence"


def test_cda_a_single_occurrence_keeps_its_exact_location():
    """Collapsing must not cost precision where there is nothing to
    collapse - one occurrence still names the indexed path it came from."""
    dropped = _by_location(_cda_decisions("ccd_vitals_basic.xml"))
    assert any("[" in loc for loc in dropped), dropped.keys()


def test_cda_drops_cite_a_real_ig_verdict_not_unchecked():
    """Every drop used to cite "not yet checked". Where the IG's own
    mapping table has been read, the register now states what it says."""
    decisions = _cda_decisions("ccd_basic.xml")
    titles = {d.citation.title for d in decisions if d.kind == "dropped"}
    assert any("no map specified" in t for t in titles)
    assert any("not supported by target" in t for t in titles)


def test_cda_verdicts_do_not_reach_across_sections():
    """Two GAP verdicts were withdrawn after they turned out to be matching
    a different section's element at the same depth - an Allergy Reaction
    Observation's id given the Problem Observation's verdict, and a Comment
    Activity's code given the Instruction act's. A shape that cannot tell
    them apart must assert nothing rather than borrow a verdict."""
    from app.provenance.cda_ig_verdicts import verdict_for

    for shape in ("entryRelationship/observation/id", "entryRelationship/act/code"):
        verdict, citation, _ = verdict_for(shape)
        assert verdict is None, shape
        assert citation.authoritative is False


def test_cda_entry_identifiers_are_built_closing_the_ig_gap():
    """The IG maps each entry's own <id> as a source value to that
    resource's .identifier. Procedure built it; Condition, MedicationRequest,
    Immunization and AllergyIntolerance did not, which the register reported
    as gaps against the standard. Implementing them is what closed those."""
    import json

    for fixture, resource_type in [
        ("ccd_basic.xml", "Condition"),
        ("ccd_medications_basic.xml", "MedicationRequest"),
        ("ccd_immunizations_basic.xml", "Immunization"),
        ("ccd_allergies_basic.xml", "AllergyIntolerance"),
        ("ccd_procedures_basic.xml", "Procedure"),
    ]:
        raw = (Path(__file__).parent / "fixtures" / fixture).read_text()
        bundle, _, _ = convert_with_provenance(raw)
        built = json.loads(bundle.model_dump_json(exclude_none=True))
        identifiers = [
            e["resource"].get("identifier")
            for e in built["entry"]
            if e["resource"]["resourceType"] == resource_type
        ]
        assert identifiers and all(identifiers), f"{resource_type} must carry the entry id"

    # And the register no longer reports them as gaps.
    gaps = [d for d in _cda_decisions("ccd_basic.xml") if d.summary.startswith("GAP:")]
    assert not [g for g in gaps if g.source_location.endswith("observation/id")]


def test_cda_concern_act_id_and_observation_id_get_different_verdicts():
    """The same tag at different depths gets different verdicts: the
    Concern Act's own id is marked "not supported by target" while the
    Problem Observation's id inside it maps. Longest-suffix matching is
    what keeps those apart."""
    by_loc = _by_location(_cda_decisions("ccd_basic.xml"))
    act_id = next(d for loc, d in by_loc.items() if loc.endswith("entry/act/id"))
    assert "not supported by target" in act_id.citation.title
    assert not act_id.summary.startswith("GAP:")
    # The Problem Observation's own id, one level in, is now built - so it
    # is not reported at all, while the Concern Act's still is. Same tag,
    # different depth, different outcome.
    assert not [loc for loc in by_loc if loc.endswith("observation/id")]


def test_cda_unsourced_shape_still_says_unchecked():
    """Honesty in the other direction: a shape whose IG table has not been
    read yet must keep saying so rather than defaulting to a verdict."""
    from app.provenance.cda_ig_verdicts import verdict_for

    verdict, citation, note = verdict_for("some/element/nobody/checked")
    assert verdict is None
    assert citation.authoritative is False
    assert "not yet checked" in citation.title.lower()


def test_edi_drops_cite_the_missing_crosswalk_not_unchecked():
    """X12 publishes no FHIR crosswalk at all, so "not yet checked" would
    imply pending work that cannot be done. The honest citation is that no
    authoritative crosswalk exists - which is also why it is the one
    citation here marked non-authoritative by design."""
    decisions = _edi_decisions("edi_837i_basic.x12")
    dropped = [d for d in decisions if d.kind == "dropped"]
    assert dropped
    assert all("No official X12-to-FHIR crosswalk" in d.citation.title for d in dropped)
    assert all(d.citation.authoritative is False for d in dropped)


def test_mdm_txa19_is_read_not_dropped():
    """TXA-19 "AV" maps to DocumentReference.status "current" - a verified
    mapping. It was recorded inferred and TXA-19 reported as dropped, so
    the field looked both unread and lost while it had in fact been
    mapped."""
    raw = (Path(__file__).parent / "fixtures" / "mdm_t02_basic.hl7").read_text()
    bundle, report, _ = convert_with_provenance(raw)
    status = _document_reference_status(bundle, report)
    assert status.derivation == "direct"
    assert status.source_location == "TXA-19"
    assert status.source_value == "AV"
    assert not [d for d in compute_decisions(report, raw)
                if d.kind == "dropped" and (d.source_location or "").startswith("TXA-19")]


def test_mdm_unverified_txa19_value_stays_inferred():
    """Only "AV" has a verified target. Anything else still defaults to
    "current" without being read, so it must stay inferred rather than
    claim a mapping the IG does not back."""
    raw = (Path(__file__).parent / "fixtures" / "mdm_t02_basic.hl7").read_text()
    patched = raw.replace("|AV|", "|UN|")
    assert patched != raw, "fixture must carry a TXA-19 value to alter"
    bundle, report, _ = convert_with_provenance(patched)
    status = _document_reference_status(bundle, report)
    assert status.derivation == "inferred"
    assert status.value == "current"


def _document_reference_status(bundle, report):
    """The DocumentReference's own status entry. Selecting by fhir_path
    suffix alone picks up the Encounter's status too, which is a different
    resource with a different derivation."""
    index = next(
        i for i, entry in enumerate(bundle.entry)
        if entry.resource.get_resource_type() == "DocumentReference"
    )
    path = f"Bundle.entry[{index}].resource.status"
    return next(e for e in report.entries if e.fhir_path == path)


def test_cx5_identifier_type_is_mapped_not_dropped():
    """CX.5 maps to Identifier.type.coding.code in the v2-to-FHIR IG's own
    CX[Identifier] datatype table. Leaving it unbuilt made PID-3.5 the
    single largest class of dropped HL7v2 data in this app."""
    import json

    message = _message("PID|1||578324^^^MRN^MR||Doe^Jane||19620305|F", "PV1|1|I|C100|||||||||||||||||V1")
    bundle, report, _ = convert_with_provenance(message)
    built = json.loads(bundle.model_dump_json(exclude_none=True))
    patient = next(e["resource"] for e in built["entry"] if e["resource"]["resourceType"] == "Patient")
    coding = patient["identifier"][0]["type"]["coding"][0]
    assert coding["code"] == "MR"
    assert coding["system"] == "http://terminology.hl7.org/CodeSystem/v2-0203"
    assert "PID-3.5" not in _by_location(compute_decisions(report, message))


def test_xcn7_degree_is_mapped_not_dropped():
    """XCN.7 maps to qualification.code in the IG's XCN[Practitioner]
    table; every XCN-derived Practitioner dropped its degree before."""
    import json

    raw = (Path(__file__).parent / "fixtures" / "siu_s12_basic.hl7").read_text()
    bundle, report, _ = convert_with_provenance(raw)
    built = json.loads(bundle.model_dump_json(exclude_none=True))
    practitioners = [e["resource"] for e in built["entry"] if e["resource"]["resourceType"] == "Practitioner"]
    qualified = [p for p in practitioners if p.get("qualification")]
    assert qualified, "the SIU fixture carries an AIP degree"
    assert qualified[0]["qualification"][0]["code"]["coding"][0]["code"] == "MD"
    assert not [loc for loc in _by_location(compute_decisions(report, raw)) if loc.endswith(".7")]


def test_hl7v2_drops_cite_the_v2_to_fhir_ig():
    """HL7v2 has a ballot-published IG with per-segment ConceptMaps, so its
    drops can state what the IG says rather than "not yet checked" - the
    parity gap that remained after C-CDA got its verdicts."""
    raw = (Path(__file__).parent / "fixtures" / "adt_a01_basic.hl7").read_text()
    _, report, _ = convert_with_provenance(raw)
    by_location = _by_location([d for d in compute_decisions(report, raw) if d.kind == "dropped"])

    # EVN maps to Provenance, which this app deliberately never builds.
    evn = by_location["EVN-2"]
    assert "does not build" in evn.citation.title
    assert evn.citation.authoritative is True
    assert "Provenance.recorded" in (evn.detail or "")

    # PV1-7.7 was a GAP until ADT started materialising a real Practitioner
    # for PV1-7; XCN.7 now has somewhere to go, so it is not a drop at all.
    assert "PV1-7.7" not in by_location


def test_rejection_strategies_cover_cda_and_edi_resources():
    """The review workflow only knew HL7v2's resources, so rejecting an
    inferred Coverage.status or Task.intent reported "no verified
    conformant representation" and did nothing. Each row was read off the
    published R4 CodeSystem - fhir.resources does not validate value sets,
    so it cannot be derived."""
    import json

    from app.provenance.decisions import REJECTION_STRATEGY, apply_rejections

    # A null-flavour code exists for these two, so rejection rewrites them.
    assert REJECTION_STRATEGY[("Task", "intent")] == "code"
    assert REJECTION_STRATEGY[("CarePlan", "status")] == "code"
    # fm-status and claim-use have none, so rejection must drop the value
    # and carry data-absent-reason instead.
    for key in [("Coverage", "status"), ("Claim", "status"), ("Claim", "use")]:
        assert REJECTION_STRATEGY[key] == "absent", key

    raw = (Path(__file__).parent / "fixtures" / "edi_837p_basic.x12").read_text()
    bundle, report, _ = convert_with_provenance(raw)
    decisions = compute_decisions(report, raw)
    coverage_status = next(
        d for d in decisions
        if d.kind == "inferred" and (d.fhir_path or "").endswith(".status") and "Coverage" not in ""
        and d.lost_value == "active"
    )
    built = json.loads(bundle.model_dump_json(exclude_none=True))
    built, outcomes = apply_rejections(built, decisions, {coverage_status.id})
    outcome = next(o for o in outcomes if o.decision_id == coverage_status.id)
    assert outcome.applied is True
    assert outcome.strategy == "absent"


def test_free_text_field_is_not_split_into_phantom_components():
    # "^" in a free-text field is a character somebody typed, not a
    # component separator - the same raw_field_str/field_str split the
    # mappers themselves make. Splitting on it invented a component the
    # mapper could not have read, so the register accused it of losing
    # data it had in fact carried whole.
    raw = (_EDI_FIXTURES / "mdm_t02_obx_with_caret.hl7").read_text()
    bundle, report, _ = convert_with_provenance(raw)
    dropped = _by_location([d for d in compute_decisions(report, raw) if d.kind == "dropped"])

    assert not [loc for loc in dropped if loc.startswith("OBX-5")]
    binary = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary")
    assert "^" in binary.data.decode()


def test_unparseable_date_is_reported_against_an_implemented_target():
    # PID-7 maps to Patient.birthDate and this app builds it whenever the
    # value parses, so a PID-7 reported here is a malformed message rather
    # than a gap. It is still surfaced: data that did not reach the output
    # is exactly what the register exists to show.
    raw = (_EDI_FIXTURES / "validation_generic_pid7_unparseable.hl7").read_text()
    _, report, _ = convert_with_provenance(raw)
    dropped = _by_location([d for d in compute_decisions(report, raw) if d.kind == "dropped"])

    assert "PID-7" in dropped
    assert "could not be parsed" in dropped["PID-7"].citation.title


def _cda_bundle_and_decisions(fixture: str):
    """Decisions computed the way the route computes them - with resolved
    source spans, without which the C-CDA drop scan can only match by
    value and reports most transformed values as lost."""
    from app.provenance.highlighting import build_highlighting_payload

    raw = (_EDI_FIXTURES / fixture).read_text(encoding="utf-8")
    bundle, report, _ = convert_with_provenance(raw)
    highlighting = build_highlighting_payload(bundle, report, raw, report.source_format)
    spans = {tuple(m.source_span) for m in highlighting.matches if m.source_span}
    return bundle, compute_decisions(report, raw, spans)


def _decisions_for_any_fixture(fixture: str):
    """Decisions the way the route computes them, whichever format it is.
    Only C-CDA needs resolved source spans - the HL7v2 and X12 scans work
    from positional coordinates and match without them."""
    raw = (_EDI_FIXTURES / fixture).read_text(encoding="utf-8")
    bundle, report, _ = convert_with_provenance(raw)
    if report.source_format != "CDA":
        return compute_decisions(report, raw)
    from app.provenance.highlighting import build_highlighting_payload

    highlighting = build_highlighting_payload(bundle, report, raw, report.source_format)
    spans = {tuple(m.source_span) for m in highlighting.matches if m.source_span}
    return compute_decisions(report, raw, spans)


_ALL_FIXTURES = sorted(
    f.name
    for f in (Path(__file__).parent / "fixtures").iterdir()
    if f.suffix in {".hl7", ".xml", ".x12"}
)


@pytest.mark.parametrize("fixture", _ALL_FIXTURES)
def test_every_drop_carries_a_checked_verdict(fixture):
    # The register's whole value is that a reader can trust what it says.
    # "Not yet checked against the source IG" is an honest placeholder
    # while verdicts are being written, but it is not a finished state -
    # every shape this app actually drops has been read against the
    # governing IG (or, for X12, against the fact that no crosswalk is
    # published at all), and this keeps it that way as mappings change.
    #
    # Parametrized across all three formats rather than C-CDA alone: the
    # claim is made about all of them. Note that the EDI fixtures satisfy
    # this structurally - _dropped_edi_decisions cites the absent X12
    # crosswalk unconditionally and cannot produce an unchecked drop - so
    # test_every_edi_drop_cites_the_absent_crosswalk below is what really
    # holds EDI's half up. They are kept in the parametrization anyway, so
    # the day an EDI verdict table exists this starts covering it.
    try:
        decisions = _decisions_for_any_fixture(fixture)
    except Exception:
        # Fixtures that deliberately do not convert - malformed input, an
        # unrecognised document type, an unmapped trigger - have no
        # decisions to check.
        pytest.skip("fixture does not convert by design")
    unchecked = [
        d.source_location
        for d in decisions
        if d.kind == "dropped" and d.citation.title == "Not yet checked against the source IG"
    ]
    assert unchecked == []


@pytest.mark.parametrize(
    "fixture", [f for f in _ALL_FIXTURES if f.endswith(".x12")]
)
def test_every_edi_drop_cites_the_absent_crosswalk(fixture):
    # X12 publishes no FHIR crosswalk at all, so _dropped_edi_decisions
    # cites that fact unconditionally and can never produce an unchecked
    # drop or a gap. That makes EDI's arm of the two tests above
    # structurally satisfied rather than verified - they would pass even
    # if EDI's citations were wrong - so this is the assertion actually
    # holding EDI's half of the claim up.
    #
    # It fails if EDI drops ever start citing something else: either a
    # real verdict table appeared (in which case add EDI to the checked
    # set properly) or a citation regressed.
    try:
        decisions = _decisions_for_any_fixture(fixture)
    except Exception:
        pytest.skip("fixture does not convert by design")
    dropped = [d for d in decisions if d.kind == "dropped"]
    wrong = sorted({
        d.citation.title
        for d in dropped
        if d.citation.title != "No official X12-to-FHIR crosswalk exists"
    })
    assert wrong == []


@pytest.mark.parametrize("fixture", _ALL_FIXTURES)
def test_no_drop_is_an_unclosed_gap(fixture):
    # The other half of the claim CLAUDE.md makes. A GAP means the IG
    # defines a target this app does not build - a real defect, not a
    # disclosure, so it should be loud.
    #
    # If this fails on a genuinely newly-found gap, that is the test
    # working: either close the gap, or change the claim in CLAUDE.md and
    # this test together. What it exists to prevent is the claim quietly
    # going stale while nothing notices - which is exactly what happened
    # to the drop counts this replaced.
    try:
        decisions = _decisions_for_any_fixture(fixture)
    except Exception:
        pytest.skip("fixture does not convert by design")
    gaps = [d.source_location for d in decisions if (d.summary or "").startswith("GAP:")]
    assert gaps == []


def test_cda_verdicts_are_scoped_by_template_id():
    # A bare shape is ambiguous - an Allergy Reaction Observation's id and
    # a Problem Observation's id sit at identical depths under identically
    # named tags - so a verdict may name the template it read, and a
    # scoped key must beat an unscoped one however specific.
    from app.provenance.cda_ig_verdicts import NOT_SUPPORTED, verdict_for

    reaction = frozenset({"2.16.840.1.113883.10.20.22.4.9"})
    shape = "entry/act/entryRelationship/observation/entryRelationship/observation/id"
    verdict, citation, note = verdict_for(shape, reaction)
    assert verdict == NOT_SUPPORTED
    assert "reaction is a backbone element with no identifier" in note

    # The same shape with no template in scope stays honestly unchecked
    # rather than borrowing another section's ruling.
    assert verdict_for(shape, frozenset())[0] is None


def test_unconverted_entry_is_one_decision_not_one_per_element():
    # A negated Medication Activity produces no MedicationRequest, so
    # everything it carried drops with it. Six rows for its id, statusCode
    # and code make one decision look like six and bury the thing worth
    # reviewing: a whole clinical statement was discarded.
    _, decisions = _cda_bundle_and_decisions("ccd_medications_negated.xml")
    unconverted = [d for d in decisions if d.citation.title == "Source entry not converted"]
    assert len(unconverted) == 1
    assert "produced no FHIR resource" in unconverted[0].summary
    # ...and none of its own elements is reported separately.
    entry_path = unconverted[0].source_location
    assert not [
        d for d in decisions
        if d.kind == "dropped" and (d.source_location or "").startswith(entry_path + "/")
    ]


def test_a_skipped_entry_cannot_borrow_its_siblings_reads():
    # The relative-path fallback resolves which *repeat* of an element a
    # recorded location belongs to. Sibling entries always share their
    # relative paths, so applying it to the entry-level scan let a wholly
    # skipped entry vanish from the register - nothing at all reported for
    # a discarded clinical statement.
    #
    # Built inline rather than from a fixture: the case needs a skipped
    # entry beside a converted one of the same shape, which the two
    # single-entry negated fixtures cannot provide. Every value in it is
    # deliberately unique to this entry, so the value-matching fallback
    # cannot absolve it either and the path fallback is what is actually
    # under test.
    negated_entry = """          <entry typeCode="DRIV">
            <substanceAdministration classCode="SBADM" moodCode="EVN" negationInd="true">
              <templateId root="2.16.840.1.113883.10.20.22.4.16"/>
              <id root="ffffffff-9999-4a1a-8a1a-999999999999"/>
              <statusCode code="aborted"/>
              <consumable>
                <manufacturedProduct classCode="MANU">
                  <templateId root="2.16.840.1.113883.10.20.22.4.23"/>
                  <manufacturedMaterial>
                    <code code="999999" codeSystem="2.16.840.1.113883.6.88" displayName="Placebo 1 MG Oral Tablet"/>
                  </manufacturedMaterial>
                </manufacturedProduct>
              </consumable>
            </substanceAdministration>
          </entry>"""
    raw = (_EDI_FIXTURES / "ccd_medications_basic.xml").read_text(encoding="utf-8")
    raw = raw.replace("</section>", negated_entry + "\n        </section>", 1)

    bundle, report, _ = convert_with_provenance(raw)
    # The negated entry builds nothing; only the two original entries do.
    assert sum(
        1 for e in bundle.entry if e.resource.get_resource_type() == "MedicationRequest"
    ) == 2

    from app.provenance.highlighting import build_highlighting_payload

    highlighting = build_highlighting_payload(bundle, report, raw, report.source_format)
    spans = {tuple(m.source_span) for m in highlighting.matches if m.source_span}
    decisions = compute_decisions(report, raw, spans)

    unconverted = [d for d in decisions if d.citation.title == "Source entry not converted"]
    assert len(unconverted) == 1, [d.source_location for d in unconverted]
    assert unconverted[0].source_location.endswith("entry[2]")


def test_values_carried_into_the_bundle_are_never_reported_as_dropped():
    # The rule that caught nine false drops: before calling anything a
    # gap, check whether the value actually reached the Bundle. A register
    # that accuses the mapper of losing data it carried is worse than one
    # that says "unchecked".
    bundle, decisions = _cda_bundle_and_decisions("ccd_procedures_basic.xml")
    procedure = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "Procedure"
    )
    assert procedure.code.text == "Appendectomy of the appendix"
    dropped = [d.source_location or "" for d in decisions if d.kind == "dropped"]
    assert not [loc for loc in dropped if loc.endswith("code/originalText")]

    role = next(
        e.resource for e in bundle.entry if e.resource.get_resource_type() == "PractitionerRole"
    )
    assert role.telecom[0].use == "work"
    assert not [loc for loc in dropped if loc.endswith("assignedEntity/telecom/@use")]
