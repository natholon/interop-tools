# interop-tools

A healthcare interoperability toolkit - transformation, FHIR conversion, validation, and synthetic test-data generation across HL7v2, C-CDA, and X12 EDI.

## Disclaimer — read this before using interop-tools with real data

**interop-tools is provided as-is, without warranty of any kind**, under the
[Apache License 2.0](LICENSE) — see its "Disclaimer of Warranty" (section 7)
and "Limitation of Liability" (section 8), which govern. It is an open-source
engineering tool for exploring, prototyping, and inspecting healthcare data
transformation. It is not certified, accredited, or independently validated
software.

Deciding whether this tool is appropriate for your purpose is your
responsibility, not the author's. That decision should be based on your own
evaluation against the source specifications and your trading partners'
requirements — not on this README.

### What it is not

- **Not certified or conformance-tested.** No ONC Health IT certification, no
  HIPAA compliance attestation, no conformance testing against any
  implementation guide's official test suite.
- **Not a clinical system.** Nothing it produces should be used for clinical
  decision-making, diagnosis, treatment, or patient care.
- **Not a billing or claims system.** Do not submit X12 output to a payer or
  clearinghouse without independent validation. Incorrectly submitted claims
  can carry financial and legal consequences.
- **Not a substitute** for a certified interface engine, a licensed X12
  translator, or a validated FHIR server.

### How the mappings are derived, and where they are not authoritative

The whole point of the Data Specification crosswalk is that you do not have
to take any mapping on trust — every field decision is shown, sourced, and
reviewable. Use it. In particular:

- **HL7v2 and C-CDA** follow HL7's free, ballot-published
  [v2-to-FHIR](https://build.fhir.org/ig/HL7/v2-to-fhir/) and
  [C-CDA on FHIR](https://build.fhir.org/ig/HL7/ccda-on-fhir/) guides wherever
  a mapping is published for the field in question. *Ballot-published is not
  the same as normative and final* — these guides are still changing.
- **X12 EDI has no free, official X12-to-FHIR crosswalk.** The TR3
  Implementation Guides are commercial. Every X12 mapping here is this
  project's own documented judgment, checked against free X12.org examples,
  free companion guides, and the base FHIR specification. **A reasonable
  implementer could map these differently, and your trading partner may
  expect something different.**
- Where a standard is silent, or the tool has to choose a default, that
  choice is recorded as `inferred` and surfaced in the crosswalk rather than
  hidden. Those rows are the ones worth reading first.
- **Coverage is deliberately partial.** Each module documents its own scope
  limits — unmapped fields, lossy joins, "first transaction set only", and so
  on — in [CLAUDE.md](CLAUDE.md), next to the code they apply to.

### What it does not check

- **No FHIR profile or implementation-guide conformance validation.** The
  underlying `fhir.resources` library validates structure, not terminology
  bindings: it will happily accept a code that is not in a required value set.
- **No terminology validation.** There is no LOINC, SNOMED CT, RxNorm, CPT, or
  CDT licence behind this tool and no terminology server. Codes are carried
  through and, where a system OID is recognized, relabelled — never verified.
- **The built-in validator is not a conformance validator.** It reports
  structural completeness and data-quality plausibility (a birth date in the
  future, a discharge before an admit). Passing it says nothing about whether
  a message conforms to any standard.
- **Round trips are lossy in documented places.** Converting to FHIR and back
  will not always reproduce the original message byte-for-byte; where it
  cannot, the reason is documented.

### Data handling

- The application runs locally and processes input in-process. It does not
  persist messages, and makes no outbound network calls at runtime.
- **How you deploy it is your responsibility.** Logs, reverse proxies, browser
  history, and the hosting environment are outside this project's control. Do
  not paste PHI into an instance you do not control.
- Generated sample data is synthetic and contains no real patient
  information. Do not treat it as clinically or financially meaningful.

### Your responsibility

Complying with HIPAA and any other applicable law, regulation, contract, or
trading-partner agreement is entirely yours. Validate output independently
before relying on it for anything that matters.

*This disclaimer is a good-faith description of what the software does and
does not do. It is not legal advice, and it is not a substitute for having a
lawyer review your own use of it.*

## Status

**Implemented:**
- **Conversions**
  - **HL7v2 ADT → FHIR R4 Bundle** (Patient + Encounter) — A01 Admit, A02 Transfer, A03 Discharge, A04 Register, A05 Pre-admit, A08 Update, A11 Cancel Admit, A13 Cancel Discharge, A38 Cancel Pre-Admit.
  - **HL7v2 SIU → FHIR R4 Bundle** (Patient + Appointment, plus real Practitioner/Location/Device resources for the appointment's personnel/location/equipment participants) — S12 New booking, S13 Reschedule, S14 Modify, S15 Cancel, S17 Delete, S26 Patient No-Show.
  - **HL7v2 ORU → FHIR R4 Bundle** (Patient + optional Encounter + DiagnosticReport + Observation per result, with positional OBR/OBX grouping so each report only references its own results) — R01 Observation result, R30/R31/R32/R40 point-of-care result.
  - **HL7v2 MDM → FHIR R4 Bundle** (Patient + optional Encounter + DocumentReference, with document text content carried via a separate Binary resource) — T02 New document, T04 Document status update, T06 Document addendum, T08 Document edit, T10 Document replacement, T11 Document cancel.
  - **C-CDA → FHIR R4 Bundle**: three document types (CCD, Discharge Summary, History and Physical Note) sharing a common header (Patient + optional Encounter) and seven general-purpose sections recognized on any of them - Problems → Condition, Medications → MedicationRequest, Allergies → AllergyIntolerance, Immunizations → Immunization, Vital Signs → an Observation panel, Results → DiagnosticReport + Observation, Procedures → Procedure - plus two sections specific to Discharge Summary (Hospital Discharge Diagnosis → Condition, Discharge Medications → MedicationRequest).
  - **X12 EDI → FHIR R4 Bundle**: the full "big five" HIPAA transaction-set suite - **270/271** Eligibility Inquiry/Response → CoverageEligibilityRequest/Response, **276/277** Claim Status Request/Response → Task, **278** Prior Authorization → Claim/ClaimResponse, **835** Remittance Advice → PaymentReconciliation, **837P/837I/837D** Professional/Institutional/Dental Claims → Claim.
- Input format (HL7v2 / C-CDA XML / X12 EDI) is auto-detected — paste or upload any supported format into the same textarea/file field and **Convert to FHIR** routes to the right pipeline automatically.
- **Bundle deduplication** — an opt-in post-conversion pass that merges duplicate Patient/Practitioner/Organization/Location entries within one already-converted Bundle (matched by identifier, or by name when no identifier is present) and rewrites every surviving reference to point at the kept, canonical resource.
- **Bidirectional transformation (FHIR Bundle → source-format text)** — the reverse of every conversion pillar above, at full family-level breadth: every HL7v2 trigger event this app converts *to* FHIR (all ADT/SIU/ORU/MDM triggers, including all three cancel triggers) is also a reverse target; all three C-CDA document types convert back out, with both Discharge-Summary-specific sections reversing via their own real category markers (Hospital Discharge Diagnosis via `Condition.category`, Discharge Medications via `MedicationRequest.category`); and every X12 EDI transaction-set family above, including all three 837 variants, round-trips back to X12 text.
- A **synthetic test-data generator** covering every combination above, with realistic field-level randomization (required fields always populated, optional fields randomly included or omitted), selectable from a dropdown in the web UI or via the JSON API.
- A **message validator**, independent of conversion — checks any message or document (supported for conversion or not) and returns a report of `error`/`warning`/`info` findings, each pointing at the offending location, covering structural correctness (required fields, well-formed values) as well as healthcare data-quality plausibility (a birth date in the future, a discharge before an admit, an appointment ending before it starts, a lab value outside its own reference range).
- **Data Specification** — a field-level provenance crosswalk shown alongside the conversion it came from, showing exactly which source field (e.g. `PID-5`, a C-CDA XPath-like location such as `recordTarget/patientRole/patient/name[0]/family`, or an X12 element like `NM1-9`) produced which FHIR R4 field (e.g. `Patient.name[0].family`) for an actual converted message, including an honest explanation for fields with no single source field to point at (a trigger-event-driven status, internal UUID wiring). Fully instrumented for all four HL7v2 message types this app converts - ADT (all 9 triggers), SIU (all 6 triggers), ORU (all 5 triggers), and MDM (all 6 triggers) - for every X12 EDI family this app converts (270, 271, 276, 277, 278, 835, 837P, 837I, 837D - the complete "big five" HIPAA EDI suite) - and for every general-purpose C-CDA section this app recognizes (document header, Problems, Medications, Allergies, Immunizations, Vital Signs, Results, Procedures), every narrative-only section either document type requires, and the real structured entries Plan of Treatment/Social History/Family History can carry beneath their own narrative text. All three C-CDA document types are reported fully supported, alongside every HL7v2 message type and EDI family. A **mapping-decision register** sits above the crosswalk: every value the conversion inferred, and every source element it did not map, computed rather than hand-listed, each reviewable and rejectable for the message at hand. The dropped-data half covers all three formats, and every drop carries a citation checked against the source specification.

Conversion, generation, validation, deduplication, bidirectional transformation, and the Data Specification crosswalk are all available for every input format, through the same web UI and JSON API.

**Planned next:** the Data Specification pillar has reached full breadth across every format, section, and document type this app's conversion recognizes - HL7v2 (all four message types), X12 EDI (the complete "big five" suite), and C-CDA (all three document types, every general-purpose and narrative-only section, the structured entries Plan of Treatment/Social History/Family History can carry, Procedures' own Indication/Comment Activity cross-references and recorder, and `originalText` resolution including the narrative-anchor shape). Mapping C-CDA `<author>` to a FHIR `Provenance` resource is a deliberate, permanent scope decision rather than pending work: `Provenance` models an audit trail over stored records, which a stateless converter has no lifecycle for, and where `<author>` has a real home on the resource itself the plain attribute (`Procedure.recorder`, `Annotation.authorReference`) already carries it.

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

## Installation

No platform-specific dependencies — everything in `pyproject.toml` is pure Python, so setup only differs in how the virtual environment gets activated. Requires Python 3.10+.

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

If `Activate.ps1` is blocked by your execution policy, run once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### macOS / Linux (bash/zsh)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

(`python3`/`pip` — not `python`/`pip3` — is what most macOS/Linux distributions ship; substitute whichever your system actually resolves `python -V` to if it already points at Python 3.10+.)

## Usage

Start the app (identical on every platform once the virtual environment above is active):
```
uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Paste a raw HL7v2 message, a C-CDA XML document, or an X12 EDI interchange (any supported type/trigger, see Status above) into the text box, pick a message type from the dropdown and click **Generate sample** for a fresh randomized example, or upload a `.hl7`/`.txt`/`.xml`/`.x12` file — then click **Convert to FHIR** to see the resulting FHIR Bundle JSON, or **Validate** to see a report of error/warning/info findings instead. Input format is auto-detected for both buttons. Parse errors and mapping/FHIR-construction errors are shown as clear categorized messages rather than raw errors; a validation report is returned even for messages with issues — it's an analysis result, not an error.

A JSON API is also available. The `-d` payload quoting below is genuinely platform-specific (PowerShell and a POSIX shell handle embedded quotes inside a `curl` argument differently), so both are shown - use whichever matches your terminal, not necessarily your OS (e.g. Git Bash on Windows wants the bash form):

**PowerShell:**
```powershell
curl -X POST http://127.0.0.1:8000/api/convert -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl -X POST http://127.0.0.1:8000/api/validate -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
curl "http://127.0.0.1:8000/api/generate?message_type=ADT&trigger_event=A01"
curl "http://127.0.0.1:8000/api/generate?message_type=CDA&trigger_event=CCD"
curl "http://127.0.0.1:8000/api/generate?message_type=EDI&trigger_event=837P"
curl -X POST http://127.0.0.1:8000/api/transform -H "Content-Type: application/json" -d '{\"bundle_json\": \"{...}\", \"target_format\": \"HL7\", \"target_type\": \"ADT\", \"target_trigger\": \"A01\"}'
curl -X POST http://127.0.0.1:8000/api/data-specification -H "Content-Type: application/json" -d '{\"hl7_text\": \"MSH|...\"}'
```

**bash/zsh:**
```bash
curl -X POST http://127.0.0.1:8000/api/convert -H "Content-Type: application/json" -d '{"hl7_text": "MSH|..."}'
curl -X POST http://127.0.0.1:8000/api/validate -H "Content-Type: application/json" -d '{"hl7_text": "MSH|..."}'
curl "http://127.0.0.1:8000/api/generate?message_type=ADT&trigger_event=A01"
curl "http://127.0.0.1:8000/api/generate?message_type=CDA&trigger_event=CCD"
curl "http://127.0.0.1:8000/api/generate?message_type=EDI&trigger_event=837P"
curl -X POST http://127.0.0.1:8000/api/transform -H "Content-Type: application/json" -d '{"bundle_json": "{...}", "target_format": "HL7", "target_type": "ADT", "target_trigger": "A01"}'
curl -X POST http://127.0.0.1:8000/api/data-specification -H "Content-Type: application/json" -d '{"hl7_text": "MSH|..."}'
```

Pass `&seed=<int>` to `/api/generate` for a reproducible message instead of a fresh random one. `/api/transform` takes a FHIR Bundle back the other way, to any of the targets `/api/convert` can produce - see [CLAUDE.md](CLAUDE.md) for the full list. `/api/data-specification` (the endpoint behind **Convert to FHIR** in the UI) returns both the converted Bundle and a field-level crosswalk report for it.

## Running tests

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

**macOS / Linux:**
```bash
source .venv/bin/activate
pytest
```

## Project layout

See [CLAUDE.md](CLAUDE.md) for an architecture overview, including how HL7v2 message-type support is added and notes on the underlying `hl7` and `fhir.resources` libraries.
