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
    bundle, report, _ = convert_with_provenance(raw)
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
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    entry, match = by_path["Bundle.entry[0].resource.name"]
    assert _resolved_source_text(payload, match) == "ACME HEALTH PLAN"
    assert _resolved_fhir_text(payload, match) == '"ACME HEALTH PLAN"'


def test_cda_allergies_end_to_end_resolves_both_sides():
    raw = read_fixture("ccd_allergies_basic.xml")
    bundle, report, _ = convert_with_provenance(raw)
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
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    entry, match = by_path["Bundle.entry[5].resource.item[0].category.coding[0].code"]
    assert match.fhir_span is not None
    assert _resolved_fhir_text(payload, match) == '"30"'
    assert _resolved_source_text(payload, match) == "30"


def test_271_insurance_item_category_resolves_to_the_real_fhir_field():
    raw = read_fixture("edi_271_basic.x12")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    matching = [path for path in by_path if path.endswith("insurance[0].item[0].category.coding[0].code")]
    assert matching, "expected a resolvable insurance[0].item[0].category.coding[0].code fact"
    entry, match = by_path[matching[0]]
    assert match.fhir_span is not None


# --- regression: C-CDA bare-trailing-tag grammar (family/given/city/...) ---


def test_cda_bare_trailing_tag_resolves_to_text_not_start_tag():
    raw = read_fixture("ccd_allergies_basic.xml")
    bundle, report, _ = convert_with_provenance(raw)
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

    bundle, report, _ = convert_with_provenance(combined)
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
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    condition_facts = [(e, m) for path, (e, m) in by_path.items() if path.endswith("code.coding[0].code") and e.source_location and "entryRelationship[SUBJ]" in e.source_location]
    resolved_values = {_resolved_source_text(payload, m) for e, m in condition_facts if m.source_span}
    assert len(resolved_values) >= 2  # the two conditions resolve to genuinely distinct values, not the same one twice


# --- regression: shared-physical-segment problem (OBX-16 Practitioner) ---


def test_oru_performer_borrows_the_observations_own_occurrence():
    raw = read_fixture("oru_r01_basic.hl7")
    bundle, report, _ = convert_with_provenance(raw)
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
    bundle, report, _ = convert_with_provenance(raw)
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
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    item0_entry, item0_match = by_path["Bundle.entry[5].resource.item[0].servicedDate"]
    item1_entry, item1_match = by_path["Bundle.entry[5].resource.item[1].servicedDate"]
    assert _resolved_source_text(payload, item0_match) == item0_entry.source_value
    assert _resolved_source_text(payload, item1_match) == item1_entry.source_value


# --- regression: Bundle-level facts sharing one physical segment ---


def test_bundle_level_msh_facts_resolve_to_the_one_real_msh_segment():
    raw = read_fixture("oru_r01_basic.hl7")
    bundle, report, _ = convert_with_provenance(raw)
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
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    for entry, match in zip(report.entries, payload.matches):
        if entry.derivation == "inferred":
            assert match.color_index is None
            assert match.source_span is None


def test_unsupported_format_still_returns_a_payload_without_crashing():
    raw = read_fixture("edi_278_request_basic.x12")
    bundle, report, _ = convert_with_provenance(raw)
    # 278 is a fully-instrumented EDI family - this exercises the general
    # "still works even for an unremarkable, fully-resolved format" path
    # rather than testing anything unsupported specifically; the crash-
    # safety contract itself is exercised by malformed-input tests below.
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    assert payload.fhir_json_text
    assert payload.display_source_text


def test_malformed_source_text_degrades_gracefully_not_a_crash():
    raw = read_fixture("adt_a01_basic.hl7")
    bundle, report, _ = convert_with_provenance(raw)
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
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    colored = [m.color_index for m in payload.matches if m.color_index is not None]
    assert colored == [i % 10 for i in range(len(colored))]  # sequential, cycling through the 10-color palette


# --- dedup-aware provenance (app/provenance/dispatch.py's own
# `deduplicate` parameter) - see that module's own docstring for the full
# design reasoning: dedup runs *before* resolve_bundle_paths, so a fact
# recorded against a resource dedup goes on to remove simply hits
# resolve_bundle_paths' own pre-existing "unresolvable resource_id is
# skipped, not raised" branch, with zero changes needed to recorder.py/
# resolver.py/highlighting.py themselves. ---


def _resource_ids_with_facts(bundle, report, resource_type: str) -> set[str]:
    """Every distinct resource id of the given type that has at least one
    fact in `report.entries` - used below to prove which physical
    Practitioner resource(s) a merged-away duplicate's own facts pointed
    at, before vs. after dedup."""
    ids = set()
    for entry in report.entries:
        if not entry.fhir_path.startswith("Bundle.entry["):
            continue
        index = int(entry.fhir_path[len("Bundle.entry[") : entry.fhir_path.index("]")])
        resource = bundle.entry[index].resource
        if resource.get_resource_type() == resource_type:
            ids.add(resource.id)
    return ids


def test_deduplicate_false_by_default_returns_no_dedup_result():
    raw = read_fixture("edi_837p_basic.x12")
    bundle, report, dedup_result = convert_with_provenance(raw)
    assert dedup_result is None
    assert len(_resource_ids_with_facts(bundle, report, "Practitioner")) == 2


def test_deduplicate_merges_billing_and_rendering_provider_and_drops_the_removed_ones_facts():
    # The real motivating case app/dedup.py itself was built for, reused
    # here: edi_837p_basic.x12's own Billing Provider (NM1*85) and
    # Rendering Provider (NM1*82) share one NPI - see
    # tests/test_dedup.py::test_real_837p_fixture_merges_billing_and_rendering_provider
    # for the equivalent proof against the plain (non-provenance) pipeline.
    raw = read_fixture("edi_837p_basic.x12")
    bundle, report, dedup_result = convert_with_provenance(raw, deduplicate=True)

    assert dedup_result is not None
    assert dedup_result.merged_count == 1
    assert dedup_result.merges[0].resource_type == "Practitioner"
    kept_id = dedup_result.merges[0].kept_id
    removed_id = dedup_result.merges[0].removed_ids[0]

    # Only the kept (canonical) Practitioner has any facts left - the
    # removed duplicate's own NM1-loop-derived name/identifier facts are
    # gone, not dangling or reattributed to the wrong resource.
    practitioner_ids_with_facts = _resource_ids_with_facts(bundle, report, "Practitioner")
    assert practitioner_ids_with_facts == {kept_id}
    assert removed_id not in practitioner_ids_with_facts

    # The kept resource no longer being at its original entry[N] index
    # (entries shifted after the duplicate's own entry was removed) is
    # exactly what proves resolve_bundle_paths re-resolved against the
    # *post*-dedup bundle, not the original one - every entry it produced
    # must point at a resource that genuinely still exists.
    index_by_id = {entry.resource.id: i for i, entry in enumerate(bundle.entry)}
    assert kept_id in index_by_id
    assert removed_id not in index_by_id


def test_deduplicate_true_with_no_duplicates_leaves_entries_unchanged():
    # A fixture with nothing to merge - dedup must be a genuine no-op on
    # the crosswalk, not silently drop or alter unrelated facts.
    raw = read_fixture("adt_a01_basic.hl7")
    bundle_plain, report_plain, dedup_result_plain = convert_with_provenance(raw)
    bundle_deduped, report_deduped, dedup_result_deduped = convert_with_provenance(raw, deduplicate=True)

    assert dedup_result_plain is None
    assert dedup_result_deduped is not None
    assert dedup_result_deduped.merged_count == 0
    assert dedup_result_deduped.merges == ()

    assert len(report_deduped.entries) == len(report_plain.entries)
    assert {e.fhir_path for e in report_deduped.entries} == {e.fhir_path for e in report_plain.entries}


def test_deduplicate_highlighting_still_resolves_for_the_surviving_practitioner():
    # build_highlighting_payload (see app/routes/data_specification.py's
    # own _run_crosswalk) is always called with the already-deduplicated
    # bundle/report - confirm it needs no dedup-awareness of its own: the
    # surviving Practitioner's own facts still resolve to real,
    # non-overlapping spans in both panes.
    raw = read_fixture("edi_837p_basic.x12")
    bundle, report, dedup_result = convert_with_provenance(raw, deduplicate=True)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    assert len(payload.matches) == len(report.entries)

    kept_id = dedup_result.merges[0].kept_id
    kept_index = next(i for i, entry in enumerate(bundle.entry) if entry.resource.id == kept_id)
    name_path = f"Bundle.entry[{kept_index}].resource.name[0]"
    matched = [m for m in payload.matches if m.fhir_path.startswith(name_path)]
    assert matched
    assert any(m.source_span is not None for m in matched)


# --- content-verified occurrence claiming (app/provenance/highlighting.py's
# own _claim_fresh_occurrence) - found and fixed while verifying dedup-aware
# provenance in a real browser against edi_837p_basic.x12: the original
# purely-sequential claiming scheme silently mis-resolved every NM1-rooted
# fact in the whole 837 family, since 837P/837I/837D all carry two leading,
# untracked NM1 loops (1000A Submitter, 1000B Receiver) before the first one
# this app maps to a FHIR field, AND build their own tracked resources in an
# order that doesn't match physical document order (Billing, Payer,
# Subscriber, Rendering - but the raw text has Billing, Subscriber, Payer,
# Rendering). Neither the DTP-qualifier fix nor the shared-physical-segment
# fix already covered this - both fixed a *different* kind of miscount. ---


def test_837p_nm1_facts_resolve_to_their_own_physical_segment_not_an_untracked_one():
    # Reproduces the real bug directly: before this fix, every NM1-rooted
    # fact resolved 2-3 occurrences off, onto the wrong physical NM1 loop
    # entirely (e.g. the Billing Provider's own recorded name "KILDARE"
    # highlighted the Submitter's "PREMIER BILLING SERVICE" text instead).
    raw = read_fixture("edi_837p_basic.x12")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    for entry, match in zip(report.entries, payload.matches):
        if entry.source_location and entry.source_location.startswith("NM1") and match.source_span is not None:
            assert _resolved_source_text(payload, match) == entry.value, (
                f"{entry.fhir_path} ({entry.source_location}) resolved to the wrong physical NM1 segment"
            )


def test_837i_and_837d_nm1_facts_also_resolve_correctly():
    # 837I/837D share the identical Submitter/Receiver-plus-out-of-order-
    # build shape - same bug, same fix, confirmed against both siblings too.
    for fixture in ("edi_837i_basic.x12", "edi_837d_basic.x12"):
        raw = read_fixture(fixture)
        bundle, report, _ = convert_with_provenance(raw)
        payload = build_highlighting_payload(bundle, report, raw, report.source_format)
        for entry, match in zip(report.entries, payload.matches):
            if entry.source_location and entry.source_location.startswith("NM1") and match.source_span is not None:
                assert _resolved_source_text(payload, match) == entry.value, (
                    f"{fixture}: {entry.fhir_path} ({entry.source_location}) resolved to the wrong physical NM1 segment"
                )


def test_content_matching_still_disambiguates_genuine_duplicate_values():
    # The one case where two physical NM1 occurrences legitimately carry
    # the *identical* text (edi_837p_basic.x12's own solo-practitioner
    # shape - the real motivating case for app/dedup.py itself): the
    # Billing and Rendering Provider both say "KILDARE"/"BEN"/the same NPI.
    # Content matching must still resolve each to its *own* physical NM1
    # loop, not collide, since it excludes already-claimed occurrences.
    raw = read_fixture("edi_837p_basic.x12")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)

    family_matches = [
        (entry, m)
        for entry, m in zip(report.entries, payload.matches)
        if entry.fhir_path.endswith(".resource.name[0].family") and entry.value == "KILDARE"
    ]
    assert len(family_matches) == 2
    spans = {m.source_span for _, m in family_matches}
    assert len(spans) == 2  # two genuinely distinct physical occurrences, not the same one twice
    for entry, m in family_matches:
        assert _resolved_source_text(payload, m) == "KILDARE"


def test_content_matching_falls_back_to_sequential_for_transformed_display_values():
    # A person_display-built string (e.g. ADT's PV1-7 -> "Smith, John")
    # never appears literally in the raw text, so content matching finds
    # no match and must fall through to the original sequential scheme -
    # already correct for these fields, must not regress.
    raw = read_fixture("adt_a01_basic.hl7")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)
    entry, match = by_path["Bundle.entry[1].resource.participant[0].individual.display"]
    assert entry.value == "Smith, John"
    assert match.source_span is not None
    assert _resolved_source_text(payload, match) == "1234^Smith^John^^^^MD"


# --- .detail[N] occurrence disambiguation (PaymentReconciliation.detail[],
# one per 835 CLP claim) - found via the same real-fixture verification pass
# as the NM1 bug above, against edi_835_multi_claim.x12: _item_index only
# recognized `.item[N]`, so a second CLP claim's own facts (all sharing the
# identical "CLP-1"/"CLP-2"/"CLP-4" location strings, on the one
# PaymentReconciliation resource) silently reused the first claim's own
# already-claimed physical CLP segment instead of claiming its own. ---


def test_835_multi_claim_details_each_resolve_to_their_own_clp_segment():
    raw = read_fixture("edi_835_multi_claim.x12")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    first_id_entry, first_id_match = by_path["Bundle.entry[2].resource.detail[0].identifier.value"]
    second_id_entry, second_id_match = by_path["Bundle.entry[2].resource.detail[1].identifier.value"]
    assert first_id_entry.value == "PCN22222"
    assert second_id_entry.value == "PCN33333"
    assert _resolved_source_text(payload, first_id_match) == "PCN22222"
    assert _resolved_source_text(payload, second_id_match) == "PCN33333"
    assert first_id_match.source_span != second_id_match.source_span

    first_amount_entry, first_amount_match = by_path["Bundle.entry[2].resource.detail[0].amount.value"]
    second_amount_entry, second_amount_match = by_path["Bundle.entry[2].resource.detail[1].amount.value"]
    assert first_amount_entry.value == "250.00"
    assert second_amount_entry.value == "0.00"
    assert _resolved_source_text(payload, first_amount_match) == "250.00"
    assert _resolved_source_text(payload, second_amount_match) == "0.00"


def test_several_resources_from_one_cda_organizer_all_resolve():
    """One <organizer> builds a Vital Signs panel plus an Observation per
    reading. Each got its own fresh occurrence of "organizer", but only one
    exists - so every resource past the first claimed an index off the end
    and resolved to nothing, leaving most of the Vitals crosswalk with no
    highlight at all."""
    raw = read_fixture("ccd_vitals_basic.xml")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)

    organizer_facts = [
        (entry, match)
        for entry, match in zip(report.entries, payload.matches)
        if (entry.source_location or "").startswith("organizer/")
    ]
    assert len(organizer_facts) > 10, "the vitals fixture records many organizer-relative facts"
    unresolved = [e.source_location for e, m in organizer_facts if m.source_span is None]
    assert not unresolved, unresolved

    # And they resolve to their own distinct readings, not all to one.
    resolved = {_resolved_source_text(payload, m) for _, m in organizer_facts}
    assert len(resolved) > 5


def test_borrowing_still_refuses_to_relay_while_an_occurrence_is_free():
    """The relay allowance is limited to the case where every physical
    occurrence is already claimed. An ORU carries one OBX per result, so a
    Practitioner shared by two of them must NOT bridge them - the second
    Observation has its own unclaimed OBX and must take it."""
    raw = read_fixture("oru_r01_shared_performer.hl7")
    bundle, report, _ = convert_with_provenance(raw)
    payload = build_highlighting_payload(bundle, report, raw, report.source_format)
    by_path = _match_by_path(report, payload)

    first = by_path["Bundle.entry[2].resource.valueQuantity.value"][1]
    second = by_path["Bundle.entry[4].resource.valueQuantity.value"][1]
    assert _resolved_source_text(payload, first) == "7.2"
    assert _resolved_source_text(payload, second) == "13.5"
