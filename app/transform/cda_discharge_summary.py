"""FHIR Bundle -> C-CDA Discharge Summary XML - the eighth reverse-
direction slice, and the first proof this app's reverse direction
generalizes across C-CDA *document types*, not just sections within one
document type. Mirrors the forward `DischargeSummaryBuilder`'s own scope
exactly: reuses `app.transform.cda_ccd.build_sectioned_document` (the
identical header + all-seven-general-purpose-sections shape CCD's own
reverse builder already established), rather than re-deriving header/
section logic independently - confirmed structurally identical the same
way the forward direction's own `DischargeSummaryBuilder` was (both
document types share CCD's own `recordTarget`/`componentOf` header shape,
verified against a real HL7 C-CDA-Examples Discharge Summary).

**Disclosed scope limit, matching the forward direction's own first
Discharge Summary slice exactly**: the two discharge-specific sections
(Hospital Discharge Diagnosis, Discharge Medications) are not
reconstructed here - on the FHIR side, a `Condition`/`MedicationRequest`
built from either of those sections is structurally close to but not
perfectly indistinguishable from one built from a plain Problems/
Medications section (Hospital Discharge Diagnosis's own `Condition.
category = "encounter-diagnosis"` is a real, disclosed marker; Discharge
Medications has no equivalent marker at all, since it reuses
`build_medication_request` with zero modification) - reliably telling
them apart to route them into the *correct* discharge-specific section on
the way back out, rather than folding them into the plain Problems/
Medications sections this slice already reverses, is real, additional
scope this slice deliberately doesn't attempt, the same "one thing per
slice" precedent this project's own forward direction already followed
(Discharge Summary shipped with only its already-recognized general
sections before Hospital Discharge Diagnosis/Discharge Medications were
added as their own later slice)."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.discharge_summary import DISCHARGE_SUMMARY_TEMPLATE_ID
from app.transform.base import MessageBuilder
from app.transform.cda_ccd import build_sectioned_document


class DischargeSummaryReverseBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        return build_sectioned_document(
            bundle, DISCHARGE_SUMMARY_TEMPLATE_ID, "18842-5", "Discharge Summary", "Discharge Summary"
        )
