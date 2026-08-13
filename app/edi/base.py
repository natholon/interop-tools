from abc import ABC, abstractmethod

from fhir.resources.R4B.bundle import Bundle

from app.edi.parser import TransactionSet


class EdiTransactionBuilder(ABC):
    """Interface implemented by each X12 transaction-set -> FHIR Bundle
    builder - the app/cda/base.py::CdaDocumentBuilder / app/mappings/
    base.py::MessageMapper equivalent. Transaction sets (270 vs. 271, and a
    future 276/277/835/837) are a "different shape dispatched via registry"
    situation, same as HL7v2 message types and C-CDA document types -
    hence an ABC + registry here rather than plain functions."""

    transaction_set_id: str

    @abstractmethod
    def build_bundle(self, transaction_set: TransactionSet) -> Bundle:
        ...
