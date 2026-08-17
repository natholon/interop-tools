"""FHIR Bundle -> C-CDA History and Physical Note XML - the tenth reverse-
direction slice, and a second real proof (after Discharge Summary) that
`app.transform.cda_ccd.build_sectioned_document` generalizes across C-CDA
document types - `build_message()` is, like `DischargeSummaryReverseBuilder`'s
own, just a call to that shared function with this document type's own
templateId/LOINC code. Confirmed structurally identical to CCD/Discharge
Summary at the header level (`recordTarget`, `componentOf/
encompassingEncounter`) the same way the forward `HistoryAndPhysicalBuilder`
itself was, by fetching a real HL7 C-CDA-Examples History and Physical
document.

**Disclosed scope limit, mirroring Discharge Summary's own reverse slice
and the forward `HistoryAndPhysicalBuilder`'s own precedent exactly**: an
H&P Note's own IG-required narrative sections (Reason for Visit, History
of Present Illness, Physical Exam, Assessment, Plan of Care, Review of
Systems, Social History, Family History, General Status) are never
regenerated - none of them round-trip through any resource type this
app's forward direction actually builds from an H&P document (they're
either narrative-only, needing a proper FHIR Composition this app doesn't
build, or use entry shapes no section builder here recognizes), so
there's no FHIR-side data to reverse them from in the first place. A
reversed H&P document still carries all seven general-purpose sections
whenever the source Bundle has the resources for them - the identical
useful-even-without-the-H&P-specific-sections shape the forward direction
itself already established. Unlike Discharge Summary's own forced
`componentOf` (`force_encounter=True` on the forward generator, since a
discharge is inherently tied to one hospitalization), an H&P's own
`componentOf`/`Encounter` is genuinely optional here too - a real H&P can
precede any admission (e.g. a pre-operative visit) - so this builder
needs no H&P-specific handling beyond `build_sectioned_document`'s own
already-optional `Encounter` treatment."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.history_and_physical import HISTORY_AND_PHYSICAL_TEMPLATE_ID
from app.transform.base import MessageBuilder
from app.transform.cda_ccd import build_sectioned_document


class HistoryAndPhysicalReverseBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        return build_sectioned_document(
            bundle, HISTORY_AND_PHYSICAL_TEMPLATE_ID, "34117-2", "History and physical note", "History and Physical Note"
        )
