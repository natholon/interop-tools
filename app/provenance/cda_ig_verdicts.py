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
# The element was read; its code simply is not in the published
# ConceptMap, so the mapping fell back to its documented default. That is
# a property of the document, not a missing capability - the HL7v2 half
# says the same about a PID-7 the date parser cannot read.
UNRECOGNIZED = "unrecognized"
# The target is a fixed value per the spec or the IG, so the source
# element is never consulted at all.
FIXED_TARGET = "fixed_target"
# The IG names a target on a resource this element did not produce,
# because it was folded into another one.
FOLDED = "folded"
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

CDA_IG_UNRECOGNIZED_CODE = Citation(
    title="Code not in the published ConceptMap",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=True,
    note=(
        "This app reads the element and maps it through the IG's own ConceptMap. The document's code "
        "has no row there, so the mapping fell back to its documented default - a property of this "
        "document rather than a missing capability."
    ),
)

CDA_IG_FIXED_TARGET = Citation(
    title="Target is a fixed value; the source element is not read",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=True,
    note="The IG (or C-CDA itself) fixes the FHIR value for this field, so the source element is never consulted.",
)

CDA_IG_FOLDED = Citation(
    title="Folded into another resource, which has nowhere to carry this",
    url="https://build.fhir.org/ig/HL7/ccda-on-fhir/",
    authoritative=True,
    note=(
        "The IG maps this to a field on the resource built from this element - but the element was "
        "folded into another resource per the IG's own panel guidance, so it produced none of its own."
    ),
)

_CITATION_BY_VERDICT = {
    NOT_SUPPORTED: CDA_IG_NOT_SUPPORTED,
    SUPERSEDED: CDA_IG_SUPERSEDED,
    NO_MAP: CDA_IG_NO_MAP_SPECIFIED,
    OUT_OF_SCOPE: CDA_IG_TARGET_OUT_OF_SCOPE,
    GAP: CDA_IG_DEFINES_TARGET,
    UNRECOGNIZED: CDA_IG_UNRECOGNIZED_CODE,
    FIXED_TARGET: CDA_IG_FIXED_TARGET,
    FOLDED: CDA_IG_FOLDED,
}

# (shape suffix) -> (verdict, what the IG says). Matched by longest suffix,
# so a deeper, more specific path wins over a bare tag name.
IG_VERDICTS: dict[str, tuple[str, str]] = {
    # --- Document header: the IG publishes no header mapping at all -----
    "ClinicalDocument/code": (NO_MAP, "No header mapping is published; document type has no specified FHIR target."),
    # Header participations the base R4 Composition mapping names no
    # target for. Composition has fields for author, attester and
    # custodian and nothing else of this kind, and no header table is
    # published to name one.
    "ClinicalDocument/informant": (NO_MAP, "Composition has no informant field and no header mapping is published."),
    "ClinicalDocument/dataEnterer": (NO_MAP, "Composition has no dataEnterer field and no header mapping is published."),
    "ClinicalDocument/informationRecipient": (
        NO_MAP,
        "Composition has no recipient field and no header mapping is published.",
    ),
    "ClinicalDocument/relatedDocument": (
        NO_MAP,
        "The base mapping routes .relatedDocument to Composition.relatesTo, which points at another document - "
        "a single-document conversion has nothing to resolve it to.",
    ),
    # --- a negated Medication Activity ------------------------------------
    # negationInd="true" means this administration did NOT happen, and the
    # Medications mapper skips the entry entirely rather than asserting a
    # MedicationRequest for it - the same negation handling Problems uses.
    # Nothing the entry carries has anywhere to go, so these reach the
    # register as collateral. Most such entries report once, as "entry not
    # converted"; a few leaves escape that grouping where the span
    # resolver cannot attribute them, which is its own disclosed
    # imprecision rather than a second cause.
    "2.16.840.1.113883.10.20.22.4.16|@negationInd": (
        NOT_SUPPORTED,
        "A negated Medication Activity produces no MedicationRequest, and the CSV gives negationInd "
        "no target of its own.",
    ),
    "2.16.840.1.113883.10.20.22.4.16|author/assignedAuthor/assignedPerson/name/family": (
        NOT_SUPPORTED,
        "The MedicationRequest table maps the author to .requester, which a negated entry never "
        "builds - it produces no MedicationRequest at all.",
    ),
    "2.16.840.1.113883.10.20.22.4.16|author/assignedAuthor/assignedPerson/name/given": (
        NOT_SUPPORTED,
        "The MedicationRequest table maps the author to .requester, which a negated entry never "
        "builds - it produces no MedicationRequest at all.",
    ),

    # C-CDA fixes statusCode to "completed" for both the Vital Signs
    # Organizer and each Vital Sign Observation, and CF-vitals states the
    # FHIR status plainly - so neither source element is ever consulted.
    "2.16.840.1.113883.10.20.22.4.26|statusCode": (
        FIXED_TARGET,
        "C-CDA fixes the Vital Signs Organizer statusCode to completed, and CF-vitals fixes the "
        "panel Observation.status to final.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|statusCode": (
        FIXED_TARGET,
        "C-CDA fixes a Vital Sign Observation statusCode to completed, and CF-vitals fixes "
        "Observation.status to final.",
    ),

    # A Family History Organizer's statusCode is fixed to "completed" by
    # the template itself, so this app sets FamilyMemberHistory.status
    # from the spec rather than reading it. Same for the member
    # observations beneath it.
    "2.16.840.1.113883.10.20.22.4.45|statusCode": (
        FIXED_TARGET,
        "The Family History Organizer template fixes statusCode to completed, so FamilyMemberHistory"
        ".status is set from the spec rather than read.",
    ),
    "2.16.840.1.113883.10.20.22.4.46|statusCode": (
        FIXED_TARGET,
        "A Family History Observation sits under an organizer whose status is fixed; its own "
        "statusCode has no separate FHIR target.",
    ),

    # The Medication Free Text Sig maps to Dosage.patientInstruction, which
    # this app builds - but only for an entry that becomes a
    # MedicationRequest at all. Where the entry is skipped (negated, or no
    # resolvable medication code) the sig is collateral. Unscoped
    # deliberately: the sig template is the only thing in this app with
    # this shape, and its own templateId does not reach the scanner here.
    "entryRelationship/substanceAdministration/text()": (
        NOT_SUPPORTED,
        "The free-text sig maps to Dosage.patientInstruction, which only exists on a MedicationRequest "
        "- and the entry carrying this one produced none.",
    ),

    # --- effectiveTime bounds with no target ------------------------------
    # The Allergy CSV answers this outright: "..effectiveTime..low" is a
    # source value to .onsetDateTime and "..effectiveTime..high" is "not
    # supported by target". AllergyIntolerance.lastOccurrence exists, but
    # the IG declines to map it there, so neither do we.
    "2.16.840.1.113883.10.20.22.4.7|effectiveTime/high": (
        NOT_SUPPORTED,
        "The Allergy table marks the high bound not supported by target.",
    ),
    # A Reaction Observation's effectiveTime maps as a whole to
    # .reaction.onset, a single dateTime built from the low bound.
    "2.16.840.1.113883.10.20.22.4.9|effectiveTime/high": (
        SUPERSEDED,
        "The Allergy table maps the reaction effectiveTime to .reaction.onset, a single dateTime "
        "this app builds from the low bound.",
    ),
    # An administration happens at a point in time, so only the low bound
    # has a home on Immunization.occurrenceDateTime.
    "2.16.840.1.113883.10.20.22.4.52|effectiveTime/high": (
        SUPERSEDED,
        "The Immunization table maps effectiveTime to occurrenceDateTime, a single point in time "
        "this app builds from the low bound.",
    ),

    # --- statusCode -------------------------------------------------------
    # Read and mapped through each section's own published ConceptMap; a
    # code with no row there falls back to that map's documented default.
    "2.16.840.1.113883.10.20.22.4.1|statusCode": (
        UNRECOGNIZED,
        "ConceptMap-CF-ResultReportStatus has no row for this code, so DiagnosticReport.status fell "
        "back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.2|statusCode": (
        UNRECOGNIZED,
        "ConceptMap-CF-ResultStatus has no row for this code, so Observation.status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.14|statusCode": (
        UNRECOGNIZED,
        "ConceptMap-CF-ProcedureStatus has no row for this code, so Procedure.status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.52|statusCode": (
        UNRECOGNIZED,
        "CF_ImmunizationStatus has no row for this code, so Immunization.status fell back to "
        "completed - that value set has no unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.44|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.41|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.40|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.42|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.120|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.43|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.146|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    "2.16.840.1.113883.10.20.22.4.20|statusCode": (
        UNRECOGNIZED,
        "The disclosed Plan of Treatment status map has no row for this code, so the CarePlan "
        "activity status fell back to unknown.",
    ),
    # Social History and its specialisations fix the FHIR status, so the
    # source statusCode is never consulted.
    "2.16.840.1.113883.10.20.22.4.38|statusCode": (
        FIXED_TARGET,
        "CF-social fixes Observation.status to final for a Social History Observation.",
    ),
    "2.16.840.1.113883.10.20.22.4.78|statusCode": (
        FIXED_TARGET,
        "CF-social fixes Observation.status to final for a Smoking Status Observation.",
    ),
    "2.16.840.1.113883.10.20.22.4.200|statusCode": (
        FIXED_TARGET,
        "Birth Sex becomes a us-core-birthsex extension on Patient, which carries a value and no status.",
    ),
    "2.16.840.1.113883.10.20.34.3.45|statusCode": (
        FIXED_TARGET,
        "Gender Identity becomes a us-core-genderIdentity extension on Patient, which carries a "
        "value and no status.",
    ),
    "2.16.840.1.113883.10.20.22.4.200|value/@displayName": (
        NOT_SUPPORTED,
        "us-core-birthsex is valueCode - a bare code, with nowhere to carry a display name.",
    ),

    # --- folded into a panel ---------------------------------------------
    # CF-vitals groups a systolic/diastolic pair, and an O2 saturation with
    # its siblings, into one panel Observation. A reading folded into one
    # produces no Observation of its own - the same reason its id, status
    # and effectiveTime are not carried either.
    "2.16.840.1.113883.10.20.22.4.27|author/assignedAuthor/assignedPerson/name/family": (
        FOLDED,
        "CF-vitals maps a Vital Sign Observation author to the Observation built from it; a reading "
        "folded into a panel produces none.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|author/assignedAuthor/assignedPerson/name/given": (
        FOLDED,
        "CF-vitals maps a Vital Sign Observation author to the Observation built from it; a reading "
        "folded into a panel produces none.",
    ),
    "2.16.840.1.113883.10.20.22.4.27|interpretationCode": (
        FOLDED,
        "CF-vitals maps interpretationCode to the Observation built from this reading; a reading "
        "folded into a panel produces none.",
    ),
    # An author's own <time> has no target on any of these tables - only
    # .attester (the authenticators) has a time.
    "2.16.840.1.113883.10.20.22.4.27|author/time": (
        NO_MAP,
        "CF-vitals maps the author to .performer and names no target for when they authored it.",
    ),

    # The document author's own <time>. The base R4 Composition mapping
    # routes ".author.assignedAuthor" to Composition.author and names no
    # target for when they authored it - Composition.date is the
    # document's effectiveTime, a different fact. Provenance.recorded
    # would carry it, on a resource this app never builds.
    "ClinicalDocument/author/time": (
        NO_MAP,
        "The base Composition mapping routes .author.assignedAuthor to Composition.author and names no "
        "target for the author's own time; only .attester (authenticators) has a time.",
    ),
    # author (1..*) and custodian (1..1) are required of every C-CDA
    # document, so a verdict for them has to be right. Both now map, to
    # Composition.author/.custodian, and normally do not reach here at all.
    #
    # **These reasons said "this app builds no Composition - it emits a
    # collection Bundle".** That was true when written and false the day
    # app/cda/composition.py shipped, and nothing re-checked it: the same
    # trap that once froze an implementation choice into the Discharge
    # Medications disclosure as though it were a standards constraint. A
    # verdict asserting what this app does is a claim with an expiry date.
    #
    # What is left is genuinely per-document: Composition eagerly requires
    # a type, a date and an author at construction, and none has an honest
    # default, so a document missing any of them builds no Composition at
    # all and these two have nothing to hang on.
    #
    # Not the same thing as the Bundle staying a "collection" - a date-only
    # effectiveTime supplies no Bundle.timestamp, so bdl-10 blocks
    # type="document", but the Composition is still built and the author
    # and custodian still map. Confirming that against a real Bundle is
    # what caught a *replacement* for the stale reason above being wrong in
    # its own new way.
    "ClinicalDocument/author": (
        NO_MAP,
        "No header mapping is published. The author maps to Composition.author, and this document built "
        "no Composition - that needs a document code, a parseable effectiveTime and an author, and one of "
        "those is missing here.",
    ),
    "ClinicalDocument/custodian": (
        NO_MAP,
        "No header mapping is published. The custodian maps to Composition.custodian, and this document "
        "built no Composition - that needs a document code, a parseable effectiveTime and an author, and "
        "one of those is missing here.",
    ),
    "ClinicalDocument/title": (NO_MAP, "No header mapping is published."),
    # Reaches here for the same reason author and custodian do, and only
    # then: the language maps to Resource.language on the Composition,
    # so a document that builds none has nowhere to put it.
    "ClinicalDocument/languageCode": (
        NO_MAP,
        "No header mapping is published. The document language maps to Composition.language, and this "
        "document built no Composition - that needs a document code, a parseable effectiveTime and an "
        "author, and one of those is missing here.",
    ),
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
    # Medication Activity's author time. The MedicationRequest table maps
    # ".author Participation" twice - to Provenance and to .requester -
    # and names no target for the time itself, unlike the Allergy table,
    # which does map its author time to .recorded.
    "2.16.840.1.113883.10.20.22.4.16|substanceAdministration/author/time": (
        NO_MAP,
        "The MedicationRequest table maps the author Participation to .requester and to Provenance, "
        "but lists no row for the author's own time.",
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
    # Gender Identity, Birth Sex and Sex specialise Social History
    # Observation, so each declares its templateId and inherits its 1..1
    # id. But the IG says explicitly that no Observation should be created
    # for them - they map to a us-core extension on Patient, and an
    # extension carries no identifier - so the id has nowhere to go.
    "2.16.840.1.113883.10.20.34.3.45|observation/id": (
        NOT_SUPPORTED,
        "Gender Identity maps to the us-core-genderIdentity extension on Patient rather than to an "
        "Observation, and an extension has no identifier for the entry's own id.",
    ),
    "2.16.840.1.113883.10.20.22.4.200|observation/id": (
        NOT_SUPPORTED,
        "Birth Sex maps to the us-core-birthsex extension on Patient rather than to an Observation, "
        "and an extension has no identifier for the entry's own id.",
    ),
    "2.16.840.1.113883.10.20.22.4.507|observation/id": (
        NOT_SUPPORTED,
        "Sex maps to the us-core-sex extension on Patient rather than to an Observation, and an "
        "extension has no identifier for the entry's own id.",
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
