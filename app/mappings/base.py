from abc import ABC, abstractmethod

import hl7
from fhir.resources.R4B.bundle import Bundle


class MessageMapper(ABC):
    """Interface implemented by each HL7 message-type -> FHIR Bundle mapper."""

    message_type: str
    trigger_event: str

    @abstractmethod
    def to_bundle(self, message: hl7.Message) -> Bundle:
        ...
