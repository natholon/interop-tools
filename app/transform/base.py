from abc import ABC, abstractmethod

from fhir.resources.R4B.bundle import Bundle


class MessageBuilder(ABC):
    """Interface for FHIR Bundle -> raw message text builders - the reverse
    direction's app.mappings.base.MessageMapper/app.cda.base.
    CdaDocumentBuilder/app.edi.base.EdiTransactionBuilder equivalent. Each
    (source format, message/document type, trigger) this app can build
    *out* gets its own concrete builder, dispatched via
    app/transform/registry.py the same "different shape dispatched via
    registry" way every forward-direction pillar already is."""

    @abstractmethod
    def build_message(self, bundle: Bundle) -> str:
        ...
