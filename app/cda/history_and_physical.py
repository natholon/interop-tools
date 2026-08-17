"""History and Physical Note (templateId 2.16.840.1.113883.10.20.22.1.3) ->
FHIR Bundle. The third document type, and a second real proof (after
Discharge Summary) that build_sectioned_bundle generalizes - build_bundle()
is, like DischargeSummaryBuilder's, just build_sectioned_bundle(document).
Confirmed structurally identical to CCD/Discharge Summary at the header
level (recordTarget, componentOf/encompassingEncounter) by fetching a real
HL7 C-CDA-Examples History and Physical document and comparing directly,
not assumed from the document-type name alone - this was also the exact
same fetch that surfaced the Procedures section's own "entries optional"
templateId gap (see app/cda/procedures.py).

**A real, disclosed scope limit, not a redesign - mirroring Discharge
Summary's own precedent exactly**: an H&P Note's own IG-required narrative
sections (Reason for Visit/Chief Complaint, History of Present Illness,
Physical Exam, Assessment, Plan of Care, Review of Systems, Social History,
Family History, General Status) either carry no structured entries at all
(would need a proper FHIR Composition to represent narrative-only content,
already an out-of-scope item) or use entry shapes this app's existing
section builders don't recognize - none are mapped this slice, and are
silently skipped, the same "unrecognized section" treatment
build_sectioned_bundle already gives any section without a registered
builder. Verified directly against the real fetched example (not assumed):
alongside these H&P-specific sections, it also carried Allergies,
Immunizations, Medications, Problems, Procedures, Results, and Vital Signs
sections - every one of this app's seven currently-recognized section types
in a single document - so a real H&P Note converts to something genuinely
useful (header + all seven recognized sections) even without the
H&P-specific narrative ones."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import build_sectioned_bundle

HISTORY_AND_PHYSICAL_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.3"


class HistoryAndPhysicalBuilder(CdaDocumentBuilder):
    template_id = HISTORY_AND_PHYSICAL_TEMPLATE_ID

    def build_bundle(self, document: Element, recorder=None) -> Bundle:
        return build_sectioned_bundle(document, recorder=recorder)
