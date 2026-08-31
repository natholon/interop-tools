"""C-CDA entry templates' required elements, and whether an entry has them.

**Source**: each template's own published StructureDefinition on
`hl7.org/cda/us/ccda`, read from its **snapshot** rather than its
differential. That distinction is load-bearing: the differential lists
only what a template *constrains*, so reading it reported
AllergyIntoleranceObservation as requiring nothing at all - every one of
its minimums is inherited from the base CDA `Observation` type and simply
not restated. The snapshot is the resolved view.

Structural attributes (`classCode`, `moodCode`, `typeCode`, `templateId`,
`negationInd`, `nullFlavor`, `inversionInd`) are excluded: they are how
CDA says what an element *is*, not data an entry carries, and a parse that
got this far already read them.

**Scope**: the entry templates this app converts. Two disclosed absences:
the document header is checked separately (see
`app/cda/validation.py::_rule_required_header_elements`), and **Planned
Observation (2.16.840.1.113883.10.20.22.4.44) has no published
StructureDefinition** - it is a C-CDA 2.1 template the current IG's
artifact index does not carry, so no minimum can be read for it.
"""

from app.cda.parser import find_child, has_template_id
from app.validation.models import ValidationFinding

RULE_ID = "cda.entry-missing-required-element"

# templateId -> (human name, the elements its snapshot marks 1..1 or 1..*)
REQUIRED_ELEMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "2.16.840.1.113883.10.20.22.4.3": (
        "Problem Concern Act",
        ("code", "effectiveTime", "entryRelationship", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.4": (
        "Problem Observation",
        ("code", "effectiveTime", "id", "statusCode", "value"),
    ),
    "2.16.840.1.113883.10.20.22.4.16": (
        "Medication Activity",
        ("consumable", "doseQuantity", "effectiveTime", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.30": (
        "Allergy Concern Act",
        ("code", "effectiveTime", "entryRelationship", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.7": (
        "Allergy-Intolerance Observation",
        ("code", "effectiveTime", "id", "statusCode", "value"),
    ),
    "2.16.840.1.113883.10.20.22.4.52": (
        "Immunization Activity",
        ("consumable", "effectiveTime", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.26": (
        "Vital Signs Organizer",
        ("code", "component", "effectiveTime", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.27": (
        "Vital Sign Observation",
        ("code", "effectiveTime", "id", "statusCode", "value"),
    ),
    "2.16.840.1.113883.10.20.22.4.1": (
        "Result Organizer",
        ("code", "component", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.2": (
        "Result Observation",
        ("code", "effectiveTime", "id", "statusCode", "value"),
    ),
    "2.16.840.1.113883.10.20.22.4.14": (
        "Procedure Activity Procedure",
        ("code", "effectiveTime", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.38": (
        "Social History Observation",
        ("code", "effectiveTime", "id", "statusCode"),
    ),
    "2.16.840.1.113883.10.20.22.4.45": (
        "Family History Organizer",
        ("component", "id", "statusCode", "subject"),
    ),
    "2.16.840.1.113883.10.20.22.4.46": (
        "Family History Observation",
        ("code", "id", "statusCode", "value"),
    ),
    "2.16.840.1.113883.10.20.22.4.41": (
        "Planned Procedure",
        ("code", "id", "statusCode"),
    ),
}


def check_required_elements(document) -> list[ValidationFinding]:
    """Findings for every recognized entry in the document that omits one
    of its template's required elements.

    Template-driven rather than section-driven, so an entry is checked
    wherever it appears - the same reason the HL7v2 half walks segments
    rather than message types.

    `error`, because these are the template's own 1..1 and 1..* minimums.
    """
    findings = []
    for element in document.iter():
        for template_id, (label, required) in REQUIRED_ELEMENTS.items():
            if not has_template_id(element, template_id):
                continue
            for name in required:
                if find_child(element, name) is not None:
                    continue
                findings.append(
                    ValidationFinding(
                        severity="error",
                        rule_id=RULE_ID,
                        segment=f"{label}/{name}",
                        message=(
                            f"{label} requires {name}, and this entry does not carry one."
                        ),
                    )
                )
    return findings
