"""What the v2-to-FHIR IG says about each HL7v2 field this app drops.

The C-CDA sibling of `cda_ig_verdicts.py`, and built the same way: from
the IG's own published mapping tables (`mappings/segments/*.csv` and
`mappings/datatypes/*.csv` in HL7/v2-to-fhir), whose `FHIR Attribute`
column names the target when one exists and is blank when none does.

Unlike C-CDA, this IG publishes no equivalent of the "no map specified
means no consensus target" statement, so a *blank* row here is recorded as
"the IG names no target" rather than borrowed language about consensus.

**A verdict is only GAP once the target is confirmed absent from the
converted Bundle** - the rule the C-CDA pass established after the IG's
Procedure table made an already-implemented field look like a gap.

Keys are matched longest-suffix-first, so a component-level verdict beats
a whole-field one.
"""

from app.provenance.citations import Citation, DROP_NOT_YET_CHECKED, V2_TO_FHIR

NO_TARGET = "no_target"
OUT_OF_SCOPE = "out_of_scope"
SUPERSEDED = "superseded"
GAP = "gap"

IG_NAMES_NO_TARGET = Citation(
    title="v2-to-FHIR names no target for this field",
    url="https://github.com/HL7/v2-to-fhir/tree/master/mappings",
    authoritative=True,
    note="The IG's own mapping table leaves this field's FHIR Attribute blank - it defines no target to map to.",
)

IG_TARGET_OUT_OF_SCOPE = Citation(
    title="v2-to-FHIR maps this to a resource this app does not build",
    url="https://github.com/HL7/v2-to-fhir/tree/master/mappings",
    authoritative=True,
    note=(
        "The IG defines a target, but on a resource this app deliberately never builds. "
        "Provenance in particular models who created or revised a stored record, which a "
        "stateless converter has no lifecycle for - the same permanent scope decision taken "
        "for C-CDA <author>."
    ),
)

IG_DEFINES_TARGET = Citation(
    title="v2-to-FHIR defines a target this app does not implement",
    url="https://github.com/HL7/v2-to-fhir/tree/master/mappings",
    authoritative=True,
    note="The IG's own mapping table defines a FHIR target for this field, so this is a real gap in this app's conversion.",
)

IG_TARGET_SUPERSEDED = Citation(
    title="v2-to-FHIR target implemented, but a preferred source supplied it",
    url="https://github.com/HL7/v2-to-fhir/tree/master/mappings",
    authoritative=True,
    note=(
        "The IG defines a target and this app implements it, reading this field as a fallback. "
        "In this particular message another field the mapper prefers carried the value, so this "
        "one went unused - a fallback that was not needed, not a capability this app lacks."
    ),
)

_CITATION_BY_VERDICT = {
    NO_TARGET: IG_NAMES_NO_TARGET,
    SUPERSEDED: IG_TARGET_SUPERSEDED,
    OUT_OF_SCOPE: IG_TARGET_OUT_OF_SCOPE,
    GAP: IG_DEFINES_TARGET,
}

# (field or component) -> (verdict, what the IG says).
IG_VERDICTS: dict[str, tuple[str, str]] = {
    # --- EVN (Segment - EVN[Provenance]) --------------------------------
    "EVN-2": (
        OUT_OF_SCOPE,
        "EVN-2 maps to Provenance.recorded. The whole EVN segment maps to Provenance, which this "
        "app does not build.",
    ),
    "EVN-1": (NO_TARGET, "EVN-1's FHIR Attribute column is blank - the IG names no target."),
    "EVN-3": (NO_TARGET, "EVN-3's FHIR Attribute column is blank - the IG names no target."),
    # --- XCN datatype (XCN[Practitioner]) -------------------------------
    "PV1-7.7": (
        GAP,
        "XCN.7 maps to qualification.code, but ADT builds no Practitioner for PV1-7 at all - only a "
        "display string - so there is nothing to carry the degree on.",
    ),
    "XCN.8": (NO_TARGET, "XCN.8 (Source Table) has a blank FHIR Attribute - the IG names no target."),
    # --- CX datatype (CX[Identifier]) -----------------------------------
    # CX.6's target is the literal placeholder "extension??-assigningFacility",
    # an unresolved item in the IG itself rather than a real extension URL -
    # the same shape as PL's own "/extension??-poc/", which this app already
    # declines to invent a value for.
    # --- SCH (Segment - SCH[Appointment]) -------------------------------
    "SCH-9": (
        SUPERSEDED,
        "SCH-9 maps to minutesDuration, which this app builds - preferring TQ1-6 when present, "
        "per resolve_appointment_timing.",
    ),
    "SCH-10": (NO_TARGET, "SCH-10 (Appointment Duration Units) has a blank FHIR Attribute."),
    "SCH-11": (
        SUPERSEDED,
        "SCH-11 maps to the appointment timing as a whole, which this app reads as the legacy "
        "fallback when TQ1 is absent.",
    ),
    # --- AIG (Segment - AIG[Appointment]) --------------------------------
    "AIG-4": (
        NO_TARGET,
        "AIG-4 maps to participant.type; this app instead carries the resource type on the "
        "materialised Device.type, so no participant.type is produced for it to fill.",
    ),
    # --- CX datatype (CX[Identifier]) -----------------------------------
    "PID-3.6": (
        NO_TARGET,
        'CX.6 (Assigning Facility) maps only to the placeholder "extension??-assigningFacility", '
        "which is an unresolved item in the IG rather than a defined target.",
    ),
}


def verdict_for(location: str) -> tuple[str | None, Citation, str | None]:
    """(verdict, citation, what the IG says) for a source location.

    Longest matching suffix wins. An unmatched location keeps the honest
    "not yet checked" citation rather than being assumed either way.
    """
    best: str | None = None
    for key in IG_VERDICTS:
        if (location == key or location.endswith("." + key) or location.startswith(key)) and (
            best is None or len(key) > len(best)
        ):
            best = key
    if best is None:
        return None, DROP_NOT_YET_CHECKED, None
    verdict, note = IG_VERDICTS[best]
    return verdict, _CITATION_BY_VERDICT[verdict], note


__all__ = ["GAP", "NO_TARGET", "OUT_OF_SCOPE", "V2_TO_FHIR", "IG_VERDICTS", "verdict_for"]
