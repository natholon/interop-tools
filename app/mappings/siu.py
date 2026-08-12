import uuid
from abc import abstractmethod

import hl7
from fhir.resources.R4B.appointment import Appointment, AppointmentParticipant
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.device import Device, DeviceDeviceName
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.fhir_models.builders import build_codeable_concept_from_cwe, parse_hl7_datetime
from app.hl7.errors import MappingError
from app.hl7.parser import field_str, optional_segments, raw_field_str, require_segment
from app.mappings.base import MessageMapper
from app.mappings.common import (
    assemble_bundle,
    build_location_from_pl,
    build_patient,
    build_practitioner_from_xcn,
    build_reference_with_optional_display,
    location_display,
    person_display,
)

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


_AIG_LOCATION_TYPE_CODES = {"LOCATION", "ROOM"}


def _build_aig_resource(aig) -> tuple[Resource, str] | None:
    """AIG ("general resource") has no single fixed FHIR target in the
    official mapping - the real resource type depends on what AIG-4
    (Resource Type, a local/user-defined table) actually says the resource
    is. We branch on it: a location-ish type code builds a Location, anything
    else - the common case, equipment - builds a Device, preserving AIG-4's
    coded value as Device.type so that category information survives (it's
    otherwise only used to pick the branch and would be lost). Built directly
    here rather than via build_location_from_pl/build_practitioner_from_xcn:
    AIG-3/4 is CWE-shaped, not PL, so reusing a PL-scoped helper here would
    work only by coincidence (the first two CWE components happening to line
    up with PL's facility/room), not by design. Returns None when AIG-3 is
    empty; otherwise the resource plus a display string for its participant
    Reference."""
    resource_id = field_str(aig, 3, component=1)
    resource_display = field_str(aig, 3, component=2) or resource_id
    if not resource_display:
        return None
    resource_type_code = field_str(aig, 4, component=1).strip().upper()
    if resource_type_code in _AIG_LOCATION_TYPE_CODES:
        location = Location(id=str(uuid.uuid4()), name=resource_display)
        if resource_id:
            location.identifier = [Identifier(system="urn:hl7-tools:location-id", value=resource_id)]
        return location, resource_display

    device = Device(
        id=str(uuid.uuid4()), deviceName=[DeviceDeviceName(name=resource_display, type="user-friendly-name")]
    )
    if resource_id:
        device.identifier = [Identifier(system="urn:hl7-tools:device-id", value=resource_id)]
    resource_type = build_codeable_concept_from_cwe(aig, 4)
    if resource_type:
        device.type = resource_type
    return device, resource_display


def _build_participants(
    patient, aip_segments, ail_segments, aig_segments
) -> tuple[list[AppointmentParticipant], list[Resource]]:
    """Patient first (referencing the real Patient resource), then one entry
    per AIP (personnel), AIL (location), and AIG (general resource/equipment)
    segment - each materialized as a real Practitioner/Location/Device
    resource (per the official AIP->Practitioner and AIL->Location v2-to-FHIR
    mappings) rather than a display-only reference, and returned alongside
    the participant list so the caller can add them to the Bundle. Each
    actor Reference carries both `reference` (the real resource) and
    `display` (human-readable text) - FHIR's own guidance is that `display`
    SHOULD be populated even when `reference` is present, for consumers that
    render participant lists without resolving the Bundle. Only AIP gets a
    `type` (ATND); the participant-type value set has no fitting code for
    patient/location/equipment roles."""
    participants = [
        AppointmentParticipant(status="accepted", actor=Reference(reference=f"urn:uuid:{patient.id}")),
    ]
    extra_resources: list[Resource] = []

    def add_participant(resource: Resource | None, display: str, type_coding: Coding | None = None) -> None:
        if resource is None:
            return
        extra_resources.append(resource)
        participants.append(
            AppointmentParticipant(
                status="accepted",
                actor=build_reference_with_optional_display(resource.id, display),
                type=[CodeableConcept(coding=[type_coding])] if type_coding else None,
            )
        )

    for aip in aip_segments:
        add_participant(
            build_practitioner_from_xcn(aip, 3),
            person_display(aip, 3),
            Coding(system=_PARTICIPATION_TYPE_SYSTEM, code="ATND"),
        )
    for ail in ail_segments:
        add_participant(build_location_from_pl(ail, 3), location_display(ail, 3))
    for aig in aig_segments:
        result = _build_aig_resource(aig)
        if result is not None:
            add_participant(*result)

    return participants, extra_resources


def build_appointment_core(
    sch,
    tq1_segments,
    nte_segments,
    ais_segments,
    participants: list[AppointmentParticipant],
    status: str,
    start: str | None,
    end: str | None,
    trigger_event: str,
) -> Appointment:
    """Shared SCH/TQ1/NTE/AIS -> Appointment mapping. `participants` is built
    once by the caller (to_bundle) since it's identical regardless of which
    trigger event is being mapped; `status`, `start`, and `end` are supplied
    by the caller since those depend on the trigger event."""
    appointment = Appointment(
        id=str(uuid.uuid4()),
        status=status,
        participant=participants,
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

    # Every NTE in the message is treated as appointment-scoped and
    # concatenated - not just the first one found. NTEs can trail SCH or a
    # resource-group segment (AIS/AIG/AIL/AIP), but Appointment.comment is a
    # single field with no place to preserve which segment each note
    # actually followed, and no separate field exists for per-participant
    # notes - so every note's text is folded into one comment rather than
    # arbitrarily keeping only the first (which used to pick whichever NTE
    # happened to appear first in the message, not necessarily the most
    # relevant one). Joined with a newline rather than "; " - NTE-3 is FT
    # (Formatted Text, unstructured free text - read via raw_field_str, not
    # field_str, for the same reason OBX-5 free-text values are: a literal
    # '^' in the comment is just a character, not a component separator),
    # and a newline is far less likely to collide with a note's own content
    # than "; " would be, though it doesn't fully eliminate the ambiguity of
    # folding multiple notes into one field with no boundary markers.
    comments = [text for nte in nte_segments if (text := raw_field_str(nte, 3))]
    if comments:
        appointment.comment = "\n".join(comments)

    return appointment


class BaseSiuMapper(MessageMapper):
    """Shared orchestration for SIU trigger events: require MSH/SCH/PID, read
    the optional repeating segment groups (TQ1, NTE, AIS, AIG, AIL, AIP),
    build the Patient and the participant list (identical regardless of
    trigger event), delegate Appointment construction to the subclass (the
    part that actually differs per trigger event), then assemble the
    Bundle."""

    message_type = "SIU"

    @abstractmethod
    def build_appointment(self, sch, tq1_segments, nte_segments, ais_segments, participants) -> Appointment:
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
        participants, extra_resources = _build_participants(patient, aip_segments, ail_segments, aig_segments)

        appointment = self.build_appointment(sch, tq1_segments, nte_segments, ais_segments, participants)
        # No persisted "original booking time" exists in this stateless converter,
        # so `created` is best-effort: the message's own timestamp.
        created = parse_hl7_datetime(field_str(msh, 7))
        if created:
            appointment.created = created

        return assemble_bundle(msh, patient, appointment, *extra_resources)


class _BookedSiuMapper(BaseSiuMapper):
    """Shared behavior for S12/S13/S14/S26: requires a resolvable start time
    (TQ1 or SCH-11), raising MappingError otherwise - a no-show (S26) refers
    to a specific already-scheduled time, same as a booking. `status` is a
    class attribute so subclasses vary only that: S12/S13/S14 have no
    persisted state to diff a 'reschedule' or 'modify' against, so all three
    produce the same booked shape, differing only in which trigger_event
    lands in the extension; S26 produces the same shape but with
    status="noshow"."""

    status = "booked"

    def build_appointment(self, sch, tq1_segments, nte_segments, ais_segments, participants) -> Appointment:
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
            participants,
            status=self.status,
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


class SiuS26Mapper(_BookedSiuMapper):
    """S26 - Notification that patient did not show up for a scheduled
    appointment. "noshow" is a real, near-exact-match FHIR AppointmentStatus
    code for this HL7 semantics."""

    trigger_event = "S26"
    status = "noshow"


class _UntimedSiuMapper(BaseSiuMapper):
    """Shared behavior for S15/S17: timing is resolved but not required,
    unlike the _BookedSiuMapper family - a cancellation or deletion is valid
    to record even without resolvable timing. `status` is a class attribute
    so subclasses vary only that."""

    status = "cancelled"

    def build_appointment(self, sch, tq1_segments, nte_segments, ais_segments, participants) -> Appointment:
        start, end = resolve_appointment_timing(sch, tq1_segments)
        return build_appointment_core(
            sch,
            tq1_segments,
            nte_segments,
            ais_segments,
            participants,
            status=self.status,
            start=start,
            end=end,
            trigger_event=self.trigger_event,
        )


class SiuS15Mapper(_UntimedSiuMapper):
    """S15 - Notification of appointment cancellation."""

    trigger_event = "S15"


class SiuS17Mapper(_UntimedSiuMapper):
    """S17 - Notification of appointment deletion: removes an appointment
    that was entered in error, as distinct from S15's cancellation of a
    valid request (the HL7 standard draws this distinction explicitly).
    status="entered-in-error" preserves that distinction in the FHIR output
    rather than collapsing S17 into the same "cancelled" status as S15."""

    trigger_event = "S17"
    status = "entered-in-error"
