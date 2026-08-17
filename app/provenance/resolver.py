"""Resolves a `ProvenanceRecorder`'s raw, resource-id-relative facts into
fully Bundle-qualified `ProvenanceEntry` objects - the second half of the
two-phase design `recorder.py`'s own docstring explains. Run exactly once,
immediately after the Bundle that was being built while the recorder
accumulated facts is fully assembled."""

from fhir.resources.R4B.bundle import Bundle

from app.provenance.models import ProvenanceEntry
from app.provenance.recorder import ProvenanceRecorder


def resolve_bundle_paths(bundle: Bundle, recorder: ProvenanceRecorder) -> list[ProvenanceEntry]:
    """Walk `bundle.entry` once to map each resource's `.id` to its own
    `Bundle.entry[N]` index, then translate every raw fact into a fully
    qualified `fhir_path`.

    Bundle-level fields (e.g. `Bundle.identifier`/`Bundle.timestamp`,
    recorded against `bundle.id` itself rather than any entry's resource,
    since MSH-derived metadata lives on the Bundle - see
    `app/mappings/common.py::assemble_bundle`) resolve to `f"Bundle.
    {relative_path}"` with no `entry[N].resource.` prefix, since the Bundle
    is not one of its own entries.

    A fact recorded against a `resource_id` that isn't `bundle.id` and
    isn't found in `bundle.entry` either is skipped, not raised - a
    resource a mapper built but ultimately didn't include in the Bundle
    (a real, if currently hypothetical, possibility for a future mapper)
    should never crash provenance resolution for every other, real fact."""
    index_by_id = {entry.resource.id: i for i, entry in enumerate(bundle.entry or []) if entry.resource is not None}

    entries: list[ProvenanceEntry] = []
    for fact in recorder.facts:
        if fact.resource_id == bundle.id:
            fhir_path = f"Bundle.{fact.relative_path}"
        else:
            index = index_by_id.get(fact.resource_id)
            if index is None:
                continue
            fhir_path = f"Bundle.entry[{index}].resource.{fact.relative_path}"

        entries.append(
            ProvenanceEntry(
                source_format=recorder.source_format,
                fhir_path=fhir_path,
                derivation=fact.derivation,
                source_location=fact.source_location,
                reason=fact.reason,
                source_value=fact.source_value,
                value=fact.value,
            )
        )
    return entries
