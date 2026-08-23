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
from fhir.resources.R4B.reference import Reference

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_quantity_from_pq,
    effective_time_location,
    parse_partial_ts,
    record_coding,
    record_quantity,
)
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds
from app.provenance.location import xpath_location

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

# No wrapping Act - the entry element itself, same shape as Medications'
# own bare substanceAdministration (unlike Problems'/Allergies' own
# Concern-Act-wrapped entries).
_ENTRY_BASE = "substanceAdministration"


def _resolve_status(substance_administration) -> str:
    if substance_administration.get("negationInd") == "true":
        return "not-done"
    status_element = find_child(substance_administration, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _build_immunization(substance_administration, patient_id: str, recorder=None) -> Immunization | None:
    consumable = find_child(substance_administration, "consumable")
    manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
    manufactured_material = (
        find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
    )
    code_element = find_child(manufactured_material, "code") if manufactured_material is not None else None
    vaccine_code = build_codeable_concept_from_cd(code_element)
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
    effective_time = find_child(substance_administration, "effectiveTime")
    occurrence, _ = ivl_ts_bounds(effective_time)
    occurrence_dt = parse_partial_ts(occurrence)
    occurrence_kwargs = {"occurrenceDateTime": occurrence_dt} if occurrence_dt else {"occurrenceString": "Unknown"}

    immunization_id = str(uuid.uuid4())
    status = _resolve_status(substance_administration)
    immunization = Immunization(
        id=immunization_id,
        status=status,
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        vaccineCode=vaccine_code,
        **occurrence_kwargs,
    )

    if recorder:
        code_location = xpath_location(_ENTRY_BASE, "consumable", "manufacturedProduct", "manufacturedMaterial", "code")
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(immunization_id, "vaccineCode.coding[0].code", f"{code_location}/@code", code_value)
        if display_value:
            recorder.record(immunization_id, "vaccineCode.coding[0].display", f"{code_location}/@displayName", display_value)

        if substance_administration.get("negationInd") == "true":
            recorder.record(immunization_id, "status", xpath_location(_ENTRY_BASE, "@negationInd"), status)
        else:
            status_element = find_child(substance_administration, "statusCode")
            status_code = status_element.get("code") if status_element is not None else None
            if status_code and status_code.strip().lower() in STATUS_MAP:
                recorder.record(immunization_id, "status", xpath_location(_ENTRY_BASE, "statusCode", "@code"), status)
            else:
                recorder.record_inferred(
                    immunization_id,
                    "status",
                    f"statusCode was absent or not one of the recognized CF_ImmunizationStatus codes, and negationInd wasn't \"true\" - defaults to the disclosed fallback \"{_DEFAULT_STATUS}\".",
                    status,
                )

        if occurrence_dt:
            recorder.record(
                immunization_id,
                "occurrenceDateTime",
                effective_time_location(xpath_location(_ENTRY_BASE, "effectiveTime"), effective_time, "low"),
                occurrence_dt,
            )
        else:
            recorder.record_inferred(
                immunization_id,
                "occurrenceString",
                "effectiveTime was absent or unparseable - occurrenceString falls back to FHIR's own conventional \"Unknown\" representation rather than fabricating a fake timestamp.",
                "Unknown",
            )

    if manufactured_material is not None:
        lot_element = find_child(manufactured_material, "lotNumberText")
        if lot_element is not None and lot_element.text and lot_element.text.strip():
            immunization.lotNumber = lot_element.text.strip()
            if recorder:
                recorder.record(
                    immunization_id,
                    "lotNumber",
                    xpath_location(_ENTRY_BASE, "consumable", "manufacturedProduct", "manufacturedMaterial", "lotNumberText"),
                    immunization.lotNumber,
                )

    route_element = find_child(substance_administration, "routeCode")
    route = build_codeable_concept_from_cd(route_element)
    if route:
        immunization.route = route
        record_coding(recorder, immunization_id, "route", xpath_location(_ENTRY_BASE, "routeCode"), route)

    dose_quantity_element = find_child(substance_administration, "doseQuantity")
    dose_quantity = build_quantity_from_pq(dose_quantity_element)
    if dose_quantity:
        immunization.doseQuantity = dose_quantity
        record_quantity(
            recorder, immunization_id, "doseQuantity", xpath_location(_ENTRY_BASE, "doseQuantity"), dose_quantity
        )

    return immunization


def build_immunizations(section, patient_id: str, recorder=None) -> list[Immunization]:
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
        immunization = _build_immunization(substance_administration, patient_id, recorder=recorder)
        if immunization is not None:
            immunizations.append(immunization)
    return immunizations
