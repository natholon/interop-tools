"""Shared sample-type/transform-target dropdown grouping - extracted from
app/routes/convert.py once app/routes/data_specification.py became a
second real consumer of the identical <optgroup>-by-format grouping, the
same "extract on second real consumer" discipline as app/routes/errors.py."""

from app.generators.registry import list_supported_types
from app.transform.registry import list_supported_targets

# Human-readable group labels, shared by both the "Sample message type"
# dropdown (grouped_supported_types) and the "Target format" dropdown
# (grouped_supported_targets, below) - keyed by the same message_type
# code both registries use. A flat ~40-entry <select> across three
# formats is hard to scan, so both dropdowns group by format/message-type
# instead. Falls back to the raw code for any future message_type this
# map hasn't been updated for yet, so a new generator/target never
# silently disappears from either dropdown.
_TYPE_GROUP_LABELS = {
    "ADT": "HL7v2 — ADT (Admit / Discharge / Transfer)",
    "SIU": "HL7v2 — SIU (Scheduling)",
    "ORU": "HL7v2 — ORU (Observation Results)",
    "MDM": "HL7v2 — MDM (Document Management)",
    "CDA": "C-CDA",
    "EDI": "X12 EDI",
}


def grouped_supported_types() -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Groups list_supported_types()'s flat (message_type, trigger, label)
    tuples by message_type, preserving each group's own insertion order -
    used to render a sample-type dropdown as <optgroup>s instead of one
    long flat list."""
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for msg_type, trigger, label in list_supported_types():
        groups.setdefault(msg_type, []).append((msg_type, trigger, label))
    return [(_TYPE_GROUP_LABELS.get(msg_type, msg_type), items) for msg_type, items in groups.items()]


def grouped_supported_targets() -> list[tuple[str, list[tuple[str, str]]]]:
    """The same <optgroup> treatment for the reverse-transform "Target format"
    dropdown - same grouping and group order as the sample dropdown.

    **Each option's visible text reuses `list_supported_types()`'s label
    verbatim** rather than rebuilding one from the transform registry's
    all-caps `target_type`. Rebuilding silently loses the generator label's
    deliberate mixed-case "DischargeSummary".

    Order comes from `list_supported_types()`, not `list_supported_targets()`,
    because the two genuinely disagree: 837P/837I/837D are registered in P/I/D
    order but sort D/I/P, and both dropdowns should show the order a user
    already knows.

    Each item is (value, label). `value` keeps the exact
    `"{target_format} {target_type}^{target_trigger}"` string that
    `transform_form` and `app.js` already parse via
    `partition(" ")`/`partition("^")`, so grouping changed only what is
    displayed.

    Generator keys are translated to transform keys before the membership
    check: HL7 keeps `(message_type, trigger)` directly, while CDA/EDI have no
    trigger concept, so `(target_format, target_type)` plays that role with
    `target_trigger` always `""`. See
    `test_grouped_supported_targets_covers_every_registered_target`, which
    guards against the two registries drifting apart."""
    target_keys = set(list_supported_targets())
    groups: dict[str, list[tuple[str, str]]] = {}
    for msg_type, trigger, generator_label in list_supported_types():
        target_format = "CDA" if msg_type == "CDA" else "EDI" if msg_type == "EDI" else "HL7"
        target_type, target_trigger = (msg_type, trigger) if target_format == "HL7" else (trigger, "")
        if (target_format, target_type, target_trigger) not in target_keys:
            continue
        value = f"{target_format} {target_type}^{target_trigger}" if target_trigger else f"{target_format} {target_type}"
        groups.setdefault(msg_type, []).append((value, generator_label))
    return [(_TYPE_GROUP_LABELS.get(msg_type, msg_type), items) for msg_type, items in groups.items()]
