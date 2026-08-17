# interop-tools

A healthcare interoperability toolkit - transformation, FHIR conversion, validation, and synthetic test-data generation across HL7v2, C-CDA, and X12 EDI.

## Status

**Implemented:**
- HL7v2 **ADT** → FHIR R4 Bundle (Patient + Encounter) — A01 Admit, A02 Transfer, A03 Discharge, A04 Register, A05 Pre-admit, A08 Update, A11 Cancel Admit, A13 Cancel Discharge, A38 Cancel Pre-Admit.
- HL7v2 **SIU** → FHIR R4 Bundle (Patient + Appointment, plus real Practitioner/Location/Device resources for the appointment's personnel/location/equipment participants) — S12 New booking, S13 Reschedule, S14 Modify, S15 Cancel, S17 Delete, S26 Patient No-Show.
- HL7v2 **ORU** → FHIR R4 Bundle (Patient + optional Encounter + DiagnosticReport + Observation per result, with positional OBR/OBX grouping so each report only references its own results) — R01 Observation result, R30/R31/R32/R40 point-of-care result.
- HL7v2 **MDM** → FHIR R4 Bundle (Patient + optional Encounter + DocumentReference, with document text content carried via a separate Binary resource) — T02 New document, T04 Document status update, T06 Document addendum, T08 Document edit, T10 Document replacement, T11 Document cancel.
- **C-CDA → FHIR R4 Bundle**: three document types (CCD, Discharge Summary, History and Physical Note) sharing a common header (Patient + optional Encounter) and seven general-purpose sections recognized on any of them - Problems → Condition, Medications → MedicationRequest, Allergies → AllergyIntolerance, Immunizations → Immunization, Vital Signs → an Observation panel, Results → DiagnosticReport + Observation, Procedures → Procedure - plus two sections specific to Discharge Summary (Hospital Discharge Diagnosis → Condition, Discharge Medications → MedicationRequest).
- **X12 EDI → FHIR R4 Bundle**: the full "big five" HIPAA transaction-set suite - **270/271** Eligibility Inquiry/Response → CoverageEligibilityRequest/Response, **276/277** Claim Status Request/Response → Task, **278** Prior Authorization → Claim/ClaimResponse, **835** Remittance Advice → PaymentReconciliation, **837P/837I/837D** Professional/Institutional/Dental Claims → Claim.
- Input format (HL7v2 / C-CDA XML / X12 EDI) is auto-detected — paste or upload any supported format into the same textarea/file field and **Convert to FHIR** routes to the right pipeline automatically.
- **Bundle deduplication** — an opt-in post-conversion pass that merges duplicate Patient/Practitioner/Organization/Location entries within one already-converted Bundle (matched by identifier, or by name when no identifier is present) and rewrites every surviving reference to point at the kept, canonical resource.
- **Bidirectional transformation (FHIR Bundle → source-format text)** — the reverse of every conversion pillar above, at full family-level breadth: every HL7v2 trigger event this app converts *to* FHIR (all ADT/SIU/ORU/MDM triggers, including all three cancel triggers) is also a reverse target; all three C-CDA document types convert back out, with Hospital Discharge Diagnosis reversing via its own real category marker (Discharge Medications is a disclosed, permanent exception - the forward mapping leaves no FHIR-side signal to reverse); and every X12 EDI transaction-set family above, including all three 837 variants, round-trips back to X12 text.
- A synthetic test-data **generator** covering every combination above, with realistic field-level randomization (required fields always populated, optional fields randomly included or omitted), selectable from a dropdown in the web UI or via the JSON API.
- A message **validator**, independent of conversion — checks any message or document (supported for conversion or not) and returns a report of `error`/`warning`/`info` findings, each pointing at the offending location, covering structural correctness (required fields, well-formed values) as well as healthcare data-quality plausibility (a birth date in the future, a discharge before an admit, an appointment ending before it starts, a lab value outside its own reference range).
- **Data Specification** — a field-level provenance crosswalk on its own page, showing exactly which source field (e.g. `PID-5`) produced which FHIR R4 field (e.g. `Patient.name[0].family`) for an actual converted message, including an honest explanation for fields with no single source field to point at (a trigger-event-driven status, internal UUID wiring). Currently instrumented for all four HL7v2 message types this app converts - ADT (all 9 triggers), SIU (all 6 triggers), ORU (all 5 triggers), and MDM (all 6 triggers) - every other format (C-CDA, X12 EDI) converts normally but discloses that field-level detail isn't implemented for it yet, rather than showing an incomplete picture as if it were complete.

Conversion, generation, validation, deduplication, and bidirectional transformation are all available for every input format, through the same web UI and JSON API; the Data Specification crosswalk is available wherever it's been instrumented so far (see above).

**Planned next:** extending the Data Specification crosswalk to C-CDA next, then X12 EDI - HL7v2 breadth is now complete across all four message types - the same one-type-at-a-time way every other pillar reached full breadth. History and Physical's own IG-required narrative sections (Reason for Visit, History of Present Illness, Physical Exam, etc.) remain a separate, disclosed gap - they carry no structured entries recognized by this app's section-dispatch infrastructure, and mapping them properly would need a full FHIR Composition, a deliberately out-of-scope redesign for now.

## Reference sources

This project converts and validates data in formats governed by external standards, so it's worth being explicit about what each mapping decision is actually checked against:

- **HL7v2**: checked against the free, ballot-published [v2-to-FHIR](https://build.fhir.org/ig/HL7/v2-to-fhir/) implementation guide wherever a ConceptMap exists for the segment/field in question.
- **C-CDA**: checked against the free, ballot-published [C-CDA on FHIR](https://build.fhir.org/ig/HL7/ccda-on-fhir/) implementation guide.
- **X12 EDI** (270/271, 276/277, 278, 835, 837P, 837I, 837D): X12's own TR3 Implementation Guides are commercial/paywalled - no free, official X12-to-FHIR crosswalk exists for any of these transaction sets. Field-level mapping is instead verified against:
  - Real, freely-published example files from [x12.org/examples](https://x12.org/examples), fetched and checked directly rather than relied on from memory or a summarized secondary source
  - Free companion guides published by CMS, state Medicaid agencies, and clearinghouses, cross-referenced against each other wherever the X12.org examples alone didn't label a field's exact position
  - For 278 specifically, HL7's own [Da Vinci PAS](https://hl7.org/fhir/us/davinci-pas/) implementation guide (free, ballot-published) confirms the target `Claim`/`ClaimResponse` FHIR shape, even though it doesn't publish an X12-segment-level crosswalk

  The FHIR (target) side of every EDI mapping is checked directly against `hl7.org`/`terminology.hl7.org` - CodeSystems like NUBC Revenue Codes and value sets like `claim-careteamrole` are confirmed by direct fetch, not assumed.

Every disclosed judgment call, local-system fallback, and scope limit made along the way is documented in [CLAUDE.md](CLAUDE.md)'s own per-module notes, alongside the file/line it applies to.

## Installation (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

If `Activate.ps1` is blocked by your execution policy, run once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Usage

Start the app:
```powershell
uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Paste a raw HL7v2 message, a C-CDA XML document, or an X12 EDI interchange (any supported type/trigger, see Status above) into the text box, pick a message type from the dropdown and click **Generate sample** for a fresh randomized example, or upload a `.hl7`/`.txt`/`.xml`/`.x12` file — then click **Convert to FHIR** to see the resulting FHIR Bundle JSON, or **Validate** to see a report of error/warning/info findings instead. Input format is auto-detected for both buttons. Parse errors and mapping/FHIR-construction errors are shown as clear categorized messages rather than raw errors; a validation report is returned even for messages with issues — it's an analysis result, not an error.

A JSON API is also available:
```powershell
curl -X POST http://127.0.0.1:8000/api/convert -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl -X POST http://127.0.0.1:8000/api/validate -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl "http://127.0.0.1:8000/api/generate?message_type=ADT&trigger_event=A01"
curl "http://127.0.0.1:8000/api/generate?message_type=CDA&trigger_event=CCD"
curl "http://127.0.0.1:8000/api/generate?message_type=EDI&trigger_event=837P"
curl -X POST http://127.0.0.1:8000/api/transform -H "Content-Type: application/json" -d '{\"bundle_json\": \"{...}\", \"target_format\": \"HL7\", \"target_type\": \"ADT\", \"target_trigger\": \"A01\"}'
curl -X POST http://127.0.0.1:8000/api/data-specification -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
```
Pass `&seed=<int>` to `/api/generate` for a reproducible message instead of a fresh random one. `/api/transform` takes a FHIR Bundle back the other way, to any of the targets `/api/convert` can produce - see [CLAUDE.md](CLAUDE.md) for the full list. `/api/data-specification` (also reachable via the **Data Specification** page/nav link in the UI) returns both the converted Bundle and a field-level crosswalk report for it.

## Running tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

## Project layout

See [CLAUDE.md](CLAUDE.md) for an architecture overview, including how HL7v2 message-type support is added and notes on the underlying `hl7` and `fhir.resources` libraries.
