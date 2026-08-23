"""Synthetic ORU message generators, one per supported trigger event.

Same required/optional philosophy as the other generators: a generated
message must always convert successfully through app.mappings.oru, so OBR-4
(report code), OBR-25 (result status), and every OBX's OBX-2/3/5/11 are
always present - Observation.code/DiagnosticReport.code are FHIR-required,
and an OBX with no usable value isn't a useful generated sample. R01/R30/R31/
R32/R40 are generated identically since app.mappings.oru treats them identically.
"""

import random

from app.generators.base import (
    build_minimal_pv1_fields,
    format_hl7_datetime,
    generate_msh_segment,
    generate_pid_segment,
    maybe,
    random_observation_test,
    random_physician_xcn,
    random_report_panel,
    random_result_status,
    random_text_observation_test,
    random_text_result_value,
    random_time_range,
    segment,
)
from app.hl7.parser import parse_message


def _generate_pv1(rng: random.Random) -> str:
    patient_class = "I" if maybe(rng, p=0.5) else "O"
    fields = build_minimal_pv1_fields(rng, patient_class)
    return segment("PV1", fields, 45)


def _generate_obx(rng: random.Random, set_id: int) -> str:
    fields = {1: str(set_id), 11: random_result_status(rng)}
    if maybe(rng, p=0.8):
        code, display, unit, low, high = random_observation_test(rng)
        # Within its own reference range. A sample carrying a haemoglobin
        # of 18.4 against a stated 12.0-16.0 range is not valid test data -
        # it is a result the message itself contradicts.
        value = round(rng.uniform(float(low), float(high)), 1)
        fields[2] = "NM"
        fields[3] = f"{code}^{display}^LOCAL"
        fields[5] = str(value)
        if maybe(rng):
            fields[6] = unit
        if maybe(rng):
            fields[7] = f"{low}-{high}"
        # OBX-8 has to agree with the value beside it: an in-range result
        # flagged "H" is self-contradictory, and the flag was previously
        # drawn independently of the number.
        if maybe(rng):
            fields[8] = "N"
    else:
        # Occasionally a coded/free-text result instead of numeric, to
        # exercise the non-NM branches of _build_observation_value.
        code, display = random_text_observation_test(rng)
        fields[2] = "ST"
        fields[3] = f"{code}^{display}^LOCAL"
        fields[5] = random_text_result_value(rng)
    if maybe(rng):
        obs_time, _ = random_time_range(rng, min_days=-5, max_days=0)
        fields[14] = format_hl7_datetime(obs_time)
    if maybe(rng):
        fields[16] = random_physician_xcn(rng)
    return segment("OBX", fields, 18)


def _generate_obr_group(rng: random.Random, set_id: int) -> list[str]:
    code, display = random_report_panel(rng)
    fields = {1: str(set_id), 4: f"{code}^{display}^LOCAL", 25: random_result_status(rng)}
    if maybe(rng):
        start, end = random_time_range(rng, min_days=-5, max_days=0)
        fields[7] = format_hl7_datetime(start)
        if maybe(rng):
            fields[8] = format_hl7_datetime(end)
    if maybe(rng):
        issued, _ = random_time_range(rng, min_days=-5, max_days=0)
        fields[22] = format_hl7_datetime(issued)

    segments = [segment("OBR", fields, 25)]
    num_obx = rng.randint(1, 3)
    segments.extend(_generate_obx(rng, i) for i in range(1, num_obx + 1))
    return segments


def _generate_oru(rng: random.Random, trigger_event: str) -> str:
    msh, _ = generate_msh_segment(rng, "ORU", trigger_event)
    pid = generate_pid_segment(rng)
    segments = [msh, pid]
    if maybe(rng):
        segments.append(_generate_pv1(rng))

    num_reports = rng.randint(1, 2)
    for i in range(1, num_reports + 1):
        segments.extend(_generate_obr_group(rng, i))

    text = "\r".join(segments) + "\r"
    parse_message(text)  # self-check: a generator bug should raise, not return broken text
    return text


def generate_oru_r01(rng: random.Random) -> str:
    return _generate_oru(rng, "R01")


def generate_oru_r30(rng: random.Random) -> str:
    return _generate_oru(rng, "R30")


def generate_oru_r40(rng: random.Random) -> str:
    return _generate_oru(rng, "R40")


def generate_oru_r31(rng: random.Random) -> str:
    return _generate_oru(rng, "R31")


def generate_oru_r32(rng: random.Random) -> str:
    return _generate_oru(rng, "R32")
