import uuid

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.resource import Resource

from app.fhir_models.builders import (
    build_addresses,
    build_human_names,
    build_phone_telecom,
    hl7_sex_to_fhir_gender,
    parse_hl7_date,
    parse_hl7_datetime,
)
from app.hl7.parser import component_str, field_repetitions, field_str

_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_PATIENT_CLASS_MAP = {"I": "IMP", "O": "AMB", "E": "EMER", "P": "PRENC"}


def build_patient(pid) -> Patient:
    """PID -> Patient. Shared by every HL7 message type; PID is mapped
    identically regardless of message type or trigger event."""
    patient_id = str(uuid.uuid4())
    identifiers = []
    for repetition in field_repetitions(pid, 3):
        value = component_str(repetition, 1)
        if not value:
            continue
        system = component_str(repetition, 4) or "urn:hl7-tools:patient-id"
        identifiers.append(Identifier(system=system, value=value))

    patient = Patient(id=patient_id)
    if identifiers:
        patient.identifier = identifiers
    names = build_human_names(pid)
    if names:
        patient.name = names
    birth_date = parse_hl7_date(field_str(pid, 7))
    if birth_date:
        patient.birthDate = birth_date
    sex = field_str(pid, 8)
    if sex:
        patient.gender = hl7_sex_to_fhir_gender(sex)
    addresses = build_addresses(pid)
    if addresses:
        patient.address = addresses
    telecom = build_phone_telecom(pid)
    if telecom:
        patient.telecom = [telecom]
    return patient


def location_display(segment, field_num: int) -> str:
    """Build a human-readable display string from a PL-shaped field (facility
    + room, e.g. PV1-3, PV1-6, AIL-3). Shared across message types since PL
    fields are formatted identically regardless of which segment they're in."""
    facility = field_str(segment, field_num, component=1)
    room = field_str(segment, field_num, component=2)
    return " ".join(part for part in (facility, room) if part)


def person_display(segment, field_num: int) -> str:
    """Build a human-readable display string from an XCN-shaped field (id^
    family^given, e.g. PV1-7, AIP-3). Shared across message types since XCN
    fields are formatted identically regardless of which segment they're in.
    Falls back to the id component (component 1) when family/given are both
    empty, so this stays consistent with build_practitioner_from_xcn's
    presence check ("id or family or given") - without the fallback, an
    id-only field would materialize a Practitioner but pair it with an empty
    display, which FHIR's Reference.display rejects (must be non-empty)."""
    family = field_str(segment, field_num, component=2)
    given = field_str(segment, field_num, component=3)
    name = ", ".join(part for part in (family, given) if part)
    return name or field_str(segment, field_num, component=1)


def build_practitioner_from_xcn(segment, field_num: int) -> Practitioner | None:
    """Build a real Practitioner resource from an XCN-shaped field (id^family^
    given^..., e.g. PV1-7, AIP-3). Returns None when the field is entirely
    empty. Shared so any future mapper needing a materialized (not just
    display-text) practitioner from an XCN field can reuse this rather than
    re-deriving it."""
    practitioner_id = field_str(segment, field_num, component=1)
    family = field_str(segment, field_num, component=2)
    given = field_str(segment, field_num, component=3)
    if not (practitioner_id or family or given):
        return None
    practitioner = Practitioner(id=str(uuid.uuid4()))
    if practitioner_id:
        practitioner.identifier = [Identifier(system="urn:hl7-tools:practitioner-id", value=practitioner_id)]
    if family or given:
        name = HumanName()
        if family:
            name.family = family
        if given:
            name.given = [given]
        practitioner.name = [name]
    return practitioner


def build_location_from_pl(segment, field_num: int) -> Location | None:
    """Build a real Location resource from a PL-shaped field (facility^room^
    bed^hospital, e.g. PV1-3, PV1-6, AIL-3). Returns None when the field is
    empty. Shared for the same reason as build_practitioner_from_xcn."""
    display = location_display(segment, field_num)
    if not display:
        return None
    return Location(id=str(uuid.uuid4()), name=display)


def resolve_encounter_class(pv1) -> Coding:
    """PV1-2 (patient class) -> Encounter.class Coding. Shared by every
    message type that derives an Encounter from a PV1 segment (ADT's
    admit/transfer/discharge encounters, ORU's optional result-reporting
    encounter) - the mapping from HL7's I/O/E/P codes to FHIR's
    IMP/AMB/EMER/PRENC is identical regardless of what triggered the
    message."""
    patient_class = field_str(pv1, 2).strip().upper()
    class_code = _PATIENT_CLASS_MAP.get(patient_class, "AMB")
    return Coding(system=_ENCOUNTER_CLASS_SYSTEM, code=class_code)


def build_visit_identifier(pv1) -> Identifier | None:
    """PV1-19 (visit number) -> an Identifier, or None when absent. Shared
    for the same reason as resolve_encounter_class."""
    visit_number = field_str(pv1, 19)
    if not visit_number:
        return None
    return Identifier(system="urn:hl7-tools:visit-number", value=visit_number)


def assemble_bundle(msh, patient: Patient, *resources: Resource) -> Bundle:
    """Wrap a Patient plus any number of additional resources (an Encounter,
    an Appointment, ...) into a Bundle, with MSH-derived metadata. Shared by
    every message type's mapper."""
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")
    control_id = field_str(msh, 10)
    if control_id:
        bundle.identifier = Identifier(system="urn:hl7-tools:message-control-id", value=control_id)
    message_timestamp = parse_hl7_datetime(field_str(msh, 7))
    if message_timestamp:
        bundle.timestamp = message_timestamp
    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{patient.id}", resource=patient)]
    bundle.entry.extend(BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources)
    return bundle
