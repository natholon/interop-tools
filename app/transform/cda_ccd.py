"""FHIR Bundle -> C-CDA CCD XML - the second reverse-direction slice, and
the first proof that app/transform/'s architecture generalizes across
input *formats*, not just across HL7v2 trigger events. Scoped to exactly
"header + Problems section" - the identical scope CCD's own very first
*forward*-direction slice shipped with (see CLAUDE.md/git history: "Add
C-CDA to FHIR conversion (CCD header + Problems section)" was this app's
first CDA commit, before Medications/Allergies/Immunizations/etc. arrived
in later slices) - the same "one thing per slice" precedent applied to the
reverse direction too.

Reverses app/cda/common.py::build_patient_from_header/
build_encounter_from_header and app/cda/problems.py::build_conditions
field-for-field, using each one's own exact element/attribute shapes, not
re-derived independently. **Disclosed round-trip simplifications, not
bugs**: `clinicalStatus` is written back only via the Concern Act's own
`statusCode` (the reverse of `_ACT_STATUS_TO_CLINICAL_STATUS`) - the
nested Status Observation path `_resolve_clinical_status` also reads is
not reconstructed, since a FHIR `Condition.clinicalStatus` code alone
doesn't indicate which of the two source shapes originally produced it;
this is a safe simplification (the Concern Act's own statusCode is a
first-class, spec-legitimate source on its own, not a fabricated one).
`Condition.onsetDateTime`/`abatementDateTime` are written back as a single
`effectiveTime/low`+`high` pair (the forward mapper's own `ivl_ts_bounds`
reads exactly this shape, via `<low>`/`<high>` children) - either date
alone still produces a valid `effectiveTime` with only the corresponding
child present, matching `ivl_ts_bounds`' own tolerance for a partial pair."""

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept

from app.cda.allergies import ALLERGY_CONCERN_ACT_TEMPLATE_ID, ALLERGY_OBSERVATION_TEMPLATE_ID
from app.cda.allergies import ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID, CRITICALITY_OBSERVATION_TEMPLATE_ID
from app.cda.allergies import REACTION_OBSERVATION_TEMPLATE_ID, SEVERITY_OBSERVATION_TEMPLATE_ID
from app.cda.allergies import CLINICAL_STATUS_MAP as ALLERGY_CLINICAL_STATUS_MAP
from app.cda.allergies import CRITICALITY_MAP, SEVERITY_MAP
from app.cda.allergies import CATEGORY_MAP as ALLERGY_CATEGORY_MAP
from app.cda.allergies import TYPE_MAP as ALLERGY_TYPE_MAP
from app.cda.allergies import SECTION_TEMPLATE_ID as ALLERGIES_SECTION_TEMPLATE_ID
from app.cda.ccd import CCD_TEMPLATE_ID
from app.cda.common import CD_FALLBACK_SYSTEM, OID_TO_FHIR_SYSTEM, RECOGNIZED_ENCOUNTER_CLASSES
from app.cda.immunizations import IMMUNIZATION_ACTIVITY_TEMPLATE_ID
from app.cda.immunizations import SECTION_TEMPLATE_ID as IMMUNIZATIONS_SECTION_TEMPLATE_ID
from app.cda.results import ORGANIZER_TEMPLATE_ID as RESULTS_ORGANIZER_TEMPLATE_ID
from app.cda.results import OBSERVATION_TEMPLATE_ID as RESULTS_OBSERVATION_TEMPLATE_ID
from app.cda.results import SECTION_TEMPLATE_ID as RESULTS_SECTION_TEMPLATE_ID
from app.cda.results import SPECIMEN_COLLECTION_PROCEDURE_CODE, SPECIMEN_COLLECTION_PROCEDURE_CODE_SYSTEM
from app.cda.vitals import ORGANIZER_TEMPLATE_ID as VITALS_ORGANIZER_TEMPLATE_ID
from app.cda.vitals import OBSERVATION_TEMPLATE_ID as VITALS_OBSERVATION_TEMPLATE_ID
from app.cda.vitals import PANEL_CODE as VITALS_PANEL_CODE
from app.cda.vitals import SECTION_TEMPLATE_ID as VITALS_SECTION_TEMPLATE_ID
from app.cda.vitals import BP_PANEL_CODE, PULSE_OX_PRIMARY_CODES
from app.cda.procedures import PROCEDURE_TEMPLATE_ID
from app.cda.procedures import STATUS_MAP as PROCEDURE_STATUS_MAP
from app.cda.procedures import SECTION_TEMPLATE_ID as PROCEDURES_SECTION_TEMPLATE_ID
from app.cda.procedures import SERVICE_DELIVERY_LOCATION_TEMPLATE_ID
from app.cda.procedures import INDICATION_TEMPLATE_ID, COMMENT_ACTIVITY_TEMPLATE_ID, AUTHOR_PARTICIPATION_TEMPLATE_ID
from app.cda.medications import FREE_TEXT_SIG_TEMPLATE_ID, MEDICATION_ACTIVITY_TEMPLATE_ID
from app.cda.medications import STATUS_MAP as MEDICATION_STATUS_MAP
from app.cda.medications import SECTION_TEMPLATE_ID as MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.problems import PROBLEM_OBSERVATION_TEMPLATE_ID
from app.cda.problems import CONCERN_ACT_TEMPLATE_ID
from app.cda.problems import SECTION_TEMPLATE_ID as PROBLEMS_SECTION_TEMPLATE_ID
from app.cda.discharge_medications import CATEGORY_CODE as DISCHARGE_MEDICATION_CATEGORY_CODE
from app.cda.discharge_medications import CATEGORY_SYSTEM as DISCHARGE_MEDICATION_CATEGORY_SYSTEM
from app.cda.discharge_medications import DISCHARGE_MEDICATION_ACT_TEMPLATE_ID
from app.cda.discharge_medications import SECTION_TEMPLATE_ID as DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import CATEGORY_CODE as DISCHARGE_DIAGNOSIS_CATEGORY_CODE
from app.cda.hospital_discharge_diagnosis import CATEGORY_SYSTEM as DISCHARGE_DIAGNOSIS_CATEGORY_SYSTEM
from app.cda.hospital_discharge_diagnosis import HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import SECTION_TEMPLATE_ID as HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID
from app.cda.family_history import AGE_OBSERVATION_TEMPLATE_ID, DEATH_OBSERVATION_TEMPLATE_ID
from app.cda.family_history import ORGANIZER_TEMPLATE_ID as FAMILY_HISTORY_ORGANIZER_TEMPLATE_ID
from app.cda.family_history import OBSERVATION_TEMPLATE_ID as FAMILY_HISTORY_OBSERVATION_TEMPLATE_ID
from app.cda.narrative_sections import CANONICAL_TITLES as NARRATIVE_CANONICAL_TITLES
from app.cda.narrative_sections import LOINC_TO_TEMPLATE_ID as NARRATIVE_LOINC_TO_TEMPLATE_ID
from app.cda.plan_of_treatment import PLANNED_OBSERVATION_TEMPLATE_ID
from app.cda.social_history import CATEGORY_CODE as SOCIAL_HISTORY_CATEGORY_CODE
from app.cda.social_history import OBSERVATION_TEMPLATE_ID as SOCIAL_HISTORY_OBSERVATION_TEMPLATE_ID
from app.cda.social_history import SMOKING_STATUS_TEMPLATE_ID
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_date, format_hl7_ts

# Reverse of app.cda.common.OID_TO_FHIR_SYSTEM - inverted from the same
# table rather than a second, independently-drifting copy. A FHIR system
# with no CDA-side OID at all (this app's own CD_FALLBACK_SYSTEM, or an
# arbitrary non-OID string) falls back to a disclosed placeholder OID, the
# same "can't recover, disclosed placeholder" precedent
# _reverse_identifier_root already established for identifiers below.
_FHIR_SYSTEM_TO_OID = {v: k for k, v in OID_TO_FHIR_SYSTEM.items()}
_PLACEHOLDER_CODE_SYSTEM_OID = "2.16.840.1.113883.19.5.99999.2"

# Reverse of app.cda.medications.STATUS_MAP - "active" is the disclosed
# representative for MedicationRequest.status == "unknown" (the forward
# fallback for any statusCode with no row in the published ConceptMap, so
# there's no single correct code to recover).
_MEDICATION_STATUS_TO_ACT_STATUS = {v: k for k, v in MEDICATION_STATUS_MAP.items()}
_DEFAULT_MEDICATION_ACT_STATUS = "active"

# Reverse of app.cda.medications._MOOD_TO_INTENT ({"INT": "order", "EVN":
# "plan"}) - "INT" is the disclosed representative for any
# MedicationRequest.intent this app's own forward mapper never produces
# (e.g. "proposal"/"original-order"), matching the forward side's own
# "order" default.
_INTENT_TO_MOOD_CODE = {"order": "INT", "plan": "EVN"}
_DEFAULT_MOOD_CODE = "INT"

# Reverse of app.cda.allergies.CLINICAL_STATUS_MAP - a clean bijection
# (active/inactive/resolved <-> the three Status Observation SNOMED
# codes), unlike Medications' STATUS_MAP - Allergies has no "unknown"
# fallback value on the FHIR side to worry about, since
# _resolve_clinical_status's own fixed default ("active") is a real
# recoverable code, not a synthetic catch-all.
_ALLERGY_CLINICAL_STATUS_TO_STATUS_OBSERVATION_VALUE = {v: k for k, v in ALLERGY_CLINICAL_STATUS_MAP.items()}

# Reverse of app.cda.allergies.CRITICALITY_MAP/SEVERITY_MAP - both clean
# bijections, the local HL7ObservationValue/SNOMED tables respectively.
_CRITICALITY_TO_HL7_CODE = {v: k for k, v in CRITICALITY_MAP.items()}
_SEVERITY_TO_SNOMED = {v: k for k, v in SEVERITY_MAP.items()}

# Reverse of app.cda.allergies.TYPE_MAP/CATEGORY_MAP - both keyed off the
# identical source SNOMED code, searched together rather than
# independently, since a single source code can carry a type, a category,
# or both.
_ALLERGY_VALUE_CANDIDATES = [
    (code, ALLERGY_TYPE_MAP.get(code), ALLERGY_CATEGORY_MAP.get(code))
    for code in dict.fromkeys([*ALLERGY_TYPE_MAP, *ALLERGY_CATEGORY_MAP])
]

_US_HEADER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.1"
# Reverse of app/cda/common.py::_GENDER_MAP ({"F": "female", "M": "male",
# "UN": "other"}).
_GENDER_TO_CDA_CODE = {"female": "F", "male": "M", "other": "UN"}
# Reverse of app/cda/problems.py::_ACT_STATUS_TO_CLINICAL_STATUS - "active"
# is the fallback for a clinicalStatus code with no clean reverse (there's
# no "suspended"/"aborted" distinction FHIR's own condition-clinical
# CodeSystem preserves for "inactive"/"resolved" to map back to uniquely,
# so this picks the one Concern Act status each FHIR code most plausibly
# came from).
_CLINICAL_STATUS_TO_ACT_STATUS = {"active": "active", "inactive": "suspended", "resolved": "completed"}
_PLACEHOLDER_ROOT = "2.16.840.1.113883.19.5.99999.1"

# Reverse of app.cda.plan_of_treatment.STATUS_MAP - not a clean bijection
# (both "aborted"/"cancelled" map forward to "cancelled", both "suspended"/
# "held" map forward to "on-hold"), so each target value picks one
# disclosed representative rather than whatever a naive dict-comprehension
# inversion's key-ordering would produce arbitrarily - the same discipline
# every other many-to-one STATUS_MAP reversal in this app already
# establishes (e.g. Results'). "cancelled" prefers its own exact-match
# source code over "aborted"; "on-hold" has no exact-match candidate on
# either side, so "suspended" is the disclosed pick over "held".
_CARE_PLAN_ACTIVITY_STATUS_TO_ACT_STATUS = {
    "scheduled": "active",
    "completed": "completed",
    "cancelled": "cancelled",
    "on-hold": "suspended",
}


def _esc(value) -> str:
    """XML-escapes a value before it's interpolated into element text
    content or a double-quoted attribute value - this module builds XML
    via raw f-strings (not ElementTree), so nothing does this
    automatically, unlike the parsing side's own `xml.etree.ElementTree`
    use. `&` must be escaped first (escaping `<`/`>` before `&` would
    double-escape the literal ampersand `&lt;` etc. just produced).
    Escaping `"` is what attribute-value safety needs (every attribute in
    this module uses double quotes, never single); escaping it in text
    content too is harmless, so one function safely covers both contexts.
    A real, reproduced bug this fixes: any `Coding.display` (a diagnosis/
    medication/allergen display name) containing a literal `&` - extremely
    plausible for real clinical text (e.g. "Diseases of the ear & mastoid
    process") - previously produced unparseable XML (`CdaParseError: not
    well-formed`) on the very next forward pass, silently breaking
    `/api/transform` for that Bundle. `None` passes through as `""` so
    every existing `if field:`-guarded call site stays a one-line wrap."""
    if value is None:
        return ""
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _reverse_identifier_root(identifier) -> str:
    """The reverse of app.cda.common.build_identifier's own root/extension
    resolution: an Identifier.system of urn:oid:<root> (the shape
    build_identifier itself produces whenever the source <id> had a real
    @root) reverses cleanly by stripping the urn:oid: prefix back off;
    anything else (this app's own urn:interop-tools:... fallback systems,
    or a system with no recoverable OID at all) falls back to a disclosed
    placeholder root rather than emitting a non-OID string as if it were
    one."""
    if identifier.system and identifier.system.startswith("urn:oid:"):
        return identifier.system[len("urn:oid:") :]
    return _PLACEHOLDER_ROOT


def _build_patient_role(patient) -> str:
    ids = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{_esc(identifier.value)}"/>'
        for identifier in (patient.identifier or [])
    )
    name = ""
    if patient.name:
        human_name = patient.name[0]
        family = f"<family>{_esc(human_name.family)}</family>" if human_name.family else ""
        given = "".join(f"<given>{_esc(g)}</given>" for g in (human_name.given or []))
        name = f"<name>{given}{family}</name>"
    gender = ""
    if patient.gender:
        code = _GENDER_TO_CDA_CODE.get(patient.gender)
        if code:
            gender = f'<administrativeGenderCode code="{code}" codeSystem="2.16.840.1.113883.5.1"/>'
    birth_time = f'<birthTime value="{format_hl7_date(patient.birthDate)}"/>' if patient.birthDate else ""
    addr = ""
    if patient.address:
        address = patient.address[0]
        lines = "".join(f"<streetAddressLine>{_esc(line)}</streetAddressLine>" for line in (address.line or []))
        city = f"<city>{_esc(address.city)}</city>" if address.city else ""
        state = f"<state>{_esc(address.state)}</state>" if address.state else ""
        postal_code = f"<postalCode>{_esc(address.postalCode)}</postalCode>" if address.postalCode else ""
        country = f"<country>{_esc(address.country)}</country>" if address.country else ""
        addr = f"<addr>{lines}{city}{state}{postal_code}{country}</addr>"
    telecom = ""
    if patient.telecom:
        contact_point = patient.telecom[0]
        scheme = "tel" if contact_point.system == "phone" else "mailto" if contact_point.system == "email" else "tel"
        telecom = f'<telecom value="{_esc(f"{scheme}:{contact_point.value}")}"/>'

    return (
        f'<recordTarget><patientRole>{ids}{addr}{telecom}'
        f'<patient>{name}{gender}{birth_time}</patient>'
        "</patientRole></recordTarget>"
    )


def _build_component_of(encounter) -> str:
    if encounter is None:
        return ""
    ids = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{_esc(identifier.value)}"/>'
        for identifier in (encounter.identifier or [])
    )
    class_code = "AMB"
    if encounter.class_fhir and encounter.class_fhir.code in RECOGNIZED_ENCOUNTER_CLASSES:
        class_code = encounter.class_fhir.code
    effective_time = ""
    if encounter.period:
        low = f'<low value="{format_hl7_ts(encounter.period.start)}"/>' if encounter.period.start else ""
        high = f'<high value="{format_hl7_ts(encounter.period.end)}"/>' if encounter.period.end else ""
        if low or high:
            effective_time = f"<effectiveTime>{low}{high}</effectiveTime>"
    return (
        "<componentOf><encompassingEncounter>"
        f'{ids}<code code="{class_code}" codeSystem="2.16.840.1.113883.5.4"/>{effective_time}'
        "</encompassingEncounter></componentOf>"
    )


def _reverse_code_system(system: str | None) -> str | None:
    """The reverse of app.cda.common.build_codeable_concept_from_cd's own
    OID -> FHIR-system resolution: an OID recognized in OID_TO_FHIR_SYSTEM
    reverses via the inverted table, a raw urn:oid:<root> (an OID the
    forward side didn't recognize) reverses by stripping the prefix back
    off, and CD_FALLBACK_SYSTEM (the forward side's own marker for "no
    codeSystem was present at all") reverses to no codeSystem attribute -
    None, not a fabricated placeholder, since faithfully reproducing
    "absent" is more honest than inventing an OID that never existed. Any
    other FHIR system (e.g. a Bundle built by hand with a system this app
    never produces) falls back to a disclosed placeholder OID, the same
    "can't recover, disclosed placeholder" precedent
    _reverse_identifier_root already established below."""
    if not system or system == CD_FALLBACK_SYSTEM:
        return None
    if system.startswith("urn:oid:"):
        return system[len("urn:oid:") :]
    return _FHIR_SYSTEM_TO_OID.get(system, _PLACEHOLDER_CODE_SYSTEM_OID)


def _build_cd_attrs(coding) -> str:
    """The code/codeSystem/displayName attribute string shared by both a
    <value xsi:type="CD"> (Problems) and a bare <code>/<routeCode> element
    (Medications) - the identical CD attribute shape reversed, just with a
    different wrapping tag. Promoted here once Medications became a second
    real consumer of the identical reversal Problems' own value-building
    had inlined - **catching a real, pre-existing round-trip bug in the
    process, not just deduplicating**: the inline version wrote
    `coding.system` (a FHIR system URL, e.g. "http://snomed.info/sct")
    directly into the CDA `codeSystem` attribute instead of reversing it
    back to an OID, so re-parsing the regenerated document produced
    `urn:oid:http://snomed.info/sct` - a garbage system on the second
    round trip - reproduced directly against `ccd_basic.xml` before this
    fix (`Condition.code.coding[0].system` went from `http://snomed.info/
    sct` to `urn:oid:http://snomed.info/sct` after only one reverse+forward
    cycle) and confirmed fixed after (`test_ccd_round_trip_preserves_
    coding_system_not_just_code`)."""
    code_system = _reverse_code_system(coding.system)
    code_system_attr = f' codeSystem="{code_system}"' if code_system else ""
    display = f' displayName="{_esc(coding.display)}"' if coding.display else ""
    return f'code="{_esc(coding.code)}"{code_system_attr}{display}'


def _build_cd_element(tag: str, concept, prefix_attrs: str = "") -> str:
    """A complete CD-shaped element from a whole `CodeableConcept`, rather
    than the bare attribute string `_build_cd_attrs` builds from a single
    `Coding` - the difference is `CodeableConcept.text`, which lives one
    level up from the coding and reverses to a nested `<originalText>`
    (the reverse of `app.cda.common.build_codeable_concept_from_cd`'s own
    originalText -> `.text` mapping). Emits a self-closing element when
    there's no `.text` to carry, so output is byte-identical to the
    pre-existing `f"<tag {_build_cd_attrs(...)}/>"` shape for every
    concept that doesn't have one.

    **Always regenerates the inline `<originalText>text</originalText>`
    shape, never the `<reference value="#ID"/>` one** - a disclosed,
    deliberate simplification, not an oversight: `CodeableConcept.text`
    is a plain string with no record of whether it was originally inline
    or de-referenced from a narrative anchor (the forward side's own
    `resolve_narrative_references` collapses both into the same field),
    and regenerating a reference would additionally require inventing a
    narrative anchor and a matching `<text>` block this reverse builder
    doesn't otherwise produce. The inline shape re-parses to the identical
    `.text` on the next forward pass, so this is round-trip stable - the
    same "recoverable content, not recoverable original shape" tradeoff
    `_build_narrative_section`'s own table-flattens-to-paragraphs
    simplification already discloses.

    `prefix_attrs` carries any attribute that must precede the CD
    attributes themselves - only `<value xsi:type="CD" ...>` needs it."""
    if concept is None or not concept.coding:
        return ""
    open_tag = f"<{tag} {prefix_attrs}{_build_cd_attrs(concept.coding[0])}"
    if concept.text:
        return f"{open_tag}><originalText>{_esc(concept.text)}</originalText></{tag}>"
    return f"{open_tag}/>"


def _build_problem_entry(condition, act_template_id: str = CONCERN_ACT_TEMPLATE_ID) -> str:
    """`act_template_id` defaults to Problems' own Concern Act, but is
    overridden by `_build_hospital_discharge_diagnosis_entry` below with
    the Hospital Discharge Diagnosis Act's templateId instead - both wrap
    the byte-for-byte identical Problem Observation entry, confirmed by the
    forward `app.cda.hospital_discharge_diagnosis` module's own docstring
    (verified against a real HL7 C-CDA-Examples guide example), so only the
    outer Act templateId genuinely differs between the two."""
    value = _build_cd_element("value", condition.code, 'xsi:type="CD" ') or '<value xsi:type="CD" nullFlavor="UNK"/>'
    act_status = "active"
    if condition.clinicalStatus and condition.clinicalStatus.coding:
        act_status = _CLINICAL_STATUS_TO_ACT_STATUS.get(condition.clinicalStatus.coding[0].code, "active")
    # A bare @value IVL_TS per date, rather than reconstructing a shared
    # low/high pair on one effectiveTime - onsetDateTime/abatementDateTime
    # are two independent FHIR fields with no guarantee both came from the
    # same source effectiveTime element in the first place (a document
    # could have been hand-edited, or come from a different pipeline
    # entirely), so treating them as two independent point-in-time facts
    # here is the more honest reverse mapping, not a corner cut.
    onset = f'<low value="{format_hl7_date(condition.onsetDateTime)}"/>' if condition.onsetDateTime else ""
    abatement = f'<high value="{format_hl7_date(condition.abatementDateTime)}"/>' if condition.abatementDateTime else ""
    effective_time = f"<effectiveTime>{onset}{abatement}</effectiveTime>" if (onset or abatement) else ""

    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{act_template_id}"/>'
        '<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/>'
        f'<statusCode code="{act_status}"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{PROBLEM_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="55607006" codeSystem="2.16.840.1.113883.6.96" displayName="Problem"/>'
        '<statusCode code="completed"/>'
        f"{effective_time}{value}"
        "</observation></entryRelationship></act></entry>"
    )


def _build_problems_section(conditions) -> str:
    if not conditions:
        return ""
    entries = "".join(_build_problem_entry(c) for c in conditions)
    return (
        f'<component><section><templateId root="{PROBLEMS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11450-4" codeSystem="2.16.840.1.113883.6.1" displayName="Problem List"/>'
        f"<title>Problems</title>{entries}</section></component>"
    )


def _is_hospital_discharge_diagnosis(condition) -> bool:
    """The one reliable, real signal (not a guess) distinguishing a
    Condition sourced from the Hospital Discharge Diagnosis section from
    one sourced from a plain Problems section - see
    app.cda.hospital_discharge_diagnosis's own module docstring: Problems
    never populates Condition.category at all, so any condition carrying
    this exact (system, code) pair unambiguously came from there."""
    return bool(
        condition.category
        and any(
            coding.system == DISCHARGE_DIAGNOSIS_CATEGORY_SYSTEM and coding.code == DISCHARGE_DIAGNOSIS_CATEGORY_CODE
            for category in condition.category
            for coding in (category.coding or [])
        )
    )


def _build_hospital_discharge_diagnosis_section(conditions) -> str:
    if not conditions:
        return ""
    entries = "".join(_build_problem_entry(c, act_template_id=HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID) for c in conditions)
    return (
        f'<component><section><templateId root="{HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11535-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital Discharge Diagnosis"/>'
        f"<title>Discharge Diagnosis</title>{entries}</section></component>"
    )


def _build_dosage_elements(request) -> str:
    """Reverses app.cda.medications._build_dosage field-for-field: route/
    doseQuantity/rateQuantity/effectiveTime bounds/free-text SIG. Structured
    dosing and free-text SIG are alternatives on the forward side, not
    mutually exclusive here - both are emitted whenever the source Dosage
    carries both, the same tolerance the forward parser itself has."""
    if not request.dosageInstruction:
        return ""
    dosage = request.dosageInstruction[0]

    route = ""
    if dosage.route and dosage.route.coding:
        route = _build_cd_element("routeCode", dosage.route)

    dose_quantity = ""
    rate_quantity = ""
    if dosage.doseAndRate:
        dose_and_rate = dosage.doseAndRate[0]
        if dose_and_rate.doseQuantity is not None:
            unit = f' unit="{_esc(dose_and_rate.doseQuantity.unit)}"' if dose_and_rate.doseQuantity.unit else ""
            dose_quantity = f'<doseQuantity value="{dose_and_rate.doseQuantity.value}"{unit}/>'
        if dose_and_rate.rateQuantity is not None:
            unit = f' unit="{_esc(dose_and_rate.rateQuantity.unit)}"' if dose_and_rate.rateQuantity.unit else ""
            rate_quantity = f'<rateQuantity value="{dose_and_rate.rateQuantity.value}"{unit}/>'

    effective_time = ""
    if dosage.timing and dosage.timing.repeat and dosage.timing.repeat.boundsPeriod:
        period = dosage.timing.repeat.boundsPeriod
        low = f'<low value="{format_hl7_ts(period.start)}"/>' if period.start else ""
        high = f'<high value="{format_hl7_ts(period.end)}"/>' if period.end else ""
        if low or high:
            effective_time = f"<effectiveTime>{low}{high}</effectiveTime>"

    free_text_sig = ""
    if dosage.patientInstruction:
        free_text_sig = (
            '<entryRelationship typeCode="COMP"><substanceAdministration classCode="SBADM" moodCode="EVN">'
            f'<templateId root="{FREE_TEXT_SIG_TEMPLATE_ID}"/>'
            f"<text>{_esc(dosage.patientInstruction)}</text>"
            "</substanceAdministration></entryRelationship>"
        )

    return f"{effective_time}{route}{dose_quantity}{rate_quantity}{free_text_sig}"


def _build_medication_entry(request, wrap_in_discharge_act: bool = False) -> str:
    consumable_code = _build_cd_element("code", request.medicationCodeableConcept) or '<code nullFlavor="UNK"/>'
    status_code = _MEDICATION_STATUS_TO_ACT_STATUS.get(request.status, _DEFAULT_MEDICATION_ACT_STATUS)
    mood_code = _INTENT_TO_MOOD_CODE.get(request.intent, _DEFAULT_MOOD_CODE)

    substance_administration = (
        f'<substanceAdministration classCode="SBADM" moodCode="{mood_code}">'
        f'<templateId root="{MEDICATION_ACTIVITY_TEMPLATE_ID}"/>'
        f'<statusCode code="{status_code}"/>'
        f"{_build_dosage_elements(request)}"
        "<consumable><manufacturedProduct><manufacturedMaterial>"
        f"{consumable_code}"
        "</manufacturedMaterial></manufacturedProduct></consumable>"
        "</substanceAdministration>"
    )
    if wrap_in_discharge_act:
        # Discharge Medications nests the identical Medication Activity
        # one level deeper, inside its own Act - unlike Hospital Discharge
        # Diagnosis, whose plain-Problems sibling ALSO has an outer act
        # (only the templateId differs there). So this is a wrap/don't-wrap
        # choice rather than a swapped templateId. Shape confirmed against
        # the real HL7 C-CDA-Examples guide example quoted in
        # app/cda/discharge_medications.py's own docstring.
        return (
            f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
            f'<templateId root="{DISCHARGE_MEDICATION_ACT_TEMPLATE_ID}"/>'
            '<code code="10183-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital discharge medication"/>'
            f'<statusCode code="{status_code}"/>'
            f'<entryRelationship typeCode="SUBJ">{substance_administration}</entryRelationship>'
            "</act></entry>"
        )
    return f'<entry typeCode="DRIV">{substance_administration}</entry>'


def _is_discharge_medication(request) -> bool:
    """MedicationRequest.category == "discharge" is the one reliable
    marker distinguishing a Discharge-Medications-sourced request from a
    plain-Medications one - see app.cda.discharge_medications' own module
    docstring: plain Medications never populates .category at all, so any
    request carrying this exact (system, code) pair unambiguously came
    from there. Exactly mirrors _is_hospital_discharge_diagnosis above."""
    return bool(
        request.category
        and any(
            coding.system == DISCHARGE_MEDICATION_CATEGORY_SYSTEM and coding.code == DISCHARGE_MEDICATION_CATEGORY_CODE
            for category in request.category
            for coding in (category.coding or [])
        )
    )


def _build_medications_section(requests) -> str:
    if not requests:
        return ""
    entries = "".join(_build_medication_entry(r) for r in requests)
    return (
        f'<component><section><templateId root="{MEDICATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="10160-0" codeSystem="2.16.840.1.113883.6.1" displayName="History of medication use"/>'
        f"<title>Medications</title>{entries}</section></component>"
    )


def _build_discharge_medications_section(requests) -> str:
    if not requests:
        return ""
    entries = "".join(_build_medication_entry(r, wrap_in_discharge_act=True) for r in requests)
    return (
        f'<component><section><templateId root="{DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="10183-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital discharge medications"/>'
        f"<title>Discharge Medications</title>{entries}</section></component>"
    )


def _reverse_allergy_value_code(allergy) -> str | None:
    """Reverse of TYPE_MAP/CATEGORY_MAP - both keyed off the identical
    source SNOMED code, so this searches for a source code whose own
    (type, category) pair best matches the AllergyIntolerance's own
    values, in three passes of decreasing precision: an exact (type,
    category) match, then type-only, then category-only, then None (a
    genuinely irrecoverable combination, the same "no signal left to
    reverse from" outcome as every other best-effort reverse mapping in
    this app) - <value> is omitted entirely in that last case rather than
    guessing at a source code that was never really there."""
    category = allergy.category[0] if allergy.category else None
    for code, type_, cat in _ALLERGY_VALUE_CANDIDATES:
        if type_ == allergy.type and cat == category:
            return code
    for code, type_, _cat in _ALLERGY_VALUE_CANDIDATES:
        if allergy.type and type_ == allergy.type:
            return code
    for code, _type_, cat in _ALLERGY_VALUE_CANDIDATES:
        if category and cat == category:
            return code
    return None


def _build_allergy_status_observation(allergy) -> str:
    status_code = allergy.clinicalStatus.coding[0].code if allergy.clinicalStatus and allergy.clinicalStatus.coding else None
    value_code = _ALLERGY_CLINICAL_STATUS_TO_STATUS_OBSERVATION_VALUE.get(status_code) if status_code else None
    if not value_code:
        return ""
    return (
        '<entryRelationship typeCode="REFR"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="33999-4" codeSystem="2.16.840.1.113883.6.1" displayName="Status"/>'
        f'<value xsi:type="CD" code="{value_code}" codeSystem="2.16.840.1.113883.6.96"/>'
        "</observation></entryRelationship>"
    )


def _build_criticality_observation(allergy) -> str:
    criticality_code = _CRITICALITY_TO_HL7_CODE.get(allergy.criticality) if allergy.criticality else None
    if not criticality_code:
        return ""
    return (
        '<entryRelationship typeCode="SUBJ" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{CRITICALITY_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="82606-5" codeSystem="2.16.840.1.113883.6.1" displayName="Criticality"/>'
        f'<value xsi:type="CD" code="{criticality_code}" codeSystem="2.16.840.1.113883.5.1063"/>'
        "</observation></entryRelationship>"
    )


def _build_reaction_observation(reaction) -> str:
    manifestation = reaction.manifestation[0] if reaction.manifestation else None
    value = _build_cd_element("value", manifestation, 'xsi:type="CD" ')
    onset = f'<effectiveTime><low value="{format_hl7_date(reaction.onset)}"/></effectiveTime>' if reaction.onset else ""
    severity_code = _SEVERITY_TO_SNOMED.get(reaction.severity) if reaction.severity else None
    severity = (
        (
            '<entryRelationship typeCode="SUBJ" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{SEVERITY_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="SEV" codeSystem="2.16.840.1.113883.5.4"/>'
            f'<value xsi:type="CD" code="{severity_code}" codeSystem="2.16.840.1.113883.6.96"/>'
            "</observation></entryRelationship>"
        )
        if severity_code
        else ""
    )
    return (
        '<entryRelationship typeCode="MFST" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{REACTION_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/>'
        f"{onset}{value}{severity}"
        "</observation></entryRelationship>"
    )


def _build_allergen_participant(allergy) -> str:
    code_element = _build_cd_element("code", allergy.code)
    if not code_element:
        return ""
    return (
        '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
        f"{code_element}"
        "</playingEntity></participantRole></participant>"
    )


def _build_allergy_entry(allergy) -> str:
    # Empty code.coding is how a negated allergy is detected: the forward
    # _resolve_allergen_code never returns a coding for one, only text.
    # Lossy, and disclosed: an allergen that survived as text ("No known
    # allergy to Penicillin") cannot be recovered as a code, so it degrades
    # to the generic "No known allergies" shape from here on.
    negated = not (allergy.code and allergy.code.coding)
    negation_attr = ' negationInd="true"' if negated else ""
    participant = "" if negated else _build_allergen_participant(allergy)

    value_code = _reverse_allergy_value_code(allergy)
    value = (
        f'<value xsi:type="CD" code="{value_code}" codeSystem="2.16.840.1.113883.6.96"/>'
        if value_code
        else '<value xsi:type="CD" nullFlavor="UNK"/>'
    )

    onset = f'<effectiveTime><low value="{format_hl7_date(allergy.onsetDateTime)}"/></effectiveTime>' if allergy.onsetDateTime else ""
    author = (
        f'<author><time value="{format_hl7_ts(allergy.recordedDate)}"/></author>' if allergy.recordedDate else ""
    )
    reactions = "".join(_build_reaction_observation(r) for r in (allergy.reaction or []))

    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{ALLERGY_CONCERN_ACT_TEMPLATE_ID}"/>'
        '<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/>'
        '<statusCode code="active"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN"{negation_attr}>'
        f'<templateId root="{ALLERGY_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/>'
        f"{onset}{value}{author}{participant}"
        f"{_build_allergy_status_observation(allergy)}{_build_criticality_observation(allergy)}{reactions}"
        "</observation></entryRelationship></act></entry>"
    )


def _build_allergies_section(allergies) -> str:
    if not allergies:
        return ""
    entries = "".join(_build_allergy_entry(a) for a in allergies)
    return (
        f'<component><section><templateId root="{ALLERGIES_SECTION_TEMPLATE_ID}"/>'
        '<code code="48765-2" codeSystem="2.16.840.1.113883.6.1" displayName="Allergies and adverse reactions"/>'
        f"<title>Allergies</title>{entries}</section></component>"
    )


# Reverse of app.cda.immunizations.STATUS_MAP. "completed"/"entered-in-
# error" invert cleanly; "not-done" is many-to-one (six statusCode values
# plus negationInd all collapse to it), so it reverses via
# negationInd="true" - the forward side's primary signal, checked before
# the table is consulted at all. The "aborted" statusCode emitted
# alongside is inert on the next forward pass, and there for realism.
_IMMUNIZATION_STATUS_TO_ACT_STATUS = {"completed": "completed", "entered-in-error": "nullified"}
_DEFAULT_IMMUNIZATION_ACT_STATUS = "aborted"


def _build_immunization_entry(immunization) -> str:
    consumable_code = _build_cd_element("code", immunization.vaccineCode) or '<code nullFlavor="UNK"/>'
    lot_number = f"<lotNumberText>{_esc(immunization.lotNumber)}</lotNumberText>" if immunization.lotNumber else ""

    act_status = _IMMUNIZATION_STATUS_TO_ACT_STATUS.get(immunization.status, _DEFAULT_IMMUNIZATION_ACT_STATUS)
    negation_attr = ' negationInd="true"' if immunization.status == "not-done" else ""

    # occurrenceString == "Unknown" is itself this builder's own forward
    # side's disclosed fallback for "no effectiveTime resolved" - omitting
    # <effectiveTime> here rather than trying to encode "Unknown" as a
    # real HL7 date lets the next forward pass regenerate the identical
    # fallback naturally, rather than fabricating a fake timestamp.
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(immunization.occurrenceDateTime)}"/>'
        if immunization.occurrenceDateTime
        else ""
    )

    route = ""
    if immunization.route and immunization.route.coding:
        route = _build_cd_element("routeCode", immunization.route)

    dose_quantity = ""
    if immunization.doseQuantity is not None:
        unit = f' unit="{_esc(immunization.doseQuantity.unit)}"' if immunization.doseQuantity.unit else ""
        dose_quantity = f'<doseQuantity value="{immunization.doseQuantity.value}"{unit}/>'

    return (
        f'<entry typeCode="DRIV"><substanceAdministration classCode="SBADM" moodCode="EVN"{negation_attr}>'
        f'<templateId root="{IMMUNIZATION_ACTIVITY_TEMPLATE_ID}"/>'
        f'<statusCode code="{act_status}"/>'
        f"{effective_time}{route}{dose_quantity}"
        "<consumable><manufacturedProduct><manufacturedMaterial>"
        f"{consumable_code}{lot_number}"
        "</manufacturedMaterial></manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
    )


def _build_immunizations_section(immunizations) -> str:
    if not immunizations:
        return ""
    entries = "".join(_build_immunization_entry(i) for i in immunizations)
    return (
        f'<component><section><templateId root="{IMMUNIZATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11369-6" codeSystem="2.16.840.1.113883.6.1" displayName="History of immunizations"/>'
        f"<title>Immunizations</title>{entries}</section></component>"
    )


def _is_vital_signs_panel(observation) -> bool:
    """The fixed LOINC 85353-1 "Vital Signs Panel" code - not merely
    "has .hasMember" - is what tells a Vital Signs panel Observation apart
    from an individual vital-sign Observation within one flat
    Bundle.entry list, the reverse of the forward mapper's own fixed panel
    code assignment (see app.cda.vitals.PANEL_CODE)."""
    return bool(observation.code and observation.code.coding and observation.code.coding[0].code == VITALS_PANEL_CODE)


def _build_vital_sign_observation_element(observation) -> str:
    code_element = _build_cd_element("code", observation.code) or '<code nullFlavor="UNK"/>'
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(observation.effectiveDateTime)}"/>'
        if observation.effectiveDateTime
        else ""
    )
    value = ""
    if observation.valueQuantity is not None:
        unit = f' unit="{_esc(observation.valueQuantity.unit)}"' if observation.valueQuantity.unit else ""
        value = f'<value xsi:type="PQ" value="{observation.valueQuantity.value}"{unit}/>'
    interpretation = ""
    if observation.interpretation and observation.interpretation[0].coding:
        interpretation = _build_cd_element("interpretationCode", observation.interpretation[0])
    method = ""
    if observation.method and observation.method.coding:
        method = _build_cd_element("methodCode", observation.method)
    body_site = ""
    if observation.bodySite and observation.bodySite.coding:
        body_site = _build_cd_element("targetSiteCode", observation.bodySite)

    return (
        '<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{VITALS_OBSERVATION_TEMPLATE_ID}"/>'
        '<statusCode code="completed"/>'
        f"{code_element}{effective_time}{value}{interpretation}{method}{body_site}"
        "</observation></component>"
    )


def _reverse_bp_panel_elements(panel) -> str:
    """Reverse of app.cda.vitals._build_blood_pressure_panel - a Blood
    Pressure Panel Observation has no independent existence on the CDA
    side (it's a grouping of two ordinary Vital Sign Observations, not a
    genuinely distinct source shape), so this expands its own two
    required `.component` entries back into two flat `<observation>`
    elements - one per component, using that component's own real
    code+value - rather than the single element every other panel member
    reverses to. **A disclosed round-trip gap, not a bug**: the forward
    builder never records an `effectiveDateTime` on either the panel or
    its components (see that function's own docstring), so neither
    reconstructed observation gets an `<effectiveTime>` either - the same
    "regenerate what was actually kept, don't fabricate a fake timestamp"
    precedent this app's every other lossy reversal already establishes."""
    elements = []
    for component in panel.component or []:
        code_element = _build_cd_element("code", component.code) or '<code nullFlavor="UNK"/>'
        value = ""
        if component.valueQuantity is not None:
            unit = f' unit="{_esc(component.valueQuantity.unit)}"' if component.valueQuantity.unit else ""
            value = f'<value xsi:type="PQ" value="{component.valueQuantity.value}"{unit}/>'
        elements.append(
            '<component><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{VITALS_OBSERVATION_TEMPLATE_ID}"/>'
            '<statusCode code="completed"/>'
            f"{code_element}{value}"
            "</observation></component>"
        )
    return "".join(elements)


def _reverse_pulse_ox_panel_elements(panel) -> str:
    """Reverse of app.cda.vitals._build_pulse_oximetry_panel - the O2
    saturation reading itself becomes the panel on the forward side, so
    this reverses it back into one flat `<observation>` (reusing
    `_build_vital_sign_observation_element` via a shallow `model_copy`
    that swaps in just the primary reading's own single coding - "59408-5",
    the disclosed representative regenerated regardless of which one or
    both of the two IG-documented synonymous codings the panel's own
    `.code` carries, since the source can never be recovered from the FHIR
    side alone, the same "pick one, don't guess which the source really
    used" precedent this app's every other many-to-one reversal already
    establishes), plus one further flat `<observation>` per `.component`
    (concentration/flow rate) - each built with **only** its own code and
    value, since the forward builder never carries the panel's own
    effectiveDateTime/interpretation/method/bodySite onto those specific
    sibling readings either (see that function's own docstring)."""
    primary_coding = next(
        (c for c in (panel.code.coding or []) if c.code in PULSE_OX_PRIMARY_CODES),
        panel.code.coding[0] if panel.code and panel.code.coding else None,
    )
    primary_code = CodeableConcept(coding=[primary_coding]) if primary_coding else None
    primary_as_flat = panel.model_copy(update={"code": primary_code, "component": None})
    elements = [_build_vital_sign_observation_element(primary_as_flat)]
    for component in panel.component or []:
        component_as_flat = panel.model_copy(
            update={
                "code": component.code,
                "component": None,
                "valueQuantity": component.valueQuantity,
                "interpretation": None,
                "method": None,
                "bodySite": None,
            }
        )
        elements.append(_build_vital_sign_observation_element(component_as_flat))
    return "".join(elements)


def _build_vital_signs_member_elements(member) -> str:
    """Dispatches a Vital Signs Panel's own `.hasMember` entry to the
    right reverse builder - a plain flat Vital Sign Observation, a Blood
    Pressure Panel (own `.component`, no top-level value - reverses to
    TWO flat observations), or a Pulse Oximetry Panel (own top-level
    value, optional `.component` - reverses to one-plus-N flat
    observations). Detection mirrors the forward side's own detection
    exactly (fixed codes, not structural guessing)."""
    if member.code and member.code.coding and member.code.coding[0].code == BP_PANEL_CODE:
        return _reverse_bp_panel_elements(member)
    if member.code and any(c.code in PULSE_OX_PRIMARY_CODES for c in (member.code.coding or [])):
        return _reverse_pulse_ox_panel_elements(member)
    return _build_vital_sign_observation_element(member)


def _build_vital_signs_organizer(panel, members_by_id: dict) -> str:
    member_ids = [ref.reference.removeprefix("urn:uuid:") for ref in (panel.hasMember or [])]
    member_elements = "".join(
        _build_vital_signs_member_elements(members_by_id[member_id])
        for member_id in member_ids
        if member_id in members_by_id
    )
    if not member_elements:
        return ""
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(panel.effectiveDateTime)}"/>' if panel.effectiveDateTime else ""
    )
    return (
        f'<entry typeCode="DRIV"><organizer classCode="CLUSTER" moodCode="EVN">'
        f'<templateId root="{VITALS_ORGANIZER_TEMPLATE_ID}"/>'
        '<code code="46680005" codeSystem="2.16.840.1.113883.6.96" displayName="Vital signs"/>'
        f'<statusCode code="completed"/>{effective_time}{member_elements}'
        "</organizer></entry>"
    )


def _build_vitals_section(observations) -> str:
    panels = [o for o in observations if _is_vital_signs_panel(o)]
    if not panels:
        return ""
    members_by_id = {o.id: o for o in observations if not _is_vital_signs_panel(o)}
    entries = "".join(entry for panel in panels if (entry := _build_vital_signs_organizer(panel, members_by_id)))
    if not entries:
        return ""
    return (
        f'<component><section><templateId root="{VITALS_SECTION_TEMPLATE_ID}"/>'
        '<code code="8716-3" codeSystem="2.16.840.1.113883.6.1" displayName="Vital Signs"/>'
        f"<title>Vital Signs</title>{entries}</section></component>"
    )


# Reverse of app.cda.results.STATUS_MAP - genuinely many-to-one on the
# forward side ("registered" alone has three source candidates: active/
# held/suspended). Reversed to one disclosed representative per target
# value - "active" for "registered" (a freshly-registered, not-yet-
# actioned order is the more natural real-world default than "held"/
# "suspended"), "cancelled" for "cancelled" (an exact, unambiguous match),
# "completed" for "final" - rather than whatever a naive dict-comprehension
# inversion's key-ordering would pick arbitrarily, the same deliberate-
# disclosure discipline app.transform.hl7_mdm's own status reversal
# already established. "unknown" (the forward side's own fallback for an
# unrecognized/absent statusCode) has no real source to recover either,
# so it shares the same "active" default.
_RESULT_STATUS_TO_ACT_STATUS = {"registered": "active", "cancelled": "cancelled", "final": "completed"}
_DEFAULT_RESULT_ACT_STATUS = "active"


def _build_result_value_element(observation) -> str:
    """Reverses whichever Observation.value[x] choice
    app.cda.results._build_observation_value populated. **A genuine
    non-issue, not a gap**: PQ and REAL both parse identically on the
    forward side (both branches call the identical build_quantity_from_pq
    with no REAL-specific behavior), so this always emits xsi:type="PQ"
    regardless of whether the original was PQ or REAL - the choice is
    provably inert for round-trip correctness, not a corner cut.

    **`valueQuantity` with a `comparator`** reverses back to a single-bound
    `IVL_PQ` (`<high>` for `<=`/`<`, `<low>` for `>=`/`>`, `@inclusive`
    omitted for the inclusive pair and set to `"false"` for the exclusive
    one) - the exact inverse of `_build_ivl_pq_value`'s own forward
    assignment, per `mappingGuidance.md`'s own "Ranges of Physical
    Quantities" section. **`valueRange`** (both bounds originally present)
    reverses to a two-bound `IVL_PQ` directly. **`valueString`** always
    reverses to `xsi:type="ST"`, never `"ED"` - a disclosed, permanent
    round-trip simplification (the forward side already collapses ED's
    own plain-text case into the identical `valueString` field ST uses,
    with no FHIR-side marker distinguishing which one the source
    originally was)."""
    if observation.valueQuantity is not None:
        quantity = observation.valueQuantity
        unit = f' unit="{_esc(quantity.unit)}"' if quantity.unit else ""
        if quantity.comparator:
            bound_tag = "high" if quantity.comparator in ("<=", "<") else "low"
            inclusive_attr = "" if quantity.comparator in ("<=", ">=") else ' inclusive="false"'
            return f'<value xsi:type="IVL_PQ"><{bound_tag} value="{quantity.value}"{unit}{inclusive_attr}/></value>'
        return f'<value xsi:type="PQ" value="{quantity.value}"{unit}/>'
    if observation.valueRange is not None:
        range_value = observation.valueRange
        low = ""
        if range_value.low is not None:
            low_unit = f' unit="{_esc(range_value.low.unit)}"' if range_value.low.unit else ""
            low = f'<low value="{range_value.low.value}"{low_unit}/>'
        high = ""
        if range_value.high is not None:
            high_unit = f' unit="{_esc(range_value.high.unit)}"' if range_value.high.unit else ""
            high = f'<high value="{range_value.high.value}"{high_unit}/>'
        return f'<value xsi:type="IVL_PQ">{low}{high}</value>'
    if observation.valueCodeableConcept is not None and observation.valueCodeableConcept.coding:
        return _build_cd_element("value", observation.valueCodeableConcept, 'xsi:type="CD" ')
    if observation.valueInteger is not None:
        return f'<value xsi:type="INT" value="{observation.valueInteger}"/>'
    if observation.valueString is not None:
        return f'<value xsi:type="ST">{_esc(observation.valueString)}</value>'
    return ""


def _reverse_specimen_elements(specimen) -> tuple[str, str]:
    """Reverse of app.cda.results._build_specimen/_apply_collection_body_site
    - specimenPlayingEntity/code (falling back to a text-only /name when
    no code resolved, the same "one disclosed representative shape when
    two source shapes could produce the identical FHIR field" precedent
    every other reversal in this app already establishes for a many-to-one
    forward mapping), /quantity, /desc. `.collection.bodySite` reverses to
    a **separate** sibling Specimen Collection Procedure component (the
    fixed SNOMED `17636008` code) - it lives at a different nesting depth
    than <specimen> itself (a sibling <component> of the organizer, not
    nested inside <specimen>), so this returns both pieces as a tuple
    rather than one combined string, letting the caller place each at its
    own real position."""
    identifiers = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{_esc(identifier.value)}"/>'
        for identifier in (specimen.identifier or [])
    )
    code_element = ""
    name_element = ""
    if specimen.type and specimen.type.coding:
        code_element = _build_cd_element("code", specimen.type)
    elif specimen.type and specimen.type.text:
        name_element = f"<name>{_esc(specimen.type.text)}</name>"
    quantity_element = ""
    if specimen.collection and specimen.collection.quantity is not None:
        quantity = specimen.collection.quantity
        unit = f' unit="{_esc(quantity.unit)}"' if quantity.unit else ""
        quantity_element = f'<quantity value="{quantity.value}"{unit}/>'
    desc_element = f"<desc>{_esc(specimen.note[0].text)}</desc>" if specimen.note else ""
    specimen_element = (
        '<specimen typeCode="SPC"><specimenRole classCode="SPEC">'
        f"{identifiers}"
        f'<specimenPlayingEntity classCode="ENT">{code_element}{name_element}{quantity_element}{desc_element}</specimenPlayingEntity>'
        "</specimenRole></specimen>"
    )
    collection_procedure_element = ""
    if specimen.collection and specimen.collection.bodySite:
        body_site_element = _build_cd_element("targetSiteCode", specimen.collection.bodySite)
        if body_site_element:
            collection_procedure_element = (
                '<component><procedure classCode="PROC" moodCode="EVN">'
                f'<code code="{SPECIMEN_COLLECTION_PROCEDURE_CODE}" codeSystem="{SPECIMEN_COLLECTION_PROCEDURE_CODE_SYSTEM}" displayName="Specimen collection"/>'
                f"{body_site_element}"
                "</procedure></component>"
            )
    return specimen_element, collection_procedure_element


def _build_reference_range_element(observation) -> str:
    if not observation.referenceRange:
        return ""
    reference_range = observation.referenceRange[0]
    low = f'<low value="{reference_range.low.value}" unit="{_esc(reference_range.low.unit or "")}"/>' if reference_range.low else ""
    high = f'<high value="{reference_range.high.value}" unit="{_esc(reference_range.high.unit or "")}"/>' if reference_range.high else ""
    if not low and not high:
        return ""
    return (
        '<referenceRange><observationRange><value xsi:type="IVL_PQ">'
        f"{low}{high}"
        "</value></observationRange></referenceRange>"
    )


def _build_result_observation_element(
    observation, specimens_by_id: dict | None = None, organizer_specimen_id: str | None = None
) -> str:
    code_element = _build_cd_element("code", observation.code) or '<code nullFlavor="UNK"/>'
    act_status = _RESULT_STATUS_TO_ACT_STATUS.get(observation.status, _DEFAULT_RESULT_ACT_STATUS)
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(observation.effectiveDateTime)}"/>'
        if observation.effectiveDateTime
        else ""
    )
    value = _build_result_value_element(observation)
    interpretation = ""
    if observation.interpretation and observation.interpretation[0].coding:
        interpretation = _build_cd_element("interpretationCode", observation.interpretation[0])
    method = ""
    if observation.method and observation.method.coding:
        method = _build_cd_element("methodCode", observation.method)
    body_site = ""
    if observation.bodySite and observation.bodySite.coding:
        body_site = _build_cd_element("targetSiteCode", observation.bodySite)
    reference_range = _build_reference_range_element(observation)

    # An observation-level <specimen> overrides the organizer-level
    # default for this one Observation, per CF-results.md's own attachment
    # rule (mirrored by the forward _build_result_observation's own
    # own_specimen/default_specimen_id split). Only regenerated when this
    # observation's own specimen genuinely differs from the organizer's -
    # a reference equal to the organizer default came from propagation,
    # not its own <specimen> child, so re-emitting it would fabricate an
    # override the source document never had. No Specimen Collection
    # Procedure sibling is emitted here: the forward parser only ever
    # looks for one at organizer level (_find_specimen_collection_
    # procedure scans the organizer's own component children), so one
    # nested at observation level would simply never be read back.
    own_specimen_element = ""
    if observation.specimen and observation.specimen.reference and specimens_by_id:
        own_specimen_id = observation.specimen.reference.removeprefix("urn:uuid:")
        if own_specimen_id != organizer_specimen_id:
            specimen = specimens_by_id.get(own_specimen_id)
            if specimen is not None:
                own_specimen_element, _ = _reverse_specimen_elements(specimen)

    return (
        '<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{RESULTS_OBSERVATION_TEMPLATE_ID}"/>'
        f'<statusCode code="{act_status}"/>'
        f"{code_element}{effective_time}{value}{interpretation}{method}{body_site}{reference_range}{own_specimen_element}"
        "</observation></component>"
    )


def _build_result_organizer(report, observations_by_id: dict, specimens_by_id: dict) -> str:
    # DiagnosticReport.specimen[0] is the organizer-level Specimen the
    # forward builder propagates as the default onto every result
    # Observation. Resolved before the members are built so each one can
    # tell its own genuine observation-level override apart from a
    # propagated default (see _build_result_observation_element).
    specimen_element = ""
    collection_procedure_element = ""
    organizer_specimen_id = None
    if report.specimen and report.specimen[0].reference:
        organizer_specimen_id = report.specimen[0].reference.removeprefix("urn:uuid:")
        specimen = specimens_by_id.get(organizer_specimen_id)
        if specimen is not None:
            specimen_element, collection_procedure_element = _reverse_specimen_elements(specimen)

    result_ids = [ref.reference.removeprefix("urn:uuid:") for ref in (report.result or [])]
    member_elements = "".join(
        _build_result_observation_element(observations_by_id[result_id], specimens_by_id, organizer_specimen_id)
        for result_id in result_ids
        if result_id in observations_by_id
    )
    if not member_elements:
        return ""
    organizer_code = _build_cd_element("code", report.code) or '<code nullFlavor="UNK"/>'
    act_status = _RESULT_STATUS_TO_ACT_STATUS.get(report.status, _DEFAULT_RESULT_ACT_STATUS)
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(report.effectiveDateTime)}"/>' if report.effectiveDateTime else ""
    )
    return (
        f'<entry typeCode="DRIV"><organizer classCode="BATTERY" moodCode="EVN">'
        f'<templateId root="{RESULTS_ORGANIZER_TEMPLATE_ID}"/>'
        f"{organizer_code}"
        f'<statusCode code="{act_status}"/>{effective_time}{specimen_element}{member_elements}{collection_procedure_element}'
        "</organizer></entry>"
    )


def _build_results_section(reports, observations_by_id: dict, specimens_by_id: dict) -> str:
    entries = "".join(
        entry for report in reports if (entry := _build_result_organizer(report, observations_by_id, specimens_by_id))
    )
    if not entries:
        return ""
    return (
        f'<component><section><templateId root="{RESULTS_SECTION_TEMPLATE_ID}"/>'
        '<code code="30954-2" codeSystem="2.16.840.1.113883.6.1" displayName="Relevant diagnostic tests and/or laboratory data"/>'
        f"<title>Results</title>{entries}</section></component>"
    )


# Reverse of app.cda.procedures.STATUS_MAP - a genuine bijection, so it
# inverts directly. "not-done" has a second forward source, negationInd,
# indistinguishable from statusCode="cancelled" on the FHIR side; this
# always regenerates the latter. "unknown" reverses by omitting
# <statusCode> rather than fabricating a code.
_PROCEDURE_STATUS_TO_ACT_STATUS = {v: k for k, v in PROCEDURE_STATUS_MAP.items()}


def _reverse_generic_identifier(identifier) -> str:
    """Reverse of app.cda.common.build_identifier's own three-shape
    resolution (root+extension, root-only, fallback-system-with-no-root) -
    Procedures is this app's first reverse consumer of that function's
    full three-way shape (patient/encounter identifiers only ever reverse
    the simpler root+extension shape via the narrower
    _reverse_identifier_root above, which always has a real extension to
    work with)."""
    if identifier.system == "urn:ietf:rfc:3986" and identifier.value and identifier.value.startswith("urn:oid:"):
        # Root-only shape: build_identifier stashed "urn:oid:<root>" as
        # the Identifier.value itself, not its system, when no @extension
        # was present on the source <id>.
        root = identifier.value[len("urn:oid:") :]
        return f'<id root="{root}"/>'
    if identifier.system and identifier.system.startswith("urn:oid:"):
        root = identifier.system[len("urn:oid:") :]
        return f'<id root="{root}" extension="{_esc(identifier.value)}"/>'
    return f'<id extension="{_esc(identifier.value or "")}"/>'


def _reverse_address_element(address) -> str:
    """Reverse of app.cda.procedures._build_address - shared by both a
    performer's own Location (referenced via PractitionerRole.location)
    and a Service Delivery Location, the same two CDA-side consumers
    app.cda.procedures._build_address itself already serves on the
    forward side."""
    lines = "".join(f"<streetAddressLine>{_esc(line)}</streetAddressLine>" for line in (address.line or []))
    city = f"<city>{_esc(address.city)}</city>" if address.city else ""
    state = f"<state>{_esc(address.state)}</state>" if address.state else ""
    postal_code = f"<postalCode>{_esc(address.postalCode)}</postalCode>" if address.postalCode else ""
    country = f"<country>{_esc(address.country)}</country>" if address.country else ""
    return f"<addr>{lines}{city}{state}{postal_code}{country}</addr>"


def _reverse_practitioner_id_and_name(practitioner) -> str:
    """Reverse of app.cda.procedures._build_practitioner_from_assigned_
    entity - practitioner.identifier/.name -> <id>/<assignedPerson><name>,
    the inner content shared by every assignedEntity/assignedAuthor shape
    this app's own forward side builds identically (a performer's own
    assignedEntity, an author's own assignedAuthor - see that module's own
    docstring for why both reuse the identical builder). Returns the inner
    content only, not a wrapping tag, since performer/recorder/comment-
    author each wrap it in a different outer element. Extracted here once
    recorder and the Comment Activity's own nested author became this
    app's second and third real consumers of the identical reversal
    _reverse_performer_element's own assigned_person block already did
    inline."""
    ids = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{_esc(identifier.value)}"/>'
        for identifier in (practitioner.identifier or [])
    )
    assigned_person = ""
    if practitioner.name:
        name = practitioner.name[0]
        family = f"<family>{_esc(name.family)}</family>" if name.family else ""
        given = "".join(f"<given>{_esc(g)}</given>" for g in (name.given or []))
        assigned_person = f"<assignedPerson><name>{given}{family}</name></assignedPerson>"
    return f"{ids}{assigned_person}"


def _reverse_performer_element(
    performer, practitioner_roles_by_id: dict, practitioners_by_id: dict, organizations_by_id: dict, locations_by_id: dict
) -> str:
    """Reverse of app.cda.procedures._build_performer - resolves the
    PractitionerRole's own chain of references (practitioner/organization/
    location) back into one <assignedEntity>, the CDA shape a
    PractitionerRole has no direct forward-side equivalent for (it's
    purely a wrapper this app's own forward mapper assembles, per the real
    Procedure.csv mapping table - see that function's own docstring) -
    the one place in this app's C-CDA reverse direction that walks two
    levels of Reference resolution to reconstruct a single source
    element. Returns "" (this performer is silently dropped) when the
    referenced PractitionerRole itself can't be resolved - defensive, not
    currently reachable against this app's own forward output."""
    role_id = performer.actor.reference.removeprefix("urn:uuid:") if performer.actor and performer.actor.reference else None
    role = practitioner_roles_by_id.get(role_id) if role_id else None
    if role is None:
        return ""

    # role.identifier is a copy of the underlying Practitioner's own
    # identifier (see _build_performer's own docstring), so id/name both
    # reverse from role.practitioner's own Practitioner, not role itself -
    # _reverse_practitioner_id_and_name's shared reversal covers both.
    id_and_name = ""
    if role.practitioner:
        practitioner_id = role.practitioner.reference.removeprefix("urn:uuid:")
        practitioner = practitioners_by_id.get(practitioner_id)
        if practitioner is not None:
            id_and_name = _reverse_practitioner_id_and_name(practitioner)

    addr = ""
    if role.location:
        location_id = role.location[0].reference.removeprefix("urn:uuid:")
        location = locations_by_id.get(location_id)
        if location is not None and location.address:
            addr = _reverse_address_element(location.address)

    telecom = ""
    if role.telecom:
        telecom = f'<telecom use="WP" value="tel:{_esc(role.telecom[0].value)}"/>'

    represented_organization = ""
    if role.organization:
        organization_id = role.organization.reference.removeprefix("urn:uuid:")
        organization = organizations_by_id.get(organization_id)
        if organization is not None and organization.name:
            represented_organization = f"<representedOrganization><name>{_esc(organization.name)}</name></representedOrganization>"

    return (
        '<performer typeCode="PRF"><assignedEntity>'
        f"{id_and_name}{addr}{telecom}{represented_organization}"
        "</assignedEntity></performer>"
    )


def _reverse_participant_location_element(procedure, locations_by_id: dict) -> str:
    """Reverse of app.cda.procedures._build_service_delivery_location -
    Procedure.location is a direct Reference, unrelated to a performer's
    own PractitionerRole machinery. Returns "" when Procedure.location is
    absent or the referenced Location can't be resolved."""
    if procedure.location is None or not procedure.location.reference:
        return ""
    location_id = procedure.location.reference.removeprefix("urn:uuid:")
    location = locations_by_id.get(location_id)
    if location is None:
        return ""

    code_element = ""
    if location.type and location.type[0].coding:
        code_element = _build_cd_element("code", location.type[0])
    addr = _reverse_address_element(location.address) if location.address else ""
    telecom = f'<telecom use="WP" value="tel:{_esc(location.telecom[0].value)}"/>' if location.telecom else ""
    name = f"<name>{_esc(location.name)}</name>" if location.name else ""

    return (
        '<participant typeCode="LOC"><participantRole classCode="SDLOC">'
        f'<templateId root="{SERVICE_DELIVERY_LOCATION_TEMPLATE_ID}"/>'
        f"{code_element}{addr}{telecom}"
        f'<playingEntity classCode="PLC">{name}</playingEntity>'
        "</participantRole></participant>"
    )


def _reverse_reason_codes(procedure) -> str:
    """Reverse of app.cda.procedures._build_reason_codes -
    Procedure.reasonCode[] -> one entryRelationship[typeCode=RSON]
    wrapping an Indication Observation per entry, each regenerating the
    fixed INDICATION_TEMPLATE_ID and the LOINC 75321-0 "Clinical finding"
    code the forward side's own real fetched example always carries on
    that nested observation."""
    entries = []
    for reason in procedure.reasonCode or []:
        if not reason.coding:
            continue
        entries.append(
            '<entryRelationship typeCode="RSON"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{INDICATION_TEMPLATE_ID}"/>'
            '<code code="75321-0" codeSystem="2.16.840.1.113883.6.1" displayName="Clinical finding"/>'
            + _build_cd_element("value", reason, 'xsi:type="CD" ')
            + "</observation></entryRelationship>"
        )
    return "".join(entries)


def _reverse_notes(procedure, practitioners_by_id: dict) -> str:
    """Reverse of app.cda.procedures._build_notes -
    Procedure.note[] -> one entryRelationship[typeCode=SUBJ,
    inversionInd=true] wrapping a Comment Activity act per entry, each
    regenerating the fixed COMMENT_ACTIVITY_TEMPLATE_ID/LOINC 48767-8
    code. authorReference reverses via the shared
    _reverse_practitioner_id_and_name (its own third real consumer,
    after performer and recorder below) wrapped in the identical
    Author Participation <author> shape the Comment Activity's own
    nested author uses on the forward side - a plain Practitioner
    reference, not a PractitionerRole, matching this app's own confirmed
    Annotation.authorReference binding (see app/cda/procedures.py's own
    docstring for why)."""
    entries = []
    for note in procedure.note or []:
        if not note.text:
            continue
        author = ""
        if note.authorReference and note.authorReference.reference:
            practitioner_id = note.authorReference.reference.removeprefix("urn:uuid:")
            practitioner = practitioners_by_id.get(practitioner_id)
            if practitioner is not None:
                time_element = f'<time value="{format_hl7_ts(note.time)}"/>' if note.time else ""
                author = (
                    f'<author><templateId root="{AUTHOR_PARTICIPATION_TEMPLATE_ID}"/>{time_element}'
                    f"<assignedAuthor>{_reverse_practitioner_id_and_name(practitioner)}</assignedAuthor>"
                    "</author>"
                )
        entries.append(
            '<entryRelationship typeCode="SUBJ" inversionInd="true"><act classCode="ACT" moodCode="EVN">'
            f'<templateId root="{COMMENT_ACTIVITY_TEMPLATE_ID}"/>'
            '<code code="48767-8" codeSystem="2.16.840.1.113883.6.1" displayName="Annotation Comment"/>'
            f"<text>{_esc(note.text)}</text>{author}"
            "</act></entryRelationship>"
        )
    return "".join(entries)


def _reverse_procedure_recorder(procedure, practitioners_by_id: dict) -> str:
    """Reverse of app.cda.procedures._build_procedure_recorder -
    Procedure.recorder -> a direct-child <author> (Author Participation),
    distinct from a Comment Activity's own nested one - both reuse the
    identical _reverse_practitioner_id_and_name reversal since the
    forward side builds both from the identical assignedAuthor shape."""
    if procedure.recorder is None or not procedure.recorder.reference:
        return ""
    practitioner_id = procedure.recorder.reference.removeprefix("urn:uuid:")
    practitioner = practitioners_by_id.get(practitioner_id)
    if practitioner is None:
        return ""
    return (
        f'<author><templateId root="{AUTHOR_PARTICIPATION_TEMPLATE_ID}"/>'
        f"<assignedAuthor>{_reverse_practitioner_id_and_name(practitioner)}</assignedAuthor>"
        "</author>"
    )


def _build_procedure_entry(
    procedure, practitioner_roles_by_id: dict, practitioners_by_id: dict, organizations_by_id: dict, locations_by_id: dict
) -> str:
    ids = "".join(_reverse_generic_identifier(i) for i in (procedure.identifier or []))

    code = ""
    if procedure.code and procedure.code.coding:
        code = _build_cd_element("code", procedure.code)

    status_value = _PROCEDURE_STATUS_TO_ACT_STATUS.get(procedure.status)
    status_element = f'<statusCode code="{status_value}"/>' if status_value else ""

    effective_time = ""
    if procedure.performedDateTime:
        effective_time = f'<effectiveTime value="{format_hl7_ts(procedure.performedDateTime)}"/>'
    elif procedure.performedPeriod:
        low = (
            f'<low value="{format_hl7_ts(procedure.performedPeriod.start)}"/>'
            if procedure.performedPeriod.start
            else ""
        )
        high = (
            f'<high value="{format_hl7_ts(procedure.performedPeriod.end)}"/>'
            if procedure.performedPeriod.end
            else ""
        )
        if low or high:
            effective_time = f"<effectiveTime>{low}{high}</effectiveTime>"

    body_site = ""
    if procedure.bodySite and procedure.bodySite[0].coding:
        body_site = _build_cd_element("targetSiteCode", procedure.bodySite[0])

    performers = "".join(
        _reverse_performer_element(p, practitioner_roles_by_id, practitioners_by_id, organizations_by_id, locations_by_id)
        for p in (procedure.performer or [])
    )
    participant = _reverse_participant_location_element(procedure, locations_by_id)
    author = _reverse_procedure_recorder(procedure, practitioners_by_id)
    reason_codes = _reverse_reason_codes(procedure)
    notes = _reverse_notes(procedure, practitioners_by_id)

    return (
        '<entry typeCode="DRIV"><procedure classCode="PROC" moodCode="EVN">'
        f'<templateId root="{PROCEDURE_TEMPLATE_ID}"/>'
        f"{ids}{code}{status_element}{effective_time}{body_site}{performers}{participant}{author}{reason_codes}{notes}"
        "</procedure></entry>"
    )


def _build_procedures_section(
    procedures, practitioner_roles_by_id: dict, practitioners_by_id: dict, organizations_by_id: dict, locations_by_id: dict
) -> str:
    if not procedures:
        return ""
    entries = "".join(
        _build_procedure_entry(p, practitioner_roles_by_id, practitioners_by_id, organizations_by_id, locations_by_id)
        for p in procedures
    )
    return (
        f'<component><section><templateId root="{PROCEDURES_SECTION_TEMPLATE_ID}"/>'
        '<code code="47519-4" codeSystem="2.16.840.1.113883.6.1" displayName="History of Procedures"/>'
        f"<title>Procedures</title>{entries}</section></component>"
    )


def _is_social_history_observation(observation) -> bool:
    return bool(
        observation.category
        and observation.category[0].coding
        and observation.category[0].coding[0].code == SOCIAL_HISTORY_CATEGORY_CODE
    )


def _build_social_history_value(observation) -> str:
    if observation.valueQuantity is not None:
        unit = f' unit="{_esc(observation.valueQuantity.unit)}"' if observation.valueQuantity.unit else ""
        return f'<value xsi:type="PQ" value="{observation.valueQuantity.value}"{unit}/>'
    if observation.valueCodeableConcept is not None and observation.valueCodeableConcept.coding:
        return _build_cd_element("value", observation.valueCodeableConcept, 'xsi:type="CD" ')
    if observation.valueInteger is not None:
        return f'<value xsi:type="INT" value="{observation.valueInteger}"/>'
    if observation.valueString is not None:
        return f'<value xsi:type="ST">{_esc(observation.valueString)}</value>'
    return ""


def _build_social_history_entry(observation) -> str:
    """Reverses app.cda.social_history._build_social_history_observation.
    Which of the two recognized templateIds to regenerate is resolved from
    the coding's own LOINC code (72166-2 -> the Smoking-Status-specific
    template, matching the forward mapper's own hardcoded code for that
    template) - not recoverable in general, but this one code is a
    reliable signal since the forward side only ever emits it from that
    specific template. .status/.category are never reversed - both are
    fixed, disclosed literals on the forward side (see that module's own
    docstring), so this builder regenerates the identical fixed statusCode
    the template itself requires, not a guess at recovering an
    unrecoverable source value."""
    coding = observation.code.coding[0] if observation.code and observation.code.coding else None
    if coding is None:
        return ""
    code_element = _build_cd_element("code", observation.code)
    template_id = SMOKING_STATUS_TEMPLATE_ID if coding.code == "72166-2" else SOCIAL_HISTORY_OBSERVATION_TEMPLATE_ID
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(observation.effectiveDateTime)}"/>'
        if observation.effectiveDateTime
        else ""
    )
    value = _build_social_history_value(observation)
    return (
        f'<entry><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{template_id}"/>'
        f"{code_element}"
        '<statusCode code="completed"/>'
        f"{effective_time}{value}"
        "</observation></entry>"
    )


def _build_social_history_entries(observations) -> str:
    return "".join(
        entry for o in observations if _is_social_history_observation(o) and (entry := _build_social_history_entry(o))
    )


def _build_family_history_condition(condition) -> str:
    value = ""
    if condition.code and condition.code.coding:
        value = _build_cd_element("value", condition.code, 'xsi:type="CD" ')
    age_relationship = ""
    if condition.onsetAge is not None:
        unit = _esc(condition.onsetAge.unit) if condition.onsetAge.unit else "a"
        age_relationship = (
            '<entryRelationship typeCode="SUBJ" inversionInd="true">'
            '<observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{AGE_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="445518008" codeSystem="2.16.840.1.113883.6.96" displayName="Age At Onset"/>'
            '<statusCode code="completed"/>'
            f'<value xsi:type="PQ" value="{condition.onsetAge.value}" unit="{unit}"/>'
            "</observation></entryRelationship>"
        )
    death_relationship = ""
    if condition.contributedToDeath:
        death_relationship = (
            '<entryRelationship typeCode="CAUS">'
            '<observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{DEATH_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/>'
            '<statusCode code="completed"/>'
            '<value xsi:type="CD" code="419099009" codeSystem="2.16.840.1.113883.6.96" displayName="Dead"/>'
            "</observation></entryRelationship>"
        )
    return (
        '<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{FAMILY_HISTORY_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="75323-6" codeSystem="2.16.840.1.113883.6.1" displayName="Condition"/>'
        '<statusCode code="completed"/>'
        f"{value}{death_relationship}{age_relationship}"
        "</observation></component>"
    )


def _build_family_history_entry(history) -> str:
    """Reverses app.cda.family_history._build_family_member_history.
    `.status` is never reversed (fixed "completed" per the template
    itself, the same treatment Social History's own statusCode gets).
    deceased[x]'s own reverse mirrors the forward choice-type resolution
    exactly: `.deceasedDate` (whichever precision it carries - format_hl7_
    date handles a partial-precision string or a full date object the
    identical way _format_partial_date's own forward output does) implies
    a real sdtc:deceasedTime plus sdtc:deceasedInd="true"; `.deceasedBoolean`
    alone reverses to sdtc:deceasedInd only, with no deceasedTime - the
    same two branches the forward resolver itself distinguishes."""
    components = "".join(_build_family_history_condition(c) for c in (history.condition or []))
    if not components:
        return ""
    relationship = ""
    if history.relationship and history.relationship.coding:
        relationship = _build_cd_element("code", history.relationship)
    gender = ""
    if history.sex and history.sex.coding:
        gender = _build_cd_element("administrativeGenderCode", history.sex)
    deceased_extension = ""
    if history.deceasedDate is not None:
        deceased_extension = (
            '<sdtc:deceasedInd value="true"/>'
            f'<sdtc:deceasedTime value="{format_hl7_date(history.deceasedDate)}"/>'
        )
    elif history.deceasedBoolean is not None:
        deceased_value = "true" if history.deceasedBoolean else "false"
        deceased_extension = f'<sdtc:deceasedInd value="{deceased_value}"/>'
    return (
        '<entry><organizer classCode="CLUSTER" moodCode="EVN">'
        f'<templateId root="{FAMILY_HISTORY_ORGANIZER_TEMPLATE_ID}"/>'
        '<statusCode code="completed"/>'
        '<subject><relatedSubject classCode="PRS" xmlns:sdtc="urn:hl7-org:sdtc">'
        f"{relationship}<subject>{gender}{deceased_extension}</subject>"
        "</relatedSubject></subject>"
        f"{components}"
        "</organizer></entry>"
    )


def _build_family_history_entries(histories) -> str:
    return "".join(entry for h in histories if (entry := _build_family_history_entry(h)))


def _build_care_plan_activity_entry(activity) -> str:
    """Reverses app.cda.plan_of_treatment._build_activity_detail.
    **A genuine, disclosed round-trip ambiguity, not an oversight**:
    CarePlanActivityDetail.kind is always "ServiceRequest" regardless of
    whether the source entry was a Planned Observation or a Planned
    Procedure (see that module's own docstring - both feed the identical
    fixed kind), so there is no FHIR-side signal telling the two apart -
    this builder always regenerates the confirmed-primary shape (Planned
    Observation), the same "pick one disclosed representative, don't
    guess which one a given resource really came from" precedent this
    app's every other many-to-one reverse mapping already establishes."""
    detail = activity.detail
    if detail is None or not detail.code or not detail.code.coding:
        return ""
    code_element = _build_cd_element("code", detail.code)
    status_value = _CARE_PLAN_ACTIVITY_STATUS_TO_ACT_STATUS.get(detail.status)
    status_element = f'<statusCode code="{status_value}"/>' if status_value else ""
    effective_time = ""
    if detail.scheduledPeriod is not None:
        low = f'<low value="{format_hl7_date(detail.scheduledPeriod.start)}"/>' if detail.scheduledPeriod.start else ""
        high = f'<high value="{format_hl7_date(detail.scheduledPeriod.end)}"/>' if detail.scheduledPeriod.end else ""
        if low or high:
            effective_time = f"<effectiveTime>{low}{high}</effectiveTime>"
    elif detail.scheduledString:
        effective_time = f'<effectiveTime value="{format_hl7_date(detail.scheduledString)}"/>'
    return (
        '<entry><observation classCode="OBS" moodCode="RQO">'
        f'<templateId root="{PLANNED_OBSERVATION_TEMPLATE_ID}"/>'
        f"{code_element}{status_element}{effective_time}"
        "</observation></entry>"
    )


def _build_plan_of_treatment_entries(care_plans) -> str:
    entries = []
    for care_plan in care_plans:
        for activity in care_plan.activity or []:
            entry = _build_care_plan_activity_entry(activity)
            if entry:
                entries.append(entry)
    return "".join(entries)


def _build_narrative_section(document_reference, binaries_by_id: dict, extra_entries: str = "") -> str:
    """Reverses app.cda.narrative_sections.build_narrative_document_reference
    - the twelve narrative-only Discharge Summary/History and Physical
    sections (Hospital Course, Plan of Treatment, all three Reason for
    Visit/Chief Complaint shapes, History of Present Illness, Physical
    Exam, Assessment, Review of Systems, Social History, Family History,
    General Status), each converted forward to a DocumentReference+Binary
    pair rather than a structured entry (see that module's own docstring
    for why).

    **Which of the twelve templateIds to regenerate is resolved from
    DocumentReference.type.coding[0].code alone**, via
    NARRATIVE_LOINC_TO_TEMPLATE_ID (the reverse of the forward module's own
    templateId->LOINC table) - the one signal a bare FHIR DocumentReference
    reliably carries back to its real originating section, the same
    "resolve the real signal, don't guess from Bundle order" spirit every
    other cross-cutting resolver in this app already follows. A
    DocumentReference with no resolvable/recognized LOINC code - never
    produced by this app's own forward direction, but a hand-built or
    third-party Bundle legitimately could carry one (e.g. an HL7v2 MDM-
    sourced DocumentReference, whose own LOINC vocabulary is unrelated) -
    is silently skipped rather than guessed at, the same "no signal, no way
    to know which section this belongs to" precedent this app's every other
    unresolvable-input case already establishes.

    **A genuine, disclosed lossy simplification, distinct from every other
    reverse mapping in this app**: the forward direction's own
    `extract_narrative_text` collapses `<paragraph>`/`<list>`/`<table>`
    shapes all down to one joined, newline-separated plain-text string with
    no marker distinguishing which original shape produced which line (a
    table's own row/column structure becomes indistinguishable from an
    ordinary paragraph run once flattened) - so this reverse builder always
    regenerates one `<paragraph>` per line, never a `<table>`/`<list>`,
    regardless of the original section's own shape. This is round-trip
    *stable* from the second pass onward (re-running the forward extractor
    over N `<paragraph>` elements rejoins them with the identical newlines,
    reproducing the exact same Binary.data every time), even though the
    very first reverse pass can't recover a table's own original visual
    structure - the same "recoverable content, not recoverable original
    shape" tradeoff MDM's own OBX-5 join and SIU's own NTE-3 join already
    disclose for an analogous many-lines-to-one-field forward collapse."""
    if not document_reference.type or not document_reference.type.coding:
        return ""
    code = document_reference.type.coding[0].code
    template_id = NARRATIVE_LOINC_TO_TEMPLATE_ID.get(code)
    if not template_id:
        return ""
    if not document_reference.content:
        return ""
    url = document_reference.content[0].attachment.url or ""
    binary_id = url[len("urn:uuid:") :] if url.startswith("urn:uuid:") else ""
    binary = binaries_by_id.get(binary_id)
    if binary is None or not binary.data:
        return ""

    narrative_text = binary.data.decode("utf-8")
    display = document_reference.type.coding[0].display or NARRATIVE_CANONICAL_TITLES.get(template_id, "")
    title = document_reference.description or NARRATIVE_CANONICAL_TITLES.get(template_id, "")
    paragraphs = "".join(f"<paragraph>{_esc(line)}</paragraph>" for line in narrative_text.split("\n") if line)

    return (
        f'<component><section><templateId root="{template_id}"/>'
        f'<code code="{_esc(code)}" codeSystem="2.16.840.1.113883.6.1" displayName="{_esc(display)}"/>'
        f"<title>{_esc(title)}</title>"
        f"<text>{paragraphs}</text>{extra_entries}"
        "</section></component>"
    )


# LOINC code -> which structured-entry builder feeds that narrative
# section's own extra_entries (see _build_narrative_section's own
# docstring for why these three sections combine a narrative
# DocumentReference+Binary pair with real structured entries, unlike the
# other nine which are narrative-only). Keyed the same way
# NARRATIVE_LOINC_TO_TEMPLATE_ID is, so a DocumentReference's own
# .type.coding[0].code resolves both which templateId to regenerate AND
# whether it needs extra structured entries injected.
_SOCIAL_HISTORY_LOINC = "29762-2"
_FAMILY_HISTORY_LOINC = "10157-6"
_PLAN_OF_TREATMENT_LOINC = "18776-5"


def _build_narrative_sections(
    document_references, binaries_by_id: dict, extra_entries_by_loinc: dict[str, str]
) -> str:
    parts = []
    for document_reference in document_references:
        code = (
            document_reference.type.coding[0].code
            if document_reference.type and document_reference.type.coding
            else None
        )
        extra_entries = extra_entries_by_loinc.get(code, "")
        parts.append(_build_narrative_section(document_reference, binaries_by_id, extra_entries=extra_entries))
    return "".join(parts)


def build_sectioned_document(
    bundle: Bundle,
    template_id: str,
    doc_code: str,
    doc_code_display: str,
    title: str,
    include_discharge_specific_sections: bool = False,
) -> str:
    """Header + all seven general-purpose sections (Problems/Medications/
    Allergies/Immunizations/Vital Signs/Results/Procedures) - the reverse-
    direction mirror of app.cda.common.build_sectioned_bundle's own role on
    the forward side. Public (not module-private) - extracted here once
    app/transform/cda_discharge_summary.py became a second real consumer of
    the identical header+section assembly, confirmed structurally identical
    the same way the forward `DischargeSummaryBuilder` itself was (both
    document types share CCD's own recordTarget/componentOf header shape,
    verified against a real HL7 C-CDA-Examples Discharge Summary).

    `include_discharge_specific_sections=True` (only `DischargeSummaryReverseBuilder`
    passes this) additionally splits out a Hospital Discharge Diagnosis
    section: any `Condition` carrying `category == "encounter-diagnosis"`
    (the one real, reliable marker `app.cda.hospital_discharge_diagnosis`'s
    own forward module sets, and a plain Problems section never does) is
    routed there instead of into the plain Problems section. **Discharge
    Medications is deliberately NOT split out the same way, a genuine,
    permanent limitation rather than a deferred slice**: unlike Hospital
    Discharge Diagnosis, the forward `app.cda.discharge_medications` module
    reuses `build_medication_request` with zero modification - no field on
    the resulting `MedicationRequest` distinguishes it from one sourced from
    a plain Medications section (confirmed by reading that module's own
    docstring, not assumed) - so every `MedicationRequest` in the Bundle
    continues to route into the plain Medications section regardless of
    which document type is being reversed, since there is no FHIR-side
    signal this builder could reverse even in principle.

    **Also regenerates any of the twelve narrative-only sections** (Hospital
    Course, Plan of Treatment, Reason for Visit/Chief Complaint, History of
    Present Illness, Physical Exam, Assessment, Review of Systems, Social
    History, Family History, General Status) found as a DocumentReference+
    Binary pair in the Bundle - see `_build_narrative_section`'s own
    docstring for the full reasoning. Unconditional, not gated by
    `include_discharge_specific_sections` - `CcdReverseBuilder` itself never
    encounters one of these in practice (this app's own CCD generator never
    produces one), but nothing about resolving them depends on which
    document type is being built, the same "SECTION_BUILDERS itself has no
    document-type awareness" precedent the forward direction already
    established for these same twelve templateIds.

    **Three of those twelve additionally get real structured entries
    injected alongside their narrative pair**: Social History's own
    category="social-history" Observations, Family History's own
    FamilyMemberHistory resources, and Plan of Treatment's own CarePlan
    activities - see `_build_social_history_entries`/`_build_family_history_
    entries`/`_build_plan_of_treatment_entries`'s own docstrings for each
    reversal's field mapping and disclosed round-trip ambiguities."""
    patient = find_resource(bundle, "Patient")
    if patient is None:
        raise MappingError("Bundle has no Patient resource - cannot build a CDA document")
    encounter = find_resource(bundle, "Encounter")
    conditions = find_resources(bundle, "Condition")
    medication_requests = find_resources(bundle, "MedicationRequest")
    allergies = find_resources(bundle, "AllergyIntolerance")
    immunizations = find_resources(bundle, "Immunization")
    observations = find_resources(bundle, "Observation")
    diagnostic_reports = find_resources(bundle, "DiagnosticReport")
    specimens_by_id = {s.id: s for s in find_resources(bundle, "Specimen")}
    procedures = find_resources(bundle, "Procedure")
    practitioner_roles_by_id = {r.id: r for r in find_resources(bundle, "PractitionerRole")}
    practitioners_by_id = {p.id: p for p in find_resources(bundle, "Practitioner")}
    organizations_by_id = {o.id: o for o in find_resources(bundle, "Organization")}
    locations_by_id = {loc.id: loc for loc in find_resources(bundle, "Location")}
    document_references = find_resources(bundle, "DocumentReference")
    binaries_by_id = {b.id: b for b in find_resources(bundle, "Binary")}
    family_member_histories = find_resources(bundle, "FamilyMemberHistory")
    care_plans = find_resources(bundle, "CarePlan")

    document_id = _esc(bundle.identifier.value) if bundle.identifier else "TT000"
    document_root = _reverse_identifier_root(bundle.identifier) if bundle.identifier else _PLACEHOLDER_ROOT
    effective_time = format_hl7_ts(bundle.timestamp) if bundle.timestamp else ""

    hospital_discharge_diagnosis_section = ""
    discharge_medications_section = ""
    if include_discharge_specific_sections:
        discharge_diagnoses = [c for c in conditions if _is_hospital_discharge_diagnosis(c)]
        problem_conditions = [c for c in conditions if not _is_hospital_discharge_diagnosis(c)]
        hospital_discharge_diagnosis_section = _build_hospital_discharge_diagnosis_section(discharge_diagnoses)
        problems_section = _build_problems_section(problem_conditions)

        discharge_medications = [r for r in medication_requests if _is_discharge_medication(r)]
        plain_medications = [r for r in medication_requests if not _is_discharge_medication(r)]
        discharge_medications_section = _build_discharge_medications_section(discharge_medications)
        medications_section = _build_medications_section(plain_medications)
    else:
        problems_section = _build_problems_section(conditions)
        medications_section = _build_medications_section(medication_requests)
    allergies_section = _build_allergies_section(allergies)
    immunizations_section = _build_immunizations_section(immunizations)
    vitals_section = _build_vitals_section(observations)
    observations_by_id = {o.id: o for o in observations}
    results_section = _build_results_section(diagnostic_reports, observations_by_id, specimens_by_id)
    procedures_section = _build_procedures_section(
        procedures, practitioner_roles_by_id, practitioners_by_id, organizations_by_id, locations_by_id
    )
    extra_entries_by_loinc = {
        _SOCIAL_HISTORY_LOINC: _build_social_history_entries(observations),
        _FAMILY_HISTORY_LOINC: _build_family_history_entries(family_member_histories),
        _PLAN_OF_TREATMENT_LOINC: _build_plan_of_treatment_entries(care_plans),
    }
    narrative_sections = _build_narrative_sections(document_references, binaries_by_id, extra_entries_by_loinc)
    sections = (
        f"{hospital_discharge_diagnosis_section}{problems_section}{medications_section}"
        f"{discharge_medications_section}{allergies_section}"
        f"{immunizations_section}{vitals_section}{results_section}{procedures_section}{narrative_sections}"
    )
    body = f"<component><structuredBody>{sections}</structuredBody></component>" if sections else ""

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<templateId root="{_US_HEADER_TEMPLATE_ID}"/><templateId root="{template_id}"/>'
        f'<id root="{document_root}" extension="{document_id}"/>'
        f'<code code="{doc_code}" codeSystem="2.16.840.1.113883.6.1" displayName="{doc_code_display}"/>'
        f"<title>{title}</title>"
        f'{f"<effectiveTime value=\"{effective_time}\"/>" if effective_time else ""}'
        '<confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>'
        '<languageCode code="en-US"/>'
        f"{_build_patient_role(patient)}{_build_component_of(encounter)}{body}"
        "</ClinicalDocument>"
    )


class CcdReverseBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        return build_sectioned_document(
            bundle, CCD_TEMPLATE_ID, "34133-9", "Summarization of Episode Note", "Continuity of Care Document"
        )
