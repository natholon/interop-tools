"""Discharge Summary (templateId 2.16.840.1.113883.10.20.22.1.8) -> FHIR
Bundle.

Structurally the same header shape as CCD (`recordTarget`,
`componentOf/encompassingEncounter` - here almost always present, since a
discharge summary is tied to one hospitalization), confirmed by comparing
a real HL7 C-CDA-Examples Discharge Summary against CCD's rather than
assumed from the name. So `build_bundle()` is just
`build_sectioned_bundle(document)`, the same "header + generic sections"
shape CCD uses.

Its four IG-required sections each need an entry template the general
section builders do not recognize, and each is handled elsewhere:

    Hospital Discharge Diagnosis (...2.24)  hospital_discharge_diagnosis.py
      wraps a Problem Observation in Act ...4.33, not Problems' ...4.3
    Discharge Medications (...2.11.1)       discharge_medications.py
      wraps a Medication Activity in Act ...4.35, not a bare
      substanceAdministration
    Hospital Course, Plan of Treatment      narrative_sections.py
      narrative-only in practice - Hospital Course carries no <entry> at
      all in the real example, and Plan of Treatment's entries are too
      heterogeneous for one resource type

A section with no registered builder is silently skipped, the same
treatment `build_sectioned_bundle` gives any unrecognized section."""

from xml.etree.ElementTree import Element

from fhir.resources.R4B.bundle import Bundle

from app.cda.base import CdaDocumentBuilder
from app.cda.common import build_sectioned_bundle

DISCHARGE_SUMMARY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.8"


class DischargeSummaryBuilder(CdaDocumentBuilder):
    template_id = DISCHARGE_SUMMARY_TEMPLATE_ID

    def build_bundle(self, document: Element, recorder=None) -> Bundle:
        return build_sectioned_bundle(document, recorder=recorder)
