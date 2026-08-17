"""FHIR Bundle -> HL7v2 ADT^A01 - the first reverse-direction slice in this
app, chosen for the same reason ADT^A01 was this app's very first
forward-direction slice: the simplest, best-understood shape (Patient +
Encounter only), so it proves the reverse-direction architecture
(app/transform/base.py's MessageBuilder interface, app/transform/
registry.py's dispatch, app/transform/common.py's shared helpers) without
also having to solve a harder message shape's own field-mapping questions
at the same time.

Reverses app/mappings/common.py::build_patient/app/mappings/adt.py::
build_encounter_core field-for-field, using each one's own exact field
positions/component shapes - not re-deriving them independently, so a
future change to the forward mapping's field shape has an obvious reverse
counterpart to update. **A real, disclosed round-trip fidelity gap, not a
bug**: several HL7v2 fields the forward mapper reads have no FHIR-side
home at all (MSH-3/4/5/6 sending/receiving application-facility, PV1-2's
full patient-class nuance beyond the four codes FHIR's ActEncounterCode
binding covers), and two forward-mapped fields are collapsed into a single
*display string* rather than kept as separate components
(app.mappings.common.location_display for PV1-3, person_display for
PV1-7) - reversing a display string back into structured components is
inherently lossy/ambiguous, so this builder makes the same one deliberate
choice both fields share: place the whole display string in the field's
first component and leave the rest empty, rather than guessing at a split
point. Fields with no FHIR-side source at all get a fixed, disclosed
placeholder value (matching this app's own generator's own "always
produce valid output" precedent) rather than being left empty, since MSH's
own structural fields are required by the HL7v2 standard itself."""

from datetime import datetime, timezone

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, format_hl7_date, format_hl7_ts

# Reverse of app/mappings/common.py::_PATIENT_CLASS_MAP - "AMB" is the
# fallback on the forward side, so it's also the safest default here for
# an Encounter.class code this table doesn't recognize.
_CLASS_TO_PATIENT_CLASS = {"IMP": "I", "AMB": "O", "EMER": "E", "PRENC": "P"}
# Reverse of app/fhir_models/builders.py::_GENDER_MAP.
_GENDER_TO_HL7_SEX = {"male": "M", "female": "F", "other": "O"}

_DEFAULT_SENDING_APP = "interop-tools"
_DEFAULT_SENDING_FACILITY = "INTEROP"
_DEFAULT_RECEIVING_APP = "UNKNOWN"
_DEFAULT_RECEIVING_FACILITY = "UNKNOWN"
_DEFAULT_CONTROL_ID = "MSG00001"


def _build_msh(bundle: Bundle) -> tuple[str, str]:
    """Returns (segment_text, formatted_datetime) - MSH-7 is reused for
    EVN-2 when the Encounter itself has no period.start, the same "reuse
    the message-level timestamp as an event fallback" relationship the
    forward mapper's build_encounter_core already has with EVN-2."""
    dt = format_hl7_ts(datetime.now(timezone.utc))
    if bundle.timestamp:
        dt = format_hl7_ts(bundle.timestamp) or dt
    control_id = (bundle.identifier.value if bundle.identifier else None) or _DEFAULT_CONTROL_ID
    text = (
        f"MSH|^~\\&|{_DEFAULT_SENDING_APP}|{_DEFAULT_SENDING_FACILITY}|"
        f"{_DEFAULT_RECEIVING_APP}|{_DEFAULT_RECEIVING_FACILITY}|{dt}||ADT^A01|{control_id}|P|2.5"
    )
    return text, dt


def _build_evn(encounter, msh_dt: str) -> str:
    evn_dt = msh_dt
    if encounter is not None and encounter.period and encounter.period.start:
        evn_dt = format_hl7_ts(encounter.period.start) or msh_dt
    return segment("EVN", {1: "A01", 2: evn_dt}, 2)


def _build_pid(patient) -> str:
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
        sex = _GENDER_TO_HL7_SEX.get(patient.gender)
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


def _build_pv1(encounter) -> str:
    fields: dict[int, str] = {1: "1"}
    if encounter is None:
        return segment("PV1", fields, 45)

    if encounter.class_fhir and encounter.class_fhir.code:
        fields[2] = _CLASS_TO_PATIENT_CLASS.get(encounter.class_fhir.code, "O")

    if encounter.location:
        display = encounter.location[0].location.display if encounter.location[0].location else None
        if display:
            fields[3] = display

    if encounter.participant:
        display = encounter.participant[0].individual.display if encounter.participant[0].individual else None
        if display:
            fields[7] = display

    if encounter.identifier:
        visit_number = encounter.identifier[0].value
        if visit_number:
            fields[19] = visit_number

    if encounter.period:
        if encounter.period.start:
            fields[44] = format_hl7_ts(encounter.period.start)
        if encounter.period.end:
            fields[45] = format_hl7_ts(encounter.period.end)

    return segment("PV1", fields, 45)


class AdtA01Builder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError("Bundle has no Patient resource - cannot build an ADT^A01 message")
        encounter = find_resource(bundle, "Encounter")

        msh, msh_dt = _build_msh(bundle)
        evn = _build_evn(encounter, msh_dt)
        pid = _build_pid(patient)
        pv1 = _build_pv1(encounter)

        return "\r".join([msh, evn, pid, pv1]) + "\r"
