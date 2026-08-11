import uuid

import hl7
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.encounter import Encounter, EncounterLocation, EncounterParticipant
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.reference import Reference

from app.fhir_models.builders import (
    build_addresses,
    build_human_names,
    build_phone_telecom,
    hl7_sex_to_fhir_gender,
    parse_hl7_date,
    parse_hl7_datetime,
)
from app.hl7.errors import MissingSegmentError
from app.hl7.parser import component_str, field_repetitions, field_str, require_segment
from app.mappings.base import MessageMapper

_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_PARTICIPATION_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
_PATIENT_CLASS_MAP = {"I": "IMP", "O": "AMB", "E": "EMER", "P": "PRENC"}


def _build_patient(pid) -> Patient:
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


def _build_encounter(pv1, evn, patient_id: str) -> Encounter:
    encounter_id = str(uuid.uuid4())
    patient_class = field_str(pv1, 2).strip().upper()
    class_code = _PATIENT_CLASS_MAP.get(patient_class, "AMB")

    encounter = Encounter(
        id=encounter_id,
        status="in-progress",
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=Coding(system=_ENCOUNTER_CLASS_SYSTEM, code=class_code),
    )

    visit_number = field_str(pv1, 19)
    if visit_number:
        encounter.identifier = [Identifier(system="urn:hl7-tools:visit-number", value=visit_number)]

    location_facility = field_str(pv1, 3, component=1)
    location_room = field_str(pv1, 3, component=2)
    location_display = " ".join(part for part in (location_facility, location_room) if part)
    if location_display:
        encounter.location = [EncounterLocation(location=Reference(display=location_display))]

    attending_family = field_str(pv1, 7, component=2)
    attending_given = field_str(pv1, 7, component=3)
    attending_display = ", ".join(part for part in (attending_family, attending_given) if part)
    if attending_display:
        encounter.participant = [
            EncounterParticipant(
                type=[CodeableConcept(coding=[Coding(system=_PARTICIPATION_TYPE_SYSTEM, code="ATND")])],
                individual=Reference(display=attending_display),
            )
        ]

    period_start = parse_hl7_datetime(field_str(pv1, 44))
    if not period_start and evn is not None:
        period_start = parse_hl7_datetime(field_str(evn, 2))
    period_end = parse_hl7_datetime(field_str(pv1, 45))
    if period_start or period_end:
        period = Period()
        if period_start:
            period.start = period_start
        if period_end:
            period.end = period_end
        encounter.period = period

    return encounter


class AdtA01Mapper(MessageMapper):
    message_type = "ADT"
    trigger_event = "A01"

    def to_bundle(self, message: hl7.Message) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        pv1 = require_segment(message, "PV1")
        try:
            evn = require_segment(message, "EVN")
        except MissingSegmentError:
            evn = None

        patient = _build_patient(pid)
        encounter = _build_encounter(pv1, evn, patient.id)

        bundle = Bundle(id=str(uuid.uuid4()), type="collection")
        control_id = field_str(msh, 10)
        if control_id:
            bundle.identifier = Identifier(system="urn:hl7-tools:message-control-id", value=control_id)
        message_timestamp = parse_hl7_datetime(field_str(msh, 7))
        if message_timestamp:
            bundle.timestamp = message_timestamp

        bundle.entry = [
            BundleEntry(fullUrl=f"urn:uuid:{patient.id}", resource=patient),
            BundleEntry(fullUrl=f"urn:uuid:{encounter.id}", resource=encounter),
        ]
        return bundle
