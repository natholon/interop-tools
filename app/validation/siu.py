"""SIU-specific validation rules - data-quality checks plus one structural
check for SCH (the segment every SIU mapper requires). Timing is resolved
via the mappers' own resolve_appointment_timing(), not re-derived, so this
stays in sync with whatever TQ1/SCH-11 resolution the real converter does."""

from app.hl7.parser import field_str, optional_segment, optional_segments
from app.mappings.siu import resolve_minutes_duration, resolve_appointment_timing
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
    findings.extend(_rule_empty_participants(message))
    return findings
