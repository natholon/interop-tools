"""Top-level entry point for the reverse (FHIR Bundle -> raw text)
direction - the app/transform/ mirror of app/pipeline.py's own
format-sniffing dispatch for the forward direction. There's no format to
*sniff* here (the caller already knows what they want back out - a FHIR
Bundle alone doesn't uniquely determine a target message type the way raw
message text uniquely determines its own source format), so this is a
direct dispatch to app/transform/registry.py rather than a sniff-then-
dispatch function."""

from fhir.resources.R4B.bundle import Bundle

from app.transform.registry import get_builder


def build_message_from_bundle(bundle: Bundle, target_format: str, target_type: str, target_trigger: str = "") -> str:
    """Raises MappingError (from get_builder) when no builder is registered
    for the requested target, or whatever a concrete builder itself raises
    (MappingError when a structurally-required resource, e.g. Patient, is
    absent from the Bundle)."""
    builder = get_builder(target_format, target_type, target_trigger)
    return builder.build_message(bundle)
