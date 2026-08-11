from app.hl7.errors import MappingError
from app.mappings.adt_a01 import AdtA01Mapper
from app.mappings.base import MessageMapper

_MAPPERS: dict[tuple[str, str], MessageMapper] = {
    ("ADT", "A01"): AdtA01Mapper(),
}


def get_mapper(message_type: str, trigger_event: str) -> MessageMapper:
    mapper = _MAPPERS.get((message_type.strip().upper(), trigger_event.strip().upper()))
    if mapper is None:
        raise MappingError(f"No mapper registered for message type {message_type}^{trigger_event}")
    return mapper
