from app.hl7.errors import MappingError
from app.mappings.adt import (
    AdtA01Mapper,
    AdtA02Mapper,
    AdtA03Mapper,
    AdtA04Mapper,
    AdtA05Mapper,
    AdtA08Mapper,
    AdtA11Mapper,
    AdtA13Mapper,
    AdtA38Mapper,
)
from app.mappings.base import MessageMapper
from app.mappings.mdm import (
    MdmT02Mapper,
    MdmT04Mapper,
    MdmT06Mapper,
    MdmT08Mapper,
    MdmT10Mapper,
    MdmT11Mapper,
)
from app.mappings.oru import OruR01Mapper, OruR30Mapper, OruR31Mapper, OruR32Mapper, OruR40Mapper
from app.mappings.siu import SiuS12Mapper, SiuS13Mapper, SiuS14Mapper, SiuS15Mapper, SiuS17Mapper, SiuS26Mapper

_MAPPERS: dict[tuple[str, str], MessageMapper] = {
    ("ADT", "A01"): AdtA01Mapper(),
    ("ADT", "A02"): AdtA02Mapper(),
    ("ADT", "A03"): AdtA03Mapper(),
    ("ADT", "A04"): AdtA04Mapper(),
    ("ADT", "A05"): AdtA05Mapper(),
    ("ADT", "A08"): AdtA08Mapper(),
    ("ADT", "A11"): AdtA11Mapper(),
    ("ADT", "A13"): AdtA13Mapper(),
    ("ADT", "A38"): AdtA38Mapper(),
    ("SIU", "S12"): SiuS12Mapper(),
    ("SIU", "S13"): SiuS13Mapper(),
    ("SIU", "S14"): SiuS14Mapper(),
    ("SIU", "S15"): SiuS15Mapper(),
    ("SIU", "S17"): SiuS17Mapper(),
    ("SIU", "S26"): SiuS26Mapper(),
    ("ORU", "R01"): OruR01Mapper(),
    ("ORU", "R30"): OruR30Mapper(),
    ("ORU", "R31"): OruR31Mapper(),
    ("ORU", "R32"): OruR32Mapper(),
    ("ORU", "R40"): OruR40Mapper(),
    ("MDM", "T02"): MdmT02Mapper(),
    ("MDM", "T04"): MdmT04Mapper(),
    ("MDM", "T06"): MdmT06Mapper(),
    ("MDM", "T08"): MdmT08Mapper(),
    ("MDM", "T10"): MdmT10Mapper(),
    ("MDM", "T11"): MdmT11Mapper(),
}


def get_mapper(message_type: str, trigger_event: str) -> MessageMapper:
    mapper = _MAPPERS.get((message_type.strip().upper(), trigger_event.strip().upper()))
    if mapper is None:
        raise MappingError(f"No mapper registered for message type {message_type}^{trigger_event}")
    return mapper
