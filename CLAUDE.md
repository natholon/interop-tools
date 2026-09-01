# CLAUDE.md

Guidance for Claude Code working in this repository.

**Scope of this file**: how to work here — where things plug in, which
libraries lie, what the standing decisions are, and the discipline that
keeps the mappings honest. It is deliberately short, because it is loaded
into context on every request.

**How the app was built, slice by slice, is in
[`docs/build-history.md`](docs/build-history.md)** — not loaded
automatically. Go there for *why* a decision went the way it did. Do not
read it as a statement of what the code does now; it contains claims that
have expired. Check the code, or run it.

## Comment style — keep it slim

Write the *shortest* comment that stops the next person making a mistake:

- **Comment the non-obvious**: a spec quirk, a library that lies
  (`fhir.resources` accepts codes outside a required binding), a
  similar-name-different-vocabulary trap (ADA vs FDI tooth numbering), a
  constraint that looks wrong until you know why. These earn their space.
- **Skip the narrative**: "shipped once and was caught by code review",
  "turned out to need zero changes", "as a follow-up slice". Git history
  holds all of it, and it ages badly — a "recent" fix reads as current
  forever.
- **Skip restating the code.** If the line says what it does, the comment
  shouldn't.
- **A module docstring is an orientation, not a design record.** Target
  ~10–25 lines: what this maps, where the mapping is specified, and the
  handful of places it genuinely diverges or falls back.
- **Cite the source, not the journey.** "Per the v2-to-FHIR PL[Location]
  map" beats a paragraph on how it was fetched and verified.

**One thing to keep doing**: state a real limitation plainly — an
unmapped field, a lossy join, a guessed default. Slim means fewer words,
not fewer disclosures. Say it in a sentence rather than a paragraph.

## Project

`interop-tools` is a Python/FastAPI web app that converts healthcare
messages and documents to FHIR R4 Bundles, and back. Seven capabilities,
all reachable from one page and one set of API routes:

| pillar | entry point |
|---|---|
| Convert to FHIR | `app/pipeline.py::convert_to_bundle` |
| Validate a source message | `app/pipeline.py::validate_any` |
| Generate synthetic samples | `app/generators/registry.py::generate` |
| Deduplicate a Bundle (opt-in) | `app/dedup.py::deduplicate_bundle` |
| FHIR → source message | `app/transform/pipeline.py::build_message_from_bundle` |
| Field-level provenance (crosswalk) | `app/provenance/dispatch.py::convert_with_provenance` |
| FHIR R4 conformance of the output | `app/fhir_conformance/checker.py::check_bundle` |

**Scope snapshot** — accurate at the time of writing, and the kind of
claim that goes stale; `list_supported_types()` and
`list_supported_targets()` are authoritative.

- **HL7v2**: ADT (9 triggers), SIU (6), ORU (5), MDM (6)
- **C-CDA**: CCD, Discharge Summary, History and Physical
- **X12 EDI**: 270/271, 276/277, 278, 835, 837P/837I/837D
- **Reverse transform**: 39 targets — every message type, document type
  and transaction set the forward direction supports

## Commands

No platform-specific dependencies (everything in `pyproject.toml` is pure
Python) — only the venv activation step differs by shell.

```powershell
# one-time setup (Windows / PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

```bash
# one-time setup (macOS / Linux, bash/zsh)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```
# run the app (http://127.0.0.1:8000)
uvicorn app.main:app --reload

# the full suite (network-marked tests are deselected by default)
pytest

# re-derive the transcribed spec tables from their live published sources
pytest -m network

# a single test
pytest tests/test_adt_a03_mapping.py::test_discharge_sets_finished_status_and_period_end -v
```

There is no separate lint/build step. `pyflakes app tests` is what CI
would run if it existed.

## Architecture

**Request flow.** `app/routes/` calls one of two thin format-sniffing
dispatchers in `app/pipeline.py`, which sit above three independent
format pipelines. The sniff order is `is_x12` (starts with `ISA`) →
`is_xml` (starts with `<`) → HL7v2 as the default; the three signatures
are mutually exclusive by construction.

```
app/pipeline.py          sniff, then dispatch
├── app/hl7/pipeline.py  → app/mappings/   (registry: message type + trigger)
├── app/cda/pipeline.py  → app/cda/        (registry: document templateId)
└── app/edi/pipeline.py  → app/edi/        (registry: ST01, plus ST03 for 837)
```

None of the three knows the others exist, and all three raise the same
exception shapes, so the route layer needs one dispatch table per axis
rather than one per format.

### Where to add things

| adding | do this |
|---|---|
| A trigger event for an existing HL7v2 message type | Add a subclass in that type's module (`app/mappings/adt.py` etc.). Not a new file. |
| A new HL7v2 message type | New module in `app/mappings/`, register in `app/mappings/registry.py` |
| A C-CDA section | New module in `app/cda/`, register in `SECTION_BUILDERS` (`app/cda/registry.py`) |
| A C-CDA document type | Implement `CdaDocumentBuilder`; if it is header + sections, `build_bundle()` is one call to `build_sectioned_bundle` |
| An X12 transaction set | Implement `EdiTransactionBuilder`, register by `ST01` |
| A validation rule | A small `_rule_*` function in that format's validation module, composed by concatenation |
| A reverse-transform target | Implement `MessageBuilder`, register `(format, type, trigger)` |

Cross-message-type shared logic lives in `app/mappings/common.py`,
`app/cda/common.py` and `app/edi/common.py`. Generic field-level FHIR
fragment builders live in `app/fhir_models/builders.py`.

### Standing decisions

These are settled. Reopen them deliberately, not by accident.

- **No `Provenance` resource, ever.** It models "who created/revised this
  record, and when", which presumes a system that stores records over
  time. This is a stateless converter; its required `recorded` timestamp
  has no honest value here. Where a source `<author>` has a real home on
  the resource itself (`Procedure.recorder`, `MedicationRequest.requester`)
  that is used instead.
- **`Composition` only for a source that *is* a document** — C-CDA. The
  v2-to-FHIR IG assigns `Bundle.type = "message"` for HL7v2 and maps
  MDM^T02 to `DocumentReference`; X12 carries no clinical document.
- **Only the first message / transaction set / claim is processed.** Real
  files are often batched. Disclosed rather than silent.
- **X12 has no free official FHIR crosswalk.** The TR3s are paywalled.
  Every X12 mapping here is this project's own reading, verified against
  X12.org-published examples, and says so. Do not invent one.
- **Errors are never raised for something a real sender could produce.**
  That is what the validators are for.

### Error taxonomy

`app/hl7/errors.py` defines `Hl7ParseError`, `MissingSegmentError` and
`MappingError`; `app/cda/errors.py` adds `CdaParseError` and
`app/edi/errors.py` adds `EdiParseError`. The two segment/mapping errors
are format-agnostic and reused as-is. `app/routes/errors.py` holds the
shared exception → (category, HTTP status) tables.

## Working discipline

Every rule here was learned by getting it wrong at least once.

**Verify against the primary source.** A summarised secondary page has
been wrong more than once (an AI-summarised X12 reference contradicted
the real published example on HL03 codes). Fetch the spec, the IG sheet,
the StructureDefinition. Quote it.

**Before recording something as unmapped, check whether it should be.**
The register's value is that it says "this was lost". Confirm the value
really is absent from the built Bundle before calling anything a gap — a
spec naming a target is not evidence this app fails to build it. Several
"gaps" turned out to be facts the mapper carried and merely failed to
record, and a register that accuses the mapper of losing data it carried
is worse than one that says "unchecked".

**A claim about what this app does has an expiry date.** Verdicts and
disclosures that assert current behaviour go stale silently and read as
settled. Two were found stating the opposite of the truth. Prefer
statements about the *spec*; where you must describe the code, expect to
re-check it.

**Don't trust `fhir.resources`' declared cardinality.** `model_fields`
understates what is required — `MedicationRequest.intent`,
`Immunization.status`, `Task.status`/`.intent`, `Claim.created` and many
others are enforced eagerly at construction while reporting as optional.
Construct the resource directly the first time you use it. And it
validates neither required bindings nor invariants: `Observation(status=
"banana")` is accepted. That is what `app/fhir_conformance/` exists for.

**Extract on the second real consumer, not in anticipation.** And when
two call sites look identical, confirm it rather than assuming — several
"identical" pairs had already drifted.

**A shared table's key must be right for every occurrence it matches.**
An over-reaching key is worse than no key: verdicts scoped by element
shape alone were giving one section's answer to another's identically
named element.

**Generated output must be valid, not merely convertible.** The
generators' contract is that every sample converts *and* validates *and*
produces conformant FHIR. When a new rule flags generated output, the
generator is usually what is wrong.

**A test pinned to a seed will break when the RNG shifts.** Search for
the shape the test needs instead. Several did break, for changes
unrelated to what they were testing.

**Drive the UI in a real browser.** Install Puppeteer to the scratch
directory; do not review HTML/CSS/JS statically. Two real bugs — a CSS
specificity failure that made `hidden` inert on every `<button>`, and a
stale-cached static asset — were invisible to code reading and to
route-level tests.

**One thing per slice.** ADT^A01 before the other eight triggers; CCD's
Problems section before the other six.

## Transcribed spec tables

Four tables are transcribed from published specs, and each has a
`network`-marked test that re-derives it from the live source. Run
`pytest -m network` after touching one.

| table | source |
|---|---|
| `app/validation/required_fields.py` | v2-to-FHIR segment sheets, `Cardinality - Min` on the HL7v2 side |
| `app/cda/required_elements.py` | each C-CDA template's StructureDefinition **snapshot** |
| `app/fhir_conformance/tables.py` | R4 `<resource>.profile.json` snapshots + `valuesets.json` |
| `app/fhir_conformance/invariants.py` | R4 root `constraint`s, each rule quoting its FHIRPath |

Two traps, both of which produced a confidently wrong table first:

- **The v2-to-FHIR sheets name their columns twice** — once for the HL7v2
  source and again for the FHIR target. Keying a dict by column name
  silently reads the *target* cardinality and reports every field as
  optional. Take the first occurrence.
- **Read a C-CDA StructureDefinition's snapshot, not its differential.**
  A differential lists only what a template *constrains*, so
  `AllergyIntoleranceObservation` reads as requiring nothing at all —
  every minimum is inherited from the base type and not restated.

## `hl7` library gotchas (important)

The `hl7` package (PyPI: `hl7`, i.e. python-hl7) is used for HL7v2
parsing:

- `hl7.parse()` **always** splits segments on `\r`, regardless of input.
  `app/hl7/parser.py::parse_message()` normalizes `\n`/`\r\n` to `\r`
  first and drops blank lines.
- `hl7.parse()` has **no MSH-boundary awareness**. A batch file (several
  MSH-led messages concatenated) parses into one `Message`, and
  `optional_segments`/`group_segments_by_leader` would then pull segments
  from every message into one Bundle with nothing to distinguish the
  result. `_truncate_to_first_message()` cuts at the second `MSH`.
- When a field has no component separator (`^`), the library collapses it
  to a bare `str`. Indexing a second level (`field[0][0]`) then does
  **character** indexing and silently returns a truncated value. Use
  `component_str()` / `field_str()`, never raw indexing.
- The library splits on `^` **with no awareness of data types**, so a
  literal `^` in an `ST`/`FT`/`TX` free-text field is treated as a
  component separator and `field_str` truncates it. Use
  `raw_field_str(segment, field_num, repetition=0)` for any free-text
  field (`NTE-3`, `TXA-25`, `OBX-5` when `OBX-2` says so). This shipped
  once in both ORU and MDM.
- **Presence must be checked with `raw_field_str`, not `field_str`.**
  `field_str` returns component 1, so a composite populating only later
  components reads as absent — `SCH-11` is exactly that shape, its start
  and end being TQ.4 and TQ.5 with TQ.1 empty.

## `fhir.resources` library notes

- Import from the `R4B` subpackage (`fhir.resources.R4B.*`); the root
  package defaults to R5 as of v7+.
- `Encounter.class` is the Python attribute `class_fhir` and `Task.for`
  is `for_fhir` (reserved words). Both must be passed as constructor
  kwargs where required.
- Bundles use `type = "collection"`, except a C-CDA conversion that
  qualifies as a FHIR Document (`"document"`, Composition first).
- `Quantity.value` deserializes as `decimal.Decimal`, so
  `obs.valueQuantity.value == 7.2` is `False`. Compare with `float(...)`
  or a `Decimal` literal.
- Before using an unfamiliar sub-object (`EncounterHospitalization`,
  `AppointmentParticipant`, `ObservationReferenceRange`), inspect its
  real `model_fields` rather than guessing field names — and see the
  discipline note above about `model_fields` understating requirements.

## HL7 SCH/SIU facts worth remembering

- The `TQ` composite in the legacy `SCH-11` puts **start at component 4
  and end at component 5**, not 1 and 2. `TQ1` is simpler: TQ1-7 start,
  TQ1-8 end. `resolve_appointment_timing()` prefers TQ1 and falls back to
  SCH-11 **per field**, not all-or-nothing — a TQ1 supplying only a start
  must still let a usable SCH-11 end through.
- `_resolve_minutes_duration` prefers TQ1-6 over SCH-9/10 so a reschedule
  with a stale SCH-9 cannot contradict a fresh TQ1-derived start/end.
- `parse_hl7_datetime` preserves a trailing timezone offset rather than
  mislabelling it `Z`. Silently wrong-by-hours timing is a real
  scheduling error.
- `RGS` is purely structural and is not required; `AIS`/`AIG`/`AIL`/`AIP`
  are read directly.
- **AIP/AIL/AIG participants are materialized as real resources**, and
  each actor `Reference` carries both `reference` and `display`. `AIG`
  has no single fixed target — `AIG-4`'s resource-type code decides
  between `Location` and `Device`, and AIG-3/4 is CWE-shaped, *not* PL,
  so it must not be routed through the PL-scoped helper.
- **R4's `app-3` requires `start` and `end` unless the status is
  `proposed`, `cancelled` or `waitlist`.** An S17 delete maps to
  `entered-in-error`, so an untimed S17 converts to an invalid
  Appointment and nothing can infer the values.
  `siu.appointment-timing-required-for-status` flags it.
- `Appointment.comment` concatenates every `NTE`. Known limitation: every
  note is treated as appointment-scoped, since `Appointment.comment` is
  one field with nowhere to record which segment each note trailed.

## HL7 ORU/OBR/OBX facts worth remembering

- **OBR/OBX grouping is load-bearing.** `DiagnosticReport.result` must
  reference only the Observations from its own group — use
  `group_segments_by_leader(message, "OBR", ["OBX"])`, not a flat scan.
- **OBX-2 drives which `value[x]` is populated**: `NM` → `valueQuantity`
  (OBX-6 as unit), `ST`/`FT`/`TX` → `valueString`, `CE`/`CWE`/`CNE`/`IS`
  → `valueCodeableConcept`, `DT`/`DTM` → `valueDateTime`. Others are left
  unset rather than guessed.
- OBX-11 / OBR-25 → status share the core table 0085/0123 codes;
  unmapped codes fall back to `"unknown"` rather than raising.
- **OBX-16 performers are deduped within a message** by XCN id, so an
  N-result panel verified by one physician yields one `Practitioner`.
  Only record provenance on a cache *miss*.
- `Encounter.status` for ORU's context encounter is honestly `"unknown"`
  — a result-reporting PV1 carries no lifecycle signal.

## HL7 MDM/TXA facts worth remembering

- The v2-to-FHIR IG ships exactly one MDM ConceptMap (`MDM_T02` →
  `Bundle`) and treats the TXA map as trigger-agnostic, which is why
  `BaseMdmMapper` is not trigger-polymorphic.
- TXA-19 is conditional: `"AV"` → `status = "current"`; `"CA"`/`"OB"`/
  `"UN"` put the sender's own code on `status__ext` as an alternate-codes
  extension while status stays the inferred `"current"`.
- **TXA-17 stays unmapped.** The IG names `docStatus` but publishes no
  ConceptMap from its table 0271 codes to the four `composition-status`
  values. The one candidate in its codesystems directory,
  "CompletionStatus", is table 0322 (medication administration) —
  checked, not assumed from the name.
- **Document content comes from `OBX` segments following `TXA`**, joined
  into one plaintext body on a separate `Binary`. TXA-3 → MIME type has
  no IG crosswalk; the table in `app/mappings/mdm.py` is a disclosed
  local judgment call.
- TXA-9 and TXA-10 are deduped when they name the same person, but the
  `authenticator.display` fact must still record TXA-10 as its source.

## Testing

`pytest` runs everything except the `network`-marked table checks.

**Fixtures** (`tests/fixtures/`) are synthetic and non-PHI. Build a new
HL7v2 or X12 one by constructing positional field lists and joining, not
by hand-typing delimiters — miscounting a field position by hand is easy
and has happened. Parse it back and assert the fields you rely on before
saving. Fixtures must be *conformant*: several dozen were not, and the
converter was being tested against input no real system produces.

**Three complementary layers**, and each catches things the others cannot:

1. **Fixture tests** — exact field assertions against known values.
2. **Generator fuzz tests** — every generator round-trips through the
   real converter across fixed seed ranges, and every documented
   optional field is proven to occur both present *and* absent. This is
   what catches a `maybe()` accidentally hardcoded.
3. **Standing invariants** — properties no per-slice test asserts:
   - `test_reverse_transform_preserves_resource_type_counts` — the
     resource-type multiset survives a round trip, across all 39 targets.
     Per-slice tests assert on named fields of resources they assume
     exist; none notices a resource vanishing. It caught a real
     Specimen-loss bug the whole suite was green through.
   - `test_every_drop_carries_a_checked_verdict` / `..._is_an_unclosed_gap`
     — the register stays honest.
   - `tests/test_fhir_conformance.py` — every generated message and every
     hand-written fixture converts to conformant FHIR R4.

**Validation-rule tests build their own small inline documents** rather
than reusing generator output — a generator's random values are not a
reliable way to hit one specific rule condition on demand.

**Anything UI-facing gets driven in a real browser** before it is called
done. See the discipline note above.
