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
...). `CLASS_TO_PATIENT_CLASS` (promoted from `hl7_adt.py`'s own private
copy) and `build_minimal_pv1` (promoted from `hl7_oru.py`'s own private
`_build_pv1`) were both extracted here once `app/transform/hl7_mdm.py`
became a third real consumer of the identical "PV1-2 class code +
PV1-19 visit identifier only" reversal that `app.mappings.common.
build_minimal_encounter` itself reads on the forward side - ORU and MDM's
own minimal, lifecycle-free Encounter shape, distinct from `hl7_adt.py`'s
own full ADT-shaped PV1 builder (which additionally reverses PV1-3/6/7/
36/44/45, none of which a minimal Encounter ever populates)."""

from datetime import datetime, timezone

from fhir.resources.R4B.bundle import Bundle

from app.fhir_models.builders import CWE_FALLBACK_SYSTEM
from app.generators.base import segment
from app.transform.common import format_hl7_date, format_hl7_ts

# Reverse of app/fhir_models/builders.py::_GENDER_MAP.
GENDER_TO_HL7_SEX = {"male": "M", "female": "F", "other": "O"}

# Reverse of app/mappings/common.py::_PATIENT_CLASS_MAP - "O" (outpatient/
# ambulatory) is the fallback on the forward side, so it's also the safest
# default here for an Encounter.class code this table doesn't recognize.
CLASS_TO_PATIENT_CLASS = {"IMP": "I", "AMB": "O", "EMER": "E", "PRENC": "P"}

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


def build_xcn_from_practitioner(practitioner) -> str:
    """A Practitioner back into an XCN: id^family^given^^^^degree.

    Component 7 is the degree, which `build_practitioner_from_xcn` reads
    into `qualification` - carrying it means an XCN-sourced Practitioner
    round-trips whole rather than losing its trailing `^^^^MD`. Promoted
    from hl7_mdm.py once the minimal PV1 became a third consumer.
    """
    identifier = practitioner.identifier[0].value if practitioner.identifier else ""
    name = practitioner.name[0] if practitioner.name else None
    family = (name.family or "") if name else ""
    given = name.given[0] if name and name.given else ""
    degree = ""
    qualification = getattr(practitioner, "qualification", None)
    if qualification and qualification[0].code and qualification[0].code.coding:
        degree = qualification[0].code.coding[0].code or ""
    if degree:
        return f"{identifier}^{family}^{given}^^^^{degree}"
    return f"{identifier}^{family}^{given}"


# The inverse of app.mappings.adt._PV1_DOCTOR_FIELDS. Participants are
# grouped back by their ParticipationType code, and each group's members
# become repetitions of that field - which is how a second attending
# doctor survives the trip.
_PARTICIPATION_TYPE_TO_PV1_FIELD = {"ATND": 7, "REF": 8, "CON": 9, "ADM": 17}


def reverse_pv1_doctor_fields(encounter, practitioners_by_id: dict) -> dict:
    """Encounter.participant -> PV1-7/8/9/17, as repeating XCN fields.

    Without the practitioner map a reference cannot be resolved to a real
    XCN, so those participants are skipped rather than written as a bare
    display string.
    """
    fields: dict[int, list[str]] = {}
    if not practitioners_by_id:
        return {}
    for participant in encounter.participant or []:
        if not participant.type or not participant.type[0].coding:
            continue
        field_num = _PARTICIPATION_TYPE_TO_PV1_FIELD.get(participant.type[0].coding[0].code)
        individual = participant.individual
        if field_num is None or individual is None or not individual.reference:
            continue
        practitioner = practitioners_by_id.get(individual.reference.removeprefix("urn:uuid:"))
        if practitioner is None:
            continue
        fields.setdefault(field_num, []).append(build_xcn_from_practitioner(practitioner))
    return {num: "~".join(values) for num, values in fields.items()}


def build_minimal_pv1(encounter, practitioners_by_id: dict | None = None) -> str | None:
    """The minimal PV1 shape app.mappings.common.build_minimal_encounter
    itself reads: class code, visit identifier, the PL location chain and
    the PV1-7 attending practitioner - not the full ADT-shaped PV1
    app/transform/hl7_adt.py's own PV1 builder reverses.

    `practitioners_by_id` is what lets PV1-7 come back as a real XCN
    rather than a display string; without it the participant is skipped,
    since a Practitioner reference alone cannot be resolved here.
    """
    if encounter is None:
        return None
    fields: dict[int, str] = {1: "1"}
    class_code = encounter.class_fhir.code if encounter.class_fhir else None
    if class_code:
        fields[2] = CLASS_TO_PATIENT_CLASS.get(class_code, "O")
    fields.update(reverse_pv1_doctor_fields(encounter, practitioners_by_id or {}))
    if encounter.identifier:
        visit_number = encounter.identifier[0].value
        if visit_number:
            fields[19] = visit_number
    return segment("PV1", fields, 19)

# PL component number per physicalType code, and the one level the IG
# gives no code for. Reversing the chain means walking .partOf upward and
# placing each Location's identifier back in its own PL component.
_PHYSICAL_TYPE_TO_PL_COMPONENT = {"bd": 3, "ro": 2, "lvl": 8, "bu": 7, "si": 4}
_POINT_OF_CARE_COMPONENT = 1


def reverse_pl_field(location_reference, locations_by_id: dict) -> str:
    """Rebuild a PL field from the Location chain the forward mapper
    built, rather than from Reference.display.

    Walking .partOf and restoring each identifier to its own component is
    what makes PV1-3 round-trip exactly; the previous version wrote the
    joined display string into component 1, which both lost the other
    components and put a multi-part string where a receiver expects only
    the point of care."""
    reference = location_reference.reference if location_reference else None
    if not reference:
        # No resolvable chain - fall back to the display text, which at
        # least preserves something readable.
        return (location_reference.display if location_reference else None) or ""

    components: dict[int, str] = {}
    seen: set[str] = set()
    location_id = reference.removeprefix("urn:uuid:")
    while location_id and location_id in locations_by_id and location_id not in seen:
        seen.add(location_id)
        location = locations_by_id[location_id]
        value = location.identifier[0].value if location.identifier else None
        if value:
            code = (
                location.physicalType.coding[0].code
                if location.physicalType and location.physicalType.coding
                else None
            )
            components[_PHYSICAL_TYPE_TO_PL_COMPONENT.get(code, _POINT_OF_CARE_COMPONENT)] = value
        parent = location.partOf.reference if location.partOf else None
        location_id = parent.removeprefix("urn:uuid:") if parent else None

    if not components:
        return ""
    return "^".join(components.get(i, "") for i in range(1, max(components) + 1))
