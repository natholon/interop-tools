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
from app.transform.edi_270 import Edi270Builder
from app.transform.edi_271 import Edi271Builder
from app.transform.hl7_adt import (
    AdtA01Builder,
    AdtA02Builder,
    AdtA03Builder,
    AdtA04Builder,
    AdtA05Builder,
    AdtA08Builder,
)
from app.transform.hl7_mdm import MdmT02Builder
from app.transform.hl7_oru import OruR01Builder, OruR30Builder, OruR31Builder, OruR32Builder, OruR40Builder
from app.transform.hl7_siu import SiuS12Builder

_BUILDERS: dict[tuple[str, str, str], MessageBuilder] = {
    ("HL7", "ADT", "A01"): AdtA01Builder(),
    ("HL7", "ADT", "A02"): AdtA02Builder(),
    ("HL7", "ADT", "A03"): AdtA03Builder(),
    ("HL7", "ADT", "A04"): AdtA04Builder(),
    ("HL7", "ADT", "A05"): AdtA05Builder(),
    ("HL7", "ADT", "A08"): AdtA08Builder(),
    ("HL7", "SIU", "S12"): SiuS12Builder(),
    ("HL7", "ORU", "R01"): OruR01Builder(),
    ("HL7", "ORU", "R30"): OruR30Builder(),
    ("HL7", "ORU", "R31"): OruR31Builder(),
    ("HL7", "ORU", "R32"): OruR32Builder(),
    ("HL7", "ORU", "R40"): OruR40Builder(),
    ("HL7", "MDM", "T02"): MdmT02Builder(),
    ("CDA", "CCD", ""): CcdReverseBuilder(),
    ("EDI", "270", ""): Edi270Builder(),
    ("EDI", "271", ""): Edi271Builder(),
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
