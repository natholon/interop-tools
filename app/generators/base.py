"""Shared randomization primitives for the synthetic HL7 message generators.

Every generator function takes an `rng: random.Random` instance rather than
calling the global `random` module directly, so `registry.generate(...,
seed=...)` can reproduce the exact same message byte-for-byte.
"""

import random
from datetime import datetime, timedelta, timezone

# All data below is deliberately synthetic/fake - illustrative example values,
# not real people, places, or facilities.

_FIRST_NAMES_MALE = [
    "James", "John", "Robert", "Michael", "David", "Carlos", "Wei", "Arjun",
    "Kenji", "Mohammed", "Liam", "Noah", "Elijah", "Omar", "Diego",
]
_FIRST_NAMES_FEMALE = [
    "Mary", "Patricia", "Jennifer", "Linda", "Priya", "Betty", "Wendy",
    "Anh", "Fatima", "Sofia", "Emma", "Olivia", "Ava", "Mei", "Amara",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Patel", "Garcia", "Kim",
    "Nguyen", "Reyes", "Tran", "Ortiz", "Chen", "Okafor", "Rossi", "Muller",
    "Silva", "Novak", "Haddad", "Ivanov", "Andersen",
]
_CITY_STATE_ZIP = [
    ("Springfield", "IL", "62704"),
    ("Riverside", "CA", "92501"),
    ("Portland", "OR", "97201"),
    ("Austin", "TX", "73301"),
    ("Madison", "WI", "53703"),
    ("Asheville", "NC", "28801"),
    ("Boulder", "CO", "80301"),
    ("Burlington", "VT", "05401"),
]
_STREET_NAMES = [
    "Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Elm St", "Park Blvd",
    "Sunset Rd", "River Rd", "Highland Ave", "Willow Way",
]
_LOCATION_CODES = [
    ("W123", "456"), ("W456", "101"), ("C100", ""), ("ICU1", "201"),
    ("ER1", "B12"), ("W789", "212"), ("OR3", ""), ("PEDS2", "310"),
]
_HOSPITAL_CODES = ["HOSP", "GENHOSP", "STMARY", "CITYCLINIC"]
_SENDING_APPS = ["REGSYS", "ADTSYS", "SCHEDSYS"]
_FACILITY_NAMES = ["GENHOSP", "STMARY", "CITYCLINIC", "MAINCAMPUS"]

_REASON_CODES = [
    ("CHECKUP", "Routine Checkup"), ("FOLLOWUP", "Follow-up Visit"),
    ("CONSULT", "Consultation"), ("URGENT", "Urgent Care Visit"),
    ("PHYSICAL", "Annual Physical"),
]
_APPOINTMENT_TYPE_CODES = [
    ("ROUTINE", "Routine Appointment"), ("WALKIN", "Walk-in"),
    ("FOLLOWUP", "Follow-up"), ("NEW", "New Patient"),
]
_SERVICE_NAMES = [
    ("MRI", "MRI Scan"), ("CT", "CT Scan"), ("XRAY", "X-Ray"),
    ("PT", "Physical Therapy"), ("LAB", "Lab Work"), ("CONSULT", "Consultation"),
]
_EQUIPMENT = [
    ("EQ001", "Portable X-Ray"), ("EQ002", "Ultrasound Unit"),
    ("EQ003", "ECG Monitor"), ("EQ004", "Infusion Pump"),
]
# Real HL7 table 0112 (discharge disposition) codes - a representative subset.
_DISCHARGE_DISPOSITION_CODES = ["01", "02", "03", "06", "07"]
_NTE_COMMENTS = [
    "Patient prefers morning slots",
    "Contrast required for imaging",
    "Follow up in two weeks",
    "Interpreter requested",
    "Wheelchair access needed",
]


def maybe(rng: random.Random, p: float = 0.6) -> bool:
    """True with probability p. The one place "should this optional field be
    included" gets decided, so every generator applies the same policy."""
    return rng.random() < p


def format_hl7_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def random_datetime_near_now(rng: random.Random, min_days: int = -2, max_days: int = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        days=rng.randint(min_days, max_days),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )


def random_time_range(rng: random.Random, min_days: int = -2, max_days: int = 30) -> tuple[datetime, datetime]:
    start = random_datetime_near_now(rng, min_days, max_days)
    end = start + timedelta(minutes=rng.randint(15, 90))
    return start, end


def random_control_id(rng: random.Random) -> str:
    return f"MSG{rng.randint(100000, 999999)}"


def random_identifier(rng: random.Random, digits: int = 6) -> str:
    return str(rng.randint(10 ** (digits - 1), 10**digits - 1))


def random_sex(rng: random.Random) -> str:
    return rng.choice(("M", "F"))


def random_person_name(rng: random.Random, sex: str | None = None) -> tuple[str, str]:
    """Return (family, given). Drawn from a sex-appropriate first-name pool
    when sex is 'M'/'F' (keeps PID-5/PID-8 internally consistent); otherwise
    from either pool."""
    if sex == "M":
        given = rng.choice(_FIRST_NAMES_MALE)
    elif sex == "F":
        given = rng.choice(_FIRST_NAMES_FEMALE)
    else:
        given = rng.choice(_FIRST_NAMES_MALE + _FIRST_NAMES_FEMALE)
    family = rng.choice(_LAST_NAMES)
    return family, given


def random_address(rng: random.Random) -> tuple[str, str, str, str]:
    """Return (line1, city, state, zip)."""
    line1 = f"{rng.randint(100, 9999)} {rng.choice(_STREET_NAMES)}"
    city, state, zip_code = rng.choice(_CITY_STATE_ZIP)
    return line1, city, state, zip_code


def random_phone(rng: random.Random) -> str:
    return f"({rng.randint(200, 999)}){rng.randint(200, 999)}-{rng.randint(1000, 9999)}"


def random_location(rng: random.Random) -> tuple[str, str]:
    """Return (facility, room) for a PL-shaped field (PV1-3/6, AIL-3)."""
    return rng.choice(_LOCATION_CODES)


def random_hospital_code(rng: random.Random) -> str:
    return rng.choice(_HOSPITAL_CODES)


def random_location_field(rng: random.Random) -> str:
    """A full PL-shaped field value (facility^room^bed^hospital), used for
    PV1-3, PV1-6, and AIL-3 - all the same shape."""
    facility, room = random_location(rng)
    return f"{facility}^{room}^A^{random_hospital_code(rng)}"


def random_physician_xcn(rng: random.Random) -> str:
    """A full XCN-shaped field value (id^family^given^^^^MD), used for
    PV1-7 and AIP-3 - all the same shape."""
    family, given = random_person_name(rng)
    return f"{random_identifier(rng, 4)}^{family}^{given}^^^^MD"


def random_reason_code(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_REASON_CODES)


def random_appointment_type_code(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_APPOINTMENT_TYPE_CODES)


def random_service(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_SERVICE_NAMES)


def random_equipment(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_EQUIPMENT)


def random_discharge_disposition(rng: random.Random) -> str:
    return rng.choice(_DISCHARGE_DISPOSITION_CODES)


def random_nte_comment(rng: random.Random) -> str:
    return rng.choice(_NTE_COMMENTS)


def segment(name: str, fields: dict[int, str], count: int) -> str:
    """Build a segment as a list of positional fields joined with '|', the
    same programmatic construction used for every test fixture in this repo
    - never hand-count pipe positions."""
    parts = [name] + [""] * count
    for idx, val in fields.items():
        parts[idx] = val
    return "|".join(parts)


def generate_msh_segment(rng: random.Random, message_type: str, trigger_event: str) -> tuple[str, str]:
    """Returns (segment_text, formatted_datetime) - the datetime is exposed so
    callers can reuse the exact same value for EVN-2 (recorded date/time)."""
    dt = format_hl7_datetime(random_datetime_near_now(rng, min_days=0, max_days=0))
    sending_app = rng.choice(_SENDING_APPS)
    sending_facility = rng.choice(_FACILITY_NAMES)
    receiving_app = rng.choice(_SENDING_APPS)
    receiving_facility = rng.choice(_FACILITY_NAMES)
    text = (
        f"MSH|^~\\&|{sending_app}|{sending_facility}|{receiving_app}|{receiving_facility}|"
        f"{dt}||{message_type}^{trigger_event}|{random_control_id(rng)}|P|2.5"
    )
    return text, dt


def generate_pid_segment(rng: random.Random) -> str:
    sex = random_sex(rng) if maybe(rng) else None
    family, given = random_person_name(rng, sex=sex)
    fields = {
        1: "1",
        3: f"{random_identifier(rng)}^^^{random_hospital_code(rng)}^MR",
        5: f"{family}^{given}",
    }
    if sex:
        fields[8] = sex
    if maybe(rng):
        dob = random_datetime_near_now(rng, min_days=-365 * 80, max_days=-365 * 1)
        fields[7] = dob.strftime("%Y%m%d")
    if maybe(rng):
        line1, city, state, zip_code = random_address(rng)
        fields[11] = f"{line1}^^{city}^{state}^{zip_code}^USA"
    if maybe(rng):
        fields[13] = random_phone(rng)
    return segment("PID", fields, 13)
