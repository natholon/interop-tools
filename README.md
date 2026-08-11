# hl7-tools

Tools to assist with common HL7 processes - transformation, fhir conversion, validation

## Status

**Implemented:** HL7v2 ADT^A01 → FHIR R4 Bundle (Patient + Encounter) conversion, via a web UI and a JSON API.

**Planned next:** SIU (scheduling) message conversion, then broader transformation, validation, deduplication, test-data generation, and mapping tooling across HL7v2/FHIR/CDA/C-CDA.

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

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Paste a raw HL7v2 ADT^A01 message into the text box (or use "Load sample message"), or upload a `.hl7`/`.txt` file, and click **Convert to FHIR** to see the resulting FHIR Bundle JSON. Parse, mapping, and validation errors are shown as clear categorized messages rather than raw errors.

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
