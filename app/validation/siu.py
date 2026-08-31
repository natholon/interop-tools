"""SIU-specific validation rules - data-quality checks plus one structural
check for SCH (the segment every SIU mapper requires). Timing is resolved
via the mappers' own resolve_appointment_timing(), not re-derived, so this
stays in sync with whatever TQ1/SCH-11 resolution the real converter does."""

from app.hl7.parser import field_str, optional_segment, optional_segments
from app.mappings.registry import get_mapper
from app.mappings.siu import (
    resolve_appointment_timing,
    resolve_filler_status,
    resolve_minutes_duration,
)
from app.hl7.errors import MappingError
from app.validation.common import parse_comparable_fhir_datetime
from app.validation.models import ValidationFinding

# (segment name, core field number) - AIP-3/AIL-3/AIG-3 are each that
# segment's "who/what/where" field; component 1 empty means the mapper
# will silently drop this participant (build_practitioner_from_xcn /
# build_location_from_pl / _build_aig_resource all return None on it).
_PARTICIPANT_CORE_FIELDS = [("AIP", 3), ("AIL", 3), ("AIG", 3)]


def _rule_appointment_end_before_start(sch, tq1_segments) -> list[ValidationFinding]:
    start, end = resolve_appointment_timing(sch, tq1_segments)
    if not start or not end:
        return []
    start_dt = parse_comparable_fhir_datetime(start)
    end_dt = parse_comparable_fhir_datetime(end)
    if start_dt is not None and end_dt is not None and end_dt <= start_dt:
        return [
            ValidationFinding(
                severity="error",
                rule_id="siu.appointment-end-before-start",
                segment="SCH",
                message="The appointment's resolved end time is at or before its resolved start time.",
            )
        ]
    return []


def _rule_duration_disagrees_with_timing(sch, tq1_segments) -> list[ValidationFinding]:
    """A stated duration that contradicts the appointment's own start and
    end - two fields of one message disagreeing about the same fact.

    Resolved through the mappers own helpers rather than re-derived, so
    this cannot drift from whichever of TQ1-6/SCH-9 and TQ1-7/8/SCH-11 the
    converter actually reads. Only reported when all three resolve: a
    duration with no timing to check it against is legal and common.
    """
    duration = resolve_minutes_duration(sch, tq1_segments)
    if duration is None:
        return []
    start, end = resolve_appointment_timing(sch, tq1_segments)
    start_dt = parse_comparable_fhir_datetime(start) if start else None
    end_dt = parse_comparable_fhir_datetime(end) if end else None
    if start_dt is None or end_dt is None:
        return []
    actual = int((end_dt - start_dt).total_seconds() // 60)
    if actual == duration:
        return []
    return [
        ValidationFinding(
            severity="warning",
            rule_id="siu.appointment-duration-disagrees-with-timing",
            segment="SCH",
            message=(
                f"The stated duration ({duration} minutes) does not match the appointment's own start "
                f"and end ({actual} minutes)."
            ),
        )
    ]


def _rule_empty_participants(message) -> list[ValidationFinding]:
    findings = []
    for segment_name, field_num in _PARTICIPANT_CORE_FIELDS:
        for segment in optional_segments(message, segment_name):
            if not field_str(segment, field_num, component=1):
                findings.append(
                    ValidationFinding(
                        severity="info",
                        rule_id="siu.participant-core-field-empty",
                        segment=segment_name,
                        field=field_num,
                        message=(
                            f"{segment_name}-{field_num} is empty - this participant will be "
                            "silently dropped from the Appointment rather than materialized."
                        ),
                    )
                )
    return findings



# R4's app-3 excuses a missing start/end for exactly these three statuses.
# Every other status makes both effectively required, so a message that
# carries no timing cannot produce a valid Appointment - the values are
# not inferable from anything else in the message.
_STATUSES_NOT_NEEDING_TIMING = frozenset({"proposed", "cancelled", "waitlist"})


def _rule_timing_required_for_resulting_status(sch, tq1_segments, trigger_event: str) -> list[ValidationFinding]:
    """Timing the resulting Appointment.status makes mandatory.

    Not a rule about HL7 - the message is well-formed either way - but
    about what it converts into. `app-3` reads:

        (start.exists() and end.exists())
        or (status in ('proposed' | 'cancelled' | 'waitlist'))

    so start and end are required unless the status is one of those
    three. An S17 delete maps to `entered-in-error` (per the v2-to-FHIR
    FillerStatusCodes ConceptMap for a Deleted filler status, and per this
    app's own trigger-derived default), which is not among them - so an
    S17 carrying no TQ1 and no SCH-11 timing converts to an Appointment
    that is invalid FHIR, and nothing can infer the missing values.

    An S15 cancel is genuinely exempt, which is why this checks the
    resulting status rather than the trigger.
    """
    start, end = resolve_appointment_timing(sch, tq1_segments)
    if start and end:
        return []
    status = resolve_filler_status(sch)
    if status is None:
        try:
            status = getattr(get_mapper("SIU", trigger_event), "status", None)
        except MappingError:
            return []
    if status is None or status in _STATUSES_NOT_NEEDING_TIMING:
        return []
    missing = "start and end" if not start and not end else ("end" if start else "start")
    return [
        ValidationFinding(
            severity="error",
            rule_id="siu.appointment-timing-required-for-status",
            segment="SCH",
            field=11,
            message=(
                f"No appointment {missing} could be resolved from TQ1 or SCH-11, and the "
                f"resulting Appointment.status is {status!r}. FHIR R4's app-3 requires both "
                f"unless the status is proposed, cancelled or waitlist, so this converts to "
                f"an invalid Appointment - and neither value can be inferred."
            ),
        )
    ]


def validate(message, trigger_event: str) -> list[ValidationFinding]:
    sch = optional_segment(message, "SCH")
    if sch is None:
        return [
            ValidationFinding(severity="error", rule_id="siu.sch-missing", segment="SCH", message="SCH segment is missing.")
        ]

    findings: list[ValidationFinding] = []
    tq1_segments = optional_segments(message, "TQ1")
    findings.extend(_rule_appointment_end_before_start(sch, tq1_segments))
    findings.extend(_rule_duration_disagrees_with_timing(sch, tq1_segments))
    findings.extend(_rule_timing_required_for_resulting_status(sch, tq1_segments, trigger_event))
    findings.extend(_rule_empty_participants(message))
    return findings
