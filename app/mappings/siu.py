import uuid
from abc import abstractmethod

import hl7
from fhir.resources.R4B.appointment import Appointment, AppointmentParticipant
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.reference import Reference

from app.fhir_models.builders import build_codeable_concept_from_cwe, parse_hl7_datetime
from app.hl7.errors import MappingError
from app.hl7.parser import field_str, optional_segments, require_segment
from app.mappings.base import MessageMapper
from app.mappings.common import assemble_bundle, build_patient, location_display, person_display

_PARTICIPATION_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
_TRIGGER_EVENT_EXTENSION_URL = "urn:hl7-tools:siu-trigger-event"


def resolve_appointment_timing(sch, tq1_segments) -> tuple[str | None, str | None]:
    """Resolve (start, end) independently: TQ1-7/TQ1-8 (first TQ1 segment)
    when usable, else SCH-11's TQ composite value (component 4 = start
    date/time, component 5 = end date/time). Resolved per-field rather than
    all-or-nothing, so a TQ1 segment that only supplies one of the two (e.g.
    a start but no end) doesn't block a usable SCH-11 value for the other."""
    tq1 = tq1_segments[0] if tq1_segments else None
    tq1_start = parse_hl7_datetime(field_str(tq1, 7)) if tq1 is not None else None
    tq1_end = parse_hl7_datetime(field_str(tq1, 8)) if tq1 is not None else None
    start = tq1_start or parse_hl7_datetime(field_str(sch, 11, component=4))
    end = tq1_end or parse_hl7_datetime(field_str(sch, 11, component=5))
    return start, end


def _resolve_minutes_duration(sch, tq1_segments) -> int | None:
    """TQ1-6 (Service Duration) is preferred, consistent with TQ1 being
    preferred for timing generally - this avoids a stale legacy SCH-9
    duration contradicting a TQ1-derived start/end (e.g. on a reschedule
    where SCH-9 wasn't updated but TQ1 was). Falls back to SCH-9/10 when TQ1
    doesn't supply a usable duration; SCH-10's unit check is lenient
    (startswith "MIN") to tolerate spellings like "MINUTES", not just "MIN"."""
    if tq1_segments:
        tq1_duration = field_str(tq1_segments[0], 6)
        if tq1_duration.isdigit():
            return int(tq1_duration)
    duration = field_str(sch, 9)
    units = field_str(sch, 10).strip().upper()
    if duration.isdigit() and (not units or units.startswith("MIN")):
        return int(duration)
    return None


def _build_identifiers(sch) -> list[Identifier]:
    identifiers = []
    placer_id = field_str(sch, 1)
    if placer_id:
        identifiers.append(Identifier(system="urn:hl7-tools:placer-appointment-id", value=placer_id))
    filler_id = field_str(sch, 2)
    if filler_id:
        identifiers.append(Identifier(system="urn:hl7-tools:filler-appointment-id", value=filler_id))
    return identifiers


def _build_participants(patient, aip_segments, ail_segments, aig_segments) -> list[AppointmentParticipant]:
    """Patient first (referencing the real Patient resource), then one entry
    per AIP (personnel), AIL (location), and AIG (general resource/equipment)
    segment - all display-only references, no separate resources
    materialized for them. Only AIP gets a `type` (ATND); the participant
    value set has no fitting code for patient/location/equipment roles."""
    participants = [
        AppointmentParticipant(status="accepted", actor=Reference(reference=f"urn:uuid:{patient.id}")),
    ]
    for aip in aip_segments:
        display = person_display(aip, 3)
        if not display:
            continue
        participants.append(
            AppointmentParticipant(
                status="accepted",
                actor=Reference(display=display),
                type=[CodeableConcept(coding=[Coding(system=_PARTICIPATION_TYPE_SYSTEM, code="ATND")])],
            )
        )
    for ail in ail_segments:
        display = location_display(ail, 3)
        if not display:
            continue
        participants.append(AppointmentParticipant(status="accepted", actor=Reference(display=display)))
    for aig in aig_segments:
        resource_id = field_str(aig, 3, component=2) or field_str(aig, 3, component=1)
        resource_type = field_str(aig, 4, component=2) or field_str(aig, 4, component=1)
        display = " ".join(part for part in (resource_id, f"({resource_type})" if resource_type else "") if part)
        if not display:
            continue
        participants.append(AppointmentParticipant(status="accepted", actor=Reference(display=display)))
    return participants


def build_appointment_core(
    sch,
    tq1_segments,
    nte_segments,
    ais_segments,
    aig_segments,
    ail_segments,
    aip_segments,
    patient,
    status: str,
    start: str | None,
    end: str | None,
    trigger_event: str,
) -> Appointment:
    """Shared SCH/TQ1/NTE/AIS/AIG/AIL/AIP -> Appointment mapping. `status`,
    `start`, and `end` are supplied by the caller since they depend on which
    trigger event is being mapped."""
    appointment = Appointment(
        id=str(uuid.uuid4()),
        status=status,
        participant=_build_participants(patient, aip_segments, ail_segments, aig_segments),
        extension=[Extension(url=_TRIGGER_EVENT_EXTENSION_URL, valueCode=trigger_event)],
    )

    identifiers = _build_identifiers(sch)
    if identifiers:
        appointment.identifier = identifiers

    if start:
        appointment.start = start
    if end:
        appointment.end = end

    minutes_duration = _resolve_minutes_duration(sch, tq1_segments)
    if minutes_duration is not None:
        appointment.minutesDuration = minutes_duration

    appointment_type = build_codeable_concept_from_cwe(sch, 8)
    if appointment_type:
        appointment.appointmentType = appointment_type

    reason = build_codeable_concept_from_cwe(sch, 7)
    if reason:
        appointment.reasonCode = [reason]

    service_types = [ct for ais in ais_segments if (ct := build_codeable_concept_from_cwe(ais, 3))]
    if service_types:
        appointment.serviceType = service_types

    if nte_segments:
        comment = field_str(nte_segments[0], 3)
        if comment:
            appointment.comment = comment

    return appointment


class BaseSiuMapper(MessageMapper):
    """Shared orchestration for SIU trigger events: require MSH/SCH/PID, read
    the optional repeating segment groups (TQ1, NTE, AIS, AIG, AIL, AIP),
    build the Patient, delegate Appointment construction to the subclass
    (the part that actually differs per trigger event), then assemble the
    Bundle."""

    message_type = "SIU"

    @abstractmethod
    def build_appointment(
        self, sch, tq1_segments, nte_segments, ais_segments, aig_segments, ail_segments, aip_segments, patient
    ) -> Appointment:
        ...

    def to_bundle(self, message: hl7.Message) -> Bundle:
        msh = require_segment(message, "MSH")
        sch = require_segment(message, "SCH")
        pid = require_segment(message, "PID")
        tq1_segments = optional_segments(message, "TQ1")
        nte_segments = optional_segments(message, "NTE")
        ais_segments = optional_segments(message, "AIS")
        aig_segments = optional_segments(message, "AIG")
        ail_segments = optional_segments(message, "AIL")
        aip_segments = optional_segments(message, "AIP")

        patient = build_patient(pid)
        appointment = self.build_appointment(
            sch, tq1_segments, nte_segments, ais_segments, aig_segments, ail_segments, aip_segments, patient
        )
        # No persisted "original booking time" exists in this stateless converter,
        # so `created` is best-effort: the message's own timestamp.
        created = parse_hl7_datetime(field_str(msh, 7))
        if created:
            appointment.created = created

        return assemble_bundle(msh, patient, appointment)


class _BookedSiuMapper(BaseSiuMapper):
    """Shared behavior for S12/S13/S14: this stateless converter has no prior
    state to diff a 'reschedule' or 'modify' against, so all three produce
    the same shape - a currently-booked Appointment - differing only in
    which trigger_event lands in the extension. Requires a resolvable start
    time (TQ1 or SCH-11); raises MappingError otherwise."""

    def build_appointment(
        self, sch, tq1_segments, nte_segments, ais_segments, aig_segments, ail_segments, aip_segments, patient
    ) -> Appointment:
        start, end = resolve_appointment_timing(sch, tq1_segments)
        if not start:
            raise MappingError(
                f"SIU^{self.trigger_event} requires a resolvable appointment start time (TQ1-7 or SCH-11)"
            )
        return build_appointment_core(
            sch,
            tq1_segments,
            nte_segments,
            ais_segments,
            aig_segments,
            ail_segments,
            aip_segments,
            patient,
            status="booked",
            start=start,
            end=end,
            trigger_event=self.trigger_event,
        )


class SiuS12Mapper(_BookedSiuMapper):
    """S12 - Notification of new appointment booking."""

    trigger_event = "S12"


class SiuS13Mapper(_BookedSiuMapper):
    """S13 - Notification of appointment rescheduling."""

    trigger_event = "S13"


class SiuS14Mapper(_BookedSiuMapper):
    """S14 - Notification of appointment modification."""

    trigger_event = "S14"


class SiuS15Mapper(BaseSiuMapper):
    """S15 - Notification of appointment cancellation. Unlike S12/S13/S14, a
    cancellation is valid to record even without resolvable timing."""

    trigger_event = "S15"

    def build_appointment(
        self, sch, tq1_segments, nte_segments, ais_segments, aig_segments, ail_segments, aip_segments, patient
    ) -> Appointment:
        start, end = resolve_appointment_timing(sch, tq1_segments)
        return build_appointment_core(
            sch,
            tq1_segments,
            nte_segments,
            ais_segments,
            aig_segments,
            ail_segments,
            aip_segments,
            patient,
            status="cancelled",
            start=start,
            end=end,
            trigger_event=self.trigger_event,
        )
