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
# TXA-2 (Document Type) - a representative subset, CWE-shaped (code, display).
_DOCUMENT_TYPES = [
    ("CN", "Consultation Note"), ("DS", "Discharge Summary"),
    ("HP", "History and Physical"), ("OP", "Operative Report"),
    ("PN", "Progress Note"), ("RA", "Radiology Report"),
]
# TXA-3 (Document Content Presentation, HL7 table 0191) - only codes with a
# recognized MIME mapping in app.mappings.mdm are used, so every generated
# message exercises a real contentType rather than the text/plain fallback.
_CONTENT_PRESENTATION_CODES = ["TEXT", "FORMATTED", "HTML"]
# Plausible synthetic clinical-note lines, plain-text (TX-typed OBX-5 values).
_DOCUMENT_BODY_LINES = [
    "Patient seen for scheduled follow-up.",
    "No acute distress noted on exam.",
    "Vitals stable, within normal limits.",
    "Reviewed prior imaging; no new findings.",
    "Recommend continued monitoring and follow-up in 2 weeks.",
    "Medication list reviewed and unchanged.",
    "Patient counseled on lifestyle modifications.",
]

# (code, display, unit, reference_low, reference_high) - numeric lab tests.
_OBSERVATION_TESTS = [
    ("WBC", "White Blood Cell Count", "10*3/uL", 4.0, 11.0),
    ("HGB", "Hemoglobin", "g/dL", 12.0, 16.0),
    ("GLUCOSE", "Glucose", "mg/dL", 70, 100),
    ("NA", "Sodium", "mmol/L", 136, 145),
    ("K", "Potassium", "mmol/L", 3.5, 5.1),
    ("CREAT", "Creatinine", "mg/dL", 0.6, 1.3),
]
# (code, display) - free-text/coded (non-numeric) results, for OBX-2 value-type variety.
_TEXT_OBSERVATION_TESTS = [
    ("URINE-CULTURE", "Urine Culture Result"),
    ("STREP-A", "Strep A Rapid Test"),
    ("COVID-19", "SARS-CoV-2 Result"),
]
_TEXT_RESULT_VALUES = ["Negative", "Positive", "Not detected", "Inconclusive"]
_REPORT_PANELS = [
    ("CBC", "Complete Blood Count"),
    ("BMP", "Basic Metabolic Panel"),
    ("GLU", "Glucose Panel"),
    ("MICRO", "Microbiology Panel"),
]
_ABNORMAL_FLAGS = ["N", "H", "L", "A"]
# Codes this project's OBX-11/OBR-25 result-status mapping actually recognizes
# (see app.mappings.oru._RESULT_STATUS_MAP) - excludes D/W (deleted/wrong
# patient), which aren't realistic defaults for generated sample data.
_RESULT_STATUS_CODES = ["F", "P", "C", "A"]


def maybe(rng: random.Random, p: float = 0.6) -> bool:
    """True with probability p. The one place "should this optional field be
    included" gets decided, so every generator applies the same policy."""
    return rng.random() < p


def format_hl7_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def random_datetime_near_now(rng: random.Random, min_days: int = -30, max_days: int = 0) -> datetime:
    """A datetime within roughly [now+min_days, now+max_days].

    **Defaults to the past.** Almost everything these generators date has
    already happened - an admission, an observation, a document, a claim -
    and a sample carrying next month's onset date is not realistic test
    data. The two places a future date is correct (a scheduled SIU
    appointment, a C-CDA Planned Observation) pass a positive `max_days`
    explicitly, so the intent is visible at the call site.
    """
    # Truncated to the minute: only days/hours/minutes are seeded below, so
    # leaving real seconds/microseconds in the base would let unseeded
    # wall-clock entropy leak into the result - two calls with the same
    # seed a fraction of a second apart could otherwise produce different
    # output, breaking the "same seed always reproduces the same message"
    # guarantee whenever they straddle a real second boundary.
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    result = now + timedelta(
        days=rng.randint(min_days, max_days),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )
    # The random time-of-day is added on top of the day offset, so a
    # max_days=0 draw still lands up to 23h59m in the future - which is how
    # every format produced tomorrow's date despite asking for none.
    if max_days <= 0 and result > now:
        result -= timedelta(days=1)
    return result


def random_time_range(rng: random.Random, min_days: int = -30, max_days: int = 0) -> tuple[datetime, datetime]:
    """A (start, end) pair with end 15-90 minutes after start. Past by
    default, for the reason random_datetime_near_now documents."""
    start = random_datetime_near_now(rng, min_days, max_days)
    end = start + timedelta(minutes=rng.randint(15, 90))
    if max_days <= 0:
        # The duration is added after the clamp above, so an end can still
        # cross now even when the start did not. Shift the whole range
        # rather than shortening it, keeping the duration the caller's
        # rules depend on (e.g. discharge strictly after admit).
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if end > now:
            overshoot = end - now
            start -= overshoot
            end -= overshoot
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


def random_document_type(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_DOCUMENT_TYPES)


def random_content_presentation_code(rng: random.Random) -> str:
    return rng.choice(_CONTENT_PRESENTATION_CODES)


def random_document_body_line(rng: random.Random) -> str:
    return rng.choice(_DOCUMENT_BODY_LINES)


def random_observation_test(rng: random.Random) -> tuple[str, str, str, float, float]:
    return rng.choice(_OBSERVATION_TESTS)


def random_text_observation_test(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_TEXT_OBSERVATION_TESTS)


def random_text_result_value(rng: random.Random) -> str:
    return rng.choice(_TEXT_RESULT_VALUES)


def random_report_panel(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_REPORT_PANELS)


def random_abnormal_flag(rng: random.Random) -> str:
    return rng.choice(_ABNORMAL_FLAGS)


def random_result_status(rng: random.Random) -> str:
    return rng.choice(_RESULT_STATUS_CODES)


def segment(name: str, fields: dict[int, str], count: int) -> str:
    """Build a segment as a list of positional fields joined with '|', the
    same programmatic construction used for every test fixture in this repo
    - never hand-count pipe positions.

    `count` is the minimum width; a field beyond it extends the segment
    rather than raising, since a caller naming a field has said it wants
    one that far out."""
    parts = [name] + [""] * max(count, *fields) if fields else [name] + [""] * count
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


def _random_patient_identifiers(rng: random.Random) -> str:
    """PID-3 is 0..-1 and the mapper reads every repetition, so a second
    identifier (a different type code, per HL7 table 0203) is generated
    some of the time - without one nothing exercised the repeating half."""
    identifiers = [f"{random_identifier(rng)}^^^{random_hospital_code(rng)}^MR"]
    if maybe(rng, p=0.2):
        identifiers.append(f"{random_identifier(rng)}^^^{random_hospital_code(rng)}^PI")
    return "~".join(identifiers)


def generate_pid_segment(rng: random.Random) -> str:
    sex = random_sex(rng) if maybe(rng) else None
    family, given = random_person_name(rng, sex=sex)
    dob = None
    fields = {
        1: "1",
        3: _random_patient_identifiers(rng),
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
        phones = [random_phone(rng)]
        if maybe(rng, p=0.25):
            phones.append(random_phone(rng))
        fields[13] = "~".join(phones)
    fields.update(_random_demographics(rng, dob))
    return segment("PID", fields, 30)


# PID-15 Primary Language (table 0296), PID-16 Marital Status (0002) -
# both CWE, both mapping to a real Patient field per the v2-to-FHIR
# PID[Patient] map.
_LANGUAGES = [("en", "English"), ("es", "Spanish"), ("zh", "Chinese"), ("fr", "French")]
_MARITAL_STATUSES = [("M", "Married"), ("S", "Single"), ("D", "Divorced"), ("W", "Widowed")]


def _random_demographics(rng: random.Random, dob) -> dict[int, str]:
    """PID-15/16 and the two choice-type pairs, PID-24/25 (multiple birth)
    and PID-29/30 (deceased).

    Each pair is generated on both of its branches, since the mapper picks
    a different FHIR field depending on which one is valued - generating
    only the indicator would leave multipleBirthInteger/deceasedDateTime
    untested.
    """
    fields: dict[int, str] = {}
    if maybe(rng):
        code, display = rng.choice(_LANGUAGES)
        fields[15] = f"{code}^{display}^HL70296"
    if maybe(rng):
        code, display = rng.choice(_MARITAL_STATUSES)
        fields[16] = f"{code}^{display}^HL70002"
    if maybe(rng, p=0.2):
        if maybe(rng):
            fields[25] = str(rng.randint(1, 3))
        else:
            fields[24] = "Y"
    elif maybe(rng, p=0.15):
        fields[24] = "N"
    if maybe(rng, p=0.15):
        # A death date must not precede the birth date the same segment
        # already carries, or the validator's own before-birth rule fires
        # on a message the generator promises is clean.
        earliest = -365 * 79 if dob is None else 0
        death = random_datetime_near_now(rng, min_days=earliest, max_days=0)
        if dob is None or death.date() >= dob.date():
            fields[29] = format_hl7_datetime(death)
    elif maybe(rng, p=0.15):
        fields[30] = "N"
    return fields


# PV1-7 Attending, PV1-8 Referring, PV1-9 Consulting, PV1-17 Admitting -
# every one of them XCN and 0..-1 per the v2-to-FHIR PV1[Encounter] map.
_PV1_DOCTOR_FIELDS = (7, 8, 9, 17)

# The coded PV1 fields the v2-to-FHIR map routes onto Encounter.type,
# .serviceType and .hospitalization. Values are drawn from the real HL7
# tables each field is bound to, so a generated message stays plausible.
_PV1_CODED_POOLS = {
    4: [("E", "Emergency"), ("R", "Routine"), ("U", "Urgent")],
    10: [("SUR", "Surgery"), ("MED", "Medicine"), ("CAR", "Cardiology")],
    13: [("R", "Re-admission")],
    14: [("1", "Physician referral"), ("7", "Emergency room"), ("4", "Transfer from a hospital")],
    15: [("A0", "No functional limitations"), ("B6", "Pregnant")],
    16: [("Y", "VIP"), ("N", "Not a VIP")],
    38: [("LF", "Low fat"), ("NAS", "No added salt"), ("REG", "Regular")],
}


def build_minimal_pv1_fields(rng: random.Random, patient_class: str) -> dict:
    """The PV1 fields common to every generator that includes a PV1 segment:
    PV1-1 (set id), PV1-2 (patient class), the optional doctor fields
    PV1-7/8/9/17, and PV1-19 (visit number). Shared by app.generators.adt
    and app.generators.oru so their PV1 generation doesn't independently
    drift - callers add any further trigger-specific fields (location,
    discharge time, etc.) on top of the returned dict.

    All four doctor fields are 0..-1 in the v2-to-FHIR PV1[Encounter] map,
    so a repetition is generated some of the time: without one, nothing
    exercised the repeating half of the mapping, and only the first
    attending doctor was ever read.
    """
    fields = {1: "1", 2: patient_class}
    for field_num in _PV1_DOCTOR_FIELDS:
        if not maybe(rng):
            continue
        doctors = [random_physician_xcn(rng)]
        if maybe(rng, p=0.25):
            doctors.append(random_physician_xcn(rng))
        fields[field_num] = "~".join(doctors)
    for field_num, pool in _PV1_CODED_POOLS.items():
        if maybe(rng):
            code, display = rng.choice(pool)
            fields[field_num] = f"{code}^{display}^L"
    if maybe(rng):
        fields[5] = f"PRE{random_identifier(rng, 5)}^^^HOSP"
    if maybe(rng):
        fields[19] = f"V{random_identifier(rng, 4)}"
    return fields
