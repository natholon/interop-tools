"""R4's own root constraints, for the resource types this app builds.

Each rule quotes the FHIRPath expression it implements verbatim, from
`https://hl7.org/fhir/R4/<resource>.profile.json`. They are hand-written
rather than evaluated by a FHIRPath engine - the same reason this repo
parses XML with stdlib ElementTree - so
`tests/test_fhir_conformance.py::test_every_invariant_still_matches_its_published_expression`
asserts each quoted expression is still what the spec publishes. If HL7
changes one, that test fails and the rule gets re-read rather than
silently drifting.

**Nine of R4's 28 root constraints are vacuous for this app's output and
are not implemented**, each for a structural reason rather than a
judgement call:

- `dom-2`, `dom-3`, `dom-4`, `dom-5` all constrain `contained`, and this
  app never builds a contained resource - every resource is its own
  Bundle entry (see `app/mappings/common.py::assemble_bundle`).
- `bdl-1`, `bdl-2`, `bdl-3`, `bdl-4`, `bdl-12` all key off `Bundle.type`
  being `searchset`, `history`, `batch*`, `transaction*` or `message`.
  This app emits only `collection` and `document`.

`dom-6` ("a resource should have narrative") is `warning` severity in the
spec and is implemented as one, since this app deliberately builds no
narrative for the resources it converts.
"""

from dataclasses import dataclass

CONDITION_CLINICAL_SYSTEM = "http://terminology.hl7.org/CodeSystem/condition-clinical"
CONDITION_VER_SYSTEM = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
ALLERGY_VER_SYSTEM = "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification"


@dataclass(frozen=True)
class Invariant:
    key: str
    severity: str
    human: str
    expression: str


def _codings(concept):
    return list(getattr(concept, "coding", None) or []) if concept is not None else []


def _has_code(concept, system: str, codes: set) -> bool:
    return any(c.system == system and c.code in codes for c in _codings(concept))


def _choice_present(resource, prefix: str) -> bool:
    """Whether any element of a `foo[x]` choice is set."""
    return any(
        getattr(resource, name, None) is not None
        for name in type(resource).model_fields
        if name.startswith(prefix) and not name.endswith("__ext") and name != prefix
    )


def _app_2(r):
    return (r.start is not None) == (r.end is not None)


def _app_3(r):
    return (r.start is not None and r.end is not None) or r.status in (
        "proposed",
        "cancelled",
        "waitlist",
    )


def _app_4(r):
    return getattr(r, "cancelationReason", None) is None or r.status in ("no-show", "cancelled")


def _bdl_7(b):
    urls = [e.fullUrl for e in (b.entry or []) if e.fullUrl]
    return len(urls) == len(set(urls))


def _bdl_9(b):
    return b.type != "document" or bool(
        b.identifier is not None and b.identifier.system and b.identifier.value
    )


def _bdl_10(b):
    return b.type != "document" or b.timestamp is not None


def _bdl_11(b):
    if b.type != "document":
        return True
    return bool(b.entry) and b.entry[0].resource.get_resource_type() == "Composition"


def _con_3(r):
    if r.clinicalStatus is not None:
        return True
    if _has_code(r.verificationStatus, CONDITION_VER_SYSTEM, {"entered-in-error"}):
        return True
    # ".category.select($this='problem-list-item').empty()" - the rule only
    # binds for a problem-list item.
    return not any(
        coding.code == "problem-list-item"
        for concept in (r.category or [])
        for coding in _codings(concept)
    )


def _con_4(r):
    return not _choice_present(r, "abatement") or _has_code(
        r.clinicalStatus, CONDITION_CLINICAL_SYSTEM, {"resolved", "remission", "inactive"}
    )


def _con_5(r):
    return (
        not _has_code(r.verificationStatus, CONDITION_VER_SYSTEM, {"entered-in-error"})
        or r.clinicalStatus is None
    )


def _ait_1(r):
    return (
        _has_code(r.verificationStatus, ALLERGY_VER_SYSTEM, {"entered-in-error"})
        or r.clinicalStatus is not None
    )


def _ait_2(r):
    return (
        not _has_code(r.verificationStatus, ALLERGY_VER_SYSTEM, {"entered-in-error"})
        or r.clinicalStatus is None
    )


def _fhs_1(r):
    return not (_choice_present(r, "age") and _choice_present(r, "born"))


def _fhs_2(r):
    return _choice_present(r, "age") or r.estimatedAge is None


def _obs_6(r):
    return r.dataAbsentReason is None or not _choice_present(r, "value")


def _obs_7(r):
    if not _choice_present(r, "value"):
        return True
    resource_codings = _codings(r.code)
    return not any(
        any(
            component_coding.system == resource_coding.system
            and component_coding.code == resource_coding.code
            for component_coding in _codings(component.code)
            for resource_coding in resource_codings
        )
        for component in (r.component or [])
    )


def _org_1(r):
    return bool(r.identifier) or bool(r.name)


def _inv_1(r):
    return r.lastModified is None or r.authoredOn is None or r.lastModified >= r.authoredOn


def _dom_6(r):
    text = getattr(r, "text", None)
    return text is not None and bool(getattr(text, "div", None))


# (Invariant, the predicate that holds when the resource satisfies it).
INVARIANTS: dict = {
    "Appointment": (
        (
            Invariant(
                "app-2", "error",
                "Either start and end are specified, or neither",
                "start.exists() = end.exists()",
            ),
            _app_2,
        ),
        (
            Invariant(
                "app-3", "error",
                "Only proposed or cancelled appointments can be missing start/end dates",
                "(start.exists() and end.exists()) or (status in ('proposed' | 'cancelled' | 'waitlist'))",
            ),
            _app_3,
        ),
        (
            Invariant(
                "app-4", "error",
                "Cancelation reason is only used for appointments that have been cancelled, or no-show",
                "Appointment.cancelationReason.exists() implies (Appointment.status='no-show' or Appointment.status='cancelled')",
            ),
            _app_4,
        ),
    ),
    "Bundle": (
        (
            Invariant(
                "bdl-7", "error",
                "FullUrl must be unique in a bundle, or else entries with the same fullUrl must have different meta.versionId (except in history bundles)",
                "(type = 'history') or entry.where(fullUrl.exists()).select(fullUrl&resource.meta.versionId).isDistinct()",
            ),
            _bdl_7,
        ),
        (
            Invariant(
                "bdl-9", "error",
                "A document must have an identifier with a system and a value",
                "type = 'document' implies (identifier.system.exists() and identifier.value.exists())",
            ),
            _bdl_9,
        ),
        (
            Invariant(
                "bdl-10", "error",
                "A document must have a date",
                "type = 'document' implies (timestamp.hasValue())",
            ),
            _bdl_10,
        ),
        (
            Invariant(
                "bdl-11", "error",
                "A document must have a Composition as the first resource",
                "type = 'document' implies entry.first().resource.is(Composition)",
            ),
            _bdl_11,
        ),
    ),
    "Condition": (
        (
            Invariant(
                "con-3", "warning",
                "Condition.clinicalStatus SHALL be present if verificationStatus is not entered-in-error and category is problem-list-item",
                "clinicalStatus.exists() or verificationStatus.coding.where(system='http://terminology.hl7.org/CodeSystem/condition-ver-status' and code = 'entered-in-error').exists() or category.select($this='problem-list-item').empty()",
            ),
            _con_3,
        ),
        (
            Invariant(
                "con-4", "error",
                "If condition is abated, then clinicalStatus must be either inactive, resolved, or remission",
                "abatement.empty() or clinicalStatus.coding.where(system='http://terminology.hl7.org/CodeSystem/condition-clinical' and (code='resolved' or code='remission' or code='inactive')).exists()",
            ),
            _con_4,
        ),
        (
            Invariant(
                "con-5", "error",
                "Condition.clinicalStatus SHALL NOT be present if verification Status is entered-in-error",
                "verificationStatus.coding.where(system='http://terminology.hl7.org/CodeSystem/condition-ver-status' and code='entered-in-error').empty() or clinicalStatus.empty()",
            ),
            _con_5,
        ),
    ),
    "AllergyIntolerance": (
        (
            Invariant(
                "ait-1", "error",
                "AllergyIntolerance.clinicalStatus SHALL be present if verificationStatus is not entered-in-error.",
                "verificationStatus.coding.where(system = 'http://terminology.hl7.org/CodeSystem/allergyintolerance-verification' and code = 'entered-in-error').exists() or clinicalStatus.exists()",
            ),
            _ait_1,
        ),
        (
            Invariant(
                "ait-2", "error",
                "AllergyIntolerance.clinicalStatus SHALL NOT be present if verification Status is entered-in-error",
                "verificationStatus.coding.where(system = 'http://terminology.hl7.org/CodeSystem/allergyintolerance-verification' and code = 'entered-in-error').empty() or clinicalStatus.empty()",
            ),
            _ait_2,
        ),
    ),
    "FamilyMemberHistory": (
        (
            Invariant(
                "fhs-1", "error",
                "Can have age[x] or born[x], but not both",
                "age.empty() or born.empty()",
            ),
            _fhs_1,
        ),
        (
            Invariant(
                "fhs-2", "error",
                "Can only have estimatedAge if age[x] is present",
                "age.exists() or estimatedAge.empty()",
            ),
            _fhs_2,
        ),
    ),
    "Observation": (
        (
            Invariant(
                "obs-6", "error",
                "dataAbsentReason SHALL only be present if Observation.value[x] is not present",
                "dataAbsentReason.empty() or value.empty()",
            ),
            _obs_6,
        ),
        (
            Invariant(
                "obs-7", "error",
                "If Observation.code is the same as an Observation.component.code then the value element associated with the code SHALL NOT be present",
                "value.empty() or component.code.where(coding.intersect(%resource.code.coding).exists()).empty()",
            ),
            _obs_7,
        ),
    ),
    "Organization": (
        (
            Invariant(
                "org-1", "error",
                "The organization SHALL at least have a name or an identifier, and possibly more than one",
                "(identifier.count() + name.count()) > 0",
            ),
            _org_1,
        ),
    ),
    "Task": (
        (
            Invariant(
                "inv-1", "error",
                "Last modified date must be greater than or equal to authored-on date.",
                "lastModified.exists().not() or authoredOn.exists().not() or lastModified >= authoredOn",
            ),
            _inv_1,
        ),
    ),
}

# R4 attaches this to DomainResource, so it applies to every resource
# rather than to one type.
DOM_6 = (
    Invariant(
        "dom-6", "warning",
        "A resource should have narrative for robust management",
        "text.`div`.exists()",
    ),
    _dom_6,
)
