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
    PARTICIPATION_TYPE_SYSTEM,
    assemble_bundle,
    build_location_chain_from_pl,
    build_patient,
    build_practitioner_from_xcn,
    build_reference_with_optional_display,
    location_display,
    person_display,
)
from app.provenance.location import hl7_location

_TRIGGER_EVENT_EXTENSION_URL = "urn:interop-tools:siu-trigger-event"


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


# Public (not module-private) - app/validation/siu.py became a second
# real consumer, needing the same TQ1-6-over-SCH-9 resolution to check
# a stated duration against the appointment's own start and end.
def resolve_minutes_duration(sch, tq1_segments) -> int | None:
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
        identifiers.append(Identifier(system="urn:interop-tools:placer-appointment-id", value=placer_id))
    filler_id = field_str(sch, 2)
    if filler_id:
        identifiers.append(Identifier(system="urn:interop-tools:filler-appointment-id", value=filler_id))
    return identifiers


_AIG_LOCATION_TYPE_CODES = {"LOCATION", "ROOM"}


def _aig_identifier_system(aig, fallback: str = "urn:interop-tools:location-id") -> tuple[str, int | None]:
    """The system for the Identifier AIG-3 becomes, and which component
    supplied it.

    v2-to-FHIR maps AIG-3 through CWE[Identifier], where CWE.3 (Name of
    Coding System) is `Identifier.system`. We used to hard-code a local
    placeholder and ignore CWE.3 entirely, so a sender naming its own
    coding system had that name silently dropped. The IG's own comment
    ("some mapping of the CWE.3 value to an actual URI") leaves the
    resolution open, so the raw name is carried as-is rather than guessed
    at - the same treatment `build_codeable_concept_from_cwe` already
    gives a CWE.3 it cannot resolve to a canonical URI.
    """
    named = field_str(aig, 3, component=3)
    return (named, 3) if named else (fallback, None)


def _record_identifier_system(recorder, resource_id: str, system: str, component: int | None) -> None:
    if component is not None:
        recorder.record(resource_id, "identifier[0].system", hl7_location("AIG", 3, component=component), system)
    else:
        recorder.record_inferred(
            resource_id,
            "identifier[0].system",
            f"AIG-3 named no coding system; defaulted to {system!r}.",
            system,
        )


def _build_aig_resource(aig, recorder=None) -> tuple[Resource, str] | None:
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
    # AIG-3.2 (display) falls back to AIG-3.1 (id) when empty - track which
    # component actually supplied resource_display so the recorded source
    # location is honest either way.
    resource_display_component = 2 if field_str(aig, 3, component=2) else 1
    resource_type_code = field_str(aig, 4, component=1).strip().upper()
    if resource_type_code in _AIG_LOCATION_TYPE_CODES:
        location = Location(id=str(uuid.uuid4()), name=resource_display)
        if recorder:
            recorder.record(location.id, "name", hl7_location("AIG", 3, component=resource_display_component), resource_display)
        if resource_id:
            system, system_component = _aig_identifier_system(aig)
            location.identifier = [Identifier(system=system, value=resource_id)]
            if recorder:
                recorder.record(location.id, "identifier[0].value", hl7_location("AIG", 3, component=1), resource_id)
                _record_identifier_system(recorder, location.id, system, system_component)
        return location, resource_display

    device = Device(
        id=str(uuid.uuid4()), deviceName=[DeviceDeviceName(name=resource_display, type="user-friendly-name")]
    )
    if recorder:
        recorder.record(
            device.id, "deviceName[0].name", hl7_location("AIG", 3, component=resource_display_component), resource_display
        )
    if resource_id:
        system, system_component = _aig_identifier_system(aig, fallback="urn:interop-tools:device-id")
        device.identifier = [Identifier(system=system, value=resource_id)]
        if recorder:
            recorder.record(device.id, "identifier[0].value", hl7_location("AIG", 3, component=1), resource_id)
            _record_identifier_system(recorder, device.id, system, system_component)
    resource_type = build_codeable_concept_from_cwe(aig, 4, resource_id=device.id, relative_path="type", recorder=recorder)
    if resource_type:
        device.type = resource_type
    return device, resource_display


# SCH-20 (Entered By Person) -> participant.type, per the v2-to-FHIR
# SCH[Appointment] map, which names this exact code and system. SCH-12 and
# SCH-16 get an actor but no type: the map's own cells for theirs read
# "#placer contact#"/"#filler contact#", its notation for a placeholder it
# has not resolved to a real code, and Appointment.participant.type's
# recommended value set has nothing fitting - so nothing is invented.
PROVENANCE_PARTICIPANT_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/provenance-participant-type"


def _build_participants(
    patient,
    sch,
    aip_segments,
    ail_segments,
    aig_segments,
    appointment_id: str,
    recorder=None,
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
    patient/location/equipment roles.

    `appointment_id` is the Appointment's own id, generated by the caller
    *before* this function runs (rather than by build_appointment_core, as
    it otherwise would be) specifically so `participant[i].actor.display`
    can be recorded against it here - a real "one source field, two FHIR
    destinations" case (e.g. AIP-3's family/given produces both
    Practitioner.name *and*, via person_display, this participant's own
    display string on a completely different resource)."""
    participants = [
        AppointmentParticipant(status="accepted", actor=Reference(reference=f"urn:uuid:{patient.id}")),
    ]
    extra_resources: list[Resource] = []

    def add_participant(
        resource: Resource | None,
        display: str,
        source_location: str,
        type_coding: Coding | None = None,
        type_source: tuple | None = None,
    ) -> None:
        if resource is None:
            return
        extra_resources.append(resource)
        participant_index = len(participants)
        concept = None
        if type_source is not None:
            # Built here rather than by the caller because the shared CWE
            # builder records against the participant's own index, which is
            # only known once the list position is fixed.
            type_segment, type_field = type_source
            concept = build_codeable_concept_from_cwe(
                type_segment,
                type_field,
                resource_id=appointment_id,
                relative_path=f"participant[{participant_index}].type[0]",
                recorder=recorder,
            )
        if concept is not None:
            types = [concept]
        elif type_coding is not None:
            types = [CodeableConcept(coding=[type_coding])]
        else:
            types = None
        participants.append(
            AppointmentParticipant(
                status="accepted",
                actor=build_reference_with_optional_display(resource.id, display),
                type=types,
            )
        )
        if recorder and display:
            recorder.record(appointment_id, f"participant[{participant_index}].actor.display", source_location, display)

    # SCH's own contact people, per the IG's participant[1..3] rows. They
    # come before the resource groups because the map numbers them first.
    for field_num, type_coding in (
        (12, None),
        (16, None),
        (20, Coding(system=PROVENANCE_PARTICIPANT_TYPE_SYSTEM, code="enterer")),
    ):
        add_participant(
            build_practitioner_from_xcn(sch, field_num, recorder=recorder),
            person_display(sch, field_num),
            hl7_location("SCH", field_num),
            type_coding,
        )
    for aip in aip_segments:
        # AIP-4 (Resource Type) is the source's own coded role. The IG maps
        # it to participant.type, so it is preferred over the fixed ATND
        # this used unconditionally - which said "attender" of a personnel
        # resource the message had already described.
        add_participant(
            build_practitioner_from_xcn(aip, 3, recorder=recorder),
            person_display(aip, 3),
            hl7_location("AIP", 3),
            Coding(system=PARTICIPATION_TYPE_SYSTEM, code="ATND"),
            type_source=(aip, 4),
        )
    for ail in ail_segments:
        # AIL-3 is PL-shaped, so it yields a chain of Locations (one per
        # populated component) rather than a single one - the participant
        # references the most granular; the rest ride along via .partOf.
        chain = build_location_chain_from_pl(ail, 3, recorder=recorder)
        if chain:
            extra_resources.extend(chain[1:])
            add_participant(chain[0], location_display(ail, 3), hl7_location("AIL", 3))
    for aig in aig_segments:
        result = _build_aig_resource(aig, recorder=recorder)
        if result is not None:
            add_participant(*result, hl7_location("AIG", 3))

    return participants, extra_resources


# SCH-25 (Filler Status Code, HL7 table 0278) -> Appointment.status, per
# the v2-to-FHIR SCH[Appointment] map and its named ConceptMap
# "FillerStatusCodes[Appointment]", both fetched directly. Discontinued,
# Blocked and Overbook have blank target rows there - the IG found no
# consensus target - so they fall through to the trigger-derived status
# rather than being guessed at.
FILLER_STATUS_TO_APPOINTMENT_STATUS = {
    "PENDING": "pending",
    "WAITLIST": "waitlist",
    "BOOKED": "booked",
    "STARTED": "checked-in",
    "COMPLETE": "fulfilled",
    "CANCELLED": "cancelled",
    "DELETED": "entered-in-error",
    "NOSHOW": "noshow",
}


def resolve_filler_status(sch) -> str | None:
    """SCH-25's own mapped Appointment.status, or None when it is absent or
    carries a code the published ConceptMap gives no target for.

    The filler's status is what the appointment actually *is*; the trigger
    event only says what kind of message carried it. So this wins when it
    resolves, and the trigger-derived literal stays the fallback.
    """
    return FILLER_STATUS_TO_APPOINTMENT_STATUS.get(field_str(sch, 25).strip().upper())


def build_appointment_core(
    sch,
    tq1_segments,
    nte_segments,
    ais_segments,
    participants: list[AppointmentParticipant],
    status: str,
    status_reason: str,
    start: str | None,
    end: str | None,
    trigger_event: str,
    appointment_id: str,
    recorder=None,
) -> Appointment:
    """Shared SCH/TQ1/NTE/AIS -> Appointment mapping. `participants` is built
    once by the caller (to_bundle) since it's identical regardless of which
    trigger event is being mapped; `status`/`status_reason`, `start`, and
    `end` are supplied by the caller since those depend on the trigger
    event. `appointment_id` is generated by the caller (BaseSiuMapper.
    to_bundle), not here, since _build_participants needs it before this
    function runs (see that function's own docstring) - the same "hoist the
    id so there's something to record against before construction" pattern
    app/mappings/adt.py::build_encounter_core already established."""
    filler_status = resolve_filler_status(sch)
    if filler_status:
        status = filler_status
    appointment = Appointment(
        id=appointment_id,
        status=status,
        participant=participants,
        extension=[Extension(url=_TRIGGER_EVENT_EXTENSION_URL, valueCode=trigger_event)],
    )
    if recorder:
        # SCH-25 is the one real source: the IG maps the Filler Status Code
        # to .status. Absent - or carrying one of the three codes the
        # published ConceptMap gives no target for - the status falls back
        # to the trigger's own fixed literal, which no field supplied
        # (shared across S12/S13/S14, with no persisted state to tell a new
        # booking from a reschedule), same as every ADT trigger's status.
        if filler_status:
            recorder.record(
                appointment_id, "status", hl7_location("SCH", 25), status, source_value=field_str(sch, 25)
            )
        else:
            recorder.record_inferred(appointment_id, "status", status_reason, status)

    identifiers = _build_identifiers(sch)
    if identifiers:
        appointment.identifier = identifiers
        if recorder:
            for idx, identifier in enumerate(identifiers):
                field_num = 1 if idx == 0 else 2
                recorder.record(appointment_id, f"identifier[{idx}].value", hl7_location("SCH", field_num), identifier.value)

    if start:
        appointment.start = start
        if recorder:
            recorder.record(appointment_id, "start", _timing_source_location(sch, tq1_segments, field=7), start)
    if end:
        appointment.end = end
        if recorder:
            recorder.record(appointment_id, "end", _timing_source_location(sch, tq1_segments, field=8), end)

    minutes_duration = resolve_minutes_duration(sch, tq1_segments)
    if minutes_duration is not None:
        appointment.minutesDuration = minutes_duration
        if recorder:
            duration_location = hl7_location("TQ1", 6) if _tq1_duration_used(tq1_segments) else hl7_location("SCH", 9)
            recorder.record(appointment_id, "minutesDuration", duration_location, minutes_duration)

    appointment_type = build_codeable_concept_from_cwe(
        sch, 8, resource_id=appointment_id, relative_path="appointmentType", recorder=recorder
    )
    if appointment_type:
        appointment.appointmentType = appointment_type

    reason = build_codeable_concept_from_cwe(
        sch, 7, resource_id=appointment_id, relative_path="reasonCode[0]", recorder=recorder
    )
    if reason:
        appointment.reasonCode = [reason]

    service_types = []
    for ais in ais_segments:
        service_type = build_codeable_concept_from_cwe(
            ais, 3, resource_id=appointment_id, relative_path=f"serviceType[{len(service_types)}]", recorder=recorder
        )
        if service_type:
            service_types.append(service_type)
    if service_types:
        appointment.serviceType = service_types

    # Every NTE is folded into one comment, not just the first. NTEs trail
    # SCH or a resource-group segment (AIS/AIG/AIL/AIP), but
    # Appointment.comment is one field with nowhere to record which segment
    # a note followed, and there is no per-participant note field - so
    # scoping is lost either way, and keeping only the first would lose
    # content too. Newline-joined rather than "; ", which is likelier to
    # occur inside a note's own text. NTE-3 is FT, so raw_field_str: a
    # literal '^' is a character, not a component separator.
    comments = [text for nte in nte_segments if (text := raw_field_str(nte, 3))]
    if comments:
        appointment.comment = "\n".join(comments)
        if recorder:
            # No per-segment location concept exists for "N whole NTE
            # segments joined" the way hl7_location's repetition/component
            # params model *field* repetition - disclosed as a single
            # location naming the segment and how many were folded in,
            # rather than fabricating one.
            comment_location = "NTE-3" if len(comments) == 1 else f"NTE-3 (×{len(comments)} segments)"
            recorder.record(appointment_id, "comment", comment_location, appointment.comment)

    return appointment


def _tq1_duration_used(tq1_segments) -> bool:
    """Mirrors resolve_minutes_duration's own TQ1-preferred-over-SCH-9
    branch check, purely so the recorded source_location matches whichever
    branch actually supplied the value - no change to _resolve_minutes_
    duration's own behavior."""
    if tq1_segments:
        return field_str(tq1_segments[0], 6).isdigit()
    return False


def _timing_source_location(sch, tq1_segments, field: int) -> str:
    """Mirrors resolve_appointment_timing's own TQ1-preferred-over-SCH-11
    branch check for one of start (field=7 on TQ1, component 4 on SCH-11) or
    end (field=8 on TQ1, component 5 on SCH-11), purely for provenance - no
    change to resolve_appointment_timing's own behavior."""
    if tq1_segments:
        tq1_value = field_str(tq1_segments[0], field)
        if parse_hl7_datetime(tq1_value):
            return hl7_location("TQ1", field)
    sch_component = 4 if field == 7 else 5
    return hl7_location("SCH", 11, component=sch_component)


class BaseSiuMapper(MessageMapper):
    """Shared orchestration for SIU trigger events: require MSH/SCH/PID, read
    the optional repeating segment groups (TQ1, NTE, AIS, AIG, AIL, AIP),
    build the Patient and the participant list (identical regardless of
    trigger event), delegate Appointment construction to the subclass (the
    part that actually differs per trigger event), then assemble the
    Bundle."""

    message_type = "SIU"

    @abstractmethod
    def build_appointment(self, sch, tq1_segments, nte_segments, ais_segments, participants, appointment_id, recorder=None) -> Appointment:
        ...

    def to_bundle(self, message: hl7.Message, recorder=None) -> Bundle:
        msh = require_segment(message, "MSH")
        sch = require_segment(message, "SCH")
        pid = require_segment(message, "PID")
        tq1_segments = optional_segments(message, "TQ1")
        nte_segments = optional_segments(message, "NTE")
        ais_segments = optional_segments(message, "AIS")
        aig_segments = optional_segments(message, "AIG")
        ail_segments = optional_segments(message, "AIL")
        aip_segments = optional_segments(message, "AIP")

        patient = build_patient(pid, recorder=recorder)
        # Generated here, not inside build_appointment_core, since
        # _build_participants needs it before the Appointment itself is
        # built (see that function's own docstring for why).
        appointment_id = str(uuid.uuid4())
        participants, extra_resources = _build_participants(
            patient, sch, aip_segments, ail_segments, aig_segments, appointment_id, recorder=recorder
        )

        appointment = self.build_appointment(
            sch, tq1_segments, nte_segments, ais_segments, participants, appointment_id, recorder=recorder
        )
        # No persisted "original booking time" exists in this stateless converter,
        # so `created` is best-effort: the message's own timestamp.
        created = parse_hl7_datetime(field_str(msh, 7))
        if created:
            appointment.created = created
            if recorder:
                recorder.record(appointment_id, "created", hl7_location("MSH", 7), created, source_value=field_str(msh, 7))

        return assemble_bundle(msh, patient, appointment, *extra_resources, recorder=recorder)


class _BookedSiuMapper(BaseSiuMapper):
    """Shared behavior for S12/S13/S14/S26: requires a resolvable start time
    (TQ1 or SCH-11), raising MappingError otherwise - a no-show (S26) refers
    to a specific already-scheduled time, same as a booking. `status`/
    `status_reason` are class attributes so subclasses vary only those:
    S12/S13/S14 have no persisted state to diff a 'reschedule' or 'modify'
    against, so all three produce the same booked shape, differing only in
    which trigger_event lands in the extension; S26 produces the same shape
    but with status="noshow"."""

    status = "booked"
    status_reason = "SIU^{trigger} always maps to status=booked - no persisted state exists to distinguish a new booking from a reschedule/modify; not read from any field."

    def build_appointment(self, sch, tq1_segments, nte_segments, ais_segments, participants, appointment_id, recorder=None) -> Appointment:
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
            status_reason=self.status_reason.format(trigger=self.trigger_event),
            start=start,
            end=end,
            trigger_event=self.trigger_event,
            appointment_id=appointment_id,
            recorder=recorder,
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
    status_reason = "SIU^S26 (Patient No-Show) always maps to status=noshow; not read from any field."


class _UntimedSiuMapper(BaseSiuMapper):
    """Shared behavior for S15/S17: timing is resolved but not required,
    unlike the _BookedSiuMapper family - a cancellation or deletion is valid
    to record even without resolvable timing. `status`/`status_reason` are
    class attributes so subclasses vary only those."""

    status = "cancelled"
    status_reason = "SIU^S15 (Cancel) always maps to status=cancelled; not read from any field."

    def build_appointment(self, sch, tq1_segments, nte_segments, ais_segments, participants, appointment_id, recorder=None) -> Appointment:
        start, end = resolve_appointment_timing(sch, tq1_segments)
        return build_appointment_core(
            sch,
            tq1_segments,
            nte_segments,
            ais_segments,
            participants,
            status=self.status,
            status_reason=self.status_reason,
            start=start,
            end=end,
            trigger_event=self.trigger_event,
            appointment_id=appointment_id,
            recorder=recorder,
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
    status_reason = "SIU^S17 (Delete) always maps to status=entered-in-error, distinct from S15's cancelled; not read from any field."
