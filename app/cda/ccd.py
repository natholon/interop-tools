"""CCD (Continuity of Care Document) -> FHIR Bundle. The most universally
exchanged C-CDA document type, and the starting point for this app's C-CDA
support - other document types (Discharge Summary, H&P, ...) follow the
same pattern as a later addition, per CLAUDE.md."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import assemble_bundle, build_encounter_from_header, build_patient_from_header
from app.cda.parser import find_all, has_template_id

CCD_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.2"


class CcdBuilder(CdaDocumentBuilder):
    template_id = CCD_TEMPLATE_ID

    def build_bundle(self, document: Element) -> Bundle:
        # Deferred import: app.cda.registry imports THIS module (to build
        # DOCUMENT_BUILDERS) at its own module-load time, so importing it
        # back at this module's top level would be circular. By the time
        # this method actually runs, registry.py has already finished
        # loading - see the note at the top of app/cda/registry.py.
        from app.cda.registry import SECTION_BUILDERS

        patient = build_patient_from_header(document)
        encounter = build_encounter_from_header(document, patient.id)
        resources = [encounter] if encounter is not None else []

        for section in find_all(document, "component/structuredBody/component/section"):
            for section_template_id, builder in SECTION_BUILDERS.items():
                if has_template_id(section, section_template_id):
                    resources.extend(builder(section, patient.id))
                    break
            # An unrecognized section is silently skipped - this slice's
            # section coverage is deliberately partial, disclosed in
            # CLAUDE.md, not an error.

        return assemble_bundle(document, patient, *resources)
