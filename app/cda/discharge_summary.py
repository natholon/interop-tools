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

**Disclosed scope limit, now narrower than it once was**: Discharge
Summary's own IG-required sections (Hospital Discharge Diagnosis, Hospital
Course, Plan of Treatment, Discharge Medications) each originally used entry
templates genuinely different from the ones this app's existing section
builders recognized:
- Hospital Discharge Diagnosis Section (templateId ...2.24) wraps its
  Problem Observation in a Hospital Discharge Diagnosis Act (templateId
  ...4.33), not Problems' Concern Act (...4.3) - app.cda.problems.
  build_conditions() only recognizes the latter. Now mapped - see
  app/cda/hospital_discharge_diagnosis.py.
- Discharge Medications Section (templateId ...2.11.1) wraps its
  medication entry in a Discharge Medication Act (templateId ...4.35), not
  a bare substanceAdministration - app.cda.medications.
  build_medication_requests() only recognizes the latter. Now mapped - see
  app/cda/discharge_medications.py.
- Hospital Course and Plan of Treatment are both narrative-only in
  practice (verified against the real HL7 C-CDA-Examples Discharge
  Summary - Hospital Course carries no <entry> at all, Plan of Treatment's
  own entry shape, when present, is too heterogeneous for one resource
  type). Now mapped too - each converts to its own DocumentReference +
  Binary (extracted narrative text) rather than a full FHIR-Document
  Bundle(type="document")+Composition, which stays a disclosed, deliberate
  out-of-scope item - see app/cda/narrative_sections.py for the full
  design reasoning.

So this document type's header plus all six of its own IG-required
sections now convert - verified against a real Discharge Summary example,
which (alongside its discharge-specific sections) also included a plain
Problem List section (Problems' Concern-Act shape) and a plain
Immunizations section, both of which DO convert correctly through the
existing dispatch too. Any section this app genuinely doesn't recognize
(none remain for this document type's own required set, but a real-world
document could carry others) is still silently skipped, the same
"unrecognized section" treatment build_sectioned_bundle already gives any
section without a registered builder."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import build_sectioned_bundle

DISCHARGE_SUMMARY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.8"


class DischargeSummaryBuilder(CdaDocumentBuilder):
    template_id = DISCHARGE_SUMMARY_TEMPLATE_ID

    def build_bundle(self, document: Element, recorder=None) -> Bundle:
        return build_sectioned_bundle(document, recorder=recorder)
