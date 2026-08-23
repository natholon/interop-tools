"""What the C-CDA on FHIR IG actually says about each element this app drops,
so the register can cite a verdict instead of "not yet checked".

Every entry was read off the IG's published mapping tables
(`mappings/CF/*.csv`), whose `Approach` column is the authoritative signal:

- `source value` / `transform` / `fixed value` - the IG defines a FHIR
  target. If this app does not implement it, that is a **real gap**.
- `not supported by target` - the IG states there is no element to carry
  it. Dropping it matches the standard.

An element with **no row at all** is also a real answer, per the IG's own
mappingGuidance:

    "If you have data in an input artifact that is defined in the source
    specification and for which no map is specified here, that means that
    this team did not find a target for which we could build consensus."

That settles the document header wholesale: the IG publishes no header or
Encounter table at all (confirmed by reading `CF-notes.md` and
`mappingGuidance.md`, and by listing the CSV directory), so
`ClinicalDocument/code`, `/title`, `/languageCode`,
`/confidentialityCode` and a section's own `code`/`title` are "no map
specified" rather than gaps.

**Keys match most-specific-first**, because the same tag means different
things at different depths - a Problem Concern Act's own `id` is "not
supported by target" while the Problem Observation's `id` inside it maps
to `Condition.identifier`.

**A shape alone is often not enough to identify what it is.**
`entryRelationship/observation/id` matches both an Allergy Reaction
Observation and a Problem Observation, and the IG answers them
differently; two GAP verdicts were withdrawn for exactly that
misattribution. A key may therefore name the templateId it read
(`"<root>|<shape>"`); scoped keys are tried first and beat any unscoped
one, and within each pass the longest matching suffix wins.

A shape with no entry keeps the honest `DROP_NOT_YET_CHECKED` citation.
No shape this app currently drops is in that state - see
`test_every_drop_carries_a_checked_verdict`, which is what keeps it true
as sections change."""

from app.provenance.citations import (
    CDA_IG_DEFINES_TARGET,
    CDA_IG_NO_MAP_SPECIFIED,
    CDA_IG_NOT_SUPPORTED,
    Citation,
    DROP_NOT_YET_CHECKED,
)

NOT_SUPPORTED = "not_supported"
NO_MAP = "no_map"
OUT_OF_SCOPE = "out_of_scope"
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

# The IG names a target on a resource this app deliberately never builds.
# Provenance is the whole of it: it models who created or revised a stored
# record, and a stateless converter has no record lifecycle for such a
# resource to describe (its required `recorded` timestamp has no honest
# value - conversion time is clinically meaningless). Where the source
# author has a real home on the resource itself, the plain attribute
# carries it; the IG itself declines to give guidance on when to use one
# versus the other.
CDA_IG_TARGET_OUT_OF_SCOPE = Citation(
    title="C-CDA on FHIR maps this to a resource this app does not build",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=True,
    note=(
        "The IG defines a target, but on a resource this app deliberately never builds - the same "
        "permanent scope decision taken for HL7v2's EVN segment."
    ),
)

_CITATION_BY_VERDICT = {
    NOT_SUPPORTED: CDA_IG_NOT_SUPPORTED,
    SUPERSEDED: CDA_IG_SUPERSEDED,
    NO_MAP: CDA_IG_NO_MAP_SPECIFIED,
    OUT_OF_SCOPE: CDA_IG_TARGET_OUT_OF_SCOPE,
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
    # US Realm Header. A recognised encounter class code carries its own
    # display into Encounter.class.display; this fires only for a code
    # RECOGNIZED_ENCOUNTER_CLASSES does not contain, where the display
    # describes a code we did not use.
    "2.16.840.1.113883.10.20.22.1.1|encompassingEncounter/code/@displayName": (
        SUPERSEDED,
        "The display belongs to an encounter class code this app does not recognise, so the class "
        "fell back to its disclosed default - carrying the display would name a code the output "
        "does not contain.",
    ),
    # See the 2.16.840.1.113883.10.20.22.4.4 rows above: a converted Problem Observation records
    # its whole value, so these fire only for one that produced no Condition.
    "2.16.840.1.113883.10.20.22.4.4|entryRelationship/observation/value/@code": (
        NO_MAP,
        "The Problem Observation's value maps to Condition.code, which this app builds - so this one "
        "belongs to an observation that produced no Condition at all.",
    ),
    "2.16.840.1.113883.10.20.22.4.4|entryRelationship/observation/value/@codeSystem": (
        NO_MAP,
        "As above: this observation produced no Condition, so its value's code system was never read.",
    ),
    "2.16.840.1.113883.10.20.22.4.4|entryRelationship/observation/value/@displayName": (
        NO_MAP,
        "As above: this observation produced no Condition, so its value's display was never read.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|component/observation/code/@displayName": (
        NOT_SUPPORTED,
        "CF-vitals fixes the pulse oximetry panel's own code, so neither the grouped member's source "
        "code nor its display is read into it.",
    ),
    # The Specimen Collection Procedure is found *by* this fixed SNOMED
    # code (17636008); it identifies the element rather than describing
    # the specimen, whose own body site is what gets mapped.
    "2.16.840.1.113883.10.20.22.4.1|component/procedure/code": (
        NOT_SUPPORTED,
        "The Specimen Collection Procedure's code is the fixed 17636008 marker this app matches on to "
        "find the element; its targetSiteCode is the part that maps.",
    ),
    "2.16.840.1.113883.10.20.22.4.14|code/originalText/reference": (
        SUPERSEDED,
        "The reference is consumed resolving <originalText> against the section narrative; the text "
        "it points at is what lands in CodeableConcept.text.",
    ),
    # --- Scoped by templateId ------------------------------------------
    # Everything below names the template it read, because the bare shape
    # is ambiguous: an Allergy Reaction Observation and a Problem
    # Observation sit at identical depths under identically-named tags.
    #
    # Problem Observation (2.16.840.1.113883.10.20.22.4.4) inside a Problem Concern Act.
    # A converted one records all of these, so a drop here can only be an
    # entry this app skipped by design - a negated observation, or one
    # reached through a relationship whose typeCode is not SUBJ.
    "2.16.840.1.113883.10.20.22.4.4|entryRelationship/observation/id": (
        NO_MAP,
        "The Problem Observation's own id maps to Condition.identifier, which this app builds - so "
        "this one belongs to an observation that produced no Condition at all (negated, or reached "
        "through a non-SUBJ relationship, which the IG's own entry shape does not treat as an "
        "asserted problem).",
    ),
    "2.16.840.1.113883.10.20.22.4.4|entryRelationship/observation/value": (
        NO_MAP,
        "The Problem Observation's own value maps to Condition.code, which this app builds - so this "
        "one belongs to an observation that produced no Condition at all.",
    ),
    # Allergy: the IG maps the Allergy-Intolerance Observation's value by
    # *transform* (CF_AllergyIntoleranceType/Category) onto AllergyIntolerance
    # .type and .category, both bare `code` elements. A display name has no
    # target of its own on a bare code - the same reason
    # administrativeGenderCode/@displayName is not supported.
    "2.16.840.1.113883.10.20.22.4.7|observation/value/@displayName": (
        NOT_SUPPORTED,
        "The Allergy-Intolerance Observation's value maps by transform to AllergyIntolerance.type "
        "and .category, both bare codes; a source display name has no target on either.",
    ),
    # The nested Status, Criticality and Severity Observations are the same
    # shape: each transforms to a code or a fixed-vocabulary concept, none
    # of which carries the source's own display.
    "2.16.840.1.113883.10.20.22.4.28|observation/value/@displayName": (
        NOT_SUPPORTED,
        "The Allergy Status Observation's value transforms to AllergyIntolerance.clinicalStatus, "
        "built from the IG's own fixed vocabulary rather than the source display.",
    ),
    "2.16.840.1.113883.10.20.22.4.145|observation/value/@displayName": (
        NOT_SUPPORTED,
        "The Criticality Observation's value transforms to AllergyIntolerance.criticality, a bare "
        "code with no display element.",
    ),
    "2.16.840.1.113883.10.20.22.4.8|observation/value/@displayName": (
        NOT_SUPPORTED,
        "The Severity Observation's value transforms to AllergyIntolerance.reaction.severity, a bare "
        "code with no display element.",
    ),
    "2.16.840.1.113883.10.20.22.4.6|observation/value/@displayName": (
        NOT_SUPPORTED,
        "The Problem Status Observation's value transforms to Condition.clinicalStatus, built from "
        "the IG's own fixed vocabulary rather than the source display.",
    ),
    # AllergyIntolerance.reaction is a backbone element with no identifier
    # (confirmed against the R4 resource, not assumed) - there is nowhere
    # for a Reaction Observation's own id to go.
    "2.16.840.1.113883.10.20.22.4.9|entryRelationship/observation/id": (
        NOT_SUPPORTED,
        "AllergyIntolerance.reaction is a backbone element with no identifier, so a Reaction "
        "Observation's own id has no target.",
    ),
    # Vital Signs. CF-vitals fixes both the organizer's and each
    # observation's status to "final", and groups blood pressure and pulse
    # oximetry members into Observation.component - which has no
    # identifier, status or effective element of its own (confirmed against
    # the R4 resource). The grouped members' code is likewise fixed by the
    # IG's own panel codes, not read from the source.
    "2.16.840.1.113883.10.20.22.4.26|organizer/statusCode": (
        NOT_SUPPORTED,
        "CF-vitals fixes the panel's status to final; the organizer's own statusCode is not read.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|component/observation/statusCode": (
        NOT_SUPPORTED,
        "CF-vitals fixes each vital sign's status to final; the observation's own statusCode is not "
        "read.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|component/observation/id": (
        NOT_SUPPORTED,
        "This observation was grouped into an Observation.component (a blood pressure or pulse "
        "oximetry panel, per CF-vitals), and component has no identifier element.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|component/observation/effectiveTime": (
        NOT_SUPPORTED,
        "This observation was grouped into an Observation.component, which has no effective element; "
        "the panel's own effectiveDateTime carries the time.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|component/observation/code": (
        NOT_SUPPORTED,
        "CF-vitals fixes the pulse oximetry panel's own code (59408-5 with 2708-6), so the grouped "
        "member's source code is not read into it.",
    ),
    # Family History. The Family History Observation's code is the fixed
    # 75323-6 "Condition" template marker describing what kind of statement
    # this is - the real diagnosis lives in its value.
    "2.16.840.1.113883.10.20.22.4.46|component/observation/code": (
        NOT_SUPPORTED,
        "The Family History Observation's code is the fixed 75323-6 'Condition' marker naming the "
        "template, not the diagnosis - which is carried by its value instead.",
    ),
    # Procedures.
    "2.16.840.1.113883.10.20.22.4.14|procedure/author/time": (
        OUT_OF_SCOPE,
        "The IG maps an entry's author to Provenance, which this app does not build - the same "
        "permanent scope decision taken for HL7v2's EVN segment. The plain Procedure.recorder "
        "reference is built where one is available.",
    ),
    "2.16.840.1.113883.10.20.22.4.64|act/code": (
        NOT_SUPPORTED,
        "A Comment Activity's code is the fixed template marker identifying it as a comment; the "
        "note's own content is its text, which is mapped.",
    ),
    "2.16.840.1.113883.10.20.22.4.147|substanceAdministration/code": (
        NOT_SUPPORTED,
        "A Medication Free Text Sig's code is the fixed template marker identifying it; the sig's "
        "own content is its text, which is mapped to dosageInstruction.patientInstruction.",
    ),
    # Immunization. The IG maps negationInd true to status "not-done" and
    # false-or-null to "completed" - and maps statusCode to the same field.
    # A true negationInd is read and recorded; a false one leaves status to
    # statusCode, which supplies the identical answer.
    "2.16.840.1.113883.10.20.22.4.52|substanceAdministration/@negationInd": (
        SUPERSEDED,
        "The IG maps a false or absent negationInd to status 'completed' - the same field statusCode "
        "maps to, which supplied it here. A true negationInd is read.",
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


def verdict_for(
    shape: str, template_ids: frozenset[str] = frozenset()
) -> tuple[str | None, Citation, str | None]:
    """(verdict, citation, what the IG says) for an element shape.

    A key may be scoped to a templateId (`"<root>|<shape>"`), which is how
    two identically-shaped elements governed by different IG tables are
    told apart - an Allergy Reaction Observation's `id` from a Problem
    Observation's. Scoped keys are tried first and beat any unscoped one,
    however specific; within each pass the longest matching suffix wins,
    so `entryRelationship/observation/id` beats a bare `id`.

    An unmatched shape keeps the honest "not yet checked" citation rather
    than being assumed either way.
    """
    for candidate in (_scoped_keys(template_ids), _UNSCOPED_KEYS):
        best: str | None = None
        for key, suffix in candidate:
            if (shape == suffix or shape.endswith("/" + suffix)) and (
                best is None or len(suffix) > len(_suffix_of(best))
            ):
                best = key
        if best is not None:
            verdict, note = IG_VERDICTS[best]
            return verdict, _CITATION_BY_VERDICT[verdict], note
    return None, DROP_NOT_YET_CHECKED, None


def _suffix_of(key: str) -> str:
    return key.split("|", 1)[1] if "|" in key else key


_UNSCOPED_KEYS = [(k, k) for k in IG_VERDICTS if "|" not in k]


def _scoped_keys(template_ids: frozenset[str]) -> list[tuple[str, str]]:
    return [
        (key, key.split("|", 1)[1])
        for key in IG_VERDICTS
        if "|" in key and key.split("|", 1)[0] in template_ids
    ]
