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
GAP = "gap"

_CITATION_BY_VERDICT = {
    NOT_SUPPORTED: CDA_IG_NOT_SUPPORTED,
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
    "entryRelationship/observation/id": (
        GAP,
        'Problem Observation "..id" maps as source value to Condition.identifier.',
    ),
    # --- Procedure (CCDA-FHIR Procedure.csv) ----------------------------
    "procedure/priorityCode": (NOT_SUPPORTED, 'Marked "not supported by target".'),
    "procedure/methodCode": (NOT_SUPPORTED, 'Marked "not supported by target".'),
    "entryRelationship/act/code": (
        GAP,
        'entryRelationship.act.code [Instruction] maps as source value to Procedure.followUp.',
    ),
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
