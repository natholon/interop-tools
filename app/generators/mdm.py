"""Synthetic MDM message generators, one per supported trigger event.

Same required/optional philosophy as the other generators: a generated
message must always convert successfully through app.mappings.mdm. TXA-3
(Content Presentation) and TXA-19 (Availability Status) are always
populated - TXA-3 is the IG's 1..1 target for contentType, and TXA-19 is
fixed to "AV" (the one verified availability code) so the generator never
exercises an unverified corner of _resolve_status. TXA-2 (Document Type) is
also always populated since it's what gives a document notification any
real meaning. T02/T04/T06/T08/T10/T11 are generated identically since
app.mappings.mdm treats them identically.
"""

import random

from app.generators.base import (
    build_minimal_pv1_fields,
    format_hl7_datetime,
    generate_msh_segment,
    generate_pid_segment,
    maybe,
    random_content_presentation_code,
    random_document_body_line,
    random_document_type,
    random_identifier,
    random_physician_xcn,
    random_time_range,
    segment,
)
from app.hl7.parser import parse_message


def _generate_pv1(rng: random.Random) -> str:
    patient_class = "I" if maybe(rng, p=0.5) else "O"
    fields = build_minimal_pv1_fields(rng, patient_class)
    return segment("PV1", fields, 19)


def _generate_txa(rng: random.Random) -> str:
    doc_code, doc_display = random_document_type(rng)
    fields = {
        2: f"{doc_code}^{doc_display}^LOCAL",
        3: random_content_presentation_code(rng),
        19: "AV",
    }
    if maybe(rng):
        origination, _ = random_time_range(rng, min_days=-5, max_days=0)
        fields[6] = format_hl7_datetime(origination)
    if maybe(rng):
        fields[9] = random_physician_xcn(rng)
    if maybe(rng):
        fields[10] = random_physician_xcn(rng)
    if maybe(rng):
        fields[12] = f"DOC-{random_identifier(rng, 6)}"
    if maybe(rng):
        fields[18] = rng.choice(("R", "U", "V"))
    if maybe(rng):
        fields[25] = f"{doc_display} - {random_identifier(rng, 3)}"
    return segment("TXA", fields, 28)


def _generate_obx_content(rng: random.Random) -> list[str]:
    if not maybe(rng, p=0.7):
        return []
    num_lines = rng.randint(1, 3)
    return [
        segment("OBX", {1: str(i), 2: "TX", 5: random_document_body_line(rng)}, 18) for i in range(1, num_lines + 1)
    ]


def _generate_mdm(rng: random.Random, trigger_event: str) -> str:
    msh, dt = generate_msh_segment(rng, "MDM", trigger_event)
    evn = segment("EVN", {1: trigger_event, 2: dt}, 2)
    pid = generate_pid_segment(rng)
    segments = [msh, evn, pid]
    if maybe(rng):
        segments.append(_generate_pv1(rng))
    segments.append(_generate_txa(rng))
    segments.extend(_generate_obx_content(rng))

    text = "\r".join(segments) + "\r"
    parse_message(text)  # self-check: a generator bug should raise, not return broken text
    return text


def generate_mdm_t02(rng: random.Random) -> str:
    return _generate_mdm(rng, "T02")


def generate_mdm_t04(rng: random.Random) -> str:
    return _generate_mdm(rng, "T04")


def generate_mdm_t06(rng: random.Random) -> str:
    return _generate_mdm(rng, "T06")


def generate_mdm_t08(rng: random.Random) -> str:
    return _generate_mdm(rng, "T08")


def generate_mdm_t10(rng: random.Random) -> str:
    return _generate_mdm(rng, "T10")


def generate_mdm_t11(rng: random.Random) -> str:
    return _generate_mdm(rng, "T11")
