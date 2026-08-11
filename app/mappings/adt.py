import uuid
from abc import abstractmethod

import hl7
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.encounter import (
    Encounter,
    EncounterHospitalization,
    EncounterLocation,
    EncounterParticipant,
)
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
from app.hl7.errors import MappingError, MissingSegmentError
from app.hl7.parser import component_str, field_repetitions, field_str, require_segment
from app.mappings.base import MessageMapper

_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_PARTICIPATION_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
_DISCHARGE_DISPOSITION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0112"
_PATIENT_CLASS_MAP = {"I": "IMP", "O": "AMB", "E": "EMER", "P": "PRENC"}


def build_patient(pid) -> Patient:
    """PID -> Patient. Shared by every ADT trigger event; PID is mapped identically
    regardless of what triggered the message."""
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


def _location_display(pv1, field_num: int) -> str:
    facility = field_str(pv1, field_num, component=1)
    room = field_str(pv1, field_num, component=2)
    return " ".join(part for part in (facility, room) if part)


def discharge_datetime(pv1, evn) -> str | None:
    """Resolve a discharge/end datetime from PV1-45, falling back to EVN-2."""
    value = parse_hl7_datetime(field_str(pv1, 45))
    if value:
        return value
    if evn is not None:
        return parse_hl7_datetime(field_str(evn, 2))
    return None


def build_encounter_core(pv1, evn, patient_id: str, status: str) -> Encounter:
    """Shared PV1/EVN -> Encounter mapping: class, identifier, current location,
    attending participant, and admit/discharge period. `status` is supplied by
    the caller since it depends on which trigger event is being mapped."""
    encounter_id = str(uuid.uuid4())
    patient_class = field_str(pv1, 2).strip().upper()
    class_code = _PATIENT_CLASS_MAP.get(patient_class, "AMB")

    encounter = Encounter(
        id=encounter_id,
        status=status,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=Coding(system=_ENCOUNTER_CLASS_SYSTEM, code=class_code),
    )

    visit_number = field_str(pv1, 19)
    if visit_number:
        encounter.identifier = [Identifier(system="urn:hl7-tools:visit-number", value=visit_number)]

    location_display = _location_display(pv1, 3)
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


def _assemble_bundle(msh, patient: Patient, encounter: Encounter) -> Bundle:
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


class BaseAdtMapper(MessageMapper):
    """Shared orchestration for ADT trigger events: require MSH/PID/PV1 (EVN is
    optional), build the Patient, delegate Encounter construction to the
    subclass (the part that actually differs per trigger event), then
    assemble the Bundle."""

    message_type = "ADT"

    @abstractmethod
    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        ...

    def to_bundle(self, message: hl7.Message) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        pv1 = require_segment(message, "PV1")
        try:
            evn = require_segment(message, "EVN")
        except MissingSegmentError:
            evn = None

        patient = build_patient(pid)
        encounter = self.build_encounter(pv1, evn, patient.id)
        return _assemble_bundle(msh, patient, encounter)


class AdtA01Mapper(BaseAdtMapper):
    """A01 - Admit/visit notification. Encounter is open (in-progress)."""

    trigger_event = "A01"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        return build_encounter_core(pv1, evn, patient_id, status="in-progress")


class AdtA04Mapper(BaseAdtMapper):
    """A04 - Register a patient (typically outpatient). Encounter is open,
    same as A01; kept as its own mapper so register-specific handling has
    somewhere to go later without touching A01."""

    trigger_event = "A04"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        return build_encounter_core(pv1, evn, patient_id, status="in-progress")


class AdtA02Mapper(BaseAdtMapper):
    """A02 - Transfer a patient. Adds PV1-6 (prior location) ahead of the
    current PV1-3 location as location history."""

    trigger_event = "A02"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        encounter = build_encounter_core(pv1, evn, patient_id, status="in-progress")
        prior_display = _location_display(pv1, 6)
        if prior_display:
            prior_location = EncounterLocation(location=Reference(display=prior_display), status="completed")
            if encounter.location:
                for loc in encounter.location:
                    loc.status = "active"
                encounter.location = [prior_location, *encounter.location]
            else:
                encounter.location = [prior_location]
        return encounter


class AdtA03Mapper(BaseAdtMapper):
    """A03 - Discharge/end visit. Requires a discharge date/time (PV1-45 or
    EVN-2 fallback) since a finished encounter with no end time is a real
    data-correctness problem, not something to guess at silently."""

    trigger_event = "A03"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        discharge_dt = discharge_datetime(pv1, evn)
        if not discharge_dt:
            raise MappingError("ADT^A03 (discharge) requires a discharge date/time (PV1-45 or EVN-2)")

        encounter = build_encounter_core(pv1, evn, patient_id, status="finished")
        period = encounter.period or Period()
        period.end = discharge_dt
        encounter.period = period

        disposition_code = field_str(pv1, 36)
        if disposition_code:
            encounter.hospitalization = EncounterHospitalization(
                dischargeDisposition=CodeableConcept(
                    coding=[Coding(system=_DISCHARGE_DISPOSITION_SYSTEM, code=disposition_code)]
                )
            )
        return encounter


class AdtA08Mapper(BaseAdtMapper):
    """A08 - Update patient information. Carries no explicit lifecycle signal
    and this app holds no persisted encounter state to update, so status is
    inferred: finished if a discharge time is present, in-progress otherwise."""

    trigger_event = "A08"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        status = "finished" if field_str(pv1, 45) else "in-progress"
        return build_encounter_core(pv1, evn, patient_id, status=status)
