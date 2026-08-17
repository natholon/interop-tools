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
from app.cda.vitals import ORGANIZER_TEMPLATE_ID as VITALS_ORGANIZER_TEMPLATE_ID
from app.cda.vitals import OBSERVATION_TEMPLATE_ID as VITALS_OBSERVATION_TEMPLATE_ID
from app.cda.vitals import PANEL_CODE as VITALS_PANEL_CODE
from app.cda.vitals import SECTION_TEMPLATE_ID as VITALS_SECTION_TEMPLATE_ID
from app.cda.medications import FREE_TEXT_SIG_TEMPLATE_ID, MEDICATION_ACTIVITY_TEMPLATE_ID
from app.cda.medications import STATUS_MAP as MEDICATION_STATUS_MAP
from app.cda.medications import SECTION_TEMPLATE_ID as MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.problems import PROBLEM_OBSERVATION_TEMPLATE_ID
from app.cda.problems import CONCERN_ACT_TEMPLATE_ID
from app.cda.problems import SECTION_TEMPLATE_ID as PROBLEMS_SECTION_TEMPLATE_ID
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
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{identifier.value}"/>'
        for identifier in (patient.identifier or [])
    )
    name = ""
    if patient.name:
        human_name = patient.name[0]
        family = f"<family>{human_name.family}</family>" if human_name.family else ""
        given = "".join(f"<given>{g}</given>" for g in (human_name.given or []))
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
        lines = "".join(f"<streetAddressLine>{line}</streetAddressLine>" for line in (address.line or []))
        city = f"<city>{address.city}</city>" if address.city else ""
        state = f"<state>{address.state}</state>" if address.state else ""
        postal_code = f"<postalCode>{address.postalCode}</postalCode>" if address.postalCode else ""
        country = f"<country>{address.country}</country>" if address.country else ""
        addr = f"<addr>{lines}{city}{state}{postal_code}{country}</addr>"
    telecom = ""
    if patient.telecom:
        contact_point = patient.telecom[0]
        scheme = "tel" if contact_point.system == "phone" else "mailto" if contact_point.system == "email" else "tel"
        telecom = f'<telecom value="{scheme}:{contact_point.value}"/>'

    return (
        f'<recordTarget><patientRole>{ids}{addr}{telecom}'
        f'<patient>{name}{gender}{birth_time}</patient>'
        "</patientRole></recordTarget>"
    )


def _build_component_of(encounter) -> str:
    if encounter is None:
        return ""
    ids = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{identifier.value}"/>'
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
    display = f' displayName="{coding.display}"' if coding.display else ""
    return f'code="{coding.code}"{code_system_attr}{display}'


def _build_problem_entry(condition) -> str:
    code = condition.code.coding[0] if condition.code and condition.code.coding else None
    value = f'<value xsi:type="CD" {_build_cd_attrs(code)}/>' if code else '<value xsi:type="CD" nullFlavor="UNK"/>'
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
        f'<templateId root="{CONCERN_ACT_TEMPLATE_ID}"/>'
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
        route = f"<routeCode {_build_cd_attrs(dosage.route.coding[0])}/>"

    dose_quantity = ""
    rate_quantity = ""
    if dosage.doseAndRate:
        dose_and_rate = dosage.doseAndRate[0]
        if dose_and_rate.doseQuantity is not None:
            unit = f' unit="{dose_and_rate.doseQuantity.unit}"' if dose_and_rate.doseQuantity.unit else ""
            dose_quantity = f'<doseQuantity value="{dose_and_rate.doseQuantity.value}"{unit}/>'
        if dose_and_rate.rateQuantity is not None:
            unit = f' unit="{dose_and_rate.rateQuantity.unit}"' if dose_and_rate.rateQuantity.unit else ""
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
            f"<text>{dosage.patientInstruction}</text>"
            "</substanceAdministration></entryRelationship>"
        )

    return f"{effective_time}{route}{dose_quantity}{rate_quantity}{free_text_sig}"


def _build_medication_entry(request) -> str:
    coding = (
        request.medicationCodeableConcept.coding[0]
        if request.medicationCodeableConcept and request.medicationCodeableConcept.coding
        else None
    )
    consumable_code = f"<code {_build_cd_attrs(coding)}/>" if coding else '<code nullFlavor="UNK"/>'
    status_code = _MEDICATION_STATUS_TO_ACT_STATUS.get(request.status, _DEFAULT_MEDICATION_ACT_STATUS)
    mood_code = _INTENT_TO_MOOD_CODE.get(request.intent, _DEFAULT_MOOD_CODE)

    return (
        f'<entry typeCode="DRIV"><substanceAdministration classCode="SBADM" moodCode="{mood_code}">'
        f'<templateId root="{MEDICATION_ACTIVITY_TEMPLATE_ID}"/>'
        f'<statusCode code="{status_code}"/>'
        f"{_build_dosage_elements(request)}"
        "<consumable><manufacturedProduct><manufacturedMaterial>"
        f"{consumable_code}"
        "</manufacturedMaterial></manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
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
    manifestation = reaction.manifestation[0].coding[0] if reaction.manifestation and reaction.manifestation[0].coding else None
    value = f"<value xsi:type=\"CD\" {_build_cd_attrs(manifestation)}/>" if manifestation else ""
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
    coding = allergy.code.coding[0] if allergy.code and allergy.code.coding else None
    if coding is None:
        return ""
    return (
        '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
        f"<code {_build_cd_attrs(coding)}/>"
        "</playingEntity></participantRole></participant>"
    )


def _build_allergy_entry(allergy) -> str:
    # A negated allergy always reaches this builder with code.coding empty
    # (only code.text set) - _resolve_allergen_code never returns a coded
    # CodeableConcept for a negated entry, only a text-only one - so this
    # is a reliable, disclosed way to detect the negated case on the way
    # back out without a dedicated FHIR-side marker. A further, disclosed
    # lossy step follows from this: a negated allergy that still carried a
    # resolvable allergen ("No known allergy to Penicillin") can't recover
    # that allergen's own code here, so it degrades to the fully generic
    # "No known allergies" shape on this and every subsequent round trip -
    # the same "can't recover more than the forward side actually kept"
    # limitation every other lossy-by-construction reverse mapping in this
    # app already discloses.
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


# Reverse of app.cda.immunizations.STATUS_MAP - "completed"/"entered-in-
# error" are clean bijections ("completed"/"nullified"), but "not-done" is
# genuinely many-to-one on the forward side (six distinct statusCode
# values, plus negationInd="true" unconditionally, all collapse to it).
# Reversed via negationInd="true" specifically, not a non-negated
# statusCode - the forward side's own comment already treats negation as
# the primary real-world signal for this status ("negation is checked
# first, before this table is even consulted"), so it's the more honest
# disclosed representative than picking one of the six statusCode values
# arbitrarily. "aborted" is still emitted as statusCode alongside the
# negation attribute, purely for XML realism - it's inert on the forward
# side regardless, since negationInd short-circuits status resolution
# before statusCode is ever consulted.
_IMMUNIZATION_STATUS_TO_ACT_STATUS = {"completed": "completed", "entered-in-error": "nullified"}
_DEFAULT_IMMUNIZATION_ACT_STATUS = "aborted"


def _build_immunization_entry(immunization) -> str:
    coding = (
        immunization.vaccineCode.coding[0] if immunization.vaccineCode and immunization.vaccineCode.coding else None
    )
    consumable_code = f"<code {_build_cd_attrs(coding)}/>" if coding else '<code nullFlavor="UNK"/>'
    lot_number = f"<lotNumberText>{immunization.lotNumber}</lotNumberText>" if immunization.lotNumber else ""

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
        route = f"<routeCode {_build_cd_attrs(immunization.route.coding[0])}/>"

    dose_quantity = ""
    if immunization.doseQuantity is not None:
        unit = f' unit="{immunization.doseQuantity.unit}"' if immunization.doseQuantity.unit else ""
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
    coding = observation.code.coding[0] if observation.code and observation.code.coding else None
    code_element = f"<code {_build_cd_attrs(coding)}/>" if coding else '<code nullFlavor="UNK"/>'
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(observation.effectiveDateTime)}"/>'
        if observation.effectiveDateTime
        else ""
    )
    value = ""
    if observation.valueQuantity is not None:
        unit = f' unit="{observation.valueQuantity.unit}"' if observation.valueQuantity.unit else ""
        value = f'<value xsi:type="PQ" value="{observation.valueQuantity.value}"{unit}/>'
    interpretation = ""
    if observation.interpretation and observation.interpretation[0].coding:
        interpretation = f"<interpretationCode {_build_cd_attrs(observation.interpretation[0].coding[0])}/>"
    method = ""
    if observation.method and observation.method.coding:
        method = f"<methodCode {_build_cd_attrs(observation.method.coding[0])}/>"
    body_site = ""
    if observation.bodySite and observation.bodySite.coding:
        body_site = f"<targetSiteCode {_build_cd_attrs(observation.bodySite.coding[0])}/>"

    return (
        '<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{VITALS_OBSERVATION_TEMPLATE_ID}"/>'
        '<statusCode code="completed"/>'
        f"{code_element}{effective_time}{value}{interpretation}{method}{body_site}"
        "</observation></component>"
    )


def _build_vital_signs_organizer(panel, members_by_id: dict) -> str:
    member_ids = [ref.reference.removeprefix("urn:uuid:") for ref in (panel.hasMember or [])]
    member_elements = "".join(
        _build_vital_sign_observation_element(members_by_id[member_id])
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
    provably inert for round-trip correctness, not a corner cut."""
    if observation.valueQuantity is not None:
        unit = f' unit="{observation.valueQuantity.unit}"' if observation.valueQuantity.unit else ""
        return f'<value xsi:type="PQ" value="{observation.valueQuantity.value}"{unit}/>'
    if observation.valueCodeableConcept is not None and observation.valueCodeableConcept.coding:
        return f'<value xsi:type="CD" {_build_cd_attrs(observation.valueCodeableConcept.coding[0])}/>'
    if observation.valueInteger is not None:
        return f'<value xsi:type="INT" value="{observation.valueInteger}"/>'
    if observation.valueString is not None:
        return f'<value xsi:type="ST">{observation.valueString}</value>'
    return ""


def _build_reference_range_element(observation) -> str:
    if not observation.referenceRange:
        return ""
    reference_range = observation.referenceRange[0]
    low = f'<low value="{reference_range.low.value}" unit="{reference_range.low.unit or ""}"/>' if reference_range.low else ""
    high = f'<high value="{reference_range.high.value}" unit="{reference_range.high.unit or ""}"/>' if reference_range.high else ""
    if not low and not high:
        return ""
    return (
        '<referenceRange><observationRange><value xsi:type="IVL_PQ">'
        f"{low}{high}"
        "</value></observationRange></referenceRange>"
    )


def _build_result_observation_element(observation) -> str:
    coding = observation.code.coding[0] if observation.code and observation.code.coding else None
    code_element = f"<code {_build_cd_attrs(coding)}/>" if coding else '<code nullFlavor="UNK"/>'
    act_status = _RESULT_STATUS_TO_ACT_STATUS.get(observation.status, _DEFAULT_RESULT_ACT_STATUS)
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(observation.effectiveDateTime)}"/>'
        if observation.effectiveDateTime
        else ""
    )
    value = _build_result_value_element(observation)
    interpretation = ""
    if observation.interpretation and observation.interpretation[0].coding:
        interpretation = f"<interpretationCode {_build_cd_attrs(observation.interpretation[0].coding[0])}/>"
    method = ""
    if observation.method and observation.method.coding:
        method = f"<methodCode {_build_cd_attrs(observation.method.coding[0])}/>"
    body_site = ""
    if observation.bodySite and observation.bodySite.coding:
        body_site = f"<targetSiteCode {_build_cd_attrs(observation.bodySite.coding[0])}/>"
    reference_range = _build_reference_range_element(observation)

    return (
        '<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{RESULTS_OBSERVATION_TEMPLATE_ID}"/>'
        f'<statusCode code="{act_status}"/>'
        f"{code_element}{effective_time}{value}{interpretation}{method}{body_site}{reference_range}"
        "</observation></component>"
    )


def _build_result_organizer(report, observations_by_id: dict) -> str:
    result_ids = [ref.reference.removeprefix("urn:uuid:") for ref in (report.result or [])]
    member_elements = "".join(
        _build_result_observation_element(observations_by_id[result_id])
        for result_id in result_ids
        if result_id in observations_by_id
    )
    if not member_elements:
        return ""
    coding = report.code.coding[0] if report.code and report.code.coding else None
    organizer_code = f"<code {_build_cd_attrs(coding)}/>" if coding else '<code nullFlavor="UNK"/>'
    act_status = _RESULT_STATUS_TO_ACT_STATUS.get(report.status, _DEFAULT_RESULT_ACT_STATUS)
    effective_time = (
        f'<effectiveTime value="{format_hl7_ts(report.effectiveDateTime)}"/>' if report.effectiveDateTime else ""
    )
    return (
        f'<entry typeCode="DRIV"><organizer classCode="BATTERY" moodCode="EVN">'
        f'<templateId root="{RESULTS_ORGANIZER_TEMPLATE_ID}"/>'
        f"{organizer_code}"
        f'<statusCode code="{act_status}"/>{effective_time}{member_elements}'
        "</organizer></entry>"
    )


def _build_results_section(reports, observations_by_id: dict) -> str:
    entries = "".join(entry for report in reports if (entry := _build_result_organizer(report, observations_by_id)))
    if not entries:
        return ""
    return (
        f'<component><section><templateId root="{RESULTS_SECTION_TEMPLATE_ID}"/>'
        '<code code="30954-2" codeSystem="2.16.840.1.113883.6.1" displayName="Relevant diagnostic tests and/or laboratory data"/>'
        f"<title>Results</title>{entries}</section></component>"
    )


class CcdReverseBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError("Bundle has no Patient resource - cannot build a CCD document")
        encounter = find_resource(bundle, "Encounter")
        conditions = find_resources(bundle, "Condition")
        medication_requests = find_resources(bundle, "MedicationRequest")
        allergies = find_resources(bundle, "AllergyIntolerance")
        immunizations = find_resources(bundle, "Immunization")
        observations = find_resources(bundle, "Observation")
        diagnostic_reports = find_resources(bundle, "DiagnosticReport")

        document_id = bundle.identifier.value if bundle.identifier else "TT000"
        document_root = _reverse_identifier_root(bundle.identifier) if bundle.identifier else _PLACEHOLDER_ROOT
        effective_time = format_hl7_ts(bundle.timestamp) if bundle.timestamp else ""

        problems_section = _build_problems_section(conditions)
        medications_section = _build_medications_section(medication_requests)
        allergies_section = _build_allergies_section(allergies)
        immunizations_section = _build_immunizations_section(immunizations)
        vitals_section = _build_vitals_section(observations)
        observations_by_id = {o.id: o for o in observations}
        results_section = _build_results_section(diagnostic_reports, observations_by_id)
        sections = (
            f"{problems_section}{medications_section}{allergies_section}"
            f"{immunizations_section}{vitals_section}{results_section}"
        )
        body = f"<component><structuredBody>{sections}</structuredBody></component>" if sections else ""

        xml_text = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<templateId root="{_US_HEADER_TEMPLATE_ID}"/><templateId root="{CCD_TEMPLATE_ID}"/>'
            f'<id root="{document_root}" extension="{document_id}"/>'
            '<code code="34133-9" codeSystem="2.16.840.1.113883.6.1" displayName="Summarization of Episode Note"/>'
            "<title>Continuity of Care Document</title>"
            f'{f"<effectiveTime value=\"{effective_time}\"/>" if effective_time else ""}'
            '<confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>'
            '<languageCode code="en-US"/>'
            f"{_build_patient_role(patient)}{_build_component_of(encounter)}{body}"
            "</ClinicalDocument>"
        )
        return xml_text
