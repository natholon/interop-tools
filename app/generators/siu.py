"""Synthetic SIU message generators, one per supported trigger event.

Same required/optional philosophy as app.generators.adt: a generated message
must always convert successfully through app.mappings.siu, so timing (which
the mapper requires for S12/S13/S14) is never left out - only *how* it's
supplied (TQ1 vs. legacy SCH-11) is randomized, to exercise both paths.
"""

import random

from app.generators.base import (
    format_hl7_datetime,
    generate_msh_segment,
    generate_pid_segment,
    maybe,
    random_appointment_type_code,
    random_equipment,
    random_identifier,
    random_location,
    random_location_field,
    random_nte_comment,
    random_physician_xcn,
    random_reason_code,
    random_service,
    random_time_range,
    segment,
)
from app.hl7.parser import parse_message


# SCH-25 (Filler Status Code) has to agree with the trigger, or the
# generated message contradicts itself: the IG maps SCH-25 to
# Appointment.status, so a cancel carrying "Booked" produces a booked
# appointment on a cancel message. "Discontinued" is one of the three
# codes the published ConceptMap gives no target for - included so the
# fall-back-to-the-trigger path is exercised too.
_FILLER_STATUS_BY_TRIGGER = {
    "S12": ("Booked", "Pending", "Waitlist"),
    "S13": ("Booked", "Pending"),
    "S14": ("Booked", "Discontinued"),
    "S26": ("Noshow",),
    "S15": ("Cancelled",),
    "S17": ("Deleted",),
}


# SCH-11 (Appointment Timing Quantity) is 1..1, but its start/end
# components are not - a TQ1-timed or untimed appointment carries the
# field with a bare quantity and nothing that could contradict the
# timing in force. TQ.1 is the quantity; TQ.4/TQ.5 are start and end.
_EMPTY_TIMING_QUANTITY = "1"


def _sch_common_fields(rng: random.Random, trigger_event: str) -> dict:
    # SCH-6, -12, -16 and -20 are 1..1 in the HL7 v2 standard (see
    # app/validation/required_fields.py), so they are always emitted -
    # generated output has to be conformant, not merely convertible.
    fields = {
        1: f"PLC{random_identifier(rng, 4)}",
        6: "NORMAL^Routine appointment request^LOCAL",
        12: random_physician_xcn(rng),
        16: random_physician_xcn(rng),
        20: random_physician_xcn(rng),
    }
    if maybe(rng, p=0.5):
        fields[2] = f"FIL{random_identifier(rng, 4)}"
    if maybe(rng, p=0.5):
        code, display = random_reason_code(rng)
        fields[7] = f"{code}^{display}^LOCAL"
    if maybe(rng, p=0.5):
        code, display = random_appointment_type_code(rng)
        fields[8] = f"{code}^{display}^LOCAL"
    if maybe(rng, p=0.4):
        fields[25] = rng.choice(_FILLER_STATUS_BY_TRIGGER[trigger_event])
    return fields


def _minutes(start, end) -> str:
    return str(int((end - start).total_seconds() // 60))


def _apply_sch_duration(rng: random.Random, sch_fields: dict, start, end) -> None:
    """SCH-9/10 from the SAME start/end the appointment actually uses.

    Drawing its own range made the duration disagree with end - start on
    half the generated messages - the identical two-independent-draws bug
    the ADT generator had with admit and discharge times.
    """
    if maybe(rng, p=0.5):
        sch_fields[9] = _minutes(start, end)
        sch_fields[10] = "MIN"


def _tq1_segment(rng: random.Random, start, end) -> str:
    tq1_fields = {1: "1", 7: format_hl7_datetime(start), 8: format_hl7_datetime(end)}
    if maybe(rng, p=0.5):
        tq1_fields[6] = _minutes(start, end)
    return segment("TQ1", tq1_fields, 14)


def _apply_required_timing(rng: random.Random, sch_fields: dict) -> list[str]:
    """S12/S13/S14: timing is required. ~75% via TQ1, ~25% via legacy SCH-11
    (component 4 = start, component 5 = end) - both must always resolve to a
    real start+end, exercising resolve_appointment_timing's two paths."""
    start, end = random_time_range(rng, min_days=0, max_days=30)
    _apply_sch_duration(rng, sch_fields, start, end)
    if maybe(rng, p=0.75):
        # SCH-11 is 1..1, so it is present either way - TQ1 simply wins
        # the timing, which is what resolve_appointment_timing prefers.
        # Only its quantity component is filled, so nothing here can
        # contradict the TQ1 the appointment actually uses.
        sch_fields[11] = _EMPTY_TIMING_QUANTITY
        return [_tq1_segment(rng, start, end)]
    sch_fields[11] = f"^^^{format_hl7_datetime(start)}^{format_hl7_datetime(end)}"
    return []


def _apply_optional_timing(rng: random.Random, sch_fields: dict) -> list[str]:
    """S15/S17: timing is optional - ~40% include a TQ1, ~60% omit entirely.

    With no timing at all a bare SCH-9 is still legal and still maps
    (minutesDuration with no start), and cannot contradict anything - so
    it is generated there too, just from its own duration rather than
    from a start/end pair nothing else uses.
    """
    sch_fields[11] = _EMPTY_TIMING_QUANTITY
    if not maybe(rng, p=0.4):
        if maybe(rng, p=0.4):
            sch_fields[9] = str(rng.choice((15, 20, 30, 45, 60)))
            sch_fields[10] = "MIN"
        return []
    start, end = random_time_range(rng, min_days=0, max_days=30)
    _apply_sch_duration(rng, sch_fields, start, end)
    return [_tq1_segment(rng, start, end)]


def _resource_group_segments(rng: random.Random) -> list[str]:
    segments = []
    if maybe(rng, p=0.5):
        segments.append(segment("NTE", {1: "1", 3: random_nte_comment(rng)}, 4))

    resource_segments = []
    if maybe(rng, p=0.5):
        code, display = random_service(rng)
        resource_segments.append(segment("AIS", {1: "1", 3: f"{code}^{display}^LOCAL"}, 12))
    if maybe(rng, p=0.5):
        # AIG-4 (Resource Type) drives which FHIR resource app.mappings.siu
        # materializes it as - mostly equipment (-> Device), occasionally a
        # location-typed general resource (-> Location), to exercise both
        # branches of _build_aig_resource rather than only ever hitting one.
        if maybe(rng, p=0.2):
            facility, room = random_location(rng)
            resource_segments.append(
                segment("AIG", {1: "1", 3: f"{facility}^{room}^LOCAL", 4: "LOCATION^Location^LOCAL"}, 14)
            )
        else:
            resource_id, display = random_equipment(rng)
            resource_segments.append(
                segment("AIG", {1: "1", 3: f"{resource_id}^{display}^LOCAL", 4: "EQUIPMENT^Equipment^LOCAL"}, 14)
            )
    if maybe(rng, p=0.5):
        resource_segments.append(segment("AIL", {1: "1", 3: random_location_field(rng)}, 12))
    if maybe(rng, p=0.5):
        resource_segments.append(
            segment("AIP", {1: "1", 3: random_physician_xcn(rng), 4: "PROVIDER^Provider^LOCAL"}, 12)
        )

    if resource_segments:
        segments.append(segment("RGS", {1: "1"}, 3))
        segments.extend(resource_segments)
    return segments


def _assemble(msh: str, sch_fields: dict, timing_segments: list[str], pid: str, resource_segments: list[str]) -> str:
    segments = [msh, segment("SCH", sch_fields, 27)]
    segments.extend(timing_segments)
    segments.append(pid)
    segments.extend(resource_segments)
    text = "\r".join(segments) + "\r"
    parse_message(text)  # self-check: a generator bug should raise, not return broken text
    return text


def _generate_booked(rng: random.Random, trigger_event: str) -> str:
    msh, _ = generate_msh_segment(rng, "SIU", trigger_event)
    sch_fields = _sch_common_fields(rng, trigger_event)
    timing_segments = _apply_required_timing(rng, sch_fields)
    pid = generate_pid_segment(rng)
    resource_segments = _resource_group_segments(rng)
    return _assemble(msh, sch_fields, timing_segments, pid, resource_segments)


def generate_siu_s12(rng: random.Random) -> str:
    return _generate_booked(rng, "S12")


def generate_siu_s13(rng: random.Random) -> str:
    return _generate_booked(rng, "S13")


def generate_siu_s14(rng: random.Random) -> str:
    return _generate_booked(rng, "S14")


def generate_siu_s26(rng: random.Random) -> str:
    # S26 (patient no-show) requires resolvable timing, same as S12/S13/S14 -
    # a no-show necessarily refers to a specific already-scheduled time.
    return _generate_booked(rng, "S26")


def _generate_untimed(rng: random.Random, trigger_event: str) -> str:
    msh, _ = generate_msh_segment(rng, "SIU", trigger_event)
    sch_fields = _sch_common_fields(rng, trigger_event)
    timing_segments = _apply_optional_timing(rng, sch_fields)
    pid = generate_pid_segment(rng)
    resource_segments = _resource_group_segments(rng)
    return _assemble(msh, sch_fields, timing_segments, pid, resource_segments)


def generate_siu_s15(rng: random.Random) -> str:
    return _generate_untimed(rng, "S15")


def generate_siu_s17(rng: random.Random) -> str:
    # S17 (delete) doesn't require resolvable timing, same as S15's cancel.
    return _generate_untimed(rng, "S17")
