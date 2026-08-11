import random

from app.generators.adt import (
    generate_adt_a01,
    generate_adt_a02,
    generate_adt_a03,
    generate_adt_a04,
    generate_adt_a08,
)
from app.generators.siu import generate_siu_s12, generate_siu_s13, generate_siu_s14, generate_siu_s15
from app.hl7.errors import MappingError

_GENERATORS = {
    ("ADT", "A01"): (generate_adt_a01, "ADT^A01 - Admit"),
    ("ADT", "A02"): (generate_adt_a02, "ADT^A02 - Transfer"),
    ("ADT", "A03"): (generate_adt_a03, "ADT^A03 - Discharge"),
    ("ADT", "A04"): (generate_adt_a04, "ADT^A04 - Register"),
    ("ADT", "A08"): (generate_adt_a08, "ADT^A08 - Update"),
    ("SIU", "S12"): (generate_siu_s12, "SIU^S12 - New Appointment"),
    ("SIU", "S13"): (generate_siu_s13, "SIU^S13 - Reschedule"),
    ("SIU", "S14"): (generate_siu_s14, "SIU^S14 - Modify"),
    ("SIU", "S15"): (generate_siu_s15, "SIU^S15 - Cancel"),
}


def list_supported_types() -> list[tuple[str, str, str]]:
    """Return (message_type, trigger_event, human_label) for every supported
    generator, in a stable order suitable for a UI dropdown."""
    return [(msg_type, trigger, label) for (msg_type, trigger), (_, label) in _GENERATORS.items()]


def generate(message_type: str, trigger_event: str, seed: int | None = None) -> str:
    key = (message_type.strip().upper(), trigger_event.strip().upper())
    entry = _GENERATORS.get(key)
    if entry is None:
        raise MappingError(f"No generator registered for message type {message_type}^{trigger_event}")
    generator_fn, _ = entry
    rng = random.Random(seed)
    return generator_fn(rng)
