"""`ProvenanceRecorder` - the accumulator every instrumented mapper function
writes to, threaded through as an optional trailing parameter (default
`None`) so every existing, already-tested call path (normal conversion,
`app/validation/engine.py`'s own `_check_convertibility`, generation,
transform) is completely unaffected unless a caller explicitly opts in by
constructing and passing one.

Facts are keyed by `(resource_id, relative_path)` and last-write-wins, not
append-only - a fact represents "the current true source of the field's
current value on the resource being built," not a full write history. This
matters for real, existing mapper logic that writes a field once and then
conditionally corrects it: `app/mappings/adt.py::_drop_evn2_period_start_
fallback` (called by the three cancel-trigger ADT mappers) resets
`Encounter.period.start` after `build_encounter_core` already set - and
would already have recorded - it from a PV1-44-or-EVN-2 fallback, since
EVN-2 means something different on a cancel-trigger message. Re-recording
at the same `relative_path` overwrites the stale fact rather than leaving
two contradictory entries in the crosswalk for one real field. `forget()`
is the same idea for a field that a correction removes entirely (that same
function clears `Encounter.period` altogether when nothing is left in it)."""

from dataclasses import dataclass

from app.provenance.models import Derivation, SourceFormat


@dataclass
class RawProvenanceFact:
    resource_id: str
    relative_path: str
    derivation: Derivation
    source_location: str | None
    reason: str | None
    source_value: str | None
    value: str | None


class ProvenanceRecorder:
    """One instance per `to_bundle()`/`build_bundle()` call. Never mutates
    the resource being built - purely a side-channel accumulator."""

    def __init__(self, source_format: SourceFormat) -> None:
        self.source_format = source_format
        self._facts: dict[tuple[str, str], RawProvenanceFact] = {}

    def record(
        self,
        resource_id: str,
        relative_path: str,
        source_location: str,
        value,
        source_value=None,
    ) -> None:
        """A directly-copied field: read from `source_location` (e.g.
        `"PID-5[0].1"`), optionally transformed, and written to
        `relative_path` (e.g. `"name[0].family"`) on the resource whose id
        is `resource_id`. `value`/`source_value` are stringified here so
        every caller can pass whatever type the field actually is (a
        `date`, a plain `str`, ...) without needing to know this recorder's
        own string convention."""
        self._facts[(resource_id, relative_path)] = RawProvenanceFact(
            resource_id=resource_id,
            relative_path=relative_path,
            derivation="direct",
            source_location=source_location,
            reason=None,
            source_value=None if source_value is None else str(source_value),
            value=None if value is None else str(value),
        )

    def record_inferred(self, resource_id: str, relative_path: str, reason: str, value) -> None:
        """A field with no single source location to point at - a
        trigger-event-driven literal, a value inferred from another
        field's mere *presence* rather than its content, internal UUID
        wiring. `reason` is the human-readable explanation shown in place
        of a source location."""
        self._facts[(resource_id, relative_path)] = RawProvenanceFact(
            resource_id=resource_id,
            relative_path=relative_path,
            derivation="inferred",
            source_location=None,
            reason=reason,
            source_value=None,
            value=None if value is None else str(value),
        )

    def forget(self, resource_id: str, relative_path: str) -> None:
        """Removes a previously-recorded fact - for a field a later
        correction clears entirely (see module docstring). A no-op if
        nothing was recorded at this path, so callers can call this
        defensively without checking first."""
        self._facts.pop((resource_id, relative_path), None)

    def forget_prefix(self, resource_id: str, relative_path_prefix: str) -> None:
        """Removes every previously-recorded fact whose relative_path starts
        with the given prefix - for a compound field (e.g. `period.start`
        AND `period.end`) a later correction clears as a whole."""
        for key in [k for k in self._facts if k[0] == resource_id and k[1].startswith(relative_path_prefix)]:
            del self._facts[key]

    @property
    def facts(self) -> list[RawProvenanceFact]:
        return list(self._facts.values())
