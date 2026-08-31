"""Is the Bundle this app produced conformant FHIR R4?

Every other validator here checks a *source* message. This checks the
*output*, which nothing did - and `fhir.resources` does not fill the gap:
it accepts `Observation(status="banana")` and `Encounter(status="NOT-A-
REAL-STATUS")` without complaint, because it validates structure and
types but neither required bindings nor invariants.

Three layers, all read off the published R4 spec rather than written from
memory (see `tables.py` and `invariants.py` for the provenance of each):

1. **Cardinality** - elements the resource's own snapshot marks `min >= 1`.
2. **Required bindings** - codes that must come from a required value set.
3. **Invariants** - R4's own root constraints, quoting each FHIRPath.

Findings reuse `ValidationFinding`, with `segment` carrying the FHIR path
rather than a segment id - the same reuse `app/cda/validation.py` already
makes for a path-shaped location. That keeps one report shape across the
app, so a caller renders these the way it renders every other finding.
"""

from app.fhir_conformance.invariants import DOM_6, INVARIANTS
from app.fhir_conformance.tables import REQUIRED_BINDINGS, REQUIRED_ELEMENTS
from app.validation.models import ValidationFinding, ValidationReport

CARDINALITY_RULE_ID = "fhir.missing-required-element"
BINDING_RULE_ID = "fhir.code-outside-required-binding"
INVARIANT_RULE_ID = "fhir.invariant-violated"


def check_bundle(bundle, include_narrative_warning: bool = False) -> ValidationReport:
    """Every conformance finding for a Bundle and the resources in it.

    `dom-6` ("a resource should have narrative") is off by default. It is
    a real warning-severity R4 constraint and it is implemented, but this
    app builds no narrative for anything it converts, so it holds for
    every resource of every Bundle - thousands of identical findings that
    say one thing about the converter and nothing about any message.
    Callers wanting the complete spec answer pass True.
    """
    findings: list[ValidationFinding] = []
    findings.extend(_check_resource(bundle, "Bundle", None, include_narrative_warning))
    for index, entry in enumerate(bundle.entry or []):
        resource = entry.resource
        if resource is None:
            continue
        resource_type = resource.get_resource_type()
        path = f"Bundle.entry[{index}].resource"
        findings.extend(
            _check_resource(resource, resource_type, path, include_narrative_warning)
        )
    return ValidationReport(
        message_type="FHIR",
        trigger_event=bundle.type or "",
        is_valid=not any(f.severity == "error" for f in findings),
        findings=findings,
    )


def _check_resource(
    resource, resource_type: str, path: str | None = None, include_narrative_warning: bool = False
) -> list[ValidationFinding]:
    base = path or resource_type
    findings: list[ValidationFinding] = []

    for element in REQUIRED_ELEMENTS.get(resource_type, ()):
        if _is_absent(resource, element):
            findings.append(
                ValidationFinding(
                    severity="error",
                    rule_id=CARDINALITY_RULE_ID,
                    segment=f"{base}.{element}",
                    message=f"{resource_type}.{element} is required by FHIR R4 and is absent.",
                )
            )

    for element, (value_set, codes) in _bindings_for(resource_type):
        value = getattr(resource, model_attribute(resource, element), None)
        for code, code_path in _codes_under(value, f"{base}.{element}"):
            if code in codes:
                continue
            findings.append(
                ValidationFinding(
                    severity="error",
                    rule_id=BINDING_RULE_ID,
                    segment=code_path,
                    message=(
                        f"{code!r} is not in {value_set}, which R4 binds "
                        f"{resource_type}.{element} to at required strength."
                    ),
                )
            )

    invariants = list(INVARIANTS.get(resource_type, ()))
    # dom-6 is on DomainResource, so it applies to every resource - but
    # not to Bundle, which is a Resource rather than a DomainResource and
    # has no narrative of its own.
    if resource_type != "Bundle" and include_narrative_warning:
        invariants.append(DOM_6)
    for invariant, holds in invariants:
        if holds(resource):
            continue
        findings.append(
            ValidationFinding(
                severity=invariant.severity,
                rule_id=INVARIANT_RULE_ID,
                segment=base,
                message=f"{invariant.key}: {invariant.human}",
            )
        )
    return findings


def _bindings_for(resource_type: str):
    prefix = resource_type + "."
    for path, binding in REQUIRED_BINDINGS.items():
        if path.startswith(prefix) and "." not in path[len(prefix):]:
            yield path[len(prefix):], binding


def model_attribute(resource, element: str) -> str:
    """The model's own name for a spec element.

    `fhir.resources` renames an element that collides with a Python
    keyword - `Encounter.class` is `class_fhir`, `Task.for` is
    `for_fhir`. Looking up the spec name directly reports every such
    element as absent, which it never is.
    """
    fields = type(resource).model_fields
    if element in fields:
        return element
    renamed = f"{element}_fhir"
    return renamed if renamed in fields else element


def _is_absent(resource, element: str) -> bool:
    """Whether a required element is missing.

    A choice element is spelled `foo[x]` in the spec and `fooQuantity`,
    `fooString`, ... on the model, so it is present when any of them is.
    """
    if element.endswith("[x]"):
        prefix = element[:-3]
        return not any(
            getattr(resource, name, None) is not None
            for name in type(resource).model_fields
            if name.startswith(prefix) and not name.endswith("__ext")
        )
    value = getattr(resource, model_attribute(resource, element), None)
    return value is None or value == [] or value == ""


def _codes_under(value, path: str):
    """(code, path) for every code a bound element carries.

    A `code`-typed element is one string; a CodeableConcept-typed one has
    a coding list, and R4 binds the whole concept, so each coding is
    checked. Anything else yields nothing rather than guessing at a shape.
    """
    if value is None:
        return
    if isinstance(value, str):
        yield value, path
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _codes_under(item, f"{path}[{index}]")
        return
    codings = getattr(value, "coding", None)
    if codings:
        for index, coding in enumerate(codings):
            if coding.code:
                yield coding.code, f"{path}.coding[{index}].code"
        return
    code = getattr(value, "code", None)
    if isinstance(code, str):
        yield code, f"{path}.code"
