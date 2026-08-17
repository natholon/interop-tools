"""CCD (Continuity of Care Document) -> FHIR Bundle. The most universally
exchanged C-CDA document type, and the starting point for this app's C-CDA
support. Its build_bundle() is just app.cda.common.build_sectioned_bundle -
CCD is the "header + generic sections" shape that helper was extracted for
(see that function's docstring); a document type with genuinely different
structure would implement build_bundle() directly instead."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import build_sectioned_bundle

CCD_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.2"


class CcdBuilder(CdaDocumentBuilder):
    template_id = CCD_TEMPLATE_ID

    def build_bundle(self, document: Element, recorder=None) -> Bundle:
        return build_sectioned_bundle(document, recorder=recorder)
