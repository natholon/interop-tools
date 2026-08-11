# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`hl7-tools` is a Python/FastAPI web app for converting HL7v2 messages to FHIR R4 Bundles. The long-term goal (see README.md) covers transformation, validation, deduplication, test-data generation, and mapping across HL7v2/FHIR/CDA/C-CDA. Currently implemented:
- ADT — **A01 Admit, A02 Transfer, A03 Discharge, A04 Register, A08 Update** — each converting to a FHIR Bundle (Patient + Encounter).
- SIU — **S12 New booking, S13 Reschedule, S14 Modify, S15 Cancel** — each converting to a FHIR Bundle (Patient + Appointment).

Remaining trigger events for either message type (ADT A05/A11/A13, SIU S17/S26, ...) can follow the same pattern as their siblings. The next planned message *type* is not yet decided.

## Commands

```powershell
# one-time setup (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# run the app (http://127.0.0.1:8000)
uvicorn app.main:app --reload

# run the full test suite
pytest

# run a single test
pytest tests/test_adt_a03_mapping.py::test_discharge_sets_finished_status_and_period_end -v
```

There is no separate lint/build step configured yet.

## Architecture

**Request flow:** `app/routes/convert.py` (FastAPI routes for `/`, `/convert`, `/api/convert`, `/healthz`) calls the single pipeline entrypoint `app/hl7/pipeline.py::convert_hl7_to_bundle(raw_text) -> Bundle`. That function: parses raw text via `app/hl7/parser.py`, reads MSH-9 to get the message type/trigger event, looks up a mapper in `app/mappings/registry.py`, and calls `mapper.to_bundle(message)`.

**Extension point for new message types:** `app/mappings/base.py` defines the `MessageMapper` interface (`to_bundle(message) -> Bundle`). Each HL7 message *type* gets its own module (`app/mappings/adt.py`, `app/mappings/siu.py`) implementing that interface, registered in `app/mappings/registry.py` as `{(message_type, trigger_event): mapper_instance}` — no changes to routes, the parser, or templates. Cross-message-type shared logic lives in `app/mappings/common.py`: `build_patient(pid) -> Patient` (PID is mapped identically no matter the message type), `assemble_bundle(msh, patient, *resources) -> Bundle` (MSH-derived Bundle metadata + entries, generic over however many additional resources a message type produces), and `location_display(segment, field_num)`/`person_display(segment, field_num)` (PL/XCN-shaped display-string formatting, used by both `adt.py`'s PV1-3/PV1-6/PV1-7 and `siu.py`'s AIL-3/AIP-3 — the same HL7 field shapes recur across segments and message types, so format them once). Generic, reusable *field-level* FHIR-fragment builders (name/address/datetime/gender parsing, `build_codeable_concept_from_cwe` for CWE-typed fields) live in `app/fhir_models/builders.py`, shared across all message types; message-type-specific field logic stays in that type's module.

**Extension point for trigger events within one message type:** both `app/mappings/adt.py` and `app/mappings/siu.py` follow the same shape — a `Base*Mapper(MessageMapper)` whose `to_bundle()` does the parts identical across every trigger event for that message type (require the message type's core segments, read any optional repeating segments, build the Patient via the shared `build_patient`, delegate to an abstract per-trigger build method, assemble the Bundle via the shared `assemble_bundle`), with concrete trigger subclasses implementing only the part that actually varies:
- `app/mappings/adt.py`: `BaseAdtMapper.build_encounter(pv1, evn, patient_id) -> Encounter`. Most subclasses (`AdtA01Mapper`, `AdtA04Mapper`) just call the shared `build_encounter_core(pv1, evn, patient_id, status=...)`; `AdtA02Mapper` additionally layers in PV1-6 prior-location history; `AdtA03Mapper` requires a resolvable discharge time (PV1-45 or EVN-2 fallback) and maps PV1-36 discharge disposition; `AdtA08Mapper` infers status from whether a discharge time is present.
- `app/mappings/siu.py`: `BaseSiuMapper.build_appointment(sch, tq1_segments, nte_segments, ais_segments, aig_segments, ail_segments, aip_segments, patient) -> Appointment`. `SiuS12Mapper`/`SiuS13Mapper`/`SiuS14Mapper` all inherit the private `_BookedSiuMapper`, which resolves timing via `resolve_appointment_timing()` and requires a start time (raises `MappingError` otherwise) — these three are deliberately identical apart from which `trigger_event` string ends up in the `Appointment.extension` marker, since this converter has no persisted state to make a "new booking" vs. "reschedule" vs. "modify" distinguishable in the output. `SiuS15Mapper` (cancel) resolves timing but does not require it.

When adding a new trigger event for an already-supported message type, add a subclass in that type's module rather than a new file — only add a new file for a genuinely different HL7 message type.

**Error taxonomy:** three custom exceptions in `app/hl7/errors.py` — `Hl7ParseError` (unparseable text), `MissingSegmentError` (required segment absent), `MappingError` (no mapper registered for a message type) — plus `pydantic.ValidationError` from FHIR resource construction. `app/routes/convert.py` translates each to an HTTP status + category label, and both the JSON API and the server-rendered form path route through the same `_run_conversion()` helper so error handling only lives in one place. The UI never shows a raw traceback.

### `hl7` library gotcha (important)

The `hl7` package (PyPI: `hl7`, i.e. python-hl7) is used for HL7v2 parsing:
- `hl7.parse()` **always** splits segments on `\r`, regardless of input. `app/hl7/parser.py::parse_message()` normalizes `\n`/`\r\n` input (e.g. from a browser textarea or an uploaded file) to `\r` before calling it, and drops blank lines.
- When a field/repetition has no component separator (`^`), the library collapses it to a bare Python `str` instead of a nested `[component]` list. Naively indexing a second level (`field[0][0]`) on such a field does **character** indexing, not component indexing, and silently returns the wrong (truncated) value. `app/hl7/parser.py::component_str()` guards against this and is the only sanctioned way to pull a component out of a field/repetition — always use it (or `field_str()`, which wraps it) instead of raw indexing when adding a new mapper.

### `fhir.resources` library notes

- Resources are imported from the `R4B` subpackage (`fhir.resources.R4B.*`), not the root package — as of `fhir.resources` v7+, the root package defaults to FHIR R5. R4B did not change `Patient`/`Encounter`/`Bundle`/`Appointment`, so it's a safe practical stand-in for R4 here.
- `Encounter.class` is a required field exposed as the Python attribute `class_fhir` (since `class` is a reserved word) — it must be passed as a constructor kwarg (`Encounter(..., class_fhir=Coding(...))`), not set as an attribute after construction, because required fields are validated eagerly on `__init__`. `Appointment` has no such reserved-word field and can be built incrementally (construct with just `id`/`status`/`participant`/`extension`, then set the rest via attribute assignment).
- Bundles use `Bundle.type = "collection"` (not `"transaction"`) since there's no FHIR-server interaction in this app.
- Before using a resource sub-object you haven't used before (e.g. `EncounterHospitalization`, `AppointmentParticipant`), inspect its real `model_fields` rather than guessing field names/shapes — this was done for `dischargeDisposition` when adding A03 discharge mapping, and for `Appointment`/`AppointmentParticipant` when adding SIU. Also don't assume the library enforces FHIR's own cardinality/binding rules: `fhir.resources.R4B.Appointment` doesn't actually require `status` at construction time (only `participant`) even though the FHIR spec calls status 1..1, and it does **not** validate that status/participant-status strings are real codes from the spec's value sets — getting the *values* right (not just the field names) is entirely on the mapper. Confirm real code values against the FHIR spec pages (e.g. `valueset-appointmentstatus.html`), not memory.

### HL7 SCH/SIU-shaped facts worth remembering

- The `TQ` composite data type (used in the legacy `SCH-11` field) has **component 4 = start date/time, component 5 = end date/time** — not components 1/2 as might be assumed from a generic "timing" field. `TQ1` (the segment that superseded embedding TQ in SCH-11) is much simpler: TQ1-7 = start, TQ1-8 = end, both plain `DTM` fields. `app/mappings/siu.py::resolve_appointment_timing()` prefers TQ1, falls back to SCH-11 — resolved **per-field** (start and end independently), not all-or-nothing: a TQ1 segment that supplies only a start (no end) still lets a usable SCH-11 end value through, rather than leaving `Appointment.end` blank just because TQ1 supplied *something*. Get this wrong (checking `if start or end` instead of resolving each independently) and a partially-populated TQ1 silently blocks a complete SCH-11 fallback — this exact bug shipped once and was caught by code review, not by the original test suite (see `tests/test_siu_s12_mapping.py::test_partial_tq1_falls_back_to_sch11_per_field`).
- `_resolve_minutes_duration` prefers **TQ1-6 over SCH-9/10** (matching `resolve_appointment_timing`'s TQ1-over-SCH11 preference) specifically so a reschedule (S13) with a stale SCH-9 duration but a fresh TQ1-derived start/end doesn't produce a self-contradictory Appointment where `minutesDuration` doesn't match `end - start`. The SCH-10 unit check is lenient (`startswith("MIN")`) rather than an exact match, to tolerate spellings like `"MINUTES"`.
- `parse_hl7_datetime` (`app/fhir_models/builders.py`) preserves a trailing HL7 timezone offset (`+/-ZZZZ`) rather than dropping it and mislabeling the result `Z` (UTC) — silently wrong-by-hours timing is a real scheduling error, not a cosmetic issue, once this helper feeds `Appointment.start`/`end`/`created`.
- `RGS` (Resource Group Segment) is purely structural (sequencing/action-code only, no clinical content) — SIU's `AIS`/`AIG`/`AIL`/`AIP` segments are read directly via `optional_segments()` without tracking which `RGS` group they belong to, since FHIR's `Appointment.participant` is a flat list with no group-nesting concept to preserve anyway.
- `Appointment.participant.type`'s recommended value set (`encounter-participant-type`, Extensible binding) has no code for "patient" or for a location/equipment role — only practitioner-ish roles like `ATND`. Don't force a non-fitting code in just to fill the field; omit `type` when nothing in the binding actually fits (see `app/mappings/siu.py::_build_participants`).
- **Known, disclosed limitation:** `Appointment.comment` is taken from the first `NTE` segment found anywhere in the message (`nte_segments[0]`), with no awareness of which group that NTE trails (an SCH-level note vs. one scoped to a specific AIS/AIG/AIL/AIP occurrence). A message with multiple NTEs in different groups can have the wrong one — or a more relevant later one — end up as the appointment-level comment. Fixing this properly requires positionally tracking which segment each NTE follows (the same kind of group-tracking explicitly scoped *out* for `RGS` above), which is a real scope decision, not a one-line fix — flag it rather than silently "fixing" it partially.

## Testing

`tests/fixtures/*.hl7` are synthetic, non-PHI HL7v2 messages. They were generated by building each segment as a list of positional fields joined with the correct field index (see git history of `tests/fixtures/` if regenerating) rather than hand-typing pipe-delimited strings — it's easy to miscount field positions (e.g. PV1-19 vs PV1-18) by hand. If adding a new fixture, verify field positions by parsing it back with `hl7.parse()` and checking each field you intend to rely on.
