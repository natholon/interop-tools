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

**A real, disclosed scope limit, now closed - mirroring Discharge
Summary's own precedent exactly**: an H&P Note's own nine IG-required
narrative sections (Reason for Visit/Chief Complaint, History of Present
Illness, Physical Exam, Assessment, Plan of Care, Review of Systems, Social
History, Family History, General Status) were originally silently skipped
entirely - they either carry no structured entries at all, or (Social
History/Family History/Plan of Care) can carry real structured entries in
practice that still aren't parsed this slice. All nine now convert to a
DocumentReference + Binary (extracted narrative text) rather than a full
FHIR-Document Bundle(type="document")+Composition, which stays a disclosed,
deliberate out-of-scope item - see app/cda/narrative_sections.py for the
full design reasoning, the real templateId/LOINC sourcing (four of these
nine sections use legacy IHE PCC/HITSP OIDs, not the native C-CDA
namespace - a real gotcha found during that research), and what's still
explicitly deferred (structured entries for Social History/Family
History/Plan of Care, provenance instrumentation, bidirectional transform).
Verified directly against the real fetched example (not assumed): alongside
these H&P-specific sections, it also carried Allergies, Immunizations,
Medications, Problems, Procedures, Results, and Vital Signs sections - every
one of this app's seven general-purpose section types in a single document
- so a real H&P Note now converts to something genuinely comprehensive
(header + all seven general-purpose sections + all nine H&P-specific
narrative sections)."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import build_sectioned_bundle

HISTORY_AND_PHYSICAL_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.3"


class HistoryAndPhysicalBuilder(CdaDocumentBuilder):
    template_id = HISTORY_AND_PHYSICAL_TEMPLATE_ID

    def build_bundle(self, document: Element, recorder=None) -> Bundle:
        return build_sectioned_bundle(document, recorder=recorder)
