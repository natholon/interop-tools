"""Direct unit tests for app/routes/dropdowns.py::grouped_supported_targets()
- the "Target format" dropdown's own <optgroup> grouping, matched to the
"Sample message type" dropdown's own grouped_supported_types() shape and
label style directly, not just eyeballed."""

from app.generators.registry import list_supported_types
from app.routes.dropdowns import grouped_supported_targets, grouped_supported_types
from app.transform.registry import list_supported_targets


def test_grouped_supported_targets_has_same_group_labels_and_order_as_sample_types():
    target_group_labels = [group_label for group_label, _ in grouped_supported_targets()]
    sample_group_labels = [group_label for group_label, _ in grouped_supported_types()]
    assert target_group_labels == sample_group_labels


def test_grouped_supported_targets_covers_every_registered_target():
    # Guards against the two registries' own entries ever silently
    # drifting apart - if a future transform target ships with no
    # matching generator entry (or vice versa), this catches it rather
    # than letting it silently vanish from the dropdown.
    all_items = [item for _, items in grouped_supported_targets() for item in items]
    assert len(all_items) == len(list_supported_targets())


def test_grouped_supported_targets_value_parses_back_to_a_real_registered_target():
    target_keys = set(list_supported_targets())
    for _, items in grouped_supported_targets():
        for value, _label in items:
            target_format, _, rest = value.partition(" ")
            target_type, _, target_trigger = rest.partition("^")
            assert (target_format, target_type, target_trigger) in target_keys


def test_grouped_supported_targets_reuses_generator_label_verbatim():
    # The exact text a user already sees for "ADT^A01 - Admit" in the
    # Sample message type dropdown must appear unchanged here too.
    generator_labels = {(msg_type, trigger): label for msg_type, trigger, label in list_supported_types()}
    all_items = {value: label for _, items in grouped_supported_targets() for value, label in items}
    assert all_items["HL7 ADT^A01"] == generator_labels[("ADT", "A01")]
    assert all_items["EDI 270"] == generator_labels[("EDI", "270")]
    assert all_items["CDA CCD"] == generator_labels[("CDA", "CCD")]


def test_grouped_supported_targets_preserves_mixed_case_cda_labels():
    # A real bug caught before shipping: an earlier version reconstructed
    # the option label from the transform registry's own all-caps
    # target_type ("DISCHARGESUMMARY") instead of reusing the generator
    # registry's own deliberately mixed-case label ("DischargeSummary" -
    # see app/generators/registry.py's own comment for why the casing is
    # intentional, not an inconsistency).
    all_items = {value: label for _, items in grouped_supported_targets() for value, label in items}
    assert all_items["CDA DISCHARGESUMMARY"] == "CDA^DischargeSummary - Discharge Summary"
    assert all_items["CDA HISTORYANDPHYSICAL"] == "CDA^HistoryAndPhysical - History and Physical Note"


def test_grouped_supported_targets_edi_837_group_matches_sample_dropdown_order():
    # A real, disclosed ordering discrepancy: 837P/837I/837D are
    # registered P/I/D in the generator registry but would sort D/I/P
    # alphabetically via list_supported_targets() alone.
    edi_group = next(items for group_label, items in grouped_supported_targets() if group_label == "X12 EDI")
    values = [value for value, _label in edi_group]
    assert values.index("EDI 837P") < values.index("EDI 837I") < values.index("EDI 837D")
