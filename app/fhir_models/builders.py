import re

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.humanname import HumanName

from app.hl7.parser import component_str, field_repetitions, field_str
from app.provenance.location import hl7_location

_GENDER_MAP = {"M": "male", "F": "female", "O": "other"}
# Public (not module-private) - app/transform/hl7_siu.py reuses this as
# its second real consumer, to know when a CodeableConcept.coding.system
# should reverse back to an empty CWE component 3 (fallback was used) vs.
# a real system string (component 3 was genuinely present).
CWE_FALLBACK_SYSTEM = "urn:interop-tools:coded-value"


def hl7_sex_to_fhir_gender(code: str) -> str:
    return _GENDER_MAP.get(code.strip().upper(), "unknown")


def parse_hl7_date(ts_field: str) -> str | None:
    """Convert an HL7 TS value (YYYYMMDD[HHMM[SS]]...) to a FHIR date (YYYY-MM-DD)."""
    digits = ts_field.strip()[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


_TZ_OFFSET_RE = re.compile(r"([+-]\d{4})$")


def parse_hl7_datetime(ts_field: str) -> str | None:
    """Convert an HL7 TS value (YYYYMMDDHHMM[SS][+/-ZZZZ]) to a FHIR dateTime.

    A trailing HL7-style timezone offset (+/-ZZZZ) is preserved as-is in the
    output (reformatted to FHIR's +HH:MM shape) rather than being dropped -
    silently mislabeling an offset time as UTC would be a real scheduling
    error, not just a cosmetic timestamp issue. Absent an offset, UTC (Z) is
    assumed, same as before.
    """
    raw = ts_field.strip()
    tz_match = _TZ_OFFSET_RE.search(raw)
    offset = None
    if tz_match:
        offset = tz_match.group(1)
        raw = raw[: tz_match.start()]
    digits = raw
    if len(digits) < 12 or not digits[:12].isdigit():
        return None
    year, month, day = digits[0:4], digits[4:6], digits[6:8]
    hour, minute = digits[8:10], digits[10:12]
    second = digits[12:14] if len(digits) >= 14 and digits[12:14].isdigit() else "00"
    tz = f"{offset[:3]}:{offset[3:]}" if offset else "Z"
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}{tz}"


def build_human_names(pid_segment, resource_id: str | None = None, recorder=None) -> list[HumanName]:
    """Build Patient.name from all repetitions of PID-5 (XPN). `resource_id`/
    `recorder` are optional (see app/provenance/recorder.py) - when given,
    each written field is recorded against its own real PID-5 repetition/
    component, tracking which of components 2/3 actually survived into
    `given` (an empty middle name, say, would otherwise desync the `given[i]`
    array index from its own component number)."""
    names = []
    for i, repetition in enumerate(field_repetitions(pid_segment, 5)):
        family = component_str(repetition, 1)
        given_candidates = [(2, component_str(repetition, 2)), (3, component_str(repetition, 3))]
        given_parts = [(component, value) for component, value in given_candidates if value]
        if not family and not given_parts:
            continue
        name = HumanName(use="official" if i == 0 else "old")
        name_index = len(names)
        if family:
            name.family = family
            if recorder:
                recorder.record(
                    resource_id, f"name[{name_index}].family", hl7_location("PID", 5, repetition=i, component=1), family
                )
        if given_parts:
            name.given = [value for _, value in given_parts]
            if recorder:
                for given_index, (component, value) in enumerate(given_parts):
                    recorder.record(
                        resource_id,
                        f"name[{name_index}].given[{given_index}]",
                        hl7_location("PID", 5, repetition=i, component=component),
                        value,
                    )
        names.append(name)
    return names


def build_addresses(pid_segment, resource_id: str | None = None, recorder=None) -> list[Address]:
    """Build Patient.address from all repetitions of PID-11 (XAD)."""
    addresses = []
    for i, repetition in enumerate(field_repetitions(pid_segment, 11)):
        line_candidates = [(1, component_str(repetition, 1)), (2, component_str(repetition, 2))]
        line_parts = [(component, value) for component, value in line_candidates if value]
        city = component_str(repetition, 3)
        state = component_str(repetition, 4)
        postal_code = component_str(repetition, 5)
        country = component_str(repetition, 6)
        if not any([line_parts, city, state, postal_code, country]):
            continue
        address = Address()
        address_index = len(addresses)
        if line_parts:
            address.line = [value for _, value in line_parts]
            if recorder:
                for line_index, (component, value) in enumerate(line_parts):
                    recorder.record(
                        resource_id,
                        f"address[{address_index}].line[{line_index}]",
                        hl7_location("PID", 11, repetition=i, component=component),
                        value,
                    )
        if city:
            address.city = city
            if recorder:
                recorder.record(
                    resource_id, f"address[{address_index}].city", hl7_location("PID", 11, repetition=i, component=3), city
                )
        if state:
            address.state = state
            if recorder:
                recorder.record(
                    resource_id, f"address[{address_index}].state", hl7_location("PID", 11, repetition=i, component=4), state
                )
        if postal_code:
            address.postalCode = postal_code
            if recorder:
                recorder.record(
                    resource_id,
                    f"address[{address_index}].postalCode",
                    hl7_location("PID", 11, repetition=i, component=5),
                    postal_code,
                )
        if country:
            address.country = country
            if recorder:
                recorder.record(
                    resource_id,
                    f"address[{address_index}].country",
                    hl7_location("PID", 11, repetition=i, component=6),
                    country,
                )
        addresses.append(address)
    return addresses


def build_phone_telecoms(pid_segment, resource_id: str | None = None, recorder=None) -> list[ContactPoint]:
    """Patient.telecom from every repetition of PID-13 (home phone, XTN).

    PID-13 is 0..-1 in the v2-to-FHIR PID[Patient] map - a patient with a
    home and a mobile number is ordinary - and reading only the first
    repetition silently discarded the rest.
    """
    telecoms = []
    for index, repetition in enumerate(field_repetitions(pid_segment, 13)):
        phone = component_str(repetition, 1)
        if not phone:
            continue
        telecoms.append(ContactPoint(system="phone", use="home", value=phone))
        if recorder:
            recorder.record(
                resource_id,
                f"telecom[{len(telecoms) - 1}].value",
                hl7_location("PID", 13, repetition=index),
                phone,
            )
    return telecoms


def build_codeable_concept_from_cwe(
    segment, field_num: int, resource_id: str | None = None, relative_path: str | None = None, recorder=None
) -> CodeableConcept | None:
    """Build a CodeableConcept from a CWE-shaped field (code=component 1,
    display=component 2, coding system=component 3, falling back to a
    urn:interop-tools system when component 3 is absent). Returns None when the
    code component is empty.

    `resource_id`/`relative_path`/`recorder` are optional (see
    app/provenance/recorder.py) - when all three are given, `.coding[0].code`/
    `.coding[0].display` are recorded against `{relative_path}.coding[0].*`.
    Recording lives here, not in each caller, since this builder is reused
    across multiple mappers (SIU's SCH-7/SCH-8/AIS-3, and others as their own
    slices land) - centralizing it once avoids every caller re-deriving the
    identical code/display extraction just to record it. The segment id for
    `hl7_location` is read from the segment itself (field 0 is always the
    segment's own name, e.g. "SCH"/"AIS") rather than passed in separately,
    since this function is already handed the real segment object."""
    code = field_str(segment, field_num, component=1)
    if not code:
        return None
    display = field_str(segment, field_num, component=2)
    system = field_str(segment, field_num, component=3) or CWE_FALLBACK_SYSTEM
    coding = Coding(system=system, code=code)
    if display:
        coding.display = display
    if recorder and resource_id and relative_path:
        segment_id = field_str(segment, 0)
        recorder.record(
            resource_id, f"{relative_path}.coding[0].code", hl7_location(segment_id, field_num, component=1), code
        )
        if display:
            recorder.record(
                resource_id,
                f"{relative_path}.coding[0].display",
                hl7_location(segment_id, field_num, component=2),
                display,
            )
        # Component 3 becomes Coding.system verbatim when present, so it is
        # mapped, not lost - recording only code/display made every CWE
        # field's own coding system report as dropped data while the value
        # sat in the Bundle. Absent, the system is a fixed local fallback
        # with no source component to point at, so it is inferred instead.
        source_system = field_str(segment, field_num, component=3)
        if source_system:
            recorder.record(
                resource_id,
                f"{relative_path}.coding[0].system",
                hl7_location(segment_id, field_num, component=3),
                source_system,
            )
        else:
            recorder.record_inferred(
                resource_id,
                f"{relative_path}.coding[0].system",
                f"No coding system in component 3; defaulted to {CWE_FALLBACK_SYSTEM!r}.",
                CWE_FALLBACK_SYSTEM,
            )
    return CodeableConcept(coding=[coding])
