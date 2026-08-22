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

**`performer`/`participant`** (per the real `Procedure.csv` mapping table,
fetched raw, plus a real fetched HL7 C-CDA-Examples XML - `Encounters/
Inpatient Encounter Discharged to Rehab Location` - which shares the
identical generic `assignedEntity`/`participantRole[classCode=SDLOC]` CDA
datatypes this module reuses verbatim): `performer/assignedEntity` maps to
`Procedure.performer.actor` as a real **PractitionerRole** (not a bare
Practitioner - the CSV's own target is `Procedure.performer.actor
[PractitionerRole]`, confirmed with the user directly as the shape to
build rather than a simpler `Practitioner`+`.onBehalfOf` shortcut),
wrapping a `Practitioner` (from `assignedPerson/name`, genuinely optional
per the real fetched example - a real performer can carry only an `id`
and no name at all) plus an optional `Organization` (from
`representedOrganization/name`) and an optional `Location` (from
`assignedEntity/addr`, referenced via `PractitionerRole.location` - FHIR
has no field for embedding an address directly on `PractitionerRole`
itself). `participant[@typeCode=LOC]/participantRole[@classCode=SDLOC]`
(confirmed real templateId `2.16.840.1.113883.10.20.22.4.32`) maps
directly to `Procedure.location` - a separate `Location`, unrelated to the
performer's own machinery, since `Procedure.location` is a plain
`Reference`, confirmed via `model_fields` introspection.

**Disclosed scope limits, decided up front**: `author`->Provenance is
still deferred - this app has no CDA-side Provenance builder anywhere.
The Indication (`entryRelationship[RSON]`) and Comment Activity
(`entryRelationship` wrapping a Comment Activity act) cross-references are
also deferred - both require resolving a *nested* entry into a separate
field (reasonCode, note) rather than reading an attribute directly off the
procedure element itself, more complexity than this app's own performer/
participant slice covers. No cross-performer/cross-procedure
deduplication of the Practitioner/Organization/Location resources
materialized here is attempted - this app's existing opt-in
`app/dedup.py::deduplicate_bundle` already covers the cross-resource case
generically, the same way it does for X12 837P/837I's own billing-vs-
rendering-provider duplication."""

import uuid

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.practitionerrole import PractitionerRole
from fhir.resources.R4B.procedure import Procedure, ProcedurePerformer
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.cda.common import (
    build_codeable_concept_from_cd,
    build_contact_point_from_telecom,
    build_identifier,
    build_identifiers,
    effective_time_location,
    parse_partial_ts,
)
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, ts_value
from app.provenance.location import xpath_location

_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-procedure-id"
_PRACTITIONER_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-practitioner-id"
_ORGANIZATION_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-organization-id"
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
# Service Delivery Location - the generic CDA participantRole template
# used to confirm a participant[@typeCode=LOC] is genuinely a location
# participant, not just a same-shaped one - confirmed against a real
# fetched HL7 C-CDA-Examples encounter (Encounters/Inpatient Encounter
# Discharged to Rehab Location), which reuses this exact generic CDA
# datatype rather than something Procedure-specific.
SERVICE_DELIVERY_LOCATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.32"

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


def _build_address(
    addr_element, resource_id: str | None = None, location: str | None = None, relative_path_prefix: str = "address", recorder=None
) -> Address | None:
    """A single <addr> -> Address - the shared shape behind every address
    this module builds (a performer's own Location, their represented
    Organization, a Service Delivery Location) - mirrors app/cda/
    common.py::_build_patient_addresses' own per-field extraction style,
    but for a single (not repeating) address, since none of this module's
    three callers need more than one."""
    if addr_element is None:
        return None
    lines = [e.text.strip() for e in find_all(addr_element, "streetAddressLine") if e.text and e.text.strip()]
    city_element = find_child(addr_element, "city")
    state_element = find_child(addr_element, "state")
    postal_code_element = find_child(addr_element, "postalCode")
    country_element = find_child(addr_element, "country")
    city = (city_element.text or "").strip() if city_element is not None else ""
    state = (state_element.text or "").strip() if state_element is not None else ""
    postal_code = (postal_code_element.text or "").strip() if postal_code_element is not None else ""
    country = (country_element.text or "").strip() if country_element is not None else ""
    if not any([lines, city, state, postal_code, country]):
        return None

    address = Address()
    if lines:
        address.line = lines
        if recorder and resource_id and location:
            for i, line in enumerate(lines):
                recorder.record(resource_id, f"{relative_path_prefix}.line[{i}]", xpath_location(location, f"streetAddressLine[{i}]"), line)
    if city:
        address.city = city
        if recorder and resource_id and location:
            recorder.record(resource_id, f"{relative_path_prefix}.city", xpath_location(location, "city"), city)
    if state:
        address.state = state
        if recorder and resource_id and location:
            recorder.record(resource_id, f"{relative_path_prefix}.state", xpath_location(location, "state"), state)
    if postal_code:
        address.postalCode = postal_code
        if recorder and resource_id and location:
            recorder.record(resource_id, f"{relative_path_prefix}.postalCode", xpath_location(location, "postalCode"), postal_code)
    if country:
        address.country = country
        if recorder and resource_id and location:
            recorder.record(resource_id, f"{relative_path_prefix}.country", xpath_location(location, "country"), country)
    return address


def _build_practitioner_from_assigned_entity(assigned_entity, location: str, recorder=None) -> Practitioner | None:
    """assignedEntity/id -> identifier, assignedEntity/assignedPerson/name
    (family/given) -> name. Genuinely optional per a real fetched example
    (one real performer carried only an id, no name at all) - mirrors
    app/mappings/common.py::build_practitioner_from_xcn's own HL7v2-side
    "id or family or given" presence rule: skip the whole performer only
    when NEITHER an id nor a name resolves."""
    id_element = find_child(assigned_entity, "id")
    identifier = build_identifier(id_element, _PRACTITIONER_ID_FALLBACK_SYSTEM) if id_element is not None else None

    name = None
    assigned_person = find_child(assigned_entity, "assignedPerson")
    if assigned_person is not None:
        name_element = find_child(assigned_person, "name")
        if name_element is not None:
            family_element = find_child(name_element, "family")
            family = (family_element.text or "").strip() if family_element is not None else ""
            # (source_index, value) pairs, not just values - mirrors
            # app/cda/common.py::_build_patient_names' own discipline so a
            # kept given-name index never desyncs from which XML element
            # it actually came from when an earlier one is empty.
            given_pairs = [
                (gi, g.text.strip()) for gi, g in enumerate(find_all(name_element, "given")) if g.text and g.text.strip()
            ]
            if family or given_pairs:
                name = HumanName()
                if family:
                    name.family = family
                if given_pairs:
                    name.given = [v for _, v in given_pairs]

    if identifier is None and name is None:
        return None

    practitioner_id = str(uuid.uuid4())
    practitioner = Practitioner(id=practitioner_id)
    if identifier:
        practitioner.identifier = [identifier]
        if recorder:
            recorder.record(practitioner_id, "identifier[0].value", xpath_location(location, "id"), identifier.value)
    if name:
        practitioner.name = [name]
        if recorder:
            if name.family:
                recorder.record(
                    practitioner_id, "name[0].family", xpath_location(location, "assignedPerson", "name", "family"), name.family
                )
            if name.given:
                for i, (src_i, given) in enumerate(given_pairs):
                    recorder.record(
                        practitioner_id,
                        f"name[0].given[{i}]",
                        xpath_location(location, "assignedPerson", "name", f"given[{src_i}]"),
                        given,
                    )
    return practitioner


def _build_organization_from_represented_org(represented_org_element, location: str, recorder=None) -> Organization | None:
    """assignedEntity/representedOrganization/name -> Organization.name -
    skipped entirely when absent, since name is the one field a real
    fetched example (Results/"Result with lab location") confirms always
    carries the organization's own real content. That same example shows
    representedOrganization's own addr/telecom nested here directly (not
    under assignedEntity itself) -> Organization.address/telecom."""
    name_element = find_child(represented_org_element, "name")
    name = (name_element.text or "").strip() if name_element is not None else ""
    if not name:
        return None

    organization_id = str(uuid.uuid4())
    organization = Organization(id=organization_id, name=name)
    if recorder:
        recorder.record(organization_id, "name", xpath_location(location, "name"), name)

    id_element = find_child(represented_org_element, "id")
    if id_element is not None:
        identifier = build_identifier(id_element, _ORGANIZATION_ID_FALLBACK_SYSTEM)
        if identifier:
            organization.identifier = [identifier]
            if recorder:
                recorder.record(organization_id, "identifier[0].value", xpath_location(location, "id"), identifier.value)

    addr_element = find_child(represented_org_element, "addr")
    address = _build_address(
        addr_element,
        resource_id=organization_id,
        location=xpath_location(location, "addr"),
        relative_path_prefix="address[0]",
        recorder=recorder,
    )
    if address:
        organization.address = [address]

    telecom_element = find_child(represented_org_element, "telecom")
    contact_point = build_contact_point_from_telecom(telecom_element)
    if contact_point:
        organization.telecom = [contact_point]
        if recorder:
            recorder.record(
                organization_id,
                "telecom[0].value",
                xpath_location(location, "telecom", "@value"),
                contact_point.value,
                source_value=telecom_element.get("value"),
            )

    return organization


def _build_location_from_addr(addr_element, location: str, recorder=None) -> Location | None:
    """An address-only Location (no name) - for a performer's own
    assignedEntity/addr, referenced via PractitionerRole.location (FHIR
    has no field to embed an address directly on PractitionerRole
    itself). See _build_service_delivery_location below for the separate,
    name-carrying Location shape a participant[@typeCode=LOC] builds."""
    location_id = str(uuid.uuid4())
    address = _build_address(addr_element, resource_id=location_id, location=location, recorder=recorder)
    if address is None:
        return None
    return Location(id=location_id, address=address)


def _build_performer(performer_element, index: int, recorder=None) -> tuple[ProcedurePerformer, list[Resource]] | None:
    """performer/assignedEntity -> Procedure.performer.actor -> a real
    PractitionerRole (wrapping Practitioner + optional Organization +
    optional Location) - see module docstring for why PractitionerRole,
    not a bare Practitioner, is this app's own confirmed target shape.
    Returns None (this performer is skipped entirely) only when the
    underlying Practitioner itself couldn't be built - a real fetched
    example shows a performer with only an id and no assignedPerson/name
    at all, so this genuinely happens; see
    _build_practitioner_from_assigned_entity's own docstring."""
    assigned_entity = find_child(performer_element, "assignedEntity")
    if assigned_entity is None:
        return None

    location_base = xpath_location(_ENTRY_BASE, f"performer[{index}]", "assignedEntity")
    practitioner = _build_practitioner_from_assigned_entity(assigned_entity, location_base, recorder=recorder)
    if practitioner is None:
        return None

    resources: list[Resource] = [practitioner]
    role_id = str(uuid.uuid4())
    role = PractitionerRole(id=role_id, practitioner=Reference(reference=f"urn:uuid:{practitioner.id}"))

    # assignedEntity/id is shared: it identifies both the Practitioner
    # (their own personal id) and, per the real Procedure.csv mapping
    # table, the PractitionerRole itself.
    if practitioner.identifier:
        role.identifier = practitioner.identifier
        if recorder:
            recorder.record(role_id, "identifier[0].value", xpath_location(location_base, "id"), practitioner.identifier[0].value)

    represented_org_element = find_child(assigned_entity, "representedOrganization")
    organization = None
    if represented_org_element is not None:
        organization = _build_organization_from_represented_org(
            represented_org_element, xpath_location(location_base, "representedOrganization"), recorder=recorder
        )
        if organization is not None:
            resources.append(organization)
            role.organization = Reference(reference=f"urn:uuid:{organization.id}")

    # addr/telecom: a real fetched example shows these directly under
    # assignedEntity for a "reporting location" performer, but nested
    # under representedOrganization for a "reporting lab" performer -
    # assignedEntity's own copy is checked first since it describes this
    # specific performer, falling back to the organization's own copy
    # (already reflected in Organization.address/telecom above too, if an
    # Organization was built) only when assignedEntity itself has none.
    addr_element = find_child(assigned_entity, "addr")
    addr_location = xpath_location(location_base, "addr")
    if addr_element is None and represented_org_element is not None:
        addr_element = find_child(represented_org_element, "addr")
        addr_location = xpath_location(location_base, "representedOrganization", "addr")
    if addr_element is not None:
        location = _build_location_from_addr(addr_element, addr_location, recorder=recorder)
        if location is not None:
            resources.append(location)
            role.location = [Reference(reference=f"urn:uuid:{location.id}")]

    telecom_element = find_child(assigned_entity, "telecom")
    telecom_location = xpath_location(location_base, "telecom")
    if telecom_element is None and represented_org_element is not None:
        telecom_element = find_child(represented_org_element, "telecom")
        telecom_location = xpath_location(location_base, "representedOrganization", "telecom")
    contact_point = build_contact_point_from_telecom(telecom_element)
    if contact_point:
        role.telecom = [contact_point]
        if recorder:
            recorder.record(
                role_id,
                "telecom[0].value",
                xpath_location(telecom_location, "@value"),
                contact_point.value,
                source_value=telecom_element.get("value"),
            )

    resources.append(role)
    performer = ProcedurePerformer(actor=Reference(reference=f"urn:uuid:{role.id}"))
    return performer, resources


def _build_service_delivery_location(participant_element, index: int, recorder=None) -> Location | None:
    """participant[@typeCode=LOC]/participantRole[@classCode=SDLOC] ->
    Procedure.location - a separate Location, unrelated to a performer's
    own machinery (Procedure.location is a plain Reference, confirmed via
    model_fields). Confirmed via a real fetched HL7 C-CDA-Examples
    encounter (the identical generic CDA participantRole/playingEntity
    shape), gated by SERVICE_DELIVERY_LOCATION_TEMPLATE_ID's own real,
    confirmed templateId so an unrelated same-shaped participantRole
    can't false-match."""
    participant_role = find_child(participant_element, "participantRole")
    if participant_role is None or not has_template_id(participant_role, SERVICE_DELIVERY_LOCATION_TEMPLATE_ID):
        return None

    location_base = xpath_location(_ENTRY_BASE, f"participant[{index}]", "participantRole")
    playing_entity = find_child(participant_role, "playingEntity")
    name_element = find_child(playing_entity, "name") if playing_entity is not None else None
    name = (name_element.text or "").strip() if name_element is not None else ""

    code_element = find_child(participant_role, "code")
    location_type = build_codeable_concept_from_cd(code_element)

    if not name and location_type is None:
        return None

    location_id = str(uuid.uuid4())
    location = Location(id=location_id)
    if name:
        location.name = name
        if recorder:
            recorder.record(location_id, "name", xpath_location(location_base, "playingEntity", "name"), name)
    if location_type:
        location.type = [location_type]
        if recorder:
            if code_element.get("code"):
                recorder.record(
                    location_id, "type[0].coding[0].code", xpath_location(location_base, "code", "@code"), code_element.get("code")
                )
            if code_element.get("displayName"):
                recorder.record(
                    location_id,
                    "type[0].coding[0].display",
                    xpath_location(location_base, "code", "@displayName"),
                    code_element.get("displayName"),
                )

    addr_element = find_child(participant_role, "addr")
    address = _build_address(addr_element, resource_id=location_id, location=xpath_location(location_base, "addr"), recorder=recorder)
    if address:
        location.address = address

    telecom_element = find_child(participant_role, "telecom")
    contact_point = build_contact_point_from_telecom(telecom_element)
    if contact_point:
        location.telecom = [contact_point]
        if recorder:
            recorder.record(
                location_id,
                "telecom[0].value",
                xpath_location(location_base, "telecom", "@value"),
                contact_point.value,
                source_value=telecom_element.get("value"),
            )

    return location


def _build_procedure(procedure_element, patient_id: str, recorder=None) -> tuple[Procedure, list[Resource]]:
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

    extra_resources: list[Resource] = []

    for performer_index, performer_element in enumerate(find_all(procedure_element, "performer")):
        built = _build_performer(performer_element, performer_index, recorder=recorder)
        if built is None:
            continue
        performer, performer_resources = built
        if procedure.performer is None:
            procedure.performer = []
        procedure.performer.append(performer)
        extra_resources.extend(performer_resources)

    for participant_index, participant_element in enumerate(find_all(procedure_element, "participant")):
        if participant_element.get("typeCode") != "LOC":
            continue
        location = _build_service_delivery_location(participant_element, participant_index, recorder=recorder)
        if location is not None:
            procedure.location = Reference(reference=f"urn:uuid:{location.id}")
            extra_resources.append(location)
            break  # Procedure.location is singular - only the first LOC participant is used

    return procedure, extra_resources


def build_procedures(section, patient_id: str, recorder=None) -> list[Resource]:
    """One Procedure per Procedure Activity Procedure entry in the section
    - a section can (and commonly does) have multiple entries - plus any
    Practitioner/PractitionerRole/Organization/Location resources
    materialized along the way for that entry's own performer/participant."""
    resources: list[Resource] = []
    for entry in find_all(section, "entry"):
        procedure_element = find_child(entry, "procedure")
        if procedure_element is None or not has_template_id(procedure_element, PROCEDURE_TEMPLATE_ID):
            continue
        procedure, extra_resources = _build_procedure(procedure_element, patient_id, recorder=recorder)
        resources.append(procedure)
        resources.extend(extra_resources)
    return resources
