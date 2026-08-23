"""Source documentation backing this app's mapping decisions.

A central table rather than a citation constant beside each mapping,
because this app has exactly three citation *regimes*, not one per rule:

- HL7v2 has the ballot-published v2-to-FHIR IG, with per-segment
  ConceptMaps that genuinely confirm field-level targets.
- C-CDA has the ballot-published C-CDA on FHIR IG, likewise.
- X12 EDI has **no** free, official crosswalk at all - the TR3s are
  paywalled. Every EDI mapping here is verified against real X12.org
  example files plus free companion guides instead.

That last one is the reason this table exists: an honest register has to
be able to say "no authoritative crosswalk exists for this; here is what
it was checked against instead" once, in one place, rather than have it
quietly omitted 40 times.

`CITATION_UNVERIFIED` is deliberately available for a decision this app
makes with no external backing at all (a local judgment call). Using it
is not a failure - stating it plainly is the point.
"""

from pydantic import BaseModel


class Citation(BaseModel):
    """`url` is None when the source genuinely isn't a fetchable document
    (a paywalled TR3, or a local judgment call with no external source).
    `authoritative` records whether the source actually *specifies* the
    mapping, versus corroborating it - an X12.org example file shows a
    real message but does not define a FHIR target, so it is evidence,
    not specification. A reviewer needs that distinction to know which
    decisions carry real standards weight."""

    title: str
    url: str | None = None
    authoritative: bool = True
    note: str | None = None


V2_TO_FHIR = Citation(
    title="HL7 Version 2 to FHIR Implementation Guide",
    url="https://build.fhir.org/ig/HL7/v2-to-fhir/",
    authoritative=True,
    note="Ballot-published, with per-segment ConceptMaps.",
)

CCDA_ON_FHIR = Citation(
    title="C-CDA on FHIR Implementation Guide",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=True,
    note="Ballot-published; some sections (Vital Signs, Results) have narrative guidance rather than a CSV ConceptMap.",
)

FHIR_R4_SPEC = Citation(
    title="FHIR R4 specification",
    url="https://hl7.org/fhir/R4/",
    authoritative=True,
    note="Used where the decision is about FHIR's own required fields or value sets rather than a source-format crosswalk.",
)

X12_NO_OFFICIAL_CROSSWALK = Citation(
    title="No official X12-to-FHIR crosswalk exists",
    url="https://x12.org/examples",
    authoritative=False,
    note=(
        "X12's own TR3 Implementation Guides are paywalled and publish no FHIR mapping. "
        "Mappings here are verified against real X12.org example files and free payer/clearinghouse "
        "companion guides, which evidence the source shape but do not specify a FHIR target."
    ),
)

DAVINCI_PAS = Citation(
    title="Da Vinci Prior Authorization Support (PAS) IG",
    url="https://hl7.org/fhir/us/davinci-pas/",
    authoritative=True,
    note="Confirms the Claim/ClaimResponse target shape for X12 278, though not the segment-level crosswalk.",
)

# A dropped component is NOT backed by the IG just because the IG exists.
# Whether the IG defines a target this app fails to implement, or defines
# none at all, is a per-component question that has to be checked against
# the relevant ConceptMap - and until it is, claiming the IG as the source
# would assert backing that was never verified. This states the real state
# instead.
DROP_NOT_YET_CHECKED = Citation(
    title="Not yet checked against the source IG",
    url=None,
    authoritative=False,
    note=(
        "This component is present in the source but unmapped. Whether the governing "
        "implementation guide defines a FHIR target for it (making this a gap) or defines "
        "none (making it out of scope for the standard too) has not been verified per-component."
    ),
)

# An entry whose whole subtree went unread. This app skips some entries by
# design - a negated observation, a relationship whose typeCode is not the
# one the section's template calls for - and everything inside a skipped
# entry is then dropped as a consequence, not on its own merits. Reporting
# each leaf separately made one decision look like six unrelated ones and
# buried the fact that a whole clinical statement had been discarded.
CDA_ENTRY_NOT_CONVERTED = Citation(
    title="Source entry not converted",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=False,
    note=(
        "Nothing in this entry was read, so it produced no FHIR resource at all. The individual "
        "elements beneath it are unmapped because the entry itself was skipped - which is the "
        "decision to review, rather than each element in turn."
    ),
)

# --- C-CDA drop verdicts, checked against the IG's own mapping tables ---
#
# The IG publishes per-resource CSVs (mappings/CF/*.csv) whose `Approach`
# column is the authoritative signal: "source value"/"transform" define a
# FHIR target, "not supported by target" states there is none. An element
# with no row at all is also a real answer, per the IG's own
# mappingGuidance: "If you have data in an input artifact that is defined
# in the source specification and for which no map is specified here, that
# means that this team did not find a target for which we could build
# consensus." That is a verified position, not an unchecked one.

CDA_IG_NOT_SUPPORTED = Citation(
    title="C-CDA on FHIR: not supported by target",
    url="https://github.com/HL7/ccda-on-fhir/tree/master/mappings/CF",
    authoritative=True,
    note=(
        "The IG's own mapping table marks this element \"not supported by target\" - it "
        "states there is no FHIR element to carry it, so dropping it matches the standard."
    ),
)

CDA_IG_NO_MAP_SPECIFIED = Citation(
    title="C-CDA on FHIR: no map specified",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/mappingGuidance.html",
    authoritative=True,
    note=(
        "The IG specifies no mapping for this element. Per its own mapping guidance, that "
        "means \"this team did not find a target for which we could build consensus\" - a "
        "stated position, not an oversight on this app's part."
    ),
)

CDA_IG_DEFINES_TARGET = Citation(
    title="C-CDA on FHIR defines a target this app does not implement",
    url="https://github.com/HL7/ccda-on-fhir/tree/master/mappings/CF",
    authoritative=True,
    note=(
        "The IG's own mapping table defines a FHIR target for this element, so this is a "
        "real gap in this app's conversion rather than a limit of the standard."
    ),
)

CITATION_UNVERIFIED = Citation(
    title="Local judgment call - no external source",
    url=None,
    authoritative=False,
    note="This app chose this mapping itself; no standard specifies it.",
)

# Default citation per source format, used when a decision has no more
# specific one. Deliberately NOT a silent fallback to "authoritative" -
# EDI resolves to the honest no-crosswalk note.
DEFAULT_BY_FORMAT = {
    "HL7v2": V2_TO_FHIR,
    "CDA": CCDA_ON_FHIR,
    "EDI": X12_NO_OFFICIAL_CROSSWALK,
}
