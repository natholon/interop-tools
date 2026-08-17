"""FHIR Bundle -> HL7v2 SIU^S12 - the fifth reverse-direction slice, and
the first proof this architecture handles a genuinely different FHIR
shape (`Appointment`, not `Encounter`) - not just another ADT-shaped
variant. Scoped to S12 (new booking) alone, the same "one thing per
slice" precedent every earlier reverse slice already established (and the
same trigger the *forward* direction's own SIU support could have started
with, though it actually shipped S12-S15 together - this reverse slice
draws its own, narrower first-slice boundary independently).

Reverses `app/mappings/siu.py::build_appointment_core`/`_build_participants`
field-for-field: `SCH-1`/`-2` (placer/filler identifiers), `SCH-7`/`-8`
(reason/appointment type, CWE), `TQ1-6`/`-7`/`-8` (duration/start/end),
`AIS-3` (service type, CWE), `NTE-3` (comment, split back into one segment
per original line - the reverse of `build_appointment_core`'s own
`"\\n".join`), and one `AIP`/`AIL`/`AIG` segment per non-patient
`Appointment.participant`, dispatched by the referenced resource's own
FHIR type rather than any stored marker.

**A cross-cutting resolution this slice needed that ADT's own reverse
builders didn't**: `Appointment.participant[]` can reference `Location`
from *two* different forward-side sources - `AIL` (via
`build_location_from_pl`, which sets only `.name`, no `.identifier` at
all) and `AIG` when `AIG-4`'s resource-type code is location-like (via
`_build_aig_resource`, which *does* set a
`urn:interop-tools:location-id`-systemed `.identifier`). This asymmetry -
not a stored marker - is exactly how this builder tells an AIL-sourced
Location apart from an AIG-sourced one on the way back out: a Location
with that specific identifier system reverses to `AIG`, everything else
reverses to `AIL`.

**Disclosed round-trip fidelity gaps, the same category as every earlier
slice's own**: `AIL-3`/`AIG-3`'s own `PL`/id+name shapes only round-trip
their first component(s) - `Location.name` (a single collapsed display
string, the same `location_display` collapse ADT's own PV1-3/6 already
disclose) goes in `AIL-3`'s first component only, and a `Practitioner`
built from `AIP-3` only recovers `id`/`family`/`given` (XCN components
1/2/3), not the trailing `^^^^MD` degree/suffix components the forward
mapper never reads either. `RGS` is not emitted at all - this app's own
forward parser never requires it (`SIU`'s `to_bundle()` reads `AIS`/`AIG`/
`AIL`/`AIP` directly via `optional_segments()`, with no `RGS`-grouping
requirement - see `app/mappings/siu.py`'s own module-level note), so
there's nothing on the FHIR side this builder would need it for."""

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, format_hl7_ts
from app.transform.hl7_common import build_msh, build_pid, reverse_cwe

_AIG_LOCATION_ID_SYSTEM = "urn:interop-tools:location-id"


def _build_sch(appointment) -> str:
    fields: dict[int, str] = {}
    for identifier in appointment.identifier or []:
        if identifier.system == "urn:interop-tools:placer-appointment-id" and identifier.value:
            fields[1] = identifier.value
        elif identifier.system == "urn:interop-tools:filler-appointment-id" and identifier.value:
            fields[2] = identifier.value

    reason = reverse_cwe(appointment.reasonCode[0] if appointment.reasonCode else None)
    if reason:
        fields[7] = reason
    appointment_type = reverse_cwe(appointment.appointmentType)
    if appointment_type:
        fields[8] = appointment_type
    if appointment.minutesDuration is not None:
        fields[9] = str(appointment.minutesDuration)
        fields[10] = "MIN"

    return segment("SCH", fields, 25)


def _build_tq1(appointment) -> str | None:
    if not appointment.start and not appointment.end and appointment.minutesDuration is None:
        return None
    fields: dict[int, str] = {1: "1"}
    if appointment.minutesDuration is not None:
        fields[6] = str(appointment.minutesDuration)
    if appointment.start:
        fields[7] = format_hl7_ts(appointment.start)
    if appointment.end:
        fields[8] = format_hl7_ts(appointment.end)
    return segment("TQ1", fields, 8)


def _build_nte_segments(appointment) -> list[str]:
    if not appointment.comment:
        return []
    return [segment("NTE", {1: str(i + 1), 3: line}, 3) for i, line in enumerate(appointment.comment.split("\n"))]


def _build_ais_segments(appointment) -> list[str]:
    return [
        segment("AIS", {1: str(i + 1), 3: cwe}, 3)
        for i, service_type in enumerate(appointment.serviceType or [])
        if (cwe := reverse_cwe(service_type))
    ]


def _build_aip(index: int, practitioner) -> str:
    identifier = practitioner.identifier[0].value if practitioner.identifier else ""
    name = practitioner.name[0] if practitioner.name else None
    family = name.family or "" if name else ""
    given = name.given[0] if name and name.given else ""
    return segment("AIP", {1: str(index), 3: f"{identifier}^{family}^{given}"}, 3)


def _build_ail(index: int, location) -> str:
    return segment("AIL", {1: str(index), 3: location.name or ""}, 3)


def _build_aig(index: int, resource, resource_type: str) -> str:
    fields: dict[int, str] = {1: str(index)}
    if resource_type == "Device":
        identifier = resource.identifier[0].value if resource.identifier else ""
        name = resource.deviceName[0].name if resource.deviceName else ""
        fields[3] = f"{identifier}^{name}"
        aig4 = reverse_cwe(resource.type)
        if aig4:
            fields[4] = aig4
    else:  # Location (AIG's own location-type branch)
        identifier = resource.identifier[0].value if resource.identifier else ""
        fields[3] = f"{identifier}^{resource.name or ''}"
        fields[4] = "LOCATION"
    return segment("AIG", fields, 4)


class SiuS12Builder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError("Bundle has no Patient resource - cannot build a SIU^S12 message")
        appointment = find_resource(bundle, "Appointment")
        if appointment is None:
            raise MappingError("Bundle has no Appointment resource - cannot build a SIU^S12 message")

        by_id = {r.id: r for r in (e.resource for e in bundle.entry or [])}

        msh, _msh_dt = build_msh(bundle, "SIU", "S12")
        sch = _build_sch(appointment)
        tq1 = _build_tq1(appointment)
        nte_segments = _build_nte_segments(appointment)
        pid = build_pid(patient)
        ais_segments = _build_ais_segments(appointment)

        aip_segments = []
        ail_segments = []
        aig_segments = []
        for participant in appointment.participant or []:
            if not participant.actor or not participant.actor.reference:
                continue
            actor_id = participant.actor.reference.removeprefix("urn:uuid:")
            if actor_id == patient.id:
                continue
            resource = by_id.get(actor_id)
            if resource is None:
                continue
            resource_type = resource.get_resource_type()
            if resource_type == "Practitioner":
                aip_segments.append(_build_aip(len(aip_segments) + 1, resource))
            elif resource_type == "Device":
                aig_segments.append(_build_aig(len(aig_segments) + 1, resource, "Device"))
            elif resource_type == "Location":
                is_aig_location = resource.identifier and resource.identifier[0].system == _AIG_LOCATION_ID_SYSTEM
                if is_aig_location:
                    aig_segments.append(_build_aig(len(aig_segments) + 1, resource, "Location"))
                else:
                    ail_segments.append(_build_ail(len(ail_segments) + 1, resource))

        segments = [msh, sch]
        if tq1:
            segments.append(tq1)
        segments.extend(nte_segments)
        segments.append(pid)
        segments.extend(ais_segments)
        segments.extend(aig_segments)
        segments.extend(ail_segments)
        segments.extend(aip_segments)

        return "\r".join(segments) + "\r"
