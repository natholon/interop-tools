"""Procedures section (templateId 2.16.840.1.113883.10.20.22.2.7.1) ->
Procedure, per the official "C-CDA on FHIR" IG's CF-procedures.html
guidance and its underlying CCDA-FHIR Procedure.csv mapping table
(build.fhir.org/ig/HL7/ccda-on-fhir/, github.com/HL7/ccda-on-fhir), plus
its own published ConceptMap-CF-ProcedureStatus. Section/entry templateIds
confirmed against a real HL7 C-CDA-Examples CCD document, not assumed from
the IG page's own abbreviated XPath (LOINC code only, not the templateId
OID).

**Template selection, per the IG's own explicit guidance, not this app's
own judgment call**: C-CDA 2.1 defined three Procedure templates
(Procedure Activity Act, Procedure Activity Observation, Procedure
Activity Procedure); the IG maps only the Procedure Activity Procedure
template ("the most complete... most vendors successfully and exclusively
use" it, per the C-CDA 2.1 Companion Guide the IG itself cites), and notes
the Act/Observation variants were removed entirely as of C-CDA 3.0. This
module follows the IG's own scoping exactly - only Procedure Activity
Procedure entries are recognized; a Procedure Activity Observation (which
the IG says is better modeled as a Results-shaped Observation, not a
Procedure) is out of scope, not silently mismapped.

**Disclosed scope limits, decided up front**: `performer`/`participant`
(Service Delivery Location) and `author`->Provenance are deferred - this
app has no CDA-side PractitionerRole/Location/Provenance builder yet, the
same category of gap Medications' own performer/requester omission already
discloses. The Indication (`entryRelationship[RSON]`) and Comment Activity
(`entryRelationship` wrapping a Comment Activity act) cross-references are
also deferred - both require resolving a *nested* entry into a separate
field (reasonCode, note) rather than reading an attribute directly off the
procedure element itself, more complexity than this first pass covers."""

import uuid

from fhir.resources.R4B.period import Period
from fhir.resources.R4B.procedure import Procedure
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, build_identifiers, effective_time_location, parse_partial_ts
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, ts_value
from app.provenance.location import xpath_location

_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-procedure-id"
# No wrapping Act - the entry element itself, same shape as Medications'/
# Immunizations' own bare substanceAdministration.
_ENTRY_BASE = "procedure"

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.7.1"
# The "entries optional" sibling section (no trailing ".1") - a real,
# evidenced gap, not a defensive guess: a real official HL7 History and
# Physical example (HL7/C-CDA-Examples, Documents/History and Physical)
# uses ONLY this templateId for its own Procedures section (no paired
# ".2.7.1" declaration the way its Vital Signs/Results sections both
# carry), which app/cda/vitals.py's/app/cda/results.py's original,
# single-templateId registration would have silently skipped entirely -
# the exact same class of gap this app's Allergies section already shipped
# once (see app/cda/allergies.py's own SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
# docstring) and the C-CDA on FHIR IG's own template list confirms as a
# real, distinct templateId, not a typo in that one example.
SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.7"
PROCEDURE_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.14"

# ConceptMap-CF-ProcedureStatus (github.com/HL7/ccda-on-fhir/blob/master/
# input/maps/ConceptMap-CF-ProcedureStatus.xml, fetched and confirmed
# directly) - CDA ActStatus -> FHIR Procedure.status. "held"/"new"/
# "obsolete"/"suspended" have no row in the published ConceptMap -
# disclosed, not guessed at; an unrecognized or absent statusCode falls
# back to "unknown" (a real Procedure.status code), same fallback
# philosophy as ORU's OBX-11/DiagnosticReport status map and this app's
# own Results section.
STATUS_MAP = {
    "aborted": "stopped",
    "active": "in-progress",
    "cancelled": "not-done",
    "completed": "completed",
}
_DEFAULT_STATUS = "unknown"
_NEGATED_STATUS = "not-done"


def _resolve_status(procedure_element) -> str:
    # negationInd="true" ("this procedure did NOT happen") overrides
    # statusCode unconditionally, per the IG's own first mapping row - the
    # same "check negation before consulting the status table" precedent
    # Immunizations' own negationInd handling already established.
    if procedure_element.get("negationInd") == "true":
        return _NEGATED_STATUS
    status_element = find_child(procedure_element, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _record_status(recorder, procedure_id: str, procedure_element, status: str) -> None:
    if procedure_element.get("negationInd") == "true":
        # A real source field genuinely read (negationInd's own value, not
        # merely its presence) - direct, the identical distinction
        # Immunizations' own negationInd-driven status recording already
        # established.
        recorder.record(procedure_id, "status", xpath_location(_ENTRY_BASE, "@negationInd"), status)
        return
    status_element = find_child(procedure_element, "statusCode")
    code = status_element.get("code") if status_element is not None else None
    if code and code.strip().lower() in STATUS_MAP:
        recorder.record(procedure_id, "status", xpath_location(_ENTRY_BASE, "statusCode", "@code"), status)
    else:
        recorder.record_inferred(
            procedure_id,
            "status",
            f'statusCode was absent or not one of the recognized CF_ProcedureStatus codes, and negationInd wasn\'t "true" - defaults to the disclosed fallback "{_DEFAULT_STATUS}".',
            status,
        )


def _build_procedure(procedure_element, patient_id: str, recorder=None) -> Procedure:
    procedure_id = str(uuid.uuid4())
    status = _resolve_status(procedure_element)
    procedure = Procedure(
        id=procedure_id,
        status=status,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )
    if recorder:
        _record_status(recorder, procedure_id, procedure_element, status)

    code_element = find_child(procedure_element, "code")
    code = build_codeable_concept_from_cd(code_element)
    if code:
        # Procedure.code is genuinely optional per FHIR R4 (confirmed via
        # model_fields, unlike most of this app's coded resources) - a
        # procedure with no resolvable code still gets constructed rather
        # than skipped, since the entry itself (status/date/bodySite) can
        # still be meaningful without one.
        procedure.code = code
        if recorder:
            code_value = code_element.get("code")
            display_value = code_element.get("displayName")
            if code_value:
                recorder.record(procedure_id, "code.coding[0].code", xpath_location(_ENTRY_BASE, "code", "@code"), code_value)
            if display_value:
                recorder.record(
                    procedure_id, "code.coding[0].display", xpath_location(_ENTRY_BASE, "code", "@displayName"), display_value
                )

    ids = build_identifiers(
        find_all(procedure_element, "id"),
        _ID_FALLBACK_SYSTEM,
        resource_id=procedure_id,
        location_prefix=xpath_location(_ENTRY_BASE, "id"),
        recorder=recorder,
    )
    if ids:
        procedure.identifier = ids

    # Constraint per the IG: use performedDateTime when effectiveTime@value
    # is populated (a point-in-time TS), else fall back to performedPeriod
    # from effectiveTime/low+high (an IVL_TS range) - the two are mutually
    # exclusive source shapes, matching how app/cda/common.py::
    # ivl_ts_bounds already distinguishes a bare @value from low/high
    # children for every other section's own effectiveTime handling.
    effective_time = find_child(procedure_element, "effectiveTime")
    point_in_time = parse_partial_ts(ts_value(effective_time)) if effective_time is not None else None
    if point_in_time:
        procedure.performedDateTime = point_in_time
        if recorder:
            recorder.record(procedure_id, "performedDateTime", xpath_location(_ENTRY_BASE, "effectiveTime", "@value"), point_in_time)
    else:
        low, high = ivl_ts_bounds(effective_time)
        period_start = parse_partial_ts(low)
        period_end = parse_partial_ts(high)
        if period_start or period_end:
            period = Period()
            effective_time_base = xpath_location(_ENTRY_BASE, "effectiveTime")
            if period_start:
                period.start = period_start
                if recorder:
                    recorder.record(
                        procedure_id,
                        "performedPeriod.start",
                        effective_time_location(effective_time_base, effective_time, "low"),
                        period_start,
                    )
            if period_end:
                period.end = period_end
                if recorder:
                    recorder.record(
                        procedure_id,
                        "performedPeriod.end",
                        effective_time_location(effective_time_base, effective_time, "high"),
                        period_end,
                    )
            procedure.performedPeriod = period

    body_site_element = find_child(procedure_element, "targetSiteCode")
    body_site = build_codeable_concept_from_cd(body_site_element)
    if body_site:
        procedure.bodySite = [body_site]
        if recorder:
            recorder.record(
                procedure_id, "bodySite[0].coding[0].code", xpath_location(_ENTRY_BASE, "targetSiteCode", "@code"), body_site.coding[0].code
            )

    return procedure


def build_procedures(section, patient_id: str, recorder=None) -> list[Procedure]:
    """One Procedure per Procedure Activity Procedure entry in the section
    - a section can (and commonly does) have multiple entries."""
    procedures = []
    for entry in find_all(section, "entry"):
        procedure_element = find_child(entry, "procedure")
        if procedure_element is None or not has_template_id(procedure_element, PROCEDURE_TEMPLATE_ID):
            continue
        procedures.append(_build_procedure(procedure_element, patient_id, recorder=recorder))
    return procedures
