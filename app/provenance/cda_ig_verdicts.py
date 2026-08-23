"""What the C-CDA on FHIR IG actually says about each element this app
drops, so the register can cite a verdict instead of "not yet checked".

Every entry here was read off the IG's own published mapping tables
(`mappings/CF/*.csv` in HL7/ccda-on-fhir), whose `Approach` column is the
authoritative signal:

- `source value` / `transform` / `fixed value` - the IG defines a FHIR
  target. If this app does not implement it, that is a **real gap**.
- `not supported by target` - the IG states there is no FHIR element to
  carry it. Dropping it matches the standard.

An element with **no row at all** is also a real answer rather than an
unchecked one, per the IG's own mappingGuidance:

    "If you have data in an input artifact that is defined in the source
    specification and for which no map is specified here, that means that
    this team did not find a target for which we could build consensus."

That covers the CDA document header wholesale: the IG publishes no
header mapping (confirmed by reading `CF-notes.md` and `mappingGuidance.md`
directly - the CSVs are per clinical resource, and there is no header or
Encounter CSV at all), so `ClinicalDocument/code`, `/title`,
`/languageCode`, `/confidentialityCode` and a section's own `code`/`title`
resolve to "no map specified" rather than to a gap.

**Keys are element shapes, matched most-specific-first**, because the same
tag means different things at different depths - a Problem Concern Act's
own `id` is "not supported by target" while the Problem Observation's `id`
inside it maps to `Condition.identifier`.

**A shape is not always enough, and a key that over-reaches is worse than
no key at all.** Two GAP verdicts were withdrawn after they turned out to
be matching a *different section's* element at the same depth: an Allergy
Reaction Observation's `id` was being given the Problem Observation's
verdict, and a Comment Activity's `code` was being given the Instruction
act's. Telling those apart needs the templateId, which the drop register
does not carry - so where a shape cannot distinguish them, no verdict is
asserted and the honest "not yet checked" stands.

**Scope, stated rather than implied.** The verdicts below come from the
Patient and Problem-Condition tables plus the header guidance above.
Allergy, Immunization, MedicationRequest and Procedure each publish their
own CSV that has not been read into this table yet, and Vitals/Results/
Encounters have narrative pages rather than CSVs. A shape with no entry
here keeps the honest `DROP_NOT_YET_CHECKED` citation - the register says
"unchecked" only where it genuinely is.
"""

from app.provenance.citations import (
    CDA_IG_DEFINES_TARGET,
    CDA_IG_NO_MAP_SPECIFIED,
    CDA_IG_NOT_SUPPORTED,
    Citation,
    DROP_NOT_YET_CHECKED,
)

NOT_SUPPORTED = "not_supported"
NO_MAP = "no_map"
SUPERSEDED = "superseded"
GAP = "gap"

# The IG defines a target this app builds from a different part of the same
# element - so the dropped piece is not a missing capability.
CDA_IG_SUPERSEDED = Citation(
    title="C-CDA on FHIR target built from another part of this element",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=True,
    note="The IG's target for this element is built by this app from a sibling attribute; this piece has no target of its own.",
)

_CITATION_BY_VERDICT = {
    NOT_SUPPORTED: CDA_IG_NOT_SUPPORTED,
    SUPERSEDED: CDA_IG_SUPERSEDED,
    NO_MAP: CDA_IG_NO_MAP_SPECIFIED,
    GAP: CDA_IG_DEFINES_TARGET,
}

# (shape suffix) -> (verdict, what the IG says). Matched by longest suffix,
# so a deeper, more specific path wins over a bare tag name.
IG_VERDICTS: dict[str, tuple[str, str]] = {
    # --- Document header: the IG publishes no header mapping at all -----
    "ClinicalDocument/code": (NO_MAP, "No header mapping is published; document type has no specified FHIR target."),
    "ClinicalDocument/title": (NO_MAP, "No header mapping is published."),
    "ClinicalDocument/languageCode": (NO_MAP, "No header mapping is published."),
    "ClinicalDocument/confidentialityCode": (NO_MAP, "No header mapping is published."),
    "ClinicalDocument/effectiveTime": (NO_MAP, "No header mapping is published."),
    "component/section/code": (NO_MAP, "Section-level metadata has no specified FHIR target; the IG maps entries."),
    "component/section/title": (NO_MAP, "Section-level metadata has no specified FHIR target."),
    "component/section/text()": (
        NO_MAP,
        "C-CDA requires the narrative to restate the entries; the IG maps the entries, not the prose.",
    ),
    # --- Patient (CCDA-FHIR Patient.csv) --------------------------------
    "patient/administrativeGenderCode/@displayName": (
        NOT_SUPPORTED,
        "administrativeGenderCode maps by transform (CF_AdminstrativeGender) to Patient.gender, "
        "a bare code; the source display name has no target of its own.",
    ),
    # --- Problem Concern Act (CCDA-FHIR Problem-Condition.csv) ----------
    "entry/act/id": (NOT_SUPPORTED, 'Problem Concern Act ".id" is marked "not supported by target".'),
    "entry/act/statusCode": (NOT_SUPPORTED, 'Problem Concern Act ".statusCode" is marked "not supported by target".'),
    "act/effectiveTime/low": (
        NOT_SUPPORTED,
        'Problem Concern Act ".effectiveTime" is marked "not supported by target" - only the '
        "Problem Observation's own effectiveTime maps, to onsetDateTime/abatementDateTime.",
    ),
    "entryRelationship/observation/@negationInd": (
        NOT_SUPPORTED,
        'Problem Observation "..negationInd" is marked "not supported by target".',
    ),
    # NOTE: no entry for "entryRelationship/observation/id". Condition.identifier
    # is now built, so the Problems case is not a gap - and the shape alone
    # cannot tell a Problem Observation from an Allergy Reaction Observation
    # nested at the same depth, whose own IG target is .reaction.id instead.
    # Claiming the Problems verdict for both was a real misattribution; the
    # remaining hits (a negated or REFR-wrapped entry that produces no
    # resource at all) keep the honest "not yet checked" citation rather
    # than a verdict borrowed from a different section.
    "entryRelationship/observation/statusCode": (
        NO_MAP,
        "The Problem-Condition table maps the Problem Observation's value, id, code and "
        "effectiveTime, and the Problem Status Observation's own value - but lists no row for the "
        "observation's own statusCode.",
    ),
    "entryRelationship/observation/code": (
        NOT_SUPPORTED,
        "Problem Observation \"..code\" is listed against Condition.category with the IG's own "
        'comment "map not feasible" - it names a target and then says it cannot be made.',
    ),
    "entry/act/code": (
        NO_MAP,
        "The Problem-Condition table lists no row for the Concern Act's own code - it is the fixed "
        "CONC act type, not patient data.",
    ),
    # --- Encounters (CF-encounters.md) -----------------------------------
    "encompassingEncounter/code": (
        SUPERSEDED,
        "The Encounters table maps /code to .class for V3 ActCode values, which this app builds; "
        "the display name alongside it has no target of its own.",
    ),
    # --- Vitals / Results (CF-vitals.md, CF-results.md) ------------------
    "organizer/code": (
        NOT_SUPPORTED,
        "CF-vitals fixes the panel code to 85353-1 - the organizer's own narrative-only /code is "
        "never read into it.",
    ),
    # --- Procedure (CCDA-FHIR Procedure.csv) ----------------------------
    "procedure/priorityCode": (NOT_SUPPORTED, 'Marked "not supported by target".'),
    "procedure/methodCode": (NOT_SUPPORTED, 'Marked "not supported by target".'),
    # NOTE: no entry for "entryRelationship/act/code" either. The IG maps it
    # to Procedure.followUp *for an Instruction act specifically*, and the
    # bare shape cannot tell an Instruction from the Comment Activity nested
    # at the same depth - whose own code is a fixed template marker. Keying
    # on the shape alone labelled a Comment Activity with the Instruction's
    # verdict, which is the same misattribution as the observation/id case
    # above. Distinguishing them needs the templateId, which the drop
    # register does not carry, so both keep the honest "not yet checked".
    # --- Immunization (CCDA-FHIR Immunization.csv) ----------------------
    # The Immunization Activity's own code is "not supported by target" in
    # both the EVN and INT mood rows - the vaccine is carried by
    # consumable/manufacturedMaterial/code, not by this one.
    "entry/substanceAdministration/code": (
        NOT_SUPPORTED,
        'Immunization Activity ".code" is marked "not supported by target" in both mood rows.',
    ),
    "manufacturedMaterial/lotNumberText": (
        NOT_SUPPORTED,
        'Marked "not supported by target" for the INT-mood (MedicationRequest) case.',
    ),
    "substanceAdministration/approachSiteCode": (NOT_SUPPORTED, 'Marked "not supported by target".'),
    "substanceAdministration/administrationUnitCode": (NOT_SUPPORTED, 'Marked "not supported by target".'),
}


def verdict_for(shape: str) -> tuple[str | None, Citation, str | None]:
    """(verdict, citation, what the IG says) for an element shape.

    Longest matching suffix wins, so `entryRelationship/observation/id`
    beats a bare `id` entry. An unmatched shape keeps the honest
    "not yet checked" citation rather than being assumed either way.
    """
    best: str | None = None
    for key in IG_VERDICTS:
        if (shape == key or shape.endswith("/" + key)) and (best is None or len(key) > len(best)):
            best = key
    if best is None:
        return None, DROP_NOT_YET_CHECKED, None
    verdict, note = IG_VERDICTS[best]
    return verdict, _CITATION_BY_VERDICT[verdict], note
