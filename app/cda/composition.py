"""ClinicalDocument header -> Composition, making the output a real FHIR
Document rather than a bag of resources.

**The field mapping is the FHIR spec's own.** C-CDA on FHIR publishes no
header table (confirmed by listing `mappings/CF/` and `input/pagecontent/`
- neither has one), but the base R4 `Composition` StructureDefinition
carries a per-element `cda` mapping ("CDA (R2)", `http://hl7.org/v3/cda`)
stating the correspondence directly, and `hl7.fhir.us.ccda` 2.0.0-ballot
publishes Composition profiles for exactly the three document types this
app converts. Every field below is that mapping, verbatim:

    Composition        <- ClinicalDocument
    .identifier        <- .setId
    .status            <- n/a          (see DOCUMENT_STATUS)
    .type              <- .code
    .subject           <- .recordTarget
    .encounter         <- .componentOf.encompassingEncounter
    .date              <- .effectiveTime
    .author            <- .author.assignedAuthor
    .title             <- .title
    .confidentiality   <- .confidentialityCode
    .attester          <- .authenticator / .legalAuthenticator
    .custodian         <- .custodian.assignedCustodian
    .section           <- .component.structuredBody.component.section

`.identifier` is `setId`, **not** `id` - a distinction worth keeping,
since `id` identifies this document instance and already maps to
`Bundle.identifier`, while `setId` identifies the series it belongs to. A
document with no `setId` gets no `Composition.identifier`; US Realm Header
makes it 1..1, but this app builds base FHIR and does not claim that
profile.

**`.confidentiality` is mapped, and US Realm Header prohibits it (0..0).**
The base mapping is the spec's own statement of the correspondence and the
value is real data that would otherwise be dropped, so it is carried - and
surfaced both as its own mapping decision and as
`cda.composition-confidentiality-not-us-realm-conformant` in validation, so
nobody validating against that profile meets it as a surprise.

**Scope limit, disclosed**: `.relatesTo` (`relatedDocument`) is named by
the mapping but not built - it points at *another* document, which a
single-document conversion has no way to resolve to a resource. It keeps
reporting as a drop.
"""

import uuid

from fhir.resources.R4B.composition import (
    Composition,
    CompositionAttester,
    CompositionEvent,
    CompositionSection,
)
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.cda.common import (
    build_author_from_element,
    build_codeable_concept_from_cd,
    build_contact_point_from_telecom,
    build_identifier,
    effective_time_location,
    record_identifier,
    build_practitioner_from_assigned_entity,
    parse_partial_ts,
)
from app.cda.narrative_sections import extract_narrative_text
from app.cda.parser import find_all, find_child, ivl_ts_bounds, ts_value
from app.provenance.location import xpath_location

# No CDA element carries a Composition.status - C-CDA has no
# document-level statusCode at all, and of the four values (preliminary |
# final | amended | entered-in-error) only "final" describes a document
# that was rendered and exchanged.
DOCUMENT_STATUS = "final"

_SETID_FALLBACK_SYSTEM = "urn:interop-tools:cda-document-set-id"
_CUSTODIAN_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-organization-id"
_DEFAULT_TITLE = "Clinical Document"

_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _narrative(text: str) -> Narrative:
    """A section's narrative as FHIR xhtml.

    `status="generated"` is accurate rather than convenient: the div is
    built here from the source's narrative text, not copied as markup -
    C-CDA's narrative block is not xhtml and its tags have no direct
    equivalents.
    """
    paragraphs = "".join(f"<p>{_esc(line)}</p>" for line in text.split("\n") if line.strip())
    return Narrative(status="generated", div=f'<div xmlns="{_XHTML_NS}">{paragraphs}</div>')


def _build_custodian_organization(document, recorder=None) -> Organization | None:
    """custodian/assignedCustodian/representedCustodianOrganization ->
    Organization.

    Built here rather than reusing procedures.py's performer-organization
    builder: that one reads `representedOrganization`, a different element,
    and this is the only place an organization is the whole point of the
    participation rather than a qualifier on somebody else's.
    """
    custodian = find_child(document, "custodian")
    if custodian is None:
        return None
    assigned = find_child(custodian, "assignedCustodian")
    if assigned is None:
        return None
    org_element = find_child(assigned, "representedCustodianOrganization")
    if org_element is None:
        return None

    base = xpath_location("custodian", "assignedCustodian", "representedCustodianOrganization")
    organization = Organization(id=str(uuid.uuid4()))

    name_element = find_child(org_element, "name")
    name = (name_element.text or "").strip() if name_element is not None else ""
    if name:
        organization.name = name
        if recorder:
            recorder.record(organization.id, "name", xpath_location(base, "name"), name)

    id_element = find_child(org_element, "id")
    identifier = build_identifier(id_element, _CUSTODIAN_ID_FALLBACK_SYSTEM) if id_element is not None else None
    if identifier:
        organization.identifier = [identifier]
        record_identifier(
            recorder, organization.id, "identifier[0]", id_element, xpath_location(base, "id"), identifier
        )

    telecoms = [
        contact
        for element in find_all(org_element, "telecom")
        if (contact := build_contact_point_from_telecom(element)) is not None
    ]
    if telecoms:
        organization.telecom = telecoms

    if not name and identifier is None:
        return None
    return organization


def _build_authenticator_party(element, base: str, recorder):
    """An `<authenticator>`/`<legalAuthenticator>` wraps its person in
    `<assignedEntity>` where an `<author>` uses `<assignedAuthor>` - same
    content, different tag, so this cannot simply call
    build_author_participant on the parent.

    A Device is not reachable here by construction: CDA offers no
    authoring-device choice under assignedEntity, and
    CompositionAttester.party's own enum_reference_types has no Device
    either (unlike Composition.author's, which does).
    """
    assigned = find_child(element, "assignedEntity")
    if assigned is None:
        return None
    practitioner = build_practitioner_from_assigned_entity(
        assigned, xpath_location(base, "assignedEntity"), recorder=recorder
    )
    if practitioner is None:
        return None
    return Reference(reference=f"urn:uuid:{practitioner.id}"), practitioner


def _build_attesters(
    document, composition_id: str, recorder=None
) -> tuple[list[CompositionAttester], list[Resource]]:
    """legalAuthenticator -> mode "legal", authenticator -> "professional",
    per the base mapping's `.authenticator/.legalAuthenticator` row and US
    Realm Header's own attester slices, which fix exactly these modes."""
    attesters: list[CompositionAttester] = []
    extra: list[Resource] = []
    for tag, mode in (("legalAuthenticator", "legal"), ("authenticator", "professional")):
        for index, element in enumerate(find_all(document, tag)):
            base = f"{tag}[{index}]" if index else tag
            attester = CompositionAttester(mode=mode)

            time_raw = ts_value(find_child(element, "time"))
            time = parse_partial_ts(time_raw or "")
            if time:
                attester.time = time

            party = _build_authenticator_party(element, base, recorder)
            if party is not None:
                reference, resource = party
                attester.party = reference
                extra.append(resource)

            if attester.time is None and attester.party is None:
                # Nothing but a mode this app assigned itself - an attester
                # saying only "somebody legally attested" is not a fact the
                # source stated.
                continue

            attesters.append(attester)
            if recorder:
                position = len(attesters) - 1
                recorder.record_inferred(
                    composition_id,
                    f"attester[{position}].mode",
                    f"The base R4 Composition mapping routes {tag} to .attester, and US Realm Header fixes "
                    f"that slice's mode.",
                    mode,
                )
                if time:
                    recorder.record(
                        composition_id,
                        f"attester[{position}].time",
                        xpath_location(base, "time", "@value"),
                        time,
                        source_value=time_raw,
                    )
    return attesters, extra


def _build_event(document, composition_id: str, recorder=None) -> CompositionEvent | None:
    """documentationOf/serviceEvent -> Composition.event, per the base
    mapping's `.event <- .documentationOf.serviceEvent` row.

    Only the first `documentationOf` is read: `.event` is 0..* in FHIR,
    but a second serviceEvent has no distinguishing marker here, and this
    app's own "first only, disclosed" precedent applies.
    """
    documentation_of = find_child(document, "documentationOf")
    if documentation_of is None:
        return None
    service_event = find_child(documentation_of, "serviceEvent")
    if service_event is None:
        return None

    base = xpath_location("documentationOf", "serviceEvent")
    event = CompositionEvent()

    code = build_codeable_concept_from_cd(find_child(service_event, "code"))
    if code is not None:
        event.code = [code]

    # ivl_ts_bounds returns the raw CDA @value strings; Period wants
    # FHIR dateTime, so both bounds go through parse_partial_ts the way
    # every other IVL_TS consumer in this app does.
    effective_time = find_child(service_event, "effectiveTime")
    low_raw, high_raw = ivl_ts_bounds(effective_time)
    low = parse_partial_ts(low_raw or "")
    high = parse_partial_ts(high_raw or "")
    period = Period()
    if low:
        period.start = low
    if high:
        period.end = high
    if period.start or period.end:
        event.period = period

    if event.code is None and event.period is None:
        return None

    if recorder:
        if code is not None and code.coding:
            recorder.record(
                composition_id, "event[0].code[0].coding[0].code", xpath_location(base, "code", "@code"), code.coding[0].code
            )
        if period.start:
            recorder.record(
                composition_id,
                "event[0].period.start",
                effective_time_location(xpath_location(base, "effectiveTime"), effective_time, "low"),
                period.start,
            )
        if period.end:
            recorder.record(
                composition_id,
                "event[0].period.end",
                effective_time_location(xpath_location(base, "effectiveTime"), effective_time, "high"),
                period.end,
            )
    return event


def _build_sections(section_resources, composition_id: str, recorder=None) -> list[CompositionSection]:
    """One Composition.section per converted structuredBody section, with
    its title, code, narrative, and references to what it produced.

    A section carrying neither narrative nor resources is skipped: FHIR's
    cmp-1 requires text, entries or sub-sections, and an empty one would
    assert the document has a section that says nothing.
    """
    sections: list[CompositionSection] = []
    for source_index, element, resources in section_resources:
        base = xpath_location("component", "structuredBody", f"component[{source_index}]", "section")
        section = CompositionSection()

        title_element = find_child(element, "title")
        title = (title_element.text or "").strip() if title_element is not None else ""
        if title:
            section.title = title

        code = build_codeable_concept_from_cd(find_child(element, "code"))
        if code is not None:
            section.code = code

        text_element = find_child(element, "text")
        narrative_text = extract_narrative_text(text_element) if text_element is not None else ""
        if narrative_text.strip():
            section.text = _narrative(narrative_text)

        entries = [Reference(reference=f"urn:uuid:{resource.id}") for resource in resources]
        if entries:
            section.entry = entries

        if section.text is None and not entries:
            continue

        sections.append(section)
        if recorder:
            path = f"section[{len(sections) - 1}]"
            if title:
                recorder.record(composition_id, f"{path}.title", xpath_location(base, "title"), title)
            if code is not None and code.coding:
                recorder.record(
                    composition_id,
                    f"{path}.code.coding[0].code",
                    xpath_location(base, "code", "@code"),
                    code.coding[0].code,
                )
            if section.text is not None:
                recorder.record(
                    composition_id, f"{path}.text.div", xpath_location(base, "text"), narrative_text
                )
    return sections


def build_composition(
    document, patient, encounter, section_resources, recorder=None
) -> tuple[Composition | None, list[Resource]]:
    """The document header as a Composition, plus every resource
    materialized for it (author Practitioner/Device, custodian
    Organization, attester Practitioners).

    `section_resources` is one `(source_index, section_element, resources)`
    triple per converted section, so `.section.entry` references exactly
    what that section produced.

    Returns `(None, [])` when the document cannot support one. Composition
    eagerly requires author, type, date, status and title at construction -
    `model_fields` reports only author and type, the same "don't trust the
    library's declared cardinality" trap this app has hit repeatedly - and
    of those, author, type and date have no honest default. A document
    missing any of them stays a collection Bundle rather than becoming a
    Composition with invented content.
    """
    composition_id = str(uuid.uuid4())
    extra: list[Resource] = []

    authors: list[Reference] = []
    for index, element in enumerate(find_all(document, "author")):
        base = f"author[{index}]" if index else "author"
        # allow_device=True: Composition.author's enum_reference_types
        # includes Device, unlike Observation.performer's - an EHR that
        # generated the document is an ordinary author here.
        built = build_author_from_element(element, base, allow_device=True, recorder=recorder)
        if built is None:
            continue
        reference, resource = built
        authors.append(reference)
        extra.append(resource)
        if recorder:
            recorder.record(
                composition_id,
                f"author[{len(authors) - 1}].reference",
                xpath_location(base, "assignedAuthor"),
                reference.reference,
            )
    if not authors:
        return None, []

    date_raw = ts_value(find_child(document, "effectiveTime"))
    # Composition.date is dateTime, which - unlike Bundle.timestamp's
    # instant - does have a date-only form, so parse_partial_ts's fallback
    # is right here where it would be wrong there.
    date = parse_partial_ts(date_raw or "")
    document_type = build_codeable_concept_from_cd(find_child(document, "code"))
    if date is None or document_type is None:
        return None, []

    title_element = find_child(document, "title")
    title = (title_element.text or "").strip() if title_element is not None else ""
    title_inferred = not title
    if title_inferred:
        title = (document_type.coding[0].display if document_type.coding else None) or _DEFAULT_TITLE

    composition = Composition(
        id=composition_id,
        status=DOCUMENT_STATUS,
        type=document_type,
        date=date,
        author=authors,
        title=title,
        subject=Reference(reference=f"urn:uuid:{patient.id}"),
    )
    if recorder:
        recorder.record_inferred(
            composition_id,
            "status",
            "C-CDA has no document-level statusCode; a rendered, exchanged document is a completed one.",
            DOCUMENT_STATUS,
        )
        if document_type.coding:
            recorder.record(
                composition_id, "type.coding[0].code", xpath_location("code", "@code"), document_type.coding[0].code
            )
            if document_type.coding[0].display:
                recorder.record(
                    composition_id,
                    "type.coding[0].display",
                    xpath_location("code", "@displayName"),
                    document_type.coding[0].display,
                )
        recorder.record(composition_id, "date", xpath_location("effectiveTime", "@value"), date, source_value=date_raw)
        if title_inferred:
            recorder.record_inferred(
                composition_id,
                "title",
                "Composition.title is required and the document carried no <title>; its own code display describes it.",
                title,
            )
        else:
            recorder.record(composition_id, "title", xpath_location("title"), title)

    if encounter is not None:
        composition.encounter = Reference(reference=f"urn:uuid:{encounter.id}")

    set_id_element = find_child(document, "setId")
    identifier = build_identifier(set_id_element, _SETID_FALLBACK_SYSTEM) if set_id_element is not None else None
    if identifier:
        composition.identifier = identifier
        record_identifier(recorder, composition_id, "identifier", set_id_element, xpath_location("setId"), identifier)

    confidentiality_element = find_child(document, "confidentialityCode")
    confidentiality = confidentiality_element.get("code") if confidentiality_element is not None else None
    if confidentiality:
        composition.confidentiality = confidentiality
        if recorder:
            recorder.record(
                composition_id, "confidentiality", xpath_location("confidentialityCode", "@code"), confidentiality
            )

    custodian = _build_custodian_organization(document, recorder=recorder)
    if custodian is not None:
        composition.custodian = Reference(reference=f"urn:uuid:{custodian.id}")
        extra.append(custodian)

    attesters, attester_resources = _build_attesters(document, composition_id, recorder=recorder)
    if attesters:
        composition.attester = attesters
        extra.extend(attester_resources)

    event = _build_event(document, composition_id, recorder=recorder)
    if event is not None:
        composition.event = [event]

    sections = _build_sections(section_resources, composition_id, recorder=recorder)
    if sections:
        composition.section = sections

    return composition, extra
