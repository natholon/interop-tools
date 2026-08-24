"""Procedures section (templateId 2.16.840.1.113883.10.20.22.2.7.1) ->
Procedure, per the C-CDA on FHIR IG's CF-procedures guidance, its
`CCDA-FHIR Procedure.csv` mapping table, and ConceptMap-CF-ProcedureStatus.

**Template selection follows the IG, not this app's judgment**: of C-CDA
2.1's three Procedure templates, the IG maps only Procedure Activity
Procedure ("the most complete... most vendors exclusively use it"), and
notes the Act/Observation variants were removed in C-CDA 3.0. A Procedure
Activity Observation is better modelled as a Results-shaped Observation,
so it is out of scope rather than mismapped.

`STATUS_MAP` is ConceptMap-CF-ProcedureStatus; `held`/`new`/`obsolete`/
`suspended` have no row and fall back to `"unknown"`. `negationInd="true"`
overrides statusCode entirely to `"not-done"`. `performedDateTime` and
`performedPeriod` are mutually exclusive per the IG, distinguished by
`ivl_ts_bounds`. `Procedure.code` is genuinely optional in R4 (confirmed
via `model_fields`), so a code-less procedure is still built.

**performer/participant**: `performer/assignedEntity` ->
`Procedure.performer.actor` as a real **PractitionerRole** (the CSV's own
target, confirmed with the user over a simpler `Practitioner` +
`.onBehalfOf` shortcut), wrapping a Practitioner plus an optional
Organization and an address-only Location (FHIR has nowhere to put an
address on PractitionerRole itself). `assignedPerson/name` is genuinely
optional - a real fetched example carries a performer with only an `id`.
`participant[@typeCode=LOC]/participantRole[@classCode=SDLOC]` ->
`Procedure.location`, a separate Location unrelated to the performer
machinery (`Procedure.location` is a plain Reference).

**Indication / Comment Activity / recorder**:
  - `entryRelationship[typeCode="RSON"]` wraps an Indication Observation
    (`...4.19`, a general-purpose template) whose `<value>` ->
    `Procedure.reasonCode`. The CSV's second target, `reasonReference`,
    would need the Indication resolved to an already-materialized
    resource and is not attempted.
  - `entryRelationship[typeCode="SUBJ", inversionInd="true"]` wraps a
    Comment Activity act (`...4.64`, fixed LOINC `48767-8`) ->
    `Procedure.note`. Per the IG's mappingGuidance Comment Activity
    table: `<text>` -> `.text` (required at construction despite
    `model_fields` not flagging it, so a text-less comment is skipped),
    `author/time` -> `.time`, `author/assignedAuthor` ->
    `.authorReference`. A narrative-only comment carrying just a
    `<reference>` is a disclosed, deferred shape.
  - A direct-child `<author>` (Author Participation, `...4.119`) ->
    `Procedure.recorder`, reusing the identical assignedAuthor shape.
    Built as a plain **Practitioner**, not the PractitionerRole the IG's
    prose loosely suggests ("ideally..."), because
    `Annotation.authorReference`'s real `enum_reference_types` has no
    PractitionerRole - and `Procedure.recorder`'s CSV row carries no
    `[PractitionerRole]` annotation either, unlike `performer.actor`'s.

**Scope limits**: `author` -> a real Provenance *resource* is a
deliberate permanent exclusion (see app/provenance/dispatch.py). No
cross-procedure dedup of the materialized Practitioner/Organization/
Location is attempted - `app/dedup.py` covers that generically."""

import uuid

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitionerrole import PractitionerRole
from fhir.resources.R4B.procedure import Procedure, ProcedurePerformer
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.cda.common import (
    build_author_participant,
    build_codeable_concept_from_cd,
    build_contact_point_from_telecom,
    build_identifier,
    build_identifiers,
    build_practitioner_from_assigned_entity,
    effective_time_location,
    parse_partial_ts,
    record_coding,
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
# The "entries optional" sibling section (no trailing ".1"). A real HL7
# History and Physical example uses ONLY this templateId for its Procedures
# section, with no paired ".2.7.1" - so a single-templateId registration
# skips the section entirely. The IG's template list confirms it as a
# distinct templateId, not a typo in that one document.
SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.7"
PROCEDURE_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.14"
# Service Delivery Location - the generic CDA participantRole template
# used to confirm a participant[@typeCode=LOC] is genuinely a location
# participant, not just a same-shaped one - confirmed against a real
# fetched HL7 C-CDA-Examples encounter (Encounters/Inpatient Encounter
# Discharged to Rehab Location), which reuses this exact generic CDA
# datatype rather than something Procedure-specific.
SERVICE_DELIVERY_LOCATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.32"
# Indication - a general-purpose C-CDA template (confirmed "used by many
# templates in C-CDA R2.1", not Procedure-specific) wrapped by
# entryRelationship[typeCode=RSON] - see module docstring.
INDICATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.19"
# Comment Activity - see module docstring for the full typeCode/
# inversionInd/fixed-code shape confirmed against the C-CDA on FHIR IG's
# own mappingGuidance.html and a real fetched worked example.
COMMENT_ACTIVITY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.64"
# Author Participation - the general CDA <author> shape, confirmed
# "appropriate at any place CDA allows an author... CDA Entry" and legal
# both as a direct child of the procedure element (-> Procedure.recorder)
# and nested inside a Comment Activity act (-> Annotation.authorReference).
# Public (not module-private) - app/transform/cda_ccd.py became a real
# reverse-direction consumer, needing this OID to regenerate a realistic
# <author><templateId .../>...</author> element, even though the forward
# parser itself never gates on it (see module docstring for why).
AUTHOR_PARTICIPATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.119"

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
            if contact_point.use:
                # ContactPoint.use is resolved by
                # build_contact_point_from_telecom but was never
                # recorded, so the register reported @use as dropped
                # while the Bundle carried it.
                recorder.record(
                    organization_id,
                    "telecom[0].use",
                    xpath_location(location, "telecom", "@use"),
                    contact_point.use,
                    source_value=telecom_element.get("use"),
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
    practitioner = build_practitioner_from_assigned_entity(assigned_entity, location_base, recorder=recorder)
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
            if contact_point.use:
                # ContactPoint.use is resolved by
                # build_contact_point_from_telecom but was never
                # recorded, so the register reported @use as dropped
                # while the Bundle carried it.
                recorder.record(
                    role_id,
                    "telecom[0].use",
                    xpath_location(telecom_location, "@use"),
                    contact_point.use,
                    source_value=telecom_element.get("use"),
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
            if contact_point.use:
                # ContactPoint.use is resolved by
                # build_contact_point_from_telecom but was never
                # recorded, so the register reported @use as dropped
                # while the Bundle carried it.
                recorder.record(
                    location_id,
                    "telecom[0].use",
                    xpath_location(location_base, "telecom", "@use"),
                    contact_point.use,
                    source_value=telecom_element.get("use"),
                )

    return location


def _build_reason_codes(procedure_element, recorder=None, procedure_id: str = "") -> list[CodeableConcept]:
    """entryRelationship[typeCode=RSON] wraps a nested Indication
    Observation (templateId INDICATION_TEMPLATE_ID) whose own <value> is
    the actual coded reason - per the real Procedure.csv mapping table row
    ("entryRelationship[Indication].value -> Procedure.reasonCode"), see
    module docstring. reasonReference (the CSV's own second target) is
    deliberately not attempted - see module docstring. The xml-position
    index used for each fact's own source location comes from enumerating
    every entryRelationship, not just the RSON-typed ones, so it stays
    accurate even when a Comment Activity relationship sits at an earlier
    or later physical position - the same collision this app's own 837I
    multi-HI-segment fix and Allergies' multi-reaction fix both already
    guard against."""
    reason_codes: list[CodeableConcept] = []
    for index, entry_relationship in enumerate(find_all(procedure_element, "entryRelationship")):
        if entry_relationship.get("typeCode") != "RSON":
            continue
        observation = find_child(entry_relationship, "observation")
        if observation is None or not has_template_id(observation, INDICATION_TEMPLATE_ID):
            continue
        value_element = find_child(observation, "value")
        reason = build_codeable_concept_from_cd(value_element)
        if reason is None:
            continue
        reason_codes.append(reason)
        if recorder:
            reason_index = len(reason_codes) - 1
            value_location = xpath_location(_ENTRY_BASE, f"entryRelationship[{index}]", "observation", "value")
            if value_element.get("code"):
                recorder.record(
                    procedure_id,
                    f"reasonCode[{reason_index}].coding[0].code",
                    xpath_location(value_location, "@code"),
                    value_element.get("code"),
                )
            if value_element.get("displayName"):
                recorder.record(
                    procedure_id,
                    f"reasonCode[{reason_index}].coding[0].display",
                    xpath_location(value_location, "@displayName"),
                    value_element.get("displayName"),
                )
    return reason_codes


def _build_notes(procedure_element, recorder=None, procedure_id: str = "") -> tuple[list[Annotation], list[Resource]]:
    """entryRelationship[typeCode=SUBJ, inversionInd=true] wraps a Comment
    Activity act (COMMENT_ACTIVITY_TEMPLATE_ID) - see module docstring for
    the full confirmed shape. A Comment Activity with no resolvable
    <text> is skipped entirely - Annotation.text is required at
    construction. The nested author (if present) reuses
    _build_practitioner_from_assigned_entity directly, the same
    assignedAuthor/assignedEntity shape equivalence the module docstring
    confirms."""
    notes: list[Annotation] = []
    extra_resources: list[Resource] = []
    for index, entry_relationship in enumerate(find_all(procedure_element, "entryRelationship")):
        if entry_relationship.get("typeCode") != "SUBJ" or entry_relationship.get("inversionInd") != "true":
            continue
        act = find_child(entry_relationship, "act")
        if act is None or not has_template_id(act, COMMENT_ACTIVITY_TEMPLATE_ID):
            continue
        text_element = find_child(act, "text")
        text = (text_element.text or "").strip() if text_element is not None else ""
        if not text:
            continue

        note_index = len(notes)
        act_location = xpath_location(_ENTRY_BASE, f"entryRelationship[{index}]", "act")
        annotation = Annotation(text=text)
        if recorder:
            recorder.record(procedure_id, f"note[{note_index}].text", xpath_location(act_location, "text"), text)

        author_element = find_child(act, "author")
        if author_element is not None:
            assigned_author = find_child(author_element, "assignedAuthor")
            if assigned_author is not None:
                author_location = xpath_location(act_location, "author", "assignedAuthor")
                practitioner = build_practitioner_from_assigned_entity(assigned_author, author_location, recorder=recorder)
                if practitioner is not None:
                    extra_resources.append(practitioner)
                    annotation.authorReference = Reference(reference=f"urn:uuid:{practitioner.id}")

            time_element = find_child(author_element, "time")
            author_time = parse_partial_ts(ts_value(time_element)) if time_element is not None else None
            if author_time:
                annotation.time = author_time
                if recorder:
                    recorder.record(
                        procedure_id, f"note[{note_index}].time", xpath_location(act_location, "author", "time", "@value"), author_time
                    )

        notes.append(annotation)
    return notes, extra_resources


def _build_procedure_recorder(procedure_element, recorder=None, procedure_id: str = "") -> tuple[Reference, list[Resource]] | None:
    """<author> as a DIRECT CHILD of the procedure element (Author
    Participation, sibling to <performer>/<participant>, not nested inside
    an entryRelationship) -> Procedure.recorder - see module docstring for
    why this is a plain Practitioner, not the PractitionerRole performer's
    own slice builds. Only the first <author> is used - Procedure.recorder
    is a singular Reference, the same "first-only, disclosed" precedent
    Procedure.location's own participant handling already established.

    allow_device=False: Procedure.recorder cannot reference a Device, so a
    device-authored procedure is left without a recorder rather than
    having its authoring system recorded as a person."""
    author = build_author_participant(
        procedure_element, _ENTRY_BASE, allow_device=False, recorder=recorder
    )
    if author is None:
        return None
    reference, practitioner = author
    return reference, [practitioner]


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
            if code.text:
                # <originalText>, already de-referenced into the element by
                # resolve_narrative_references. This site builds its records
                # by hand rather than through record_coding, so it needed
                # the same addition separately.
                recorder.record(
                    procedure_id, "code.text", xpath_location(_ENTRY_BASE, "code", "originalText"), code.text
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
        record_coding(
            recorder, procedure_id, "bodySite[0]", xpath_location(_ENTRY_BASE, "targetSiteCode"), body_site
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

    reason_codes = _build_reason_codes(procedure_element, recorder=recorder, procedure_id=procedure_id)
    if reason_codes:
        procedure.reasonCode = reason_codes

    notes, note_resources = _build_notes(procedure_element, recorder=recorder, procedure_id=procedure_id)
    if notes:
        procedure.note = notes
        extra_resources.extend(note_resources)

    recorder_result = _build_procedure_recorder(procedure_element, recorder=recorder, procedure_id=procedure_id)
    if recorder_result is not None:
        recorder_reference, recorder_resources = recorder_result
        procedure.recorder = recorder_reference
        extra_resources.extend(recorder_resources)

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
