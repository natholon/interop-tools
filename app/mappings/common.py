import uuid

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.patient import Patient
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
    """Build a human-readable display string from an XCN-shaped field (family,
    given, e.g. PV1-7, AIP-3). Shared across message types since XCN fields
    are formatted identically regardless of which segment they're in."""
    family = field_str(segment, field_num, component=2)
    given = field_str(segment, field_num, component=3)
    return ", ".join(part for part in (family, given) if part)


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
