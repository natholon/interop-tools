from fhir.resources.R4B.address import Address
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.humanname import HumanName

from app.hl7.parser import component_str, field_repetitions, field_str

_GENDER_MAP = {"M": "male", "F": "female", "O": "other"}


def hl7_sex_to_fhir_gender(code: str) -> str:
    return _GENDER_MAP.get(code.strip().upper(), "unknown")


def parse_hl7_date(ts_field: str) -> str | None:
    """Convert an HL7 TS value (YYYYMMDD[HHMM[SS]]...) to a FHIR date (YYYY-MM-DD)."""
    digits = ts_field.strip()[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def parse_hl7_datetime(ts_field: str) -> str | None:
    """Convert an HL7 TS value (YYYYMMDDHHMM[SS]) to a FHIR dateTime (assumes UTC)."""
    digits = ts_field.strip()
    if len(digits) < 12 or not digits[:12].isdigit():
        return None
    year, month, day = digits[0:4], digits[4:6], digits[6:8]
    hour, minute = digits[8:10], digits[10:12]
    second = digits[12:14] if len(digits) >= 14 and digits[12:14].isdigit() else "00"
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"


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
