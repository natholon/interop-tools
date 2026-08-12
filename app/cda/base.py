from abc import ABC, abstractmethod

from fhir.resources.R4B.bundle import Bundle
from xml.etree.ElementTree import Element


class CdaDocumentBuilder(ABC):
    """Interface implemented by each C-CDA document-type -> FHIR Bundle
    builder - the app/mappings/base.py::MessageMapper equivalent. Document
    types (CCD vs. a future Discharge Summary vs. History & Physical) are a
    "different shape dispatched via registry" situation, same as HL7v2
    message types, not the generator's "same shape, different field rules"
    situation - hence an ABC + registry here rather than plain functions."""

    template_id: str

    @abstractmethod
    def build_bundle(self, document: Element) -> Bundle:
        ...
