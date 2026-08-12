# hl7-tools

Tools to assist with common HL7 processes - transformation, fhir conversion, validation

## Status

**Implemented:**
- HL7v2 ADT → FHIR R4 Bundle (Patient + Encounter) for the core ADT workflow — A01 Admit, A02 Transfer, A03 Discharge, A04 Register, A05 Pre-admit, A08 Update patient information, A11 Cancel Admit, A13 Cancel Discharge.
- HL7v2 SIU → FHIR R4 Bundle (Patient + Appointment, plus real Practitioner/Location/Device resources for the appointment's personnel/location/equipment participants) for the core scheduling workflow — S12 New booking, S13 Reschedule, S14 Modify, S15 Cancel, S17 Delete, S26 Patient No-Show.
- HL7v2 ORU → FHIR R4 Bundle (Patient + optional Encounter + DiagnosticReport + Observation per result, with positional OBR/OBX grouping so each report only references its own results) — R01 Observation result, R30/R40 point-of-care result.
- HL7v2 MDM → FHIR R4 Bundle (Patient + optional Encounter + DocumentReference, with document text content carried via a separate Binary resource) — T02 New document, T04 Document status update, T06 Document addendum.
- A synthetic test-data generator covering all 20 combinations above, with realistic field-level randomization (required fields always populated, optional fields randomly included or omitted), selectable from a dropdown in the web UI or via the JSON API.

Both conversion and generation are available through the same web UI and JSON API.

**Planned next:** remaining trigger events for the message types above (e.g. ADT A38 cancel pre-admit, ORU R32, MDM T08/T10/T11), then broader transformation, validation, deduplication, and mapping tooling across HL7v2/FHIR/CDA/C-CDA.

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

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Paste a raw HL7v2 message (any supported ADT, SIU, ORU, or MDM trigger event, see Status above) into the text box, pick a message type from the dropdown and click **Generate sample** for a fresh randomized example, or upload a `.hl7`/`.txt` file — then click **Convert to FHIR** to see the resulting FHIR Bundle JSON. Parse, mapping, and validation errors are shown as clear categorized messages rather than raw errors.

A JSON API is also available:
```powershell
curl -X POST http://127.0.0.1:8000/api/convert -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl "http://127.0.0.1:8000/api/generate?message_type=ADT&trigger_event=A01"
```
Pass `&seed=<int>` to `/api/generate` for a reproducible message instead of a fresh random one.

## Running tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

## Project layout

See [CLAUDE.md](CLAUDE.md) for an architecture overview, including how HL7v2 message-type support is added and notes on the underlying `hl7` and `fhir.resources` libraries.
