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
    """The identical <optgroup> treatment for the reverse-transform
    "Target format" dropdown - same grouping, same group order, and each
    option's own visible text reuses `list_supported_types()`'s own label
    *verbatim* (e.g. `"CDA^DischargeSummary - Discharge Summary"`, the
    exact text a user already sees for the corresponding entry in the
    "Sample message type" dropdown) rather than reconstructing an
    equivalent string from the transform registry's own all-caps
    `target_type` - a real, caught-before-shipping bug in an earlier
    version of this function did exactly that and silently lost the
    generator label's own deliberate mixed-case "DischargeSummary" (see
    that registry's own comment for why the casing is deliberate), a
    literal `list_supported_types()` reuse was what CLAUDE.md is describing.
    Each item is (value, label) - `value` is the exact `"{target_format}
    {target_type}^{target_trigger}"` string (or without the trigger suffix
    when there's none) `transform_form`'s own POST handler and `app.js`'s
    own submit handler already parse via `partition(" ")`/`partition("^")`
    - unchanged from before this dropdown was grouped, so this reshaping
    only changes what's *displayed*, not how a submitted value gets parsed
    back apart.

    Driven by `list_supported_types()`'s own order (not `list_supported_
    targets()`'s own alphabetical one) specifically because they can
    genuinely disagree: EDI's 837P/837I/837D targets are registered in
    that P/I/D order in `app/generators/registry.py` but would sort D/I/P
    alphabetically - reusing the generator's own order keeps both
    dropdowns' EDI group in the same order a user already knows from the
    sample dropdown, not a coincidentally-similar one.

    Each generator `(message_type, trigger)` key is translated to its
    corresponding transform `(target_format, target_type, target_trigger)`
    one before the membership check: HL7 targets keep `(target_type,
    target_trigger) = (message_type, trigger)` directly; CDA/EDI targets
    have no real trigger-event concept (`target_trigger` is always `""`
    for them), so their own `(target_format, target_type)` plays that role
    instead (e.g. `("CDA", "CCD", "")` for generator key `("CDA", "CCD")`,
    `("EDI", "270", "")` for `("EDI", "270")`) - confirmed by direct
    inspection that every entry in both registries lines up this way, not
    assumed - see `app/transform/registry.py`'s own `_BUILDERS` for the
    identical key shape. See
    `test_grouped_supported_targets_covers_every_registered_target` for
    the regression test guarding against the two registries' own entries
    ever silently drifting apart."""
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
