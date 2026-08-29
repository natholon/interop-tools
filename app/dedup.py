"""Bundle-level resource deduplication - README's third named pillar, and the
first that operates on the *output* Bundle rather than a source message.
Every pipeline here converts one input to one Bundle, so there is no batch
to merge across; what this targets is duplication *within* a single Bundle,
where different fields of one message independently materialize the same
real-world entity.

**A real case, not a hypothetical**: 837P/837I commonly carry a Billing
Provider (2010AA) and a Rendering/Attending Provider (2310B) under the same
NPI - the ordinary shape for a solo practitioner billing under their own
name. The two loops are read independently with no cross-loop awareness, so
the output carries two `Practitioner` resources for one person.

**Opt-in, never automatic.** A Bundle with duplicates is not wrong, and
merging by default would change output the test suite depends on.

**Scope**: only the four identity types other resources *reference* -
`Patient`, `Practitioner`, `Organization`, `Location`. Never an event type
(`Encounter`, `Observation`, `Condition`, `Claim`, ...): two clinically
identical-looking events are not necessarily the same occurrence, and
merging them would be a lossy guess. `Device` is excluded too - this app's
Devices represent scheduling/booking context (see `siu.py`'s AIG handling),
not a stable identity.

**Identity key**: *any* shared `(system, value)` identifier pair - two
resources sharing one real identifier are the same entity by construction,
so they merge even when one carries identifiers the other does not. That
is the whole motivating case: an 837's billing loop routinely carries a
tax ID beside the NPI while the rendering loop carries the NPI alone, and
keying on the *set* of identifiers rather than the overlap kept those two
apart. Otherwise a name-only fallback: `Patient`/`Practitioner`
key off the first `HumanName`'s `(family, tuple(given))`,
`Organization`/`Location` off their bare `.name` string, case-folded. A
resource with neither is never merged - not enough signal to guess."""

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


def _group_by_identity(bundle: Bundle) -> dict:
    """Entries grouped by identity, merging on *any* shared identifier
    pair rather than on the whole set being equal.

    An identifier-keyed resource joins the first existing group it shares
    a pair with, and absorbs that group's pairs so a later resource
    matching either one lands in the same place - a small union-find, in
    Bundle order so the first occurrence stays canonical.
    """
    groups: dict[tuple[str, tuple], list] = {}
    # (resource_type, pair) -> the group key that pair already belongs to.
    pair_owner: dict[tuple, tuple] = {}

    for entry in bundle.entry or []:
        resource_type = entry.resource.get_resource_type()
        if resource_type not in _IDENTITY_RESOURCE_TYPES:
            continue
        key = _identity_key(entry.resource, resource_type)
        if key is None:
            continue
        if key[0] != "identifier":
            groups.setdefault((resource_type, key), []).append(entry)
            continue

        pairs = key[1]
        owned = {pair_owner[(resource_type, pair)] for pair in pairs if (resource_type, pair) in pair_owner}
        if owned:
            # Any of them identifies the same entity, so fold them all
            # into the earliest group rather than picking arbitrarily.
            group_key = sorted(owned, key=lambda k: list(groups).index(k))[0]
            for other in owned - {group_key}:
                groups[group_key].extend(groups.pop(other))
                for pair, owner in list(pair_owner.items()):
                    if owner == other:
                        pair_owner[pair] = group_key
        else:
            group_key = (resource_type, key)
            groups.setdefault(group_key, [])
        groups[group_key].append(entry)
        for pair in pairs:
            pair_owner[(resource_type, pair)] = group_key

    return groups


def deduplicate_bundle(bundle: Bundle) -> DedupResult:
    """Merge duplicate Patient/Practitioner/Organization/Location entries
    within `bundle` (see module docstring for the identity-matching rules
    and why only these four types are in scope), rewriting every
    surviving resource's own References to point at the kept, canonical
    resource. Returns a new Bundle (the input is not mutated) plus a
    record of what was merged, for a caller to report back to the user.
    Entry order among surviving resources is preserved from the original
    Bundle - only removed duplicates' entries drop out."""
    groups = _group_by_identity(bundle)

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
