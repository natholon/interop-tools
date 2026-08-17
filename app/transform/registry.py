"""Dispatch table for the reverse (FHIR Bundle -> raw text) direction - the
app/transform/ equivalent of app/mappings/registry.py/app/cda/registry.py/
app/edi/registry.py, keyed the same "different shape dispatched via
registry" way. Flat dict keyed by (target_format, target_type,
target_trigger) - trigger is "" for target shapes with no real trigger-event
concept (mirroring app/generators/registry.py's own ("CDA", "CCD") precedent
for the forward direction), never a second registry axis."""

from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.cda_ccd import CcdReverseBuilder
from app.transform.hl7_adt_a01 import AdtA01Builder

_BUILDERS: dict[tuple[str, str, str], MessageBuilder] = {
    ("HL7", "ADT", "A01"): AdtA01Builder(),
    ("CDA", "CCD", ""): CcdReverseBuilder(),
}


def get_builder(target_format: str, target_type: str, target_trigger: str = "") -> MessageBuilder:
    key = (target_format.strip().upper(), target_type.strip().upper(), (target_trigger or "").strip().upper())
    builder = _BUILDERS.get(key)
    if builder is None:
        raise MappingError(f"No reverse-transform target registered for {key}")
    return builder


def list_supported_targets() -> list[tuple[str, str, str]]:
    """(target_format, target_type, target_trigger) tuples, sorted for a
    stable UI dropdown order - mirrors
    app/generators/registry.py::list_supported_types()'s own role for the
    forward direction."""
    return sorted(_BUILDERS.keys())
