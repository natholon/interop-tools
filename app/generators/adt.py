"""Synthetic ADT message generators, one per supported trigger event.

Required/optional policy is documented per-field below; the guiding rule
(see CLAUDE.md) is that a generated message must always convert successfully
through app.mappings.adt - so anything the mapper actually requires for a
given trigger is always populated, never left to chance.
"""

import random

from app.generators.base import (
    build_minimal_pv1_fields,
    format_hl7_datetime,
    generate_msh_segment,
    generate_pid_segment,
    maybe,
    random_discharge_disposition,
    random_location_field,
    random_time_range,
    segment,
)
from app.hl7.parser import parse_message


def _assemble(msh: str, evn: str | None, pid: str, pv1: str) -> str:
    segments = [msh]
    if evn is not None:
        segments.append(evn)
    segments.extend([pid, pv1])
    text = "\r".join(segments) + "\r"
    parse_message(text)  # self-check: a generator bug should raise, not return broken text
    return text


def generate_adt_a01(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A01")
    evn = segment("EVN", {1: "A01", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    if maybe(rng):
        start, _ = random_time_range(rng)
        fields[44] = format_hl7_datetime(start)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a02(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A02")
    evn = segment("EVN", {1: "A02", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    fields[6] = random_location_field(rng)  # required for A02: prior location drives the transfer history
    if maybe(rng):
        start, _ = random_time_range(rng)
        fields[44] = format_hl7_datetime(start)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a03(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A03")
    evn = segment("EVN", {1: "A03", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    # required: the mapper raises MappingError for A03 without a discharge time
    admit, discharge = random_time_range(rng)
    fields[44] = format_hl7_datetime(admit)
    fields[45] = format_hl7_datetime(discharge)
    if maybe(rng):
        fields[36] = random_discharge_disposition(rng)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a04(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A04")
    evn = segment("EVN", {1: "A04", 2: dt}, 2) if maybe(rng) else None
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "O")
    if maybe(rng):
        fields[3] = random_location_field(rng)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a08(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A08")
    evn = segment("EVN", {1: "A08", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    if maybe(rng):
        start, _ = random_time_range(rng)
        fields[44] = format_hl7_datetime(start)
    # deliberately ~50%, not the general optional rate: this is what exercises
    # AdtA08Mapper's finished-vs-in-progress status inference in both directions
    if maybe(rng, p=0.5):
        _, discharge = random_time_range(rng)
        fields[45] = format_hl7_datetime(discharge)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a05(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A05")
    evn = segment("EVN", {1: "A05", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    if maybe(rng):
        start, _ = random_time_range(rng)
        fields[44] = format_hl7_datetime(start)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a11(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A11")
    evn = segment("EVN", {1: "A11", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    if maybe(rng):
        start, _ = random_time_range(rng)
        fields[44] = format_hl7_datetime(start)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))


def generate_adt_a13(rng: random.Random) -> str:
    msh, dt = generate_msh_segment(rng, "ADT", "A13")
    evn = segment("EVN", {1: "A13", 2: dt}, 2)
    pid = generate_pid_segment(rng)
    fields = build_minimal_pv1_fields(rng, "I")
    fields[3] = random_location_field(rng)
    # deliberately ~50%, matching A08's discharge-time split: exercises both
    # of AdtA13Mapper's optional discharge-field branches (populated vs.
    # absent) - unlike A03, A13 doesn't require a discharge date/time.
    if maybe(rng, p=0.5):
        admit, discharge = random_time_range(rng)
        fields[44] = format_hl7_datetime(admit)
        fields[45] = format_hl7_datetime(discharge)
        if maybe(rng):
            fields[36] = random_discharge_disposition(rng)
    return _assemble(msh, evn, pid, segment("PV1", fields, 45))
