"""Type-specific validator lookup - deliberately keyed by message type only,
not (message_type, trigger_event) like app/mappings/registry.py: rule
*sets* are type-scoped, and any trigger-specific nuance (e.g. ADT's A02
prior-location rule) lives inside the relevant rule body via a
trigger_event check, not via a second dispatch dimension."""

from collections.abc import Callable

from app.validation import adt, mdm, oru, siu
from app.validation.models import ValidationFinding

_TYPE_VALIDATORS: dict[str, Callable[..., list[ValidationFinding]]] = {
    "ADT": adt.validate,
    "SIU": siu.validate,
    "ORU": oru.validate,
    "MDM": mdm.validate,
}


def get_type_validator(message_type: str) -> Callable[..., list[ValidationFinding]] | None:
    return _TYPE_VALIDATORS.get(message_type.strip().upper())
