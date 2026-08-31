"""HL7v2 fields the standard requires, and whether the message carries them.

**Source**: the v2-to-FHIR IG's own segment maps
(`github.com/HL7/v2-to-fhir/tree/master/mappings/segments`), whose
`Cardinality - Min` column on the *HL7 v2* side of each sheet states the
minimum the standard sets. Read off those sheets directly rather than
from memory, the same way `app/provenance/hl7_ig_verdicts.py` reads their
mapping half.

Two things about those sheets are worth knowing, because both produced a
wrong answer first:

- **Each sheet carries the column names twice** - once for the HL7 v2
  source and again for the FHIR target. Keying a dict by column name
  silently takes the *target* cardinality, which reports every field as
  optional.
- **A few cells cram the whole cardinality into the min column** (`0..2`
  for `OBR-17`). The leading integer is the minimum either way, so
  normalising it is what turns an apparent cross-sheet conflict into
  agreement.

Where a segment publishes several sheets (one per FHIR target), all of
them agree on the HL7 v2 side once those two are handled - checked across
every sheet rather than assumed, and asserted again by
`tests/test_validation_required_fields.py`.

**Scope**: the segments this app actually reads. `RGS` publishes no sheet
at all, and this app never requires it (see `app/mappings/siu.py`).
"""

from app.hl7.parser import raw_field_str
from app.validation.models import ValidationFinding

# (field number, the standard's own name for it) per segment, for every
# field whose Cardinality - Min is greater than zero.
REQUIRED_FIELDS: dict[str, tuple[tuple[int, str], ...]] = {
    "MSH": (
        (7, "Date/Time of Message"),
        (9, "Message Type"),
        (10, "Message Control ID"),
        (11, "Processing ID"),
        (12, "Version ID"),
    ),
    "EVN": ((2, "Recorded Date/Time"),),
    "PID": (
        (3, "Patient Identifier List"),
        (5, "Patient Name"),
    ),
    "PV1": ((2, "Patient Class"),),
    "SCH": (
        (6, "Event Reason"),
        (11, "Appointment Timing Quantity"),
        (12, "Placer Contact Person"),
        (16, "Filler Contact Person"),
        (20, "Entered By Person"),
    ),
    "AIS": ((1, "Set ID - AIS"),),
    "AIG": (
        (1, "Set ID - AIG"),
        (4, "Resource Type"),
    ),
    "AIL": ((1, "Set ID - AIL"),),
    "AIP": (
        (1, "Set ID - AIP"),
        (4, "Resource Type"),
    ),
    "OBR": ((4, "Universal Service Identifier"),),
    "OBX": (
        (3, "Observation Identifier"),
        (11, "Observation Result Status"),
    ),
    "TXA": (
        (1, "Set ID - TXA"),
        (2, "Document Type"),
        (12, "Unique Document Number"),
        (17, "Document Completion Status"),
    ),
}

# MSH-1 and MSH-2 are 1..1 too, but they *are* the delimiters - MSH-1 is
# the field separator itself rather than a |-split token, and MSH-2 the
# encoding characters. A message that reached this point was parsed with
# them, so checking them would report nothing a parse failure has not
# already said, and `field_str` cannot address MSH-1 at all (see the
# MSH-numbering quirk in app/provenance/hl7_locator.py).
RULE_ID = "hl7.segment-missing-required-field"


def check_required_fields(segment, segment_name: str) -> list[ValidationFinding]:
    """Findings for the required fields this segment does not carry.

    `error`, because these are the standard's own 1..1 and 1..* minimums -
    a message missing one is non-conformant rather than merely sparse.
    """
    findings = []
    for field_num, field_name in REQUIRED_FIELDS.get(segment_name, ()):
        # The whole field, not component 1: `field_str` defaults to the
        # first component, so a composite that populates only later ones
        # reads as absent. SCH-11 is exactly that shape - TQ.4 and TQ.5
        # carry the appointment's start and end while TQ.1 is empty.
        if raw_field_str(segment, field_num).strip():
            continue
        findings.append(
            ValidationFinding(
                severity="error",
                rule_id=RULE_ID,
                segment=segment_name,
                field=field_num,
                message=(
                    f"{segment_name}-{field_num} ({field_name}) is required by the HL7 v2 "
                    f"standard and is missing."
                ),
            )
        )
    return findings
