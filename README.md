# hl7-tools

Tools to assist with common HL7 processes - transformation, fhir conversion, validation

## Status

**Implemented:**
- HL7v2 ADT → FHIR R4 Bundle (Patient + Encounter) for the core ADT workflow — A01 Admit, A02 Transfer, A03 Discharge, A04 Register, A08 Update patient information.
- HL7v2 SIU → FHIR R4 Bundle (Patient + Appointment) for the core scheduling workflow — S12 New booking, S13 Reschedule, S14 Modify, S15 Cancel.

Both via the same web UI and JSON API.

**Planned next:** remaining ADT trigger events (e.g. A05 pre-admit, A11/A13 cancel), remaining SIU trigger events (e.g. S17 delete, S26 patient did-not-show), then broader transformation, validation, deduplication, test-data generation, and mapping tooling across HL7v2/FHIR/CDA/C-CDA.

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

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Paste a raw HL7v2 message (any supported ADT or SIU trigger event, see Status above) into the text box (or use "Load sample message" for an ADT^A01 example), or upload a `.hl7`/`.txt` file, and click **Convert to FHIR** to see the resulting FHIR Bundle JSON. Parse, mapping, and validation errors are shown as clear categorized messages rather than raw errors.

A JSON API is also available:
```powershell
curl -X POST http://127.0.0.1:8000/api/convert -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
```

## Running tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

## Project layout

See [CLAUDE.md](CLAUDE.md) for an architecture overview, including how HL7v2 message-type support is added and notes on the underlying `hl7` and `fhir.resources` libraries.
