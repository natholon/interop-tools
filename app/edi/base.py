from abc import ABC, abstractmethod

from fhir.resources.R4B.bundle import Bundle

from app.edi.parser import Delimiters, TransactionSet


class EdiTransactionBuilder(ABC):
    """Interface implemented by each X12 transaction-set -> FHIR Bundle
    builder - the app/cda/base.py::CdaDocumentBuilder / app/mappings/
    base.py::MessageMapper equivalent. Transaction sets (270 vs. 271 vs.
    276/277, and a future 278/835/837) are a "different shape dispatched
    via registry" situation, same as HL7v2 message types and C-CDA
    document types - hence an ABC + registry here rather than plain
    functions.

    `delimiters` is passed alongside `transaction_set` (not bundled into
    it) because it's an interchange-level concern, not a transaction-set-
    level one - the same delimiters apply to every transaction set in the
    interchange. Added once claim_status.py became the first builder that
    needs to split a composite element (STC01's category/status/entity
    sub-parts) via app.edi.parser::component(), which requires the
    interchange's own self-declared component separator - 270/271 never
    needed it, since every field either builder reads is a simple element.
    Neither existing builder uses the parameter; both accept it purely to
    satisfy this shared interface."""

    transaction_set_id: str

    @abstractmethod
    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters) -> Bundle:
        ...
