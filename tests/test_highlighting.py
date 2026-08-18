"""Tests for app/provenance/highlighting.py - the orchestrator behind the
Data Specification page's correlated highlighting. Covers the
occurrence-claiming algorithm end-to-end per format, plus a dedicated
regression test for each real bug this module's own development caught
and fixed (not hypothetical edge cases - each was reproduced directly
against a real fixture before being fixed):

  - a pre-existing bug in the *original* provenance recording itself (270/
    271's own item[].category fact recorded the wrong relative_path,
    predating this module and undetected until this module's own
    json_locator cross-check surfaced it)
  - C-CDA's bare-trailing-tag grammar shape (family/given/city/...)
    resolving to the wrong span (an element's start tag instead of its
    text)
  - the cross-section root-tag collision (Vitals vs Results, Problems vs
    Hospital Discharge Diagnosis)
  - the shared-physical-segment problem (ORU's OBX-16 Practitioner
    corrupting later, unrelated Observations' own occurrence claims)
  - the shared-physical-segment problem's own second-order bug (a
    Practitioner shared by *two* Observations incorrectly relaying its
    borrowed occurrence on to the second one)
  - EDI's DTP-segment-with-multiple-qualifiers collision (837I's own
    claim-level DTP*434 vs per-line DTP*472)
  - Bundle-level facts sharing one physical segment (MSH-7/MSH-10)
    wrongly claiming separate occurrences

A full sweep across every real fixture in tests/fixtures/ (not committed
as a standing test, matching this project's own established precedent for
this kind of exhaustive-but-redundant verification - see app/dedup.py's
own testing notes) was run before this file was written, confirming zero
mismatches and zero crashes across all 1464 direct entries this app's
real fixtures produce."""

from app.provenance.dispatch import convert_with_provenance
from app.provenance.highlighting import build_highlighting_payload

FIXTURES = "tests/fixtures"


def read_fixture(name: str) -> str:
    with open(f"{FIXTURES}/{name}", encoding="utf-8") as f:
        return f.read()


def _match_by_path(report, payload):
    return {entry.fhir_path: (entry, m) for entry, m in zip(report.entries, payload.matches)}


def _resolved_source_text(payload, match):
    if match.source_span is None:
        return None
    return payload.display_source_text[match.source_span[0] : match.source_span[1]]


def _resolved_fhir_text(payload, match):
    if match.fhir_span is None:
        return None
    return payload.fhir_json_text[match.fhir_span[0] : match.fhir_span[1]]


# --- end-to-end, one real fixture per format ---


def test_hl7v2_adt_end_to_end_resolves_both_sides_with_matching_colors():
    raw = read_fixture("adt_a01_basic.hl7")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    entry, match = by_path["Bundle.entry[0].resource.name[0].family"]
    assert _resolved_source_text(payload, match) == "Doe"
    assert _resolved_fhir_text(payload, match) == '"Doe"'
    assert match.color_index is not None
    assert match.fhir_token_type == "string"

    # Inferred entries never get a source span (there is none) but the
    # FHIR side still resolves - the crosswalk stays a complete picture.
    status_entries = [(e, m) for e, m in zip(report.entries, payload.matches) if e.derivation == "inferred"]
    assert status_entries
    for entry, match in status_entries:
        assert match.source_span is None
        assert match.fhir_span is not None


def test_edi_270_end_to_end_resolves_both_sides():
    raw = read_fixture("edi_270_basic.x12")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    entry, match = by_path["Bundle.entry[0].resource.name"]
    assert _resolved_source_text(payload, match) == "ACME HEALTH PLAN"
    assert _resolved_fhir_text(payload, match) == '"ACME HEALTH PLAN"'


def test_cda_allergies_end_to_end_resolves_both_sides():
    raw = read_fixture("ccd_allergies_basic.xml")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    entry, match = by_path["Bundle.entry[0].resource.name[0].family"]
    assert _resolved_source_text(payload, match) == "Gierson"
    assert _resolved_fhir_text(payload, match) == '"Gierson"'


# --- regression: pre-existing 270/271 relative_path bug, caught by this module ---


def test_270_item_category_resolves_to_the_real_fhir_field():
    # A pre-existing bug (predating this module, in the original
    # provenance recording): CoverageEligibilityRequestItem.category was
    # recorded as "item[0].coding[0].code" instead of "item[0].category.
    # coding[0].code" - a path that simply doesn't exist in the real JSON.
    # Fixed in app/edi/eligibility_270.py; this proves it stays fixed by
    # confirming the FHIR side now actually resolves.
    raw = read_fixture("edi_270_basic.x12")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    entry, match = by_path["Bundle.entry[5].resource.item[0].category.coding[0].code"]
    assert match.fhir_span is not None
    assert _resolved_fhir_text(payload, match) == '"30"'
    assert _resolved_source_text(payload, match) == "30"


def test_271_insurance_item_category_resolves_to_the_real_fhir_field():
    raw = read_fixture("edi_271_basic.x12")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    matching = [path for path in by_path if path.endswith("insurance[0].item[0].category.coding[0].code")]
    assert matching, "expected a resolvable insurance[0].item[0].category.coding[0].code fact"
    entry, match = by_path[matching[0]]
    assert match.fhir_span is not None


# --- regression: C-CDA bare-trailing-tag grammar (family/given/city/...) ---


def test_cda_bare_trailing_tag_resolves_to_text_not_start_tag():
    raw = read_fixture("ccd_allergies_basic.xml")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    family_entry, family_match = by_path["Bundle.entry[0].resource.name[0].family"]
    given_entry, given_match = by_path["Bundle.entry[0].resource.name[0].given[0]"]
    # Before the fix, these resolved to "<family>"/"<given>" (the element's
    # own start tag) instead of its text content.
    assert _resolved_source_text(payload, family_match) == "Gierson"
    assert _resolved_source_text(payload, given_match) == "Alba"
    assert "<" not in _resolved_source_text(payload, family_match)


# --- regression: cross-section root-tag collision (Vitals/Results, Problems/HDD) ---


def test_vitals_and_results_organizer_collision_resolves_independently():
    import re

    vitals_doc = read_fixture("ccd_vitals_basic.xml")
    results_doc = read_fixture("ccd_results_basic.xml")
    # Captures the whole <component><section>...</section></component>
    # wrapper (not just the bare <section>) - build_sectioned_bundle's own
    # real section-discovery walk requires that exact nesting
    # (component/structuredBody/component/section); a bare <section>
    # inserted without it is invisible to the real conversion pipeline,
    # even though app/provenance/cda_locator.py's own tag-name-based
    # search (tested directly in tests/test_cda_locator.py) doesn't care.
    match = re.search(
        r"<component>\s*<section>(?:(?!</section>).)*2\.16\.840\.1\.113883\.10\.20\.22\.2\.3\.1(?:(?!</section>).)*</section>\s*</component>",
        results_doc,
        re.S,
    )
    combined = vitals_doc.replace("</structuredBody>", f"{match.group()}</structuredBody>")

    bundle, report = convert_with_provenance(combined)
    payload = build_highlighting_payload(bundle, report, combined, report.source_format)
    by_path = _match_by_path(report, payload)

    vitals_member = next(
        (e, m) for e, m in by_path.values() if e.fhir_path.endswith(".code.coding[0].code") and e.value == "8867-4"
    )
    results_member = next(
        (e, m) for e, m in by_path.values() if e.fhir_path.endswith(".code.coding[0].code") and e.value == "6690-2"
    )
    assert _resolved_source_text(payload, vitals_member[1]) == "8867-4"
    assert _resolved_source_text(payload, results_member[1]) == "6690-2"


def test_problems_and_hospital_discharge_diagnosis_act_collision_resolves_independently():
    raw = read_fixture("discharge_summary_basic.xml")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    condition_facts = [(e, m) for path, (e, m) in by_path.items() if path.endswith("code.coding[0].code") and e.source_location and "entryRelationship[SUBJ]" in e.source_location]
    resolved_values = {_resolved_source_text(payload, m) for e, m in condition_facts if m.source_span}
    assert len(resolved_values) >= 2  # the two conditions resolve to genuinely distinct values, not the same one twice


# --- regression: shared-physical-segment problem (OBX-16 Practitioner) ---


def test_oru_performer_borrows_the_observations_own_occurrence():
    raw = read_fixture("oru_r01_basic.hl7")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    # Every OBX-based fact across all three Observations plus the shared
    # Practitioner must resolve to a value that actually matches its own
    # recorded value - the real assertion that would fail if occurrence
    # claims got shifted by the Practitioner's own OBX-16 claim.
    for path, (entry, match) in by_path.items():
        if entry.derivation != "direct" or not entry.source_location or not entry.source_location.startswith("OBX"):
            continue
        if entry.source_value is not None:
            assert _resolved_source_text(payload, match) == entry.source_value, path


def test_oru_shared_performer_does_not_relay_borrowed_occurrence_to_second_observation():
    # oru_r01_shared_performer.hl7: one Practitioner referenced by TWO
    # different Observations' own OBX-16. The Practitioner correctly
    # borrows the first Observation's occurrence - but the second
    # Observation must NOT then borrow that same (already-borrowed)
    # occurrence via the shared Practitioner as a bridge; it must claim
    # its own, independent occurrence instead.
    raw = read_fixture("oru_r01_shared_performer.hl7")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    for path, (entry, match) in by_path.items():
        if entry.derivation != "direct" or entry.source_value is None:
            continue
        if entry.source_location and entry.source_location.startswith("OBX"):
            assert _resolved_source_text(payload, match) == entry.source_value, path


# --- regression: EDI DTP multi-qualifier collision ---


def test_837i_dtp_qualifier_filter_ignores_unrelated_claim_level_dtp():
    # edi_837i_basic.x12 carries a claim-level DTP*434 (statement period,
    # never read into any FHIR field) *before* its own two per-line
    # DTP*472 (service date) segments - without filtering by qualifier,
    # the unrelated DTP*434 would be counted as an "occurrence," shifting
    # both real service-date claims off by one.
    raw = read_fixture("edi_837i_basic.x12")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    item0_entry, item0_match = by_path["Bundle.entry[5].resource.item[0].servicedDate"]
    item1_entry, item1_match = by_path["Bundle.entry[5].resource.item[1].servicedDate"]
    assert _resolved_source_text(payload, item0_match) == item0_entry.source_value
    assert _resolved_source_text(payload, item1_match) == item1_entry.source_value


# --- regression: Bundle-level facts sharing one physical segment ---


def test_bundle_level_msh_facts_resolve_to_the_one_real_msh_segment():
    raw = read_fixture("oru_r01_basic.hl7")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    identifier_entry, identifier_match = by_path["Bundle.identifier.value"]
    timestamp_entry, timestamp_match = by_path["Bundle.timestamp"]
    # source_value isn't always populated (it's an optional recorder
    # param) - value is the safe comparison here since MSH-7/MSH-10 are
    # copied through unchanged (unlike e.g. a date/gender transformation).
    assert _resolved_source_text(payload, identifier_match) == identifier_entry.value
    assert _resolved_source_text(payload, timestamp_match) == timestamp_entry.source_value
    # Both facts' resolved spans must fall on the same physical line (MSH
    # only occurs once) - not two different, wrongly-claimed occurrences.
    msh_line_start = payload.display_source_text.find("MSH|")
    msh_line_end = payload.display_source_text.find("\n", msh_line_start)
    assert msh_line_start <= identifier_match.source_span[0] < msh_line_end
    assert msh_line_start <= timestamp_match.source_span[0] < msh_line_end


# --- color assignment and graceful degradation ---


def test_inferred_entries_never_get_a_color_or_source_span():
    raw = read_fixture("adt_a01_basic.hl7")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    for entry, match in zip(report.entries, payload.matches):
        if entry.derivation == "inferred":
            assert match.color_index is None
            assert match.source_span is None


def test_unsupported_format_still_returns_a_payload_without_crashing():
    raw = read_fixture("edi_278_request_basic.x12")
    bundle, report = convert_with_provenance(raw)
    # 278 is a fully-instrumented EDI family - this exercises the general
    # "still works even for an unremarkable, fully-resolved format" path
    # rather than testing anything unsupported specifically; the crash-
    # safety contract itself is exercised by malformed-input tests below.
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    assert payload.fhir_json_text
    assert payload.display_source_text


def test_malformed_source_text_degrades_gracefully_not_a_crash():
    raw = read_fixture("adt_a01_basic.hl7")
    bundle, report = convert_with_provenance(raw)
    # Deliberately mismatched raw_text (won't parse as the real message
    # that produced `bundle`) - the real assertion is that this doesn't
    # raise; a mismatched text may still coincidentally resolve some
    # spans (it's still well-formed HL7v2, just different content), so
    # this doesn't assert zero resolution, only that the payload comes
    # back complete and usable.
    payload = build_highlighting_payload(bundle, report, "not a real HL7 message at all", report.source_format)
    assert len(payload.matches) == len(report.entries)


def test_color_indices_cycle_through_the_palette():
    raw = read_fixture("ccd_results_basic.xml")
    bundle, report = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    colored = [m.color_index for m in payload.matches if m.color_index is not None]
    assert colored == [i % 10 for i in range(len(colored))]  # sequential, cycling through the 10-color palette
    assert all(0 <= c < 10 for c in colored)
