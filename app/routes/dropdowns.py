"""Shared sample-type dropdown grouping - extracted from app/routes/convert.py
once app/routes/data_specification.py became a second real consumer of the
identical <optgroup>-by-format grouping, the same "extract on second real
consumer" discipline as app/routes/errors.py."""

from app.generators.registry import list_supported_types

# Human-readable group labels for the sample-type dropdown's <optgroup>s,
# keyed by the same message_type code list_supported_types() already
# returns - a flat ~40-entry <select> across three formats is hard to scan,
# so the dropdown groups by format/message-type instead. Falls back to the
# raw code for any future message_type this map hasn't been updated for
# yet, so a new generator never silently disappears from the dropdown.
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
