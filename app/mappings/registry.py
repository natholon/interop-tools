from app.hl7.errors import MappingError
from app.mappings.adt import AdtA01Mapper, AdtA02Mapper, AdtA03Mapper, AdtA04Mapper, AdtA08Mapper
from app.mappings.base import MessageMapper

_MAPPERS: dict[tuple[str, str], MessageMapper] = {
    ("ADT", "A01"): AdtA01Mapper(),
    ("ADT", "A02"): AdtA02Mapper(),
    ("ADT", "A03"): AdtA03Mapper(),
    ("ADT", "A04"): AdtA04Mapper(),
    ("ADT", "A08"): AdtA08Mapper(),
}


def get_mapper(message_type: str, trigger_event: str) -> MessageMapper:
    mapper = _MAPPERS.get((message_type.strip().upper(), trigger_event.strip().upper()))
    if mapper is None:
        raise MappingError(f"No mapper registered for message type {message_type}^{trigger_event}")
    return mapper
