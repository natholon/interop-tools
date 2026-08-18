"""Data models for the Data Specification pillar - README's sixth named
capability, field-level provenance tracking from an actual conversion
instance (not static reference documentation, and not FHIR's own
resource-level `Provenance`, which this app has never built anywhere and
which has no established field-granular extension pattern to lean on).

Deliberately a bespoke report shape, the same "this app already has its own
bespoke report for this pillar" precedent `app/validation/models.py`'s
`ValidationReport` already set for validation rather than trying to shoehorn
findings into a FHIR `OperationOutcome`."""

from typing import Literal

from pydantic import BaseModel

SourceFormat = Literal["HL7v2", "CDA", "EDI"]
Derivation = Literal["direct", "inferred"]


class ProvenanceEntry(BaseModel):
    """One fact: a FHIR field either came directly from a specific source
    field/element, or was derived/inferred by the mapper itself (a trigger-
    event-driven status, a fixed literal, internal UUID wiring) with no
    single source field to point at. `direction="inferred"` entries have no
    `source_location` (there isn't one) - `reason` explains where the value
    actually came from instead, so the crosswalk stays a complete picture
    of the resource rather than silently only showing the directly-copied
    parts. `field_label` is a human-readable name for `source_location`
    (e.g. "Assigning Authority" for `"PID-3[0].4"`) - populated for HL7v2
    entries via `app/provenance/hl7_field_names.py`, `None` for CDA/EDI
    entries or any location string outside that module's own scoped
    table, matching this app's own "map what's confirmed, disclose the
    rest as absent" precedent rather than guessing at a name."""

    source_format: SourceFormat
    fhir_path: str
    derivation: Derivation = "direct"
    source_location: str | None = None
    field_label: str | None = None
    reason: str | None = None
    source_value: str | None = None
    value: str | None = None


class CrosswalkReport(BaseModel):
    """Mirrors `ValidationReport`'s own top-level shape (echo back type/
    trigger, then a flat list) for consistency across this app's two
    "runs alongside conversion, produces a report rather than a Bundle"
    pillars. `unsupported`/`unsupported_reason` cover both "this format
    has no field-level instrumentation at all yet" (CDA/EDI, Phase 0) and
    "this specific message type isn't instrumented yet" (HL7v2 types
    other than ADT, Phase 0) - the same disclosed-limitation precedent
    every other pillar in this app uses rather than crashing or silently
    returning an unexplained empty list."""

    message_type: str | None = None
    trigger_event: str | None = None
    source_format: SourceFormat | None = None
    entries: list[ProvenanceEntry] = []
    unsupported: bool = False
    unsupported_reason: str | None = None
