"""Shared HL7v2-reverse-direction helpers - the app/transform/ mirror of
app/mappings/common.py's own role for the forward direction. `build_pid`
was promoted here from app/transform/hl7_adt.py (module-private
`_build_pid` there originally) once app/transform/hl7_siu.py became a
second real consumer of the identical PID-3/5/7/8/11/13 reversal - PID is
mapped identically regardless of HL7v2 message type, on both the forward
and reverse sides. `build_msh` generalizes the shared MSH-shape (sending/
receiving application placeholders, MSH-7 from Bundle.timestamp, MSH-10
control id) across message types, parameterized by the caller's own
`message_type`/`trigger_event` strings (`"ADT"`/`"A01"`, `"SIU"`/`"S12"`,
...)."""

from datetime import datetime, timezone

from fhir.resources.R4B.bundle import Bundle

from app.fhir_models.builders import CWE_FALLBACK_SYSTEM
from app.generators.base import segment
from app.transform.common import format_hl7_date, format_hl7_ts

# Reverse of app/fhir_models/builders.py::_GENDER_MAP.
GENDER_TO_HL7_SEX = {"male": "M", "female": "F", "other": "O"}

DEFAULT_SENDING_APP = "interop-tools"
DEFAULT_SENDING_FACILITY = "INTEROP"
DEFAULT_RECEIVING_APP = "UNKNOWN"
DEFAULT_RECEIVING_FACILITY = "UNKNOWN"
DEFAULT_CONTROL_ID = "MSG00001"


def build_msh(bundle: Bundle, message_type: str, trigger_event: str) -> tuple[str, str]:
    """Returns (segment_text, formatted_datetime) - the datetime is exposed
    so callers can reuse the exact same value for EVN-2/a similar
    event-recorded-time fallback field, the same "reuse the message-level
    timestamp as an event fallback" relationship
    app/mappings/adt.py::build_encounter_core already has with EVN-2."""
    dt = format_hl7_ts(datetime.now(timezone.utc))
    if bundle.timestamp:
        dt = format_hl7_ts(bundle.timestamp) or dt
    control_id = (bundle.identifier.value if bundle.identifier else None) or DEFAULT_CONTROL_ID
    text = (
        f"MSH|^~\\&|{DEFAULT_SENDING_APP}|{DEFAULT_SENDING_FACILITY}|"
        f"{DEFAULT_RECEIVING_APP}|{DEFAULT_RECEIVING_FACILITY}|{dt}||{message_type}^{trigger_event}|"
        f"{control_id}|P|2.5"
    )
    return text, dt


def reverse_cwe(concept) -> str:
    """The reverse of app.fhir_models.builders.build_codeable_concept_from_cwe
    - code=component 1, display=component 2, system=component 3 (omitted
    when the source coding's system is CWE_FALLBACK_SYSTEM, since that
    value only ever exists because component 3 was itself absent on the
    forward side). Promoted here (originally module-private in
    app/transform/hl7_siu.py) once app/transform/hl7_oru.py became a
    second real consumer of the identical reversal."""
    if not concept or not concept.coding:
        return ""
    coding = concept.coding[0]
    code = coding.code or ""
    display = coding.display or ""
    system = "" if coding.system == CWE_FALLBACK_SYSTEM else (coding.system or "")
    return f"{code}^{display}^{system}"


def build_pid(patient) -> str:
    fields: dict[int, str] = {1: "1"}

    if patient.identifier:
        identifier = patient.identifier[0]
        system = identifier.system or ""
        fields[3] = f"{identifier.value or ''}^^^{system}^MR"

    if patient.name:
        name = patient.name[0]
        family = name.family or ""
        # PID-5 (XPN) components 2/3 are the first-given-name/middle-name,
        # matching app.fhir_models.builders.build_human_names' own read of
        # them as two separate components - space-joining Patient.name[0]
        # .given into one component would silently collapse that
        # distinction on the way back out.
        given = name.given or []
        first_given = given[0] if given else ""
        middle_given = given[1] if len(given) > 1 else ""
        fields[5] = f"{family}^{first_given}^{middle_given}"

    if patient.birthDate:
        fields[7] = format_hl7_date(patient.birthDate)

    if patient.gender:
        sex = GENDER_TO_HL7_SEX.get(patient.gender)
        if sex:
            fields[8] = sex

    if patient.address:
        address = patient.address[0]
        line1 = address.line[0] if address.line else ""
        line2 = address.line[1] if address.line and len(address.line) > 1 else ""
        fields[11] = (
            f"{line1}^{line2}^{address.city or ''}^{address.state or ''}"
            f"^{address.postalCode or ''}^{address.country or ''}"
        )

    if patient.telecom:
        phone = next((t.value for t in patient.telecom if t.system == "phone" and t.value), None)
        if phone:
            fields[13] = phone

    return segment("PID", fields, 13)
