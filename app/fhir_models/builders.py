import re

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.humanname import HumanName

from app.hl7.parser import component_str, field_repetitions, field_str

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


def build_human_names(pid_segment) -> list[HumanName]:
    """Build Patient.name from all repetitions of PID-5 (XPN)."""
    names = []
    for i, repetition in enumerate(field_repetitions(pid_segment, 5)):
        family = component_str(repetition, 1)
        given_parts = [part for part in (component_str(repetition, 2), component_str(repetition, 3)) if part]
        if not family and not given_parts:
            continue
        name = HumanName(use="official" if i == 0 else "old")
        if family:
            name.family = family
        if given_parts:
            name.given = given_parts
        names.append(name)
    return names


def build_addresses(pid_segment) -> list[Address]:
    """Build Patient.address from all repetitions of PID-11 (XAD)."""
    addresses = []
    for repetition in field_repetitions(pid_segment, 11):
        line1 = component_str(repetition, 1)
        line2 = component_str(repetition, 2)
        city = component_str(repetition, 3)
        state = component_str(repetition, 4)
        postal_code = component_str(repetition, 5)
        country = component_str(repetition, 6)
        lines = [part for part in (line1, line2) if part]
        if not any([lines, city, state, postal_code, country]):
            continue
        address = Address()
        if lines:
            address.line = lines
        if city:
            address.city = city
        if state:
            address.state = state
        if postal_code:
            address.postalCode = postal_code
        if country:
            address.country = country
        addresses.append(address)
    return addresses


def build_phone_telecom(pid_segment) -> ContactPoint | None:
    """Build a single Patient.telecom entry from PID-13 (home phone, XTN)."""
    phone = field_str(pid_segment, 13)
    if not phone:
        return None
    return ContactPoint(system="phone", use="home", value=phone)


def build_codeable_concept_from_cwe(segment, field_num: int) -> CodeableConcept | None:
    """Build a CodeableConcept from a CWE-shaped field (code=component 1,
    display=component 2, coding system=component 3, falling back to a
    urn:interop-tools system when component 3 is absent). Returns None when the
    code component is empty."""
    code = field_str(segment, field_num, component=1)
    if not code:
        return None
    display = field_str(segment, field_num, component=2)
    system = field_str(segment, field_num, component=3) or CWE_FALLBACK_SYSTEM
    coding = Coding(system=system, code=code)
    if display:
        coding.display = display
    return CodeableConcept(coding=[coding])
