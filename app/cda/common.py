"""Cross-document-type C-CDA shared logic - the app/mappings/common.py
equivalent for the XML input format. Header structure (recordTarget/
patientRole, componentOf/encompassingEncounter) and coded-value shape
(CD/value[xsi:type=CD]) are standardized across C-CDA document types
generally, not CCD-specific, so this lives here rather than in ccd.py - a
future Discharge Summary/H&P builder reuses it directly, same reasoning as
app/mappings/common.py::build_patient being shared across every HL7v2
message type."""

import uuid

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.device import Device, DeviceDeviceName
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.cda.parser import CDA_NS, find_all, find_child, coded_value, has_template_id, ivl_ts_bounds, ts_value
from app.fhir_models.builders import parse_hl7_date, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError
from app.provenance.location import xpath_location

# HL7 AdministrativeGender codeSystem (2.16.840.1.113883.5.1) uses F/M/UN,
# not HL7v2 PID-8's M/F/O - a genuinely different (if similarly-shaped)
# vocabulary, so this is its own table rather than reusing
# app/fhir_models/builders.py::hl7_sex_to_fhir_gender's _GENDER_MAP. "UN"
# (undifferentiated) maps to FHIR's "other", the closer semantic fit versus
# "unknown" (which means "not recorded" rather than "recorded as neither").
_GENDER_MAP = {"F": "female", "M": "male", "UN": "other"}

# Disclosed, extensible OID -> FHIR canonical system URI table (same
# category as app/mappings/mdm.py's TXA-3 MIME crosswalk) - covers the code
# systems this slice's Problems mapping actually needs (SNOMED CT) plus a
# couple of other common ones likely to appear in real C-CDA documents.
# Anything unrecognized falls back to a urn:oid: URI, matching FHIR's own
# convention for representing an untranslated OID rather than guessing.
# Public (not module-private) - app/transform/cda_ccd.py became a real
# reverse-direction consumer, inverting this same table rather than
# maintaining a second, independently-drifting copy.
OID_TO_FHIR_SYSTEM = {
    "2.16.840.1.113883.6.96": "http://snomed.info/sct",
    "2.16.840.1.113883.6.1": "http://loinc.org",
    "2.16.840.1.113883.6.88": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "2.16.840.1.113883.6.90": "http://hl7.org/fhir/sid/icd-10-cm",
    # HL7 v3 RoleCode - confirmed via a real fetched HL7 C-CDA-Examples
    # Family History Organizer, whose own relatedSubject/code carries
    # codeSystemName="HL7 FamilyMember" (a value set drawn from RoleCode,
    # not a separate code system) - app/cda/family_history.py's first real
    # consumer.
    "2.16.840.1.113883.5.111": "http://terminology.hl7.org/CodeSystem/v3-RoleCode",
    # HL7 AdministrativeGender - a genuinely different table from this
    # module's own _GENDER_MAP just below (that one converts to FHIR's
    # Patient.gender *enum string*; this one is for a CD-shaped
    # administrativeGenderCode reused as a real CodeableConcept elsewhere,
    # e.g. FamilyMemberHistory.sex).
    "2.16.840.1.113883.5.1": "http://hl7.org/fhir/administrative-gender",
}
CD_FALLBACK_SYSTEM = "urn:interop-tools:coded-value"

_TELECOM_SCHEME_TO_SYSTEM = {"tel": "phone", "mailto": "email", "fax": "fax"}
# A representative subset of HL7 AddressUse/TelecomUse codes - disclosed,
# extensible; an unrecognized use code is simply omitted rather than guessed.
_TELECOM_USE_MAP = {"HP": "home", "WP": "work", "MC": "mobile"}

# CDA's own use codes for names and addresses, mapped only where the HL7
# v3 code and the FHIR code mean the same thing. The C-CDA on FHIR IG maps
# `..addr` and `...name` as whole datatypes ("source value") and publishes
# no datatype-level table for either, so these are read off the two code
# systems directly. Deliberately partial: v3's HV (vacation home), DIR,
# PUB, ASGN and the name-use codes for artist and indigenous names have no
# unambiguous FHIR counterpart, and an entry here would be a guess. A code
# with no row stays unmapped and keeps being reported as dropped, which is
# the honest outcome rather than a plausible-looking mismap.
_NAME_USE_MAP = {"L": "official", "P": "nickname", "C": "official"}
_ADDRESS_USE_MAP = {"H": "home", "HP": "home", "WP": "work", "TMP": "temp", "BAD": "old"}


def parse_partial_ts(raw_value: str | None) -> str | None:
    """CDA TS/IVL_TS @value strings use the identical HL7 TS digit shape
    (YYYYMMDD[HHMM[SS][+/-ZZZZ]]) as HL7v2 TS fields, so the existing
    parse_hl7_datetime/parse_hl7_date are directly reusable. Onset-style
    dates are commonly date-only (8 digits) - tries the full-timestamp
    parse first, falls back to date-only, matching the same "try datetime,
    fall back to date" combinator app/validation/common.py needed for the
    identical reason. Unlike that module, this only needs the resulting
    FHIR-formatted string (for direct assignment to a dateTime/date field),
    not a comparable Python datetime. Public (not module-private) since
    every section builder needing an onset/abatement/effective date reuses
    this, not just the header builders in this module."""
    if not raw_value:
        return None
    return parse_hl7_datetime(raw_value) or parse_hl7_date(raw_value)


def narrative_anchor_text(section) -> dict[str, str]:
    """{ID: plain text} for every anchor-carrying element in a section's own
    narrative <text> block - the lookup behind resolve_narrative_references
    below. C-CDA's own "entries derive from narrative" pattern puts the
    human-readable text in the narrative and points at it from an entry via
    <originalText><reference value="#ID"/></originalText> (confirmed
    against a real HL7 C-CDA-Examples CCD, whose Encounters section carries
    exactly this shape: <td ID="Encounter1"> Checkup Examination </td>).
    Note the attribute is capital-`ID`, CDA's own spelling, not xml:id or
    lowercase id. itertext() flattens any nested inline markup (<sub>,
    <content>, ...) the same way app/cda/narrative_sections.py's own
    extraction already does - the IG requires markup be removed and the
    result stored as plain text."""
    text_element = find_child(section, "text")
    if text_element is None:
        return {}
    anchors = {}
    for element in text_element.iter():
        anchor_id = element.get("ID")
        if not anchor_id:
            continue
        content = " ".join("".join(element.itertext()).split())
        if content:
            anchors[anchor_id] = content
    return anchors


def resolve_narrative_references(section) -> None:
    """Resolve every <originalText><reference value="#ID"/></originalText>
    in a section's own entries into inline text, in place, so downstream
    entry builders never need narrative context of their own.

    **Why an in-place pre-pass rather than threading a lookup dict through
    every builder**: build_codeable_concept_from_cd has ~60 call sites
    across 13 modules, most of them several frames below the section
    element, and stdlib ElementTree has no parent pointers to walk back up
    with (see app/cda/parser.py's own notes on that constraint). Resolving
    once per section - at the one choke point that genuinely has both the
    narrative and the entries in scope - converts the reference shape into
    the inline shape build_codeable_concept_from_cd already handles with no
    extra parameter, so not one of those call sites changes.

    The mutation is deliberately **additive**: the <reference> child stays
    exactly where it was, and only originalText's own `.text` (empty for
    the reference shape) is filled in. Nothing is removed, so a provenance
    source_location pointing at code/originalText stays accurate and any
    future consumer wanting the raw reference can still read it. The parsed
    tree is this app's own per-request copy (app/cda/pipeline.py parses
    fresh each time) and validation parses its own separate document, so no
    other pillar observes the mutation."""
    anchors = narrative_anchor_text(section)
    if not anchors:
        return
    for original_text in section.iter(f"{{{CDA_NS}}}originalText"):
        if (original_text.text or "").strip():
            continue  # Already inline - nothing to resolve.
        reference = find_child(original_text, "reference")
        raw_value = reference.get("value") if reference is not None else None
        if not raw_value or not raw_value.startswith("#"):
            continue
        resolved = anchors.get(raw_value[1:])
        if resolved:
            original_text.text = resolved


def build_codeable_concept_from_cd(element) -> CodeableConcept | None:
    """Build a CodeableConcept from a CD-shaped element (a <code .../> or
    <value xsi:type="CD" .../> - both carry the same @code/@codeSystem/
    @displayName attributes). Returns None when the element is absent or
    has no @code, matching the established "no code -> None" convention
    (see app/fhir_models/builders.py::build_codeable_concept_from_cwe).

    A nested <originalText> maps to CodeableConcept.text, per the C-CDA on
    FHIR IG's own mappingGuidance.html ("when converting to a FHIR data
    type that contains a text field, like CodeableConcept, this is a direct
    map"). Only the *inline* text shape is read here - the sibling
    <reference value="#ID"/> shape is converted to inline text up front by
    resolve_narrative_references above, so both real-world shapes land here
    identically without this function needing narrative context of its
    own."""
    result = coded_value(element)
    if result is None:
        return None
    code, display_name, code_system_oid = result
    system = OID_TO_FHIR_SYSTEM.get(code_system_oid) or (
        f"urn:oid:{code_system_oid}" if code_system_oid else CD_FALLBACK_SYSTEM
    )
    coding = Coding(system=system, code=code)
    if display_name:
        coding.display = display_name
    concept = CodeableConcept(coding=[coding])

    original_text = find_child(element, "originalText")
    if original_text is not None:
        text = " ".join((original_text.text or "").split())
        if text:
            concept.text = text
    return concept


def iter_nested_observations(parent, type_code: str):
    """Yield each nested <observation> reached via
    entryRelationship[@typeCode=type_code]/observation - the shared
    traversal shape behind every Concern-Act-style nested-observation
    lookup in this app. Promoted here once app/cda/problems.py became a
    second real consumer of the identical walk app/cda/allergies.py already
    had: Problems' own Status Observation lookup matches by LOINC code
    (there's no distinct templateId to key off), while Allergies' Status/
    Criticality/Severity Observation lookups match by templateId (see
    find_nested_observation below) - genuinely different match criteria on
    top of the same traversal, not one duplicating the other by accident,
    so callers apply their own match test rather than this function
    guessing which one to use."""
    for relationship in find_all(parent, "entryRelationship"):
        if relationship.get("typeCode") != type_code:
            continue
        observation = find_child(relationship, "observation")
        if observation is not None:
            yield observation


def find_nested_observation(parent, type_code: str, template_id: str):
    """templateId-matching convenience wrapper over
    iter_nested_observations - the "check the relationship type, not just
    the nested templateId" discipline this app needs whenever a
    wrongly-typed relationship's nested element could otherwise falsely
    match a templateId check alone (see CLAUDE.md's Problems-section
    history of exactly this bug). Used by every Allergies nested-
    observation lookup (Status/Criticality/Severity Observations)."""
    for observation in iter_nested_observations(parent, type_code):
        if has_template_id(observation, template_id):
            return observation
    return None


def record_coding(recorder, resource_id: str, relative_path: str, base_location: str, concept) -> None:
    """Record a CodeableConcept's own `code` *and* `display`.

    Call sites that recorded only `.coding[0].code` left `@displayName`
    looking unread, so the drop register reported it as lost data even
    though the mapper had carried it into `.coding[0].display`. Recording
    both together in one helper keeps the pair from drifting apart again.
    """
    if not recorder or concept is None:
        return
    if concept.text:
        # CodeableConcept.text comes from <originalText>, which
        # resolve_narrative_references has already de-referenced into the
        # element by the time any builder sees it. Recording only the
        # coding left it looking dropped while the Bundle carried it.
        recorder.record(resource_id, f"{relative_path}.text", f"{base_location}/originalText", concept.text)
    if not concept.coding:
        return
    coding = concept.coding[0]
    if coding.code:
        recorder.record(resource_id, f"{relative_path}.coding[0].code", f"{base_location}/@code", coding.code)
    if coding.display:
        recorder.record(
            resource_id, f"{relative_path}.coding[0].display", f"{base_location}/@displayName", coding.display
        )
    if coding.system:
        # @codeSystem is carried into Coding.system (translated through
        # OID_TO_FHIR_SYSTEM when the OID is one FHIR names, kept as
        # urn:oid: otherwise), so it is mapped rather than lost. Recorded
        # rather than merely excluded, so the crosswalk shows which code
        # system the conversion actually used.
        recorder.record(
            resource_id,
            f"{relative_path}.coding[0].system",
            f"{base_location}/@codeSystem",
            coding.system,
        )


def record_quantity(recorder, resource_id: str, relative_path: str, base_location: str, quantity) -> None:
    """Record a Quantity's own `value` *and* `unit` - `@unit` is read by
    build_quantity_from_pq but was never recorded by several callers."""
    if not recorder or quantity is None:
        return
    if quantity.value is not None:
        recorder.record(resource_id, f"{relative_path}.value", f"{base_location}/@value", str(quantity.value))
    if quantity.unit:
        recorder.record(resource_id, f"{relative_path}.unit", f"{base_location}/@unit", quantity.unit)


def build_quantity_from_pq(element) -> Quantity | None:
    """A PQ-shaped element (@value/@unit directly on the element, e.g.
    doseQuantity, Vital Sign Observation/value, Result Observation/value)
    -> Quantity. An IVL_PQ range (low/high children instead of a bare
    @value) is left unmapped rather than guessed at - not this function's
    job to disambiguate which bound a caller wants when a range is given
    instead of a fixed value. Deliberately value+unit only, no system/code
    - the official C-CDA on FHIR IG's own worked examples for both
    Medications' doseQuantity and Vital Signs/Results' own value stop at
    unit too, not adding a UCUM system/code, so this matches rather than
    over-building. Promoted here once app/cda/medications.py's
    _resolve_pq and app/cda/immunizations.py's _resolve_dose_quantity
    were found to be byte-for-byte identical, with vitals.py/results.py
    becoming a third and fourth real consumer of the same shape."""
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


def build_identifier(id_element, fallback_system: str) -> Identifier | None:
    """A single CDA `<id root="..." extension="...">` -> Identifier -
    public (not module-private) since app/cda/procedures.py became a
    second real consumer of the identical root/extension resolution
    already used here for patient/encounter/document ids."""
    if id_element is None:
        return None
    extension = id_element.get("extension")
    root = id_element.get("root")
    if extension:
        system = f"urn:oid:{root}" if root else fallback_system
        return Identifier(system=system, value=extension)
    if root:
        # An II with a root but no extension is still a complete identifier
        # per the HL7 datatype spec - the root itself IS the identifier
        # value, not "no identifier". Represented the same way FHIR's own
        # CDA-on-FHIR mapping guidance represents a bare root.
        return Identifier(system="urn:ietf:rfc:3986", value=f"urn:oid:{root}")
    return None


def build_identifiers(
    id_elements, fallback_system: str, resource_id: str | None = None, location_prefix: str | None = None, recorder=None
) -> list[Identifier]:
    """`resource_id`/`location_prefix`/`recorder` are optional (see
    app/provenance/recorder.py) - when all three are given, each kept
    identifier's `.value` is recorded against `{location_prefix}[{index}]`
    (the 0-based index into the *kept* list, matching FHIR's own array
    index - an `<id>` element with neither `@root` nor `@extension` is
    skipped by build_identifier and correctly never consumes an index)."""
    identifiers = []
    for id_element in id_elements:
        identifier = build_identifier(id_element, fallback_system)
        if identifier:
            index = len(identifiers)
            identifiers.append(identifier)
            if recorder and resource_id and location_prefix:
                # Point at the attribute the value actually came from, not
                # the bare <id> element. cda_locator resolves an element
                # path to its start tag, which it cannot always do for a
                # childless <id> - so a mapped identifier looked unread and
                # every CDA id reported as dropped data.
                value_attribute = "@extension" if id_element.get("extension") else "@root"
                recorder.record(
                    resource_id,
                    f"identifier[{index}].value",
                    xpath_location(f"{location_prefix}[{index}]", value_attribute),
                    identifier.value,
                    source_value=id_element.get(value_attribute.lstrip("@")),
                )
                # `@root` drives .system whenever an `@extension` is present
                # (build_identifier above). Recording only .value left the
                # root looking unread - the same gap PID-3.4 had on the
                # HL7v2 side, and one the drop register reports as data loss.
                if id_element.get("root") and id_element.get("extension"):
                    recorder.record(
                        resource_id,
                        f"identifier[{index}].system",
                        xpath_location(f"{location_prefix}[{index}]", "@root"),
                        identifier.system,
                    )
    return identifiers


def _build_patient_identifiers(patient_role, resource_id: str | None = None, recorder=None) -> list[Identifier]:
    return build_identifiers(
        find_all(patient_role, "id"),
        "urn:interop-tools:cda-patient-id",
        resource_id=resource_id,
        location_prefix="recordTarget/patientRole/id",
        recorder=recorder,
    )


def _build_patient_names(patient_element, resource_id: str | None = None, recorder=None) -> list[HumanName]:
    names = []
    for i, name_element in enumerate(find_all(patient_element, "name")):
        family_element = find_child(name_element, "family")
        family = (family_element.text or "").strip() if family_element is not None else ""
        given_parts = [g.text.strip() for g in find_all(name_element, "given") if g.text and g.text.strip()]
        if not family and not given_parts:
            continue
        # The source's own use code wins where it maps. Position was the
        # only signal before, which labelled a leading <name use="P">
        # (pseudonym) "official" - stating something about the patient the
        # document never said. Position stays the fallback for a name that
        # carries no use, or one whose code has no FHIR counterpart.
        name_index = len(names)
        source_use = (name_element.get("use") or "").strip().upper()
        mapped_use = _NAME_USE_MAP.get(source_use)
        name = HumanName(use=mapped_use or ("official" if i == 0 else "old"))
        if recorder and resource_id:
            location = xpath_location("recordTarget", "patientRole", "patient", f"name[{i}]", "@use")
            if mapped_use:
                recorder.record(resource_id, f"name[{name_index}].use", location, name.use, source_value=source_use)
            else:
                recorder.record_inferred(
                    resource_id,
                    f"name[{name_index}].use",
                    f"No usable name use code{f' ({source_use!r} has no FHIR counterpart)' if source_use else ''}; "
                    "the first name is taken as the official one.",
                    name.use,
                )
        if family:
            name.family = family
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"name[{name_index}].family",
                    xpath_location("recordTarget", "patientRole", "patient", f"name[{i}]", "family"),
                    family,
                )
        if given_parts:
            name.given = given_parts
            if recorder and resource_id:
                for j, given in enumerate(given_parts):
                    recorder.record(
                        resource_id,
                        f"name[{name_index}].given[{j}]",
                        xpath_location("recordTarget", "patientRole", "patient", f"name[{i}]", f"given[{j}]"),
                        given,
                    )
        names.append(name)
    return names


def _build_patient_addresses(patient_role, resource_id: str | None = None, recorder=None) -> list[Address]:
    addresses = []
    for i, addr_element in enumerate(find_all(patient_role, "addr")):
        # (source_index, text) pairs, not just text - streetAddressLine
        # entries can be sparse (some empty, filtered out), so the kept
        # line's own FHIR array index must not desync from which XML
        # element it actually came from.
        line_pairs = [
            (src_i, e.text.strip())
            for src_i, e in enumerate(find_all(addr_element, "streetAddressLine"))
            if e.text and e.text.strip()
        ]
        lines = [text for _, text in line_pairs]
        city_element = find_child(addr_element, "city")
        state_element = find_child(addr_element, "state")
        postal_code_element = find_child(addr_element, "postalCode")
        country_element = find_child(addr_element, "country")
        city = (city_element.text or "").strip() if city_element is not None else ""
        state = (state_element.text or "").strip() if state_element is not None else ""
        postal_code = (postal_code_element.text or "").strip() if postal_code_element is not None else ""
        country = (country_element.text or "").strip() if country_element is not None else ""
        if not any([lines, city, state, postal_code, country]):
            continue
        address = Address()
        addr_index = len(addresses)
        address_use = _ADDRESS_USE_MAP.get((addr_element.get("use") or "").strip().upper())
        if address_use:
            address.use = address_use
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"address[{addr_index}].use",
                    xpath_location("recordTarget", "patientRole", f"addr[{i}]", "@use"),
                    address_use,
                    source_value=addr_element.get("use"),
                )
        if lines:
            address.line = lines
            if recorder and resource_id:
                for j, (src_i, text) in enumerate(line_pairs):
                    recorder.record(
                        resource_id,
                        f"address[{addr_index}].line[{j}]",
                        xpath_location("recordTarget", "patientRole", f"addr[{i}]", f"streetAddressLine[{src_i}]"),
                        text,
                    )
        if city:
            address.city = city
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"address[{addr_index}].city",
                    xpath_location("recordTarget", "patientRole", f"addr[{i}]", "city"),
                    city,
                )
        if state:
            address.state = state
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"address[{addr_index}].state",
                    xpath_location("recordTarget", "patientRole", f"addr[{i}]", "state"),
                    state,
                )
        if postal_code:
            address.postalCode = postal_code
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"address[{addr_index}].postalCode",
                    xpath_location("recordTarget", "patientRole", f"addr[{i}]", "postalCode"),
                    postal_code,
                )
        if country:
            address.country = country
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"address[{addr_index}].country",
                    xpath_location("recordTarget", "patientRole", f"addr[{i}]", "country"),
                    country,
                )
        addresses.append(address)
    return addresses


def _build_patient_telecoms(patient_role, resource_id: str | None = None, recorder=None) -> list[ContactPoint]:
    telecoms = []
    for i, telecom_element in enumerate(find_all(patient_role, "telecom")):
        raw_value = telecom_element.get("value")
        if not raw_value:
            continue
        scheme, _, value = raw_value.partition(":")
        contact_point = ContactPoint(value=value or raw_value)
        telecom_index = len(telecoms)
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"telecom[{telecom_index}].value",
                xpath_location("recordTarget", "patientRole", f"telecom[{i}]", "@value"),
                contact_point.value,
                source_value=raw_value,
            )
        system = _TELECOM_SCHEME_TO_SYSTEM.get(scheme.lower())
        if system:
            contact_point.system = system
        use = _TELECOM_USE_MAP.get(telecom_element.get("use", ""))
        if use:
            contact_point.use = use
            if recorder and resource_id:
                recorder.record(
                    resource_id,
                    f"telecom[{telecom_index}].use",
                    xpath_location("recordTarget", "patientRole", f"telecom[{i}]", "@use"),
                    use,
                    source_value=telecom_element.get("use"),
                )
        telecoms.append(contact_point)
    return telecoms


def build_contact_point_from_telecom(telecom_element) -> ContactPoint | None:
    """A single <telecom> element (e.g. an assignedEntity's/
    representedOrganization's/participantRole's own <telecom> - not the
    patient's own repeating telecom list _build_patient_telecoms handles)
    -> ContactPoint. Promoted here, public, once app/cda/procedures.py
    became a second real consumer of the identical scheme/use resolution
    _build_patient_telecoms already had inline - unlike that function,
    this one does no recording of its own, since a single-telecom caller's
    own resource id/relative path/source location vary per call site;
    the caller records against the returned object's own resolved fields,
    the same division of labor build_quantity_from_pq/
    build_codeable_concept_from_cd already establish."""
    if telecom_element is None:
        return None
    raw_value = telecom_element.get("value")
    if not raw_value:
        return None
    scheme, _, value = raw_value.partition(":")
    contact_point = ContactPoint(value=value or raw_value)
    system = _TELECOM_SCHEME_TO_SYSTEM.get(scheme.lower())
    if system:
        contact_point.system = system
    use = _TELECOM_USE_MAP.get(telecom_element.get("use", ""))
    if use:
        contact_point.use = use
    return contact_point


def build_patient_from_header(document, recorder=None) -> Patient:
    """recordTarget/patientRole/patient -> Patient. Raises
    MissingSegmentError when the header lacks a patientRole/patient at
    all - reused from app.hl7.errors since the meaning ("a required
    structural piece is absent") is format-agnostic."""
    record_target = find_child(document, "recordTarget")
    patient_role = find_child(record_target, "patientRole") if record_target is not None else None
    if patient_role is None:
        raise MissingSegmentError("recordTarget/patientRole is missing")
    patient_element = find_child(patient_role, "patient")
    if patient_element is None:
        raise MissingSegmentError("recordTarget/patientRole/patient is missing")

    patient_id = str(uuid.uuid4())
    patient = Patient(id=patient_id)

    identifiers = _build_patient_identifiers(patient_role, resource_id=patient_id, recorder=recorder)
    if identifiers:
        patient.identifier = identifiers

    names = _build_patient_names(patient_element, resource_id=patient_id, recorder=recorder)
    if names:
        patient.name = names

    gender_element = find_child(patient_element, "administrativeGenderCode")
    if gender_element is not None:
        code = (gender_element.get("code") or "").strip().upper()
        if code:
            gender = _GENDER_MAP.get(code, "unknown")
            patient.gender = gender
            if recorder:
                recorder.record(
                    patient_id,
                    "gender",
                    xpath_location("recordTarget", "patientRole", "patient", "administrativeGenderCode", "@code"),
                    gender,
                    source_value=code,
                )

    birth_time = find_child(patient_element, "birthTime")
    if birth_time is not None:
        birth_time_raw = ts_value(birth_time)
        birth_date = parse_hl7_date(birth_time_raw or "")
        if birth_date:
            patient.birthDate = birth_date
            if recorder:
                recorder.record(
                    patient_id,
                    "birthDate",
                    xpath_location("recordTarget", "patientRole", "patient", "birthTime", "@value"),
                    birth_date,
                    source_value=birth_time_raw,
                )

    addresses = _build_patient_addresses(patient_role, resource_id=patient_id, recorder=recorder)
    if addresses:
        patient.address = addresses

    telecoms = _build_patient_telecoms(patient_role, resource_id=patient_id, recorder=recorder)
    if telecoms:
        patient.telecom = telecoms

    return patient


_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_DEFAULT_ENCOUNTER_CLASS = "AMB"
# componentOf/encompassingEncounter/code draws from ActEncounterCode
# (2.16.840.1.113883.5.4), the same source vocabulary FHIR's own
# Encounter.class binding (v3-ActCode) mirrors - a genuine near-direct
# mapping, not a guess, with a disclosed default when absent/unrecognized.
# Public (not module-private) - reused by app/cda/validation.py so its
# "unrecognized encounter class" rule can never drift from what this
# mapper actually treats as recognized.
RECOGNIZED_ENCOUNTER_CLASSES = {"AMB", "EMER", "IMP", "ACUTE", "NONAC", "PRENC", "SS", "VR"}


def effective_time_location(base_path: str, element, bound: str) -> str:
    """Which of IVL_TS's four legal shapes ivl_ts_bounds() actually read
    `bound` ("low" or "high") from for a given effectiveTime element - a
    bare @value (the same value used for both bounds), a dedicated
    <low>/<high> child, or <center> (an approximate single point, ivl_ts_
    bounds' own fourth branch - see that function's own docstring) - so the
    Data Specification crosswalk points at the real shape a given document
    used, rather than guessing one. Purely for provenance; doesn't change
    ivl_ts_bounds' own resolution logic. Public (not module-private) -
    app/cda/problems.py's own onset/abatement dates are a second real
    consumer of the identical resolution this function's own first caller
    (build_encounter_from_header's period.start/end) already needed, so it
    lives here rather than being duplicated."""
    if element is not None and element.get("value"):
        return xpath_location(f"{base_path}/@value")
    if element is not None and find_child(element, "low") is None and find_child(element, "high") is None:
        center = find_child(element, "center")
        if center is not None and center.get("value"):
            return xpath_location(f"{base_path}/center/@value")
    return xpath_location(f"{base_path}/{bound}/@value")


def build_encounter_from_header(document, patient_id: str, recorder=None) -> Encounter | None:
    """componentOf/encompassingEncounter -> Encounter, or None when absent
    (a CCD isn't necessarily bound to one encounter - it's a summary
    document). Status is honestly "unknown" rather than inferred - unlike
    ADT's real-time admit/discharge triggers, a CCD gives no reliable
    lifecycle signal to infer status from, same rationale as ORU's/MDM's
    minimal PV1-derived Encounters (app/mappings/oru.py,
    app/mappings/mdm.py -> app/mappings/common.py::build_minimal_encounter)."""
    component_of = find_child(document, "componentOf")
    encompassing_encounter = find_child(component_of, "encompassingEncounter") if component_of is not None else None
    if encompassing_encounter is None:
        return None

    class_code = _DEFAULT_ENCOUNTER_CLASS
    class_display = ""
    code_element = find_child(encompassing_encounter, "code")
    if code_element is not None:
        raw_code = (code_element.get("code") or "").strip().upper()
        if raw_code in RECOGNIZED_ENCOUNTER_CLASSES:
            class_code = raw_code
            # Only carried for a code we actually recognised: a display
            # name belonging to some other code would misdescribe the
            # default class we fell back to.
            class_display = (code_element.get("displayName") or "").strip()

    encounter_id = str(uuid.uuid4())
    encounter = Encounter(
        id=encounter_id,
        status="unknown",
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=Coding(
            system=_ENCOUNTER_CLASS_SYSTEM, code=class_code, display=class_display or None
        ),
    )
    if recorder:
        # Recorded as direct (not inferred) even when code_element is
        # absent/unrecognized and class_code falls back to the disclosed
        # default - the same "point at where the value would have come
        # from, even when the field's own table default fired" precedent
        # app/mappings/adt.py::build_encounter_core already established for
        # PV1-2's identical recognized/unrecognized-code-with-a-default
        # shape.
        recorder.record(
            encounter_id,
            "class.code",
            xpath_location("componentOf", "encompassingEncounter", "code", "@code"),
            class_code,
            source_value=code_element.get("code") if code_element is not None else None,
        )
        if class_display:
            recorder.record(
                encounter_id,
                "class.display",
                xpath_location("componentOf", "encompassingEncounter", "code", "@displayName"),
                class_display,
            )

    identifiers = build_identifiers(
        find_all(encompassing_encounter, "id"),
        "urn:interop-tools:cda-encounter-id",
        resource_id=encounter_id,
        location_prefix="componentOf/encompassingEncounter/id",
        recorder=recorder,
    )
    if identifiers:
        encounter.identifier = identifiers

    effective_time = find_child(encompassing_encounter, "effectiveTime")
    period_start, period_end = (parse_partial_ts(v) for v in ivl_ts_bounds(effective_time))
    if period_start or period_end:
        period = Period()
        if period_start:
            period.start = period_start
            if recorder:
                recorder.record(
                    encounter_id,
                    "period.start",
                    effective_time_location("componentOf/encompassingEncounter/effectiveTime", effective_time, "low"),
                    period_start,
                )
        if period_end:
            period.end = period_end
            if recorder:
                recorder.record(
                    encounter_id,
                    "period.end",
                    effective_time_location("componentOf/encompassingEncounter/effectiveTime", effective_time, "high"),
                    period_end,
                )
        encounter.period = period

    return encounter


def assemble_bundle(document, patient: Patient, *resources: Resource, recorder=None) -> Bundle:
    """Wrap a Patient plus any number of additional resources into a
    Bundle, with ClinicalDocument-derived metadata (id -> Bundle.identifier,
    effectiveTime -> Bundle.timestamp) - the CDA-header equivalent of
    app/mappings/common.py::assemble_bundle, which reads MSH fields instead.
    Bundle.type is "collection", matching every existing HL7v2 pipeline's
    output shape - a proper FHIR-Document-shaped Bundle(type="document")
    with a Composition is deliberately out of scope for this slice (see
    CLAUDE.md). `recorder` is optional; when given, the document's own
    id/effectiveTime are recorded against `bundle.id` itself - see
    app/provenance/resolver.py's own bundle.id special case."""
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")

    identifier = build_identifier(find_child(document, "id"), "urn:interop-tools:cda-document-id")
    if identifier:
        bundle.identifier = identifier
        if recorder:
            recorder.record(bundle.id, "identifier.value", xpath_location("id"), identifier.value)

    # Bundle.timestamp is FHIR "instant", which (unlike Condition's
    # onset/abatement dateTime) has NO date-only form - it requires full
    # date+time+timezone precision. parse_partial_ts's date-only fallback
    # would produce a value the instant regex rejects, crashing resource
    # construction on a perfectly convertible document whose
    # ClinicalDocument/effectiveTime happens to be date-only. Use
    # parse_hl7_datetime directly (returns None rather than a date-only
    # string), matching app/mappings/common.py::assemble_bundle's own
    # deliberate choice not to date-only-fallback for this same reason.
    document_effective_time_raw = ts_value(find_child(document, "effectiveTime"))
    timestamp = parse_hl7_datetime(document_effective_time_raw or "")
    if timestamp:
        bundle.timestamp = timestamp
        if recorder:
            recorder.record(
                bundle.id, "timestamp", xpath_location("effectiveTime", "@value"), timestamp, source_value=document_effective_time_raw
            )

    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{patient.id}", resource=patient)]
    bundle.entry.extend(BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources)
    return bundle


def build_sectioned_bundle(document, recorder=None) -> Bundle:
    """Shared build_bundle() implementation for any C-CDA document type
    whose conversion is "header (Patient + optional Encounter) + walk every
    structuredBody section through SECTION_BUILDERS" - CCD and Discharge
    Summary both happen to be exactly this shape. Extracted into common.py
    once a second real consumer existed (app/cda/discharge_summary.py) -
    the same "extract once a second real consumer exists" pattern already
    used for build_minimal_pv1_fields/build_minimal_encounter (see
    CLAUDE.md), not a speculative abstraction ahead of need. Document types
    with genuinely different structure (e.g. a future document type that
    ISN'T just "header + generic sections") should NOT be forced through
    this helper - implement build_bundle() directly instead, the way
    CcdBuilder/DischargeSummaryBuilder did before this was extracted.

    `recorder` is threaded into every registered SECTION_BUILDERS entry
    uniformly, whether or not that section's own builder acts on it yet -
    see app/cda/medications.py::build_medication_requests' own docstring
    for why every section builder accepts it regardless."""
    # Deferred import: app.cda.registry imports the concrete document
    # builder modules (ccd, discharge_summary) at its own module-load time,
    # so importing it back at this module's top level would be circular -
    # same reasoning as ccd.py's own pre-extraction deferred import.
    from app.cda.registry import SECTION_BUILDERS
    from app.cda.social_history import (
        SECTION_TEMPLATE_ID as SOCIAL_HISTORY_SECTION_TEMPLATE_ID,
        apply_patient_extensions,
    )

    patient = build_patient_from_header(document, recorder=recorder)
    encounter = build_encounter_from_header(document, patient.id, recorder=recorder)
    resources = [encounter] if encounter is not None else []

    for section_index, section in enumerate(
        find_all(document, "component/structuredBody/component/section")
    ):
        for section_template_id, builder in SECTION_BUILDERS.items():
            if has_template_id(section, section_template_id):
                # Convert this section's own narrative-referenced
                # originalText into inline text before its entries are
                # walked - the one place with both the narrative and the
                # entries in scope. See that function's own docstring for
                # why a pre-pass rather than threading a lookup dict.
                resolve_narrative_references(section)
                resources.extend(builder(section, patient.id, recorder=recorder))
                # Social History carries observations the IG maps to
                # Patient extensions rather than to Observations. Handled
                # here rather than in the section builder because
                # SECTION_BUILDERS hands builders only `patient.id`, and
                # these have no resource of their own to return.
                if section_template_id == SOCIAL_HISTORY_SECTION_TEMPLATE_ID:
                    apply_patient_extensions(
                        section, patient, section_index, recorder=recorder
                    )
                break
        # An unrecognized section is silently skipped - disclosed, not a
        # bug (see CLAUDE.md's per-document-type scope-limit notes).

    return assemble_bundle(document, patient, *resources, recorder=recorder)

# Disclosed local system for an assignedEntity/assignedAuthor id that
# carries no recognisable authority of its own.
_PRACTITIONER_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-practitioner-id"

# An authoring device's own <id> gets its own fallback system rather than
# borrowing the practitioner one - the two identify different kinds of thing.
_DEVICE_ID_FALLBACK_SYSTEM = "urn:interop-tools:cda-device-id"


def build_authoring_device(assigned_author, author_base: str, recorder=None) -> Device | None:
    """`<assignedAuthor><assignedAuthoringDevice>` -> Device.

    CDA models the author as a choice - `assignedPerson` OR
    `assignedAuthoringDevice` - so an EHR that generated the content is a
    perfectly ordinary author. Building a Practitioner for one would
    assert a software system is a person, and would drop its model and
    software names on the way, since Practitioner has nowhere to put them.
    """
    device_element = find_child(assigned_author, "assignedAuthoringDevice")
    if device_element is None:
        return None

    device_id = str(uuid.uuid4())
    device = Device(id=device_id)
    device_base = xpath_location(author_base, "assignedAuthoringDevice")
    names = []
    for tag, name_type in (
        ("manufacturerModelName", "model-name"),
        ("softwareName", "user-friendly-name"),
    ):
        element = find_child(device_element, tag)
        text = (element.text or "").strip() if element is not None else ""
        if not text:
            continue
        names.append(DeviceDeviceName(name=text, type=name_type))
        if recorder:
            recorder.record(
                device_id,
                f"deviceName[{len(names) - 1}].name",
                xpath_location(device_base, tag),
                text,
            )
    if names:
        device.deviceName = names

    id_element = find_child(assigned_author, "id")
    identifier = build_identifier(id_element, _DEVICE_ID_FALLBACK_SYSTEM) if id_element is not None else None
    if identifier:
        device.identifier = [identifier]
        if recorder:
            recorder.record(
                device_id, "identifier[0].value", xpath_location(author_base, "id"), identifier.value
            )

    if not names and identifier is None:
        return None
    return device


def build_author_participant(element, base_location: str, allow_device: bool = True, recorder=None):
    """An element's own `<author><assignedAuthor>` -> (Reference, resource),
    or None when there is no author or nothing identifying in it.

    **The resource is a Practitioner or a Device**, mirroring the choice
    CDA itself models - `assignedPerson` or `assignedAuthoringDevice`,
    with `<id>` required either way. Callers pass `allow_device=False`
    when their own target cannot reference one: `MedicationRequest.requester`
    accepts Device, while `Observation.performer` and `Procedure.recorder`
    do not, so a device-authored entry there is left unmapped rather than
    recorded as a person.

    **Only for sections the IG gives author a resource attribute.** Its
    tables route `author` to a Provenance resource in most sections and
    name a plain attribute in only a few - Medication Activity and
    INT-mood Immunization to `MedicationRequest.requester`, Vital Sign
    Observation to `.performer`, Procedure to `.recorder`. Problems,
    Allergies and Results say Provenance only, so they deliberately do not
    call this; mapping them would invent a target the IG declines to name.

    Only the first `<author>` is used, matching the singular Reference
    every current target is.
    """
    author_element = find_child(element, "author")
    if author_element is None:
        return None
    assigned_author = find_child(author_element, "assignedAuthor")
    if assigned_author is None:
        return None
    author_base = xpath_location(base_location, "author", "assignedAuthor")
    if find_child(assigned_author, "assignedAuthoringDevice") is not None:
        if not allow_device:
            return None
        device = build_authoring_device(assigned_author, author_base, recorder=recorder)
        if device is None:
            return None
        return Reference(reference=f"urn:uuid:{device.id}"), device

    practitioner = build_practitioner_from_assigned_entity(assigned_author, author_base, recorder=recorder)
    if practitioner is None:
        return None
    return Reference(reference=f"urn:uuid:{practitioner.id}"), practitioner


def build_practitioner_from_assigned_entity(assigned_entity, location: str, recorder=None) -> Practitioner | None:
    """An `<assignedEntity>` or `<assignedAuthor>` -> Practitioner.

    Both CDA participations carry the same shape - an `<id>`, an
    optional `<assignedPerson><name>`, an optional
    `<representedOrganization>` - so one builder serves performers and
    authors alike. Promoted here from procedures.py once the sections
    the IG gives an author target needed it too.

    Original notes: assignedEntity/id -> identifier, assignedEntity/assignedPerson/name
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
            # Point at the attribute that actually supplied the value, the
            # way build_identifiers already does: an <id> carries its value
            # in @extension (or @root when that is all it has), never as
            # element text, so a bare `.../id` location resolves to nothing.
            value_attribute = "@extension" if id_element.get("extension") else "@root"
            recorder.record(
                practitioner_id,
                "identifier[0].value",
                xpath_location(location, "id", value_attribute),
                identifier.value,
            )
            if identifier.system:
                # @root becomes Identifier.system (translated where FHIR
                # names the OID, kept as urn:oid: otherwise). Recording only
                # the value left @root looking dropped.
                recorder.record(
                    practitioner_id,
                    "identifier[0].system",
                    xpath_location(location, "id", "@root"),
                    identifier.system,
                )
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

