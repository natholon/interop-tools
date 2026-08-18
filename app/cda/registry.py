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

from app.cda import (
    allergies,
    discharge_medications,
    hospital_discharge_diagnosis,
    immunizations,
    medications,
    narrative_sections,
    problems,
    procedures,
    results,
    vitals,
)
from app.cda.base import CdaDocumentBuilder
from app.cda.ccd import CcdBuilder
from app.cda.discharge_summary import DischargeSummaryBuilder
from app.cda.history_and_physical import HistoryAndPhysicalBuilder
from app.cda.parser import has_template_id
from app.hl7.errors import MappingError

SECTION_BUILDERS: dict[str, Callable[..., list[Resource]]] = {
    problems.SECTION_TEMPLATE_ID: problems.build_conditions,
    medications.SECTION_TEMPLATE_ID: medications.build_medication_requests,
    allergies.SECTION_TEMPLATE_ID: allergies.build_allergy_intolerances,
    allergies.SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL: allergies.build_allergy_intolerances,
    immunizations.SECTION_TEMPLATE_ID: immunizations.build_immunizations,
    vitals.SECTION_TEMPLATE_ID: vitals.build_vital_signs,
    vitals.SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL: vitals.build_vital_signs,
    results.SECTION_TEMPLATE_ID: results.build_diagnostic_reports,
    results.SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL: results.build_diagnostic_reports,
    procedures.SECTION_TEMPLATE_ID: procedures.build_procedures,
    procedures.SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL: procedures.build_procedures,
    hospital_discharge_diagnosis.SECTION_TEMPLATE_ID: hospital_discharge_diagnosis.build_hospital_discharge_diagnoses,
    discharge_medications.SECTION_TEMPLATE_ID: discharge_medications.build_discharge_medication_requests,
    # Narrative-only sections (Discharge Summary's Hospital Course/Plan of
    # Treatment, History and Physical's own nine required narrative
    # sections) - see app/cda/narrative_sections.py's own docstring for the
    # full templateId/LOINC sourcing and why they all share one builder.
    narrative_sections.HOSPITAL_COURSE_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.PLAN_OF_TREATMENT_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.REASON_FOR_VISIT_CHIEF_COMPLAINT_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.REASON_FOR_VISIT_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.CHIEF_COMPLAINT_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.PHYSICAL_EXAM_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.ASSESSMENT_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.REVIEW_OF_SYSTEMS_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.SOCIAL_HISTORY_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.FAMILY_HISTORY_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
    narrative_sections.GENERAL_STATUS_TEMPLATE_ID: narrative_sections.build_narrative_document_reference,
}

_DOCUMENT_BUILDERS: dict[str, CdaDocumentBuilder] = {
    CcdBuilder.template_id: CcdBuilder(),
    DischargeSummaryBuilder.template_id: DischargeSummaryBuilder(),
    HistoryAndPhysicalBuilder.template_id: HistoryAndPhysicalBuilder(),
}


def get_document_builder(document: Element) -> CdaDocumentBuilder:
    for template_id, builder in _DOCUMENT_BUILDERS.items():
        if has_template_id(document, template_id):
            return builder
    raise MappingError("No builder registered for this document's templateId(s)")
