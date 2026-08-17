"""Discharge Summary (templateId 2.16.840.1.113883.10.20.22.1.8) -> FHIR
Bundle. Structurally the same header shape as CCD (recordTarget,
componentOf/encompassingEncounter - here almost always present in real
documents, since a discharge summary is inherently tied to one
hospitalization) and reuses the exact same "header + generic sections"
shape via app.cda.common.build_sectioned_bundle, per CLAUDE.md's own
anticipated design ("a future Discharge Summary/H&P builder reuses this
exact dispatch table") - confirmed structurally identical by fetching a
real HL7 C-CDA-Examples Discharge Summary and comparing its header/
componentOf shape to CCD's, not assumed from the name alone.

**Disclosed scope limit**: Discharge Summary's own IG-required sections
(Hospital Discharge Diagnosis, Hospital Course, Plan of Treatment, Discharge
Medications) each use entry templates genuinely different from the ones
this app's existing section builders recognize:
- Hospital Discharge Diagnosis Section (templateId ...2.24) wraps its
  Problem Observation in a Hospital Discharge Diagnosis Act (templateId
  ...4.33), not Problems' Concern Act (...4.3) - app.cda.problems.
  build_conditions() only recognizes the latter.
- Discharge Medications Section (templateId ...2.11.1) wraps its
  medication entry in a Discharge Medication Act (templateId ...4.35), not
  a bare substanceAdministration - app.cda.medications.
  build_medication_requests() only recognizes the latter.
- Hospital Course is narrative-only (no structured entries to map without
  a proper FHIR Composition, already an out-of-scope item per CLAUDE.md).
- Plan of Treatment's entry shape is too heterogeneous (procedures,
  encounters, supply, observations, ...) for one resource type.

So this slice converts a Discharge Summary's header plus whatever
ALREADY-recognized sections it happens to also carry - verified against a
real Discharge Summary example, which (alongside its discharge-specific
sections) also included a plain Problem List section (Problems'
Concern-Act shape) and a plain Immunizations section, both of which DO
convert correctly through the existing dispatch. The discharge-specific
sections above are silently skipped, the same "unrecognized section"
treatment build_sectioned_bundle already gives any section without a
registered builder - not a special case for this document type."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import build_sectioned_bundle

DISCHARGE_SUMMARY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.8"


class DischargeSummaryBuilder(CdaDocumentBuilder):
    template_id = DISCHARGE_SUMMARY_TEMPLATE_ID

    def build_bundle(self, document: Element, recorder=None) -> Bundle:
        return build_sectioned_bundle(document, recorder=recorder)
