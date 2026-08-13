"""Immunizations section (templateId 2.16.840.1.113883.10.20.22.2.2.1) ->
Immunization, per the official "C-CDA on FHIR" IG's CF-immunizations.html
guidance and its underlying CCDA-FHIR Immunization.csv mapping table
(build.fhir.org/ig/HL7/ccda-on-fhir/, github.com/HL7/ccda-on-fhir).

The IG's own CSV splits Immunization Activity into two target resources by
moodCode: EVN (already administered) -> Immunization, INT (ordered/planned,
not yet given) -> MedicationRequest, each with a mostly-disjoint field set
(protocolApplied/doNotPerform/requester only make sense for the INT/
MedicationRequest side). This module deliberately handles only the EVN ->
Immunization path - the section's own purpose ("a history of vaccinations
given") makes EVN the overwhelmingly dominant real-world case, and building
a second, mostly-parallel INT -> MedicationRequest path would roughly
double this slice's scope for a rarely-populated shape. INT-mood entries
are silently skipped, matching this app's established "cover the dominant
case, disclose the deferred one" precedent (Discharge Summary/H&P document
types, Allergies' full negation crosswalk)."""

import uuid

from fhir.resources.R4B.immunization import Immunization
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as the other section modules.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.2.1"
IMMUNIZATION_ACTIVITY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.52"

# CF_ImmunizationStatus ConceptMap (build.fhir.org/ig/HL7/ccda-on-fhir) -
# CDA ActStatus -> FHIR Immunization.status, for the non-negated case only
# (negationInd="true" maps to "not-done" unconditionally regardless of
# statusCode, per the ConceptMap's own "Only map to not-done when CDA
# negation is present and true" comment on its completed->not-done row -
# every other source status already maps to not-done anyway, so negation
# is checked first, before this table is even consulted).
STATUS_MAP = {
    "completed": "completed",
    "nullified": "entered-in-error",
    "aborted": "not-done",
    "cancelled": "not-done",
    "held": "not-done",
    "new": "not-done",
    "obsolete": "not-done",
    "suspended": "not-done",
}
# Immunization.status has no "unknown" option (unlike most other status
# maps in this app) - only completed | entered-in-error | not-done. An
# unrecognized/absent statusCode (non-negated) defaults to "completed"
# rather than "not-done", since the entry is asserted in EVN (event
# already occurred) mood specifically and "completed" is by far the most
# common real-world value for immunization history - the same "default to
# the most common real value when no unknown option exists" judgment
# already made for MDM's TXA-19 (default "current") and Medications'
# moodCode (default "order").
_DEFAULT_STATUS = "completed"


def _resolve_status(substance_administration) -> str:
    if substance_administration.get("negationInd") == "true":
        return "not-done"
    status_element = find_child(substance_administration, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _resolve_dose_quantity(element) -> Quantity | None:
    """doseQuantity is PQ-shaped (@value/@unit directly on the element) in
    the common case - an IVL_PQ range (low/high children) is left unmapped,
    same "don't guess which bound" rationale as Medications' doseQuantity."""
    if element is None:
        return None
    value = element.get("value")
    if not value:
        return None
    quantity = Quantity(value=value)
    unit = element.get("unit")
    if unit:
        quantity.unit = unit
    return quantity


def _build_immunization(substance_administration, patient_id: str) -> Immunization | None:
    consumable = find_child(substance_administration, "consumable")
    manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
    manufactured_material = (
        find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
    )
    vaccine_code = build_codeable_concept_from_cd(
        find_child(manufactured_material, "code") if manufactured_material is not None else None
    )
    if vaccine_code is None:
        # vaccineCode is FHIR-required - skip the entry rather than
        # construct an invalid resource, matching every other section's
        # "no resolvable code -> skip" convention.
        return None

    # occurrence[x] (occurrenceDateTime | occurrenceString) is a required
    # one-of choice enforced eagerly at construction (same as Encounter.
    # class_fhir - must be a constructor kwarg, not set via attribute
    # assignment afterward). Falls back to occurrenceString = "Unknown"
    # when effectiveTime doesn't resolve, FHIR's own conventional
    # representation for "this vaccine was given, exact date unrecorded"
    # (see e.g. US Core's Immunization guidance), not a local guess.
    occurrence, _ = ivl_ts_bounds(find_child(substance_administration, "effectiveTime"))
    occurrence_dt = parse_partial_ts(occurrence)
    occurrence_kwargs = {"occurrenceDateTime": occurrence_dt} if occurrence_dt else {"occurrenceString": "Unknown"}

    immunization = Immunization(
        id=str(uuid.uuid4()),
        status=_resolve_status(substance_administration),
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        vaccineCode=vaccine_code,
        **occurrence_kwargs,
    )

    if manufactured_material is not None:
        lot_element = find_child(manufactured_material, "lotNumberText")
        if lot_element is not None and lot_element.text and lot_element.text.strip():
            immunization.lotNumber = lot_element.text.strip()

    route = build_codeable_concept_from_cd(find_child(substance_administration, "routeCode"))
    if route:
        immunization.route = route

    dose_quantity = _resolve_dose_quantity(find_child(substance_administration, "doseQuantity"))
    if dose_quantity:
        immunization.doseQuantity = dose_quantity

    return immunization


def build_immunizations(section, patient_id: str) -> list[Immunization]:
    """One Immunization per EVN-mood Immunization Activity entry in the
    section - a section can (and commonly does) have multiple entries.
    INT-mood entries (planned, not yet administered) are silently skipped -
    see module docstring."""
    immunizations = []
    for entry in find_all(section, "entry"):
        substance_administration = find_child(entry, "substanceAdministration")
        if substance_administration is None or not has_template_id(
            substance_administration, IMMUNIZATION_ACTIVITY_TEMPLATE_ID
        ):
            continue
        if substance_administration.get("moodCode") != "EVN":
            continue
        immunization = _build_immunization(substance_administration, patient_id)
        if immunization is not None:
            immunizations.append(immunization)
    return immunizations
