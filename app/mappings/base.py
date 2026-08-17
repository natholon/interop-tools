from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import hl7
from fhir.resources.R4B.bundle import Bundle

if TYPE_CHECKING:
    from app.provenance.recorder import ProvenanceRecorder


class MessageMapper(ABC):
    """Interface implemented by each HL7 message-type -> FHIR Bundle mapper."""

    message_type: str
    trigger_event: str

    @abstractmethod
    def to_bundle(self, message: hl7.Message, recorder: "ProvenanceRecorder | None" = None) -> Bundle:
        """`recorder`, when given, accumulates field-level provenance facts
        for the Data Specification pillar (see app/provenance/) - always
        optional and defaulting to None, so every existing caller (normal
        conversion, validation's convertibility check, generation, transform)
        is completely unaffected unless it explicitly opts in. Only ADT's
        own mappers actually record anything as of this phase - every other
        message type accepts and ignores the parameter until its own
        provenance slice is implemented, so app/provenance/dispatch.py can
        call `mapper.to_bundle(message, recorder=recorder)` uniformly
        without a TypeError for a not-yet-instrumented type."""
        ...
