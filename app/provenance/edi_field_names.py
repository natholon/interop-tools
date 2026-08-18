"""Human-readable X12 EDI segment/element/component names for the Data
Specification page's crosswalk table and hover tooltip - the EDI sibling
of `app/provenance/hl7_field_names.py`, same idea (a "source field name"
label shown alongside the raw `source_location` string, e.g. "Service
Type Code" for "EQ-1", "Procedure Code" for "SV1-1.2") but keyed off
`edi_location()`'s own `SEGMENT[repetition]-element[.component]` shape
instead of HL7v2's `SEG-N.C`.

**Scoped to exactly the segment/element/component combinations this
app's own EDI mappers actually record provenance against** - confirmed
by grepping every `edi_location(...)` call site across `app/edi/*.py`,
the same "map what's actually used, not the general X12 data dictionary"
discipline `hl7_field_names.py` already established. An unrecognized
segment/element/component resolves to `None` (no label), never a guess.

Two lookup tiers, mirroring `hl7_field_names.py`'s own field-vs-component
split, adapted for X12's own composite shape:
  - `SEGMENT_ELEMENT_NAMES` - the whole element's name, used when no
    component is present (or the component itself isn't recognized).
  - `_ELEMENT_COMPONENT_NAMES` / `_ANY_ELEMENT_COMPONENT_NAMES` - a
    composite element's own sub-component name, tried first when a
    component is present. Two shapes are needed (not one, the way
    HL7v2's segment+field->datatype->component chain is) because X12
    handles a *repeating* composite by repeating the whole element
    position (`HI01`, `HI02`, ..., one per diagnosis code, via
    `app/edi/common.py::build_diagnosis_codeable_concepts`'s own
    `position` loop) rather than repeating a component within one fixed
    element the way HL7v2 never needs to for the fields this app reads -
    `_ELEMENT_COMPONENT_NAMES` covers a composite pinned to one specific
    element number (STC01, CLM05, SV1-01, SV2-02, SV3-01), while
    `_ANY_ELEMENT_COMPONENT_NAMES` covers one whose component meaning
    doesn't depend on which element number carried it (HI's own
    diagnosis-code sub-element, recurring at HI01/HI02/.../HI0N)."""

from app.provenance.edi_locator import ParsedEdiLocation

# Whole-element names, keyed by segment id -> element number. Real X12
# element names where well-established and unambiguous (NM1/N1/DMG/BHT);
# a shorter, more instructive label than the generic X12 data-element
# name where the generic name would be uninformative on its own in a
# narrow table column (e.g. EB12's own "In-Network Indicator" rather than
# the generic "Yes/No Condition or Response Code" reused by dozens of
# unrelated X12 elements) - the same "shorten for readability" precedent
# `hl7_field_names.py`'s own CX.1 "ID" (not "ID Number") already set.
SEGMENT_ELEMENT_NAMES: dict[str, dict[int, str]] = {
    "BHT": {
        3: "Reference Identification",
        4: "Date",
        5: "Time",
    },
    "NM1": {
        3: "Name Last or Organization Name",
        4: "Name First",
        9: "Identification Code",
    },
    "N1": {
        2: "Name",
        4: "Identification Code",
    },
    "DMG": {
        2: "Date of Birth",
        3: "Gender Code",
    },
    "DTP": {
        3: "Service Date",
    },
    "EQ": {
        1: "Service Type Code",
    },
    "EB": {
        1: "Eligibility or Benefit Information Code",
        3: "Service Type Code",
        5: "Plan Coverage Description",
        12: "In-Network Indicator",
    },
    "AAA": {
        1: "Valid Request Indicator",
        3: "Reject Reason Code",
    },
    "STC": {
        1: "Health Care Claim Status Category Code",  # defensive whole-element fallback - STC01 is always component-qualified in this app's own real usage
    },
    "TRN": {
        2: "Trace Number",
    },
    "CLM": {
        1: "Patient Control Number",
        2: "Total Claim Charge Amount",
        5: "Health Care Service Location Information",  # defensive whole-element fallback - CLM05 is always component-qualified (component 1) in this app's own real usage
    },
    "SV1": {
        1: "Composite Medical Procedure Identifier",  # defensive whole-element fallback - see _ELEMENT_COMPONENT_NAMES for the component=2 "Procedure Code" case actually recorded
        2: "Line Item Charge Amount",
        4: "Quantity",
        7: "Diagnosis Code Pointer",
    },
    "SV2": {
        1: "Revenue Code",
        2: "Composite Medical Procedure Identifier",  # defensive whole-element fallback, same shape as SV1-01 above
        3: "Line Item Charge Amount",
        5: "Quantity",
    },
    "SV3": {
        1: "Composite Dental Procedure Identifier",  # defensive whole-element fallback, same shape as SV1-01 above
        2: "Line Item Charge Amount",
        6: "Quantity",
        11: "Diagnosis Code Pointer",
    },
    "TOO": {
        2: "Tooth Number",
        3: "Tooth Surface",
    },
    "CL1": {
        3: "Patient Status Code",
    },
    "UM": {
        3: "Service Type Code",
    },
    "HCR": {
        1: "Action Code",
        2: "Certification Number",
        3: "Reason Code",
    },
    "CLP": {
        1: "Patient Control Number",
        2: "Claim Status Code",
        4: "Total Claim Payment Amount",
    },
    "BPR": {
        2: "Total Actual Provider Payment Amount",
        16: "Payment Date",
    },
}

# A composite element pinned to one specific element number - the
# component's own meaning depends on *which* element carries it (STC01's
# own category vs. status sub-parts share one element number but mean
# different things).
_ELEMENT_COMPONENT_NAMES: dict[tuple[str, int], dict[int, str]] = {
    ("STC", 1): {1: "Health Care Claim Status Category Code", 2: "Claim Status Code"},
    ("CLM", 5): {1: "Place of Service Code"},
    ("SV1", 1): {2: "Procedure Code"},
    ("SV2", 2): {2: "Procedure Code"},
    ("SV3", 1): {2: "Procedure Code"},
    ("TOO", 3): {1: "Tooth Surface", 2: "Tooth Surface", 3: "Tooth Surface", 4: "Tooth Surface", 5: "Tooth Surface"},
}

# A composite recurring at more than one element number within the same
# segment (one occurrence per repeated composite) where the component's
# own meaning doesn't depend on which element number it landed at.
_ANY_ELEMENT_COMPONENT_NAMES: dict[str, dict[int, str]] = {
    "HI": {2: "Diagnosis Code"},
}


def resolve_edi_field_label(parsed: ParsedEdiLocation) -> str | None:
    """When a component is present, an element-pinned name wins first
    (the more specific of the two shapes), then an any-element name,
    then falls back to the whole element's own name - otherwise the
    whole element's own name is used directly. Returns `None` when
    nothing resolves."""
    if parsed.component is not None:
        element_name = _ELEMENT_COMPONENT_NAMES.get((parsed.segment_id, parsed.element_num), {}).get(parsed.component)
        if element_name:
            return element_name
        any_element_name = _ANY_ELEMENT_COMPONENT_NAMES.get(parsed.segment_id, {}).get(parsed.component)
        if any_element_name:
            return any_element_name
    return SEGMENT_ELEMENT_NAMES.get(parsed.segment_id, {}).get(parsed.element_num)
