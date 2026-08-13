"""Document-type and section dispatch tables - the app/mappings/registry.py
equivalent for C-CDA. DOCUMENT_BUILDERS is keyed like MessageMapper's
registry (different document types are genuinely different shapes).
SECTION_BUILDERS is keyed like the generator registry (every section
builder shares one signature, no polymorphism needed) - and lives here
rather than inline in ccd.py because section templateIds (Problems,
Medications, Allergies, ...) are standardized across C-CDA document types
generally, not CCD-specific: CcdBuilder and DischargeSummaryBuilder both
reuse this exact dispatch table via app.cda.common.build_sectioned_bundle.

Note on import direction: this module imports each concrete document
builder (ccd.CcdBuilder, discharge_summary.DischargeSummaryBuilder) at
module load time, same as app/mappings/registry.py imports AdtA01Mapper
etc. - but unlike the HL7v2 mappers, build_sectioned_bundle() genuinely
needs to look up SECTION_BUILDERS (to dispatch each section it walks),
which would be a circular import if this module were imported back at
common.py's own top level. common.py instead imports SECTION_BUILDERS
lazily, inside the function that needs it - by the time that function
actually runs, this module has finished loading."""

from collections.abc import Callable
from xml.etree.ElementTree import Element

from fhir.resources.R4B.resource import Resource

from app.cda import allergies, immunizations, medications, problems
from app.cda.base import CdaDocumentBuilder
from app.cda.ccd import CcdBuilder
from app.cda.discharge_summary import DischargeSummaryBuilder
from app.cda.parser import has_template_id
from app.hl7.errors import MappingError

SECTION_BUILDERS: dict[str, Callable[[Element, str], list[Resource]]] = {
    problems.SECTION_TEMPLATE_ID: problems.build_conditions,
    medications.SECTION_TEMPLATE_ID: medications.build_medication_requests,
    allergies.SECTION_TEMPLATE_ID: allergies.build_allergy_intolerances,
    allergies.SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL: allergies.build_allergy_intolerances,
    immunizations.SECTION_TEMPLATE_ID: immunizations.build_immunizations,
}

_DOCUMENT_BUILDERS: dict[str, CdaDocumentBuilder] = {
    CcdBuilder.template_id: CcdBuilder(),
    DischargeSummaryBuilder.template_id: DischargeSummaryBuilder(),
}


def get_document_builder(document: Element) -> CdaDocumentBuilder:
    for template_id, builder in _DOCUMENT_BUILDERS.items():
        if has_template_id(document, template_id):
            return builder
    raise MappingError("No builder registered for this document's templateId(s)")
