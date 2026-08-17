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

**One of the two discharge-specific sections is now genuinely reversed,
the other is a disclosed, permanent limitation, not a deferred slice -
resolved as a follow-up once each was actually researched, the same
"partial gap turned out to be fixable, the rest genuinely isn't" shape
Discharge Summary's own forward-direction Hospital Discharge Diagnosis/
Discharge Medications slice already went through**: `include_discharge_specific_sections=True`
tells `build_sectioned_document` to split out any `Condition` carrying
`category == "encounter-diagnosis"` (the real, reliable marker
`app.cda.hospital_discharge_diagnosis`'s own forward module sets - a plain
Problems section never populates `.category` at all) into its own Hospital
Discharge Diagnosis section rather than folding it into Problems. Discharge
Medications, by contrast, genuinely cannot be split the same way: the
forward `app.cda.discharge_medications` module reuses
`build_medication_request` with zero modification, so a `MedicationRequest`
sourced from that section is byte-for-byte structurally identical, on the
FHIR side, to one sourced from a plain Medications section (confirmed by
reading that module's own docstring, not assumed) - there is no marker
this builder could reverse even in principle, so every `MedicationRequest`
continues to route into the plain Medications section. This is the most
correct behavior achievable, not a placeholder awaiting a future fix."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.discharge_summary import DISCHARGE_SUMMARY_TEMPLATE_ID
from app.transform.base import MessageBuilder
from app.transform.cda_ccd import build_sectioned_document


class DischargeSummaryReverseBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        return build_sectioned_document(
            bundle,
            DISCHARGE_SUMMARY_TEMPLATE_ID,
            "18842-5",
            "Discharge Summary",
            "Discharge Summary",
            include_discharge_specific_sections=True,
        )
