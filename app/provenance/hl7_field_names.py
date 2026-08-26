"""Human-readable HL7v2 segment/field/component names for the Data
Specification page's crosswalk table and hover tooltip - a "source field
name" label (e.g. "Patient Name", "ID", "Assigning Authority") shown
alongside the raw `source_location` string (e.g. "PID-5[0].1",
"PID-3[0].1") so a reader unfamiliar with HL7v2's own field numbering
doesn't have to look it up separately.

**Scoped to exactly the segments/fields/datatypes this app's own mappers
actually record provenance against** - confirmed by grepping every
`hl7_location(...)` call site across `app/mappings/`/`app/fhir_models/`,
not a standalone encyclopedia of the whole HL7v2 standard - the same "map
what's actually used, not the general case" discipline this app's own
CDA/EDI field-mapping modules already establish elsewhere. An
unrecognized segment/field/component resolves to `None` (no label), the
caller's own "nothing to show" signal, never a guess.

Field names are the HL7 2.x standard's own official field names, except
a handful of composite-datatype components shortened to their common,
colloquial form (e.g. CX component 1 "ID Number" -> "ID") where the full
standard name would be redundant/verbose in a narrow table column."""

from app.provenance.hl7_locator import ParsedHl7Location

# Whole-field names, keyed by segment id -> field number. Used when a
# location string carries no .component (or the component itself isn't
# recognized below) - the field itself is what the reader needs named,
# not one of its parts.
SEGMENT_FIELD_NAMES: dict[str, dict[int, str]] = {
    "MSH": {
        7: "Date/Time of Message",
        10: "Message Control ID",
    },
    "EVN": {
        2: "Recorded Date/Time",
    },
    "PID": {
        3: "Patient Identifier List",
        5: "Patient Name",
        7: "Date/Time of Birth",
        8: "Administrative Sex",
        11: "Patient Address",
        13: "Phone Number - Home",
        15: "Primary Language",
        16: "Marital Status",
        24: "Multiple Birth Indicator",
        25: "Birth Order",
        29: "Patient Death Date and Time",
        30: "Patient Death Indicator",
    },
    "PV1": {
        2: "Patient Class",
        3: "Assigned Patient Location",
        4: "Admission Type",
        5: "Preadmit Number",
        6: "Prior Patient Location",
        7: "Attending Doctor",
        8: "Referring Doctor",
        9: "Consulting Doctor",
        10: "Hospital Service",
        13: "Re-admission Indicator",
        14: "Admit Source",
        15: "Ambulatory Status",
        16: "VIP Indicator",
        17: "Admitting Doctor",
        19: "Visit Number",
        36: "Discharge Disposition",
        38: "Diet Type",
        44: "Admit Date/Time",
        45: "Discharge Date/Time",
    },
    "SCH": {
        1: "Placer Appointment ID",
        2: "Filler Appointment ID",
        9: "Appointment Duration",
        11: "Appointment Timing Quantity",
    },
    "TQ1": {
        6: "Service Duration",
        7: "Start Date/Time",
        8: "End Date/Time",
    },
    "NTE": {
        3: "Comment",
    },
    "AIP": {
        3: "Personnel Resource ID",
    },
    "AIL": {
        3: "Location Resource ID",
    },
    "AIG": {
        3: "Resource ID",
    },
    "OBR": {
        7: "Observation Date/Time",
        8: "Observation End Date/Time",
        22: "Results Rpt/Status Chng Date/Time",
        25: "Result Status",
    },
    "OBX": {
        5: "Observation Value",
        6: "Units",
        7: "References Range",
        8: "Abnormal Flags",
        11: "Observation Result Status",
        14: "Date/Time of the Observation",
        16: "Responsible Observer",
    },
    "TXA": {
        3: "Document Content Presentation",
        6: "Origination Date/Time",
        9: "Originator",
        10: "Assigned Document Authenticator",
        12: "Unique Document Number",
        16: "Unique Document File Name",
        18: "Document Confidentiality Status",
        25: "Document Title",
    },
}

# Which composite HL7v2 datatype a given segment+field uses - only for
# fields this app actually records a .component against (see the
# hl7_location() call sites this table was built from). A field absent
# here has no component-level label to offer - SEGMENT_FIELD_NAMES'
# whole-field name is used instead.
_FIELD_DATATYPES: dict[str, dict[int, str]] = {
    "PID": {3: "CX", 5: "XPN", 11: "XAD", 13: "XTN", 15: "CWE", 16: "CWE"},
    "AIG": {3: "CWE"},
    "AIL": {3: "PL"},
    "AIP": {3: "XCN"},
    "OBX": {16: "XCN"},
    "PV1": {
        3: "PL",
        4: "CWE",
        5: "CX",
        6: "PL",
        7: "XCN",
        8: "XCN",
        9: "XCN",
        10: "CWE",
        13: "CWE",
        14: "CWE",
        15: "CWE",
        16: "CWE",
        17: "XCN",
        19: "CX",
        38: "CWE",
    },
    "TXA": {9: "XCN", 10: "XCN"},
    "SCH": {11: "TQ"},
}

# Component names per composite datatype - the HL7 2.x standard's own
# component numbering, scoped to the datatypes this app's own mappers
# actually decompose into components.
_COMPONENT_NAMES: dict[str, dict[int, str]] = {
    # PL (person location), per the HL7 v2.5 standard's own component
    # order - note component 1 is Point of Care, NOT Facility (which is
    # component 4); getting that backwards is an easy and costly mistake.
    "PL": {
        1: "Point of Care",
        2: "Room",
        3: "Bed",
        4: "Facility",
        5: "Location Status",
        6: "Person Location Type",
        7: "Building",
        8: "Floor",
        9: "Location Description",
    },
    "XTN": {
        1: "Telephone Number",
        2: "Telecommunication Use Code",
        3: "Telecommunication Equipment Type",
        4: "Email Address",
        5: "Country Code",
        6: "Area/City Code",
        7: "Local Number",
        8: "Extension",
    },
    "CX": {
        1: "ID",
        2: "Check Digit",
        3: "Check Digit Scheme",
        4: "Assigning Authority",
        5: "Identifier Type Code",
        6: "Assigning Facility",
    },
    "XPN": {
        1: "Family Name",
        2: "Given Name",
        3: "Middle Name",
        4: "Suffix",
        5: "Prefix",
        6: "Degree",
        7: "Name Type Code",
    },
    "XAD": {
        1: "Street Address",
        2: "Other Designation",
        3: "City",
        4: "State or Province",
        5: "Zip or Postal Code",
        6: "Country",
        7: "Address Type",
        8: "Other Geographic Designation",
        9: "County/Parish Code",
        10: "Census Tract",
    },
    "XCN": {
        1: "ID",
        2: "Family Name",
        3: "Given Name",
        4: "Middle Name",
        5: "Suffix",
        6: "Prefix",
        7: "Degree",
        8: "Source Table",
        9: "Assigning Authority",
    },
    "CWE": {
        1: "Identifier",
        2: "Text",
        3: "Name of Coding System",
        4: "Alternate Identifier",
        5: "Alternate Text",
        6: "Name of Alternate Coding System",
    },
    "TQ": {
        1: "Quantity",
        2: "Interval",
        3: "Duration",
        4: "Start Date/Time",
        5: "End Date/Time",
        6: "Priority",
        7: "Condition",
        8: "Text",
        9: "Conjunction",
        10: "Order Sequencing",
    },
}


def resolve_hl7_field_label(parsed: ParsedHl7Location) -> str | None:
    """When a component is present and both the field's own datatype and
    that component number are recognized, the component's own name wins
    (the more specific of the two - e.g. "Assigning Authority" for
    PID-3.4, not "Patient Identifier List") - otherwise falls back to the
    whole field's own name (e.g. "Patient Name" for a bare PID-5).
    Returns `None` when neither resolves."""
    if parsed.component is not None:
        datatype = _FIELD_DATATYPES.get(parsed.segment_id, {}).get(parsed.field)
        component_name = _COMPONENT_NAMES.get(datatype, {}).get(parsed.component) if datatype else None
        if component_name:
            return component_name
    return SEGMENT_FIELD_NAMES.get(parsed.segment_id, {}).get(parsed.field)


def component_names_for_field(segment_id: str, field: int) -> dict[int, str] | None:
    """Component names for a field, or None when its datatype isn't one
    this table covers. Public so app/provenance/decisions.py can label a
    dropped component without re-deriving the datatype lookup."""
    datatype = _FIELD_DATATYPES.get(segment_id, {}).get(field)
    return _COMPONENT_NAMES.get(datatype) if datatype else None
