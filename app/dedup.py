"""Bundle-level resource deduplication - the third pillar README's own
long-term goal names alongside transformation and validation, and (unlike
those two) the first one that operates on the *output* FHIR Bundle rather
than the source message: every existing pipeline in this app converts
exactly one input message/document to exactly one Bundle, so there is no
batch of separate messages to merge across - what this module targets
instead is a real, narrower gap that already exists *within* a single
converted Bundle: the same real-world entity independently materialized
more than once by different fields of the same source message.

**A genuinely real case, not a hypothetical one**: X12 837P/837I commonly
carry a Billing Provider (2010AA) and a Rendering/Attending Provider
(2310B) that are the exact same NPI - the common real-world shape for a
solo practitioner who bills under their own name. `app/edi/claim_837p.py`/
`claim_837i.py` materialize each independently (they're read from different
loops, with no cross-loop awareness), so today's output carries two
`Practitioner`/`Organization` resources for one real person/entity. This
module is a deliberate, **opt-in** (never automatic) post-processing pass a
caller runs against an already-built Bundle to collapse exactly this kind
of duplication - opt-in because a Bundle with "duplicate" resources isn't
wrong, and forcing a merge by default on every conversion would be a
correctness-affecting behavior change to output this app's own test suite
already depends on staying byte-for-byte stable.

**Scope, deliberately narrow and disclosed**: only four "identity"
resource types are deduplicated this slice - `Patient`, `Practitioner`,
`Organization`, `Location` - the resource types this app's mappers use to
represent a stable real-world entity that other resources *reference*,
never a resource that represents an *event* (`Encounter`, `Observation`,
`Condition`, `Procedure`, `MedicationRequest`, `AllergyIntolerance`,
`Immunization`, `DiagnosticReport`, `Appointment`, `DocumentReference`,
`Claim`, `ClaimResponse`, `Coverage`, `CoverageEligibilityRequest`/
`Response`, `PaymentReconciliation`, `Task`, `Binary`). Two clinically
identical-looking events (same code, same-ish timing) are not necessarily
the same real occurrence - merging them would be a lossy, potentially
incorrect assumption this module deliberately never makes. `Device` is
also excluded: this app's own `Device` resources represent scheduling/
resource-booking context (see `app/mappings/siu.py`'s AIG handling), not a
stable entity identity worth merging. A future slice could extend the
identity-type list if a real, evidenced case surfaces (the same "extend
once a real gap is found" discipline `app/cda/procedures.py`'s own
"entries optional" fix already established), but four is the real,
evidenced set today.

**Identity key, per resource**: if the resource carries a non-empty
`identifier` list, the key is the frozenset of its (system, value) pairs -
the strongest, most reliable signal, since two resources sharing even one
real identifier are the same entity by construction. Otherwise, a
resource-type-specific name-only fallback: `Patient`/`Practitioner` (whose
`.name` is `list[HumanName]`) key off the first name's
`(family, tuple(given))`; `Organization`/`Location` (whose `.name` is a
bare `str`) key off that string, case-folded and whitespace-trimmed. A
resource with neither a resolvable identifier nor a resolvable name gets no
identity key at all and is never merged with anything - not enough signal
to safely guess, the same "don't guess, disclose the gap" discipline this
app applies everywhere else (e.g. Results' unmapped `IVL_PQ`/`ED` value
shapes)."""

from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.reference import Reference
from pydantic import BaseModel

_IDENTITY_RESOURCE_TYPES = {"Patient", "Practitioner", "Organization", "Location"}
# Resource types whose .name is a bare string (Organization, Location) vs.
# a list[HumanName] (Patient, Practitioner) - the one place this module
# needs resource-type-specific knowledge, since the two shapes need
# genuinely different key-extraction logic.
_STRING_NAME_TYPES = {"Organization", "Location"}


@dataclass(frozen=True)
class ResourceMerge:
    resource_type: str
    kept_id: str
    removed_ids: tuple[str, ...]


@dataclass(frozen=True)
class DedupResult:
    bundle: Bundle
    merges: tuple[ResourceMerge, ...]

    @property
    def merged_count(self) -> int:
        return sum(len(merge.removed_ids) for merge in self.merges)


def _identifier_key(resource) -> frozenset | None:
    identifiers = getattr(resource, "identifier", None)
    if not identifiers:
        return None
    pairs = {(identifier.system, identifier.value) for identifier in identifiers if identifier.value}
    return frozenset(pairs) if pairs else None


def _name_key(resource, resource_type: str) -> object | None:
    if resource_type in _STRING_NAME_TYPES:
        name = getattr(resource, "name", None)
        return name.strip().casefold() if name and name.strip() else None

    names = getattr(resource, "name", None)
    if not names:
        return None
    first = names[0]
    family = (first.family or "").strip().casefold()
    given = tuple(g.strip().casefold() for g in (first.given or []) if g and g.strip())
    return (family, given) if (family or given) else None


def _identity_key(resource, resource_type: str) -> tuple | None:
    """(kind, key) so an identifier-keyed match never collides with a
    name-keyed one even if the underlying values happened to coincide."""
    identifier_key = _identifier_key(resource)
    if identifier_key is not None:
        return ("identifier", identifier_key)
    name_key = _name_key(resource, resource_type)
    return ("name", name_key) if name_key is not None else None


def _rewrite_references(node, uuid_remap: dict[str, str]) -> None:
    """Generic recursive walk over any fhir.resources.R4B model instance,
    rewriting every Reference.reference that points at a removed
    duplicate's urn:uuid to the canonical resource's urn:uuid instead. Not
    resource-type-specific - a Reference can appear at any depth in any
    resource this app builds (Encounter.subject, Appointment.participant[]
    .actor, Claim.careTeam[].provider, ...), and this walk finds all of
    them the same way regardless of which field they're on, rather than
    needing a maintained per-resource-type field list."""
    if isinstance(node, Reference):
        if node.reference and node.reference.startswith("urn:uuid:"):
            removed_id = node.reference[len("urn:uuid:") :]
            canonical_id = uuid_remap.get(removed_id)
            if canonical_id:
                node.reference = f"urn:uuid:{canonical_id}"
        return
    if isinstance(node, BaseModel):
        for field_name in type(node).model_fields:
            _rewrite_references(getattr(node, field_name), uuid_remap)
        return
    if isinstance(node, list):
        for item in node:
            _rewrite_references(item, uuid_remap)


def deduplicate_bundle(bundle: Bundle) -> DedupResult:
    """Merge duplicate Patient/Practitioner/Organization/Location entries
    within `bundle` (see module docstring for the identity-matching rules
    and why only these four types are in scope), rewriting every
    surviving resource's own References to point at the kept, canonical
    resource. Returns a new Bundle (the input is not mutated) plus a
    record of what was merged, for a caller to report back to the user.
    Entry order among surviving resources is preserved from the original
    Bundle - only removed duplicates' entries drop out."""
    groups: dict[tuple[str, tuple], list] = {}
    for entry in bundle.entry or []:
        resource_type = entry.resource.get_resource_type()
        key = _identity_key(entry.resource, resource_type) if resource_type in _IDENTITY_RESOURCE_TYPES else None
        if key is not None:
            groups.setdefault((resource_type, key), []).append(entry)

    uuid_remap: dict[str, str] = {}
    merges: list[ResourceMerge] = []
    for (resource_type, _key), group_entries in groups.items():
        duplicates = group_entries[1:]
        if not duplicates:
            continue
        canonical_id = group_entries[0].resource.id
        removed_ids = tuple(entry.resource.id for entry in duplicates)
        for removed_id in removed_ids:
            uuid_remap[removed_id] = canonical_id
        merges.append(ResourceMerge(resource_type=resource_type, kept_id=canonical_id, removed_ids=removed_ids))

    deduplicated_bundle = bundle.model_copy(deep=True)
    deduplicated_bundle.entry = [
        entry for entry in (deduplicated_bundle.entry or []) if entry.resource.id not in uuid_remap
    ]
    if uuid_remap:
        for entry in deduplicated_bundle.entry:
            _rewrite_references(entry.resource, uuid_remap)

    return DedupResult(bundle=deduplicated_bundle, merges=tuple(merges))
