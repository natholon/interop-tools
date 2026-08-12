# interop-tools

A healthcare interoperability toolkit - transformation, FHIR conversion, validation, and synthetic test-data generation across HL7v2, C-CDA, and (planned) EDI.

## Status

**Implemented:**
- HL7v2 ADT → FHIR R4 Bundle (Patient + Encounter) for the core ADT workflow — A01 Admit, A02 Transfer, A03 Discharge, A04 Register, A05 Pre-admit, A08 Update patient information, A11 Cancel Admit, A13 Cancel Discharge.
- HL7v2 SIU → FHIR R4 Bundle (Patient + Appointment, plus real Practitioner/Location/Device resources for the appointment's personnel/location/equipment participants) for the core scheduling workflow — S12 New booking, S13 Reschedule, S14 Modify, S15 Cancel, S17 Delete, S26 Patient No-Show.
- HL7v2 ORU → FHIR R4 Bundle (Patient + optional Encounter + DiagnosticReport + Observation per result, with positional OBR/OBX grouping so each report only references its own results) — R01 Observation result, R30/R40 point-of-care result.
- HL7v2 MDM → FHIR R4 Bundle (Patient + optional Encounter + DocumentReference, with document text content carried via a separate Binary resource) — T02 New document, T04 Document status update, T06 Document addendum.
- A synthetic test-data generator covering all 20 HL7v2 combinations above plus a synthetic CCD generator, with realistic field-level randomization (required fields always populated, optional fields randomly included or omitted), selectable from a dropdown in the web UI or via the JSON API.
- A message **validator**, independent of conversion — checks any message or document (supported for conversion or not) and returns a report of `error`/`warning`/`info` findings, each pointing at the offending location, covering structural correctness (required fields, well-formed values) as well as healthcare data-quality plausibility (a birth date in the future, a discharge before an admit, an appointment ending before it starts, a lab value outside its own reference range). Covers both HL7v2 and C-CDA input.
- **C-CDA → FHIR R4 Bundle** (the app's first non-HL7v2 input format): CCD (Continuity of Care Document) header → Patient + optional Encounter, and the Problems section → Condition (SNOMED CT-coded, with clinical status resolved from either the Concern Act or a nested Problem Status Observation, honoring `negationInd`). Input format is auto-detected — paste or upload either HL7v2 or C-CDA XML into the same textarea/file field and **Convert to FHIR** routes to the right pipeline automatically. Medications, Allergies, and other document types/sections are a planned follow-up on the same section-dispatch infrastructure.

Conversion, generation, and validation are all available for both input formats, through the same web UI and JSON API.

**Planned next:** remaining trigger events for the HL7v2 message types above (e.g. ADT A38 cancel pre-admit, ORU R32, MDM T08/T10/T11); more C-CDA document types and sections (Medications, Allergies) on the same infrastructure; then EDI support.

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

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Paste a raw HL7v2 message (any supported ADT, SIU, ORU, or MDM trigger event, see Status above) or a C-CDA XML document (currently: a CCD) into the text box, pick a message type from the dropdown (including "CDA^CCD") and click **Generate sample** for a fresh randomized example, or upload a `.hl7`/`.txt`/`.xml` file — then click **Convert to FHIR** to see the resulting FHIR Bundle JSON, or **Validate** to see a report of error/warning/info findings instead. Input format is auto-detected for both buttons. Parse errors and mapping/FHIR-construction errors are shown as clear categorized messages rather than raw errors; a validation report is returned even for messages with issues — it's an analysis result, not an error.

A JSON API is also available:
```powershell
curl -X POST http://127.0.0.1:8000/api/convert -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl -X POST http://127.0.0.1:8000/api/validate -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl "http://127.0.0.1:8000/api/generate?message_type=ADT&trigger_event=A01"
curl "http://127.0.0.1:8000/api/generate?message_type=CDA&trigger_event=CCD"
```
Pass `&seed=<int>` to `/api/generate` for a reproducible message instead of a fresh random one.

## Running tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

## Project layout

See [CLAUDE.md](CLAUDE.md) for an architecture overview, including how HL7v2 message-type support is added and notes on the underlying `hl7` and `fhir.resources` libraries.
