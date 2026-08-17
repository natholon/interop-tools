"""C-CDA validation - the app/cda/ mirror of app/validation/{generic,adt,
engine}.py, independent of conversion, never raising for anything a real
C-CDA sender could plausibly produce.

Structural differences from app/validation/'s registry mechanics are
deliberate, not oversights: there's only one C-CDA document type (CCD) so
far, so a get_type_validator()-style dispatch-by-document-type registry
would be a premature abstraction - the same "degenerate case of the same
pattern" reasoning already documented for app/mappings/oru.py and mdm.py
(one flat rule set, no dispatch dimension needed until a second document
type actually exists).

Reuses app.validation.models.ValidationFinding/ValidationReport as-is -
they're already format-agnostic (`segment`/`field` are documented as "the
offending location when there is one", not literally restricted to HL7
segment names; a CDA finding uses a path-like string for `segment`, e.g.
"recordTarget/patientRole/patient/administrativeGenderCode", with `field`
left None). Reuses app.validation.common.parse_comparable_datetime/
not_in_future/is_before directly - they operate on raw HL7-TS-shaped
strings, and CDA's @value uses the identical digit shape (see
app/cda/common.py::parse_partial_ts for the established precedent of this
exact reuse)."""

import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from app.cda.allergies import ALLERGY_CONCERN_ACT_TEMPLATE_ID, ALLERGY_OBSERVATION_TEMPLATE_ID
from app.cda.allergies import SECTION_TEMPLATE_ID as ALLERGIES_SECTION_TEMPLATE_ID
from app.cda.allergies import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
from app.cda.ccd import CCD_TEMPLATE_ID
from app.cda.discharge_medications import DISCHARGE_MEDICATION_ACT_TEMPLATE_ID
from app.cda.discharge_medications import SECTION_TEMPLATE_ID as DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.discharge_summary import DISCHARGE_SUMMARY_TEMPLATE_ID
from app.cda.history_and_physical import HISTORY_AND_PHYSICAL_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import (
    SECTION_TEMPLATE_ID as HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID,
)
from app.cda.common import RECOGNIZED_ENCOUNTER_CLASSES, build_codeable_concept_from_cd
from app.cda.immunizations import IMMUNIZATION_ACTIVITY_TEMPLATE_ID, STATUS_MAP as IMMUNIZATION_STATUS_MAP
from app.cda.immunizations import SECTION_TEMPLATE_ID as IMMUNIZATIONS_SECTION_TEMPLATE_ID
from app.cda.medications import MEDICATION_ACTIVITY_TEMPLATE_ID, STATUS_MAP as MEDICATION_STATUS_MAP
from app.cda.medications import SECTION_TEMPLATE_ID as MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds, ts_value
from app.cda.problems import CONCERN_ACT_TEMPLATE_ID, PROBLEM_OBSERVATION_TEMPLATE_ID
from app.cda.problems import SECTION_TEMPLATE_ID as PROBLEMS_SECTION_TEMPLATE_ID
from app.cda.procedures import PROCEDURE_TEMPLATE_ID, STATUS_MAP as PROCEDURE_STATUS_MAP
from app.cda.procedures import SECTION_TEMPLATE_ID as PROCEDURES_SECTION_TEMPLATE_ID
from app.cda.procedures import (
    SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as PROCEDURES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL,
)
from app.cda.results import ORGANIZER_TEMPLATE_ID as RESULT_ORGANIZER_TEMPLATE_ID
from app.cda.results import OBSERVATION_TEMPLATE_ID as RESULT_OBSERVATION_TEMPLATE_ID
from app.cda.results import SECTION_TEMPLATE_ID as RESULTS_SECTION_TEMPLATE_ID
from app.cda.results import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as RESULTS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
from app.cda.results import STATUS_MAP as RESULT_STATUS_MAP
from app.cda.vitals import ORGANIZER_TEMPLATE_ID as VITAL_SIGNS_ORGANIZER_TEMPLATE_ID
from app.cda.vitals import OBSERVATION_TEMPLATE_ID as VITAL_SIGN_OBSERVATION_TEMPLATE_ID
from app.cda.vitals import SECTION_TEMPLATE_ID as VITALS_SECTION_TEMPLATE_ID
from app.cda.vitals import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as VITALS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
from app.hl7.errors import MappingError, MissingSegmentError
from app.validation.common import is_before, not_in_future, parse_comparable_datetime
from app.validation.models import ValidationFinding, ValidationReport

logger = logging.getLogger(__name__)

_MAX_PLAUSIBLE_AGE_YEARS = 120


def _resolve_trigger_event(document) -> str | None:
    """ValidationReport.trigger_event for a CDA document - "CCD"/
    "DISCHARGESUMMARY" stand in for a real HL7v2 trigger event the same way
    app/generators/registry.py's ("CDA", "CCD") pairing already does (see
    that module's own comment on the same convention). Uppercase to match
    every other trigger-event string in this app (A01, S12, ...) and
    app/generators/registry.py::generate()'s own `.upper()`-normalized
    lookup, which requires its dict keys to already be uppercase. None when
    the document's own templateId isn't one this app recognizes at all."""
    if has_template_id(document, CCD_TEMPLATE_ID):
        return "CCD"
    if has_template_id(document, DISCHARGE_SUMMARY_TEMPLATE_ID):
        return "DISCHARGESUMMARY"
    if has_template_id(document, HISTORY_AND_PHYSICAL_TEMPLATE_ID):
        return "HISTORYANDPHYSICAL"
    return None


def _find_patient(document):
    record_target = find_child(document, "recordTarget")
    patient_role = find_child(record_target, "patientRole") if record_target is not None else None
    return find_child(patient_role, "patient") if patient_role is not None else None


def _find_section(document, *template_ids: str):
    """Find the first structuredBody section matching any of the given
    templateIds - most section types have exactly one, but Allergies has
    two (the "entries required" and "entries optional" variants both wrap
    the identical entry shape, see app/cda/allergies.py). A single-
    templateId `_find_*_section` helper checking only one of these would
    silently never run that section's rules against the other variant -
    this shipped once (see CLAUDE.md/git history) and this shared helper
    exists so the same class of gap can't recur for a future section type
    with more than one recognized templateId."""
    for section in find_all(document, "component/structuredBody/component/section"):
        if any(has_template_id(section, template_id) for template_id in template_ids):
            return section
    return None


def _find_problems_section(document):
    return _find_section(document, PROBLEMS_SECTION_TEMPLATE_ID)


def _find_medications_section(document):
    return _find_section(document, MEDICATIONS_SECTION_TEMPLATE_ID)


def _find_allergies_section(document):
    return _find_section(document, ALLERGIES_SECTION_TEMPLATE_ID, ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL)


def _find_immunizations_section(document):
    return _find_section(document, IMMUNIZATIONS_SECTION_TEMPLATE_ID)


def _find_vitals_section(document):
    return _find_section(document, VITALS_SECTION_TEMPLATE_ID, VITALS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL)


def _find_results_section(document):
    return _find_section(document, RESULTS_SECTION_TEMPLATE_ID, RESULTS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL)


def _find_procedures_section(document):
    return _find_section(document, PROCEDURES_SECTION_TEMPLATE_ID, PROCEDURES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL)


def _find_hospital_discharge_diagnosis_section(document):
    return _find_section(document, HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID)


def _find_discharge_medications_section(document):
    return _find_section(document, DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID)


def _rule_patient_name_missing(patient) -> list[ValidationFinding]:
    if not find_all(patient, "name"):
        return [
            ValidationFinding(
                severity="warning",
                rule_id="cda.patient-name-missing",
                segment="recordTarget/patientRole/patient/name",
                message="No <name> element is present for the patient.",
            )
        ]
    return []


def _rule_patient_gender_unrecognized(patient) -> list[ValidationFinding]:
    gender_element = find_child(patient, "administrativeGenderCode")
    code = (gender_element.get("code") or "").strip().upper() if gender_element is not None else ""
    if code and code not in {"F", "M", "UN"}:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="cda.patient-gender-unrecognized",
                segment="recordTarget/patientRole/patient/administrativeGenderCode",
                message=f"administrativeGenderCode {code!r} is not a recognized F/M/UN code.",
            )
        ]
    return []


def _rule_patient_birth_date(patient, now: datetime) -> list[ValidationFinding]:
    raw_birth = ts_value(find_child(patient, "birthTime"))
    if not raw_birth:
        return []
    birth_dt = parse_comparable_datetime(raw_birth)
    if birth_dt is None:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="cda.patient-birthtime-unparseable",
                segment="recordTarget/patientRole/patient/birthTime",
                message=f"birthTime {raw_birth!r} does not parse as a valid HL7 TS value.",
            )
        ]
    if birth_dt > now:
        return [
            ValidationFinding(
                severity="error",
                rule_id="cda.patient-birthtime-in-future",
                segment="recordTarget/patientRole/patient/birthTime",
                message="birthTime is in the future.",
            )
        ]
    if (now - birth_dt).days > _MAX_PLAUSIBLE_AGE_YEARS * 365:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="cda.patient-birthtime-implausibly-old",
                segment="recordTarget/patientRole/patient/birthTime",
                message=f"birthTime implies an age over {_MAX_PLAUSIBLE_AGE_YEARS} years.",
            )
        ]
    return []


def _rule_document_effective_time(document, now: datetime) -> list[ValidationFinding]:
    raw = ts_value(find_child(document, "effectiveTime"))
    if not raw:
        return []
    if not_in_future(raw, now) is False:
        return [
            ValidationFinding(
                severity="warning",
                rule_id="cda.document-date-in-future",
                segment="ClinicalDocument/effectiveTime",
                message="ClinicalDocument/effectiveTime is in the future.",
            )
        ]
    return []


def _rule_encounter(encompassing_encounter, now: datetime) -> list[ValidationFinding]:
    findings = []

    code_element = find_child(encompassing_encounter, "code")
    code = (code_element.get("code") or "").strip().upper() if code_element is not None else ""
    if code and code not in RECOGNIZED_ENCOUNTER_CLASSES:
        findings.append(
            ValidationFinding(
                severity="info",
                rule_id="cda.encounter-class-unrecognized",
                segment="componentOf/encompassingEncounter/code",
                message=f"Encounter class code {code!r} is not recognized - the converter will silently default to AMB.",
            )
        )

    low, high = ivl_ts_bounds(find_child(encompassing_encounter, "effectiveTime"))
    low_dt = parse_comparable_datetime(low) if low else None
    high_dt = parse_comparable_datetime(high) if high else None

    if low and low_dt is not None and not_in_future(low, now) is False:
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="cda.encounter-period-start-in-future",
                segment="componentOf/encompassingEncounter/effectiveTime",
                message="Encounter period start is in the future.",
            )
        )
    elif not low and high and high_dt is not None and not_in_future(high, now) is False:
        # IVL_TS with only @high (known end, unknown start) is a legal
        # shape ivl_ts_bounds() returns as (None, high) - nesting this
        # whole block under `if low:` would silently produce ZERO
        # encounter findings for exactly this shape, even when the known
        # end date is itself implausibly in the future.
        findings.append(
            ValidationFinding(
                severity="warning",
                rule_id="cda.encounter-period-end-in-future",
                segment="componentOf/encompassingEncounter/effectiveTime",
                message="Encounter period end is in the future.",
            )
        )

    if low and low_dt is not None and high and high_dt is not None and is_before(high, high_dt, low, low_dt):
        findings.append(
            ValidationFinding(
                severity="error",
                rule_id="cda.encounter-period-end-before-start",
                segment="componentOf/encompassingEncounter/effectiveTime",
                message="Encounter period end is before its start.",
            )
        )
    return findings


def _iter_problem_observations(section):
    """Yield each Problem Observation element found via the exact same
    act/entryRelationship[SUBJ] walk app.cda.problems.build_conditions()
    uses, so validation can never see a different set of entries than
    conversion does."""
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, CONCERN_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            observation = find_child(relationship, "observation")
            if observation is None or not has_template_id(observation, PROBLEM_OBSERVATION_TEMPLATE_ID):
                continue
            yield observation


def _rule_problem_observations(
    observations, rule_prefix: str, segment_prefix: str, patient, now: datetime
) -> list[ValidationFinding]:
    """Shared rule body for any section wrapping Problem-Observation-shaped
    entries - Problems' own Concern Act, and Hospital Discharge Diagnosis's
    own Act (see app/cda/hospital_discharge_diagnosis.py), which was
    confirmed against a real official HL7 example to wrap the byte-for-byte
    identical Problem Observation template. Promoted here once that second
    real consumer needed the identical checks against a differently-scoped
    observation set - `rule_prefix`/`segment_prefix` keep each section's own
    findings independently identifiable (e.g. `cda.problem-missing-value`
    vs. `cda.hospital-discharge-diagnosis-missing-value`)."""
    findings = []
    raw_birth = ts_value(find_child(patient, "birthTime")) if patient is not None else None
    birth_dt = parse_comparable_datetime(raw_birth) if raw_birth else None

    for observation in observations:
        if observation.get("negationInd") == "true":
            continue

        value_element = find_child(observation, "value")
        if build_codeable_concept_from_cd(value_element) is None:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id=f"{rule_prefix}-missing-value",
                    segment=f"{segment_prefix}/observation/value",
                    message="A Problem Observation has no resolvable coded value - the converter will silently skip this entry.",
                )
            )
            continue

        onset, abatement = ivl_ts_bounds(find_child(observation, "effectiveTime"))
        onset_dt = parse_comparable_datetime(onset) if onset else None
        if onset_dt is not None:
            if not_in_future(onset, now) is False:
                findings.append(
                    ValidationFinding(
                        severity="warning",
                        rule_id=f"{rule_prefix}-onset-in-future",
                        segment=f"{segment_prefix}/observation/effectiveTime",
                        message="A Problem Observation's onset date is in the future.",
                    )
                )
            if raw_birth and birth_dt is not None and is_before(onset, onset_dt, raw_birth, birth_dt):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        rule_id=f"{rule_prefix}-onset-before-birth",
                        segment=f"{segment_prefix}/observation/effectiveTime",
                        message="A Problem Observation's onset date is before the patient's birthTime.",
                    )
                )
            if abatement:
                abatement_dt = parse_comparable_datetime(abatement)
                if abatement_dt is not None and is_before(abatement, abatement_dt, onset, onset_dt):
                    findings.append(
                        ValidationFinding(
                            severity="error",
                            rule_id=f"{rule_prefix}-abatement-before-onset",
                            segment=f"{segment_prefix}/observation/effectiveTime",
                            message="A Problem Observation's abatement date is before its onset date.",
                        )
                    )
    return findings


def _rule_problems(section, patient, now: datetime) -> list[ValidationFinding]:
    return _rule_problem_observations(_iter_problem_observations(section), "cda.problem", "Problems/...", patient, now)


def _iter_medication_activities(section):
    """Yield each Medication Activity substanceAdministration element found
    via the same entry walk app.cda.medications.build_medication_requests()
    uses, so validation can never see a different set of entries than
    conversion does."""
    for entry in find_all(section, "entry"):
        substance_administration = find_child(entry, "substanceAdministration")
        if substance_administration is None or not has_template_id(
            substance_administration, MEDICATION_ACTIVITY_TEMPLATE_ID
        ):
            continue
        yield substance_administration


def _rule_medication_activities(
    substance_administrations, rule_prefix: str, segment_prefix: str
) -> list[ValidationFinding]:
    """Shared rule body for any section wrapping Medication-Activity-shaped
    entries - Medications' own bare substanceAdministration, and Discharge
    Medications' own Act wrapper (see app/cda/discharge_medications.py),
    confirmed against a real official HL7 example to wrap the byte-for-byte
    identical Medication Activity template. Same promotion rationale as
    _rule_problem_observations above."""
    findings = []
    for substance_administration in substance_administrations:
        if substance_administration.get("negationInd") == "true":
            continue

        consumable = find_child(substance_administration, "consumable")
        manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
        manufactured_material = (
            find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
        )
        code_element = find_child(manufactured_material, "code") if manufactured_material is not None else None
        if build_codeable_concept_from_cd(code_element) is None:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id=f"{rule_prefix}-missing-code",
                    segment=f"{segment_prefix}/substanceAdministration/consumable",
                    message="A Medication Activity has no resolvable medication code - the converter will silently skip this entry.",
                )
            )
            continue

        status_element = find_child(substance_administration, "statusCode")
        status_code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
        if status_code and status_code not in MEDICATION_STATUS_MAP:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id=f"{rule_prefix}-status-unrecognized",
                    segment=f"{segment_prefix}/substanceAdministration/statusCode",
                    message=f"statusCode {status_code!r} is not recognized - the converter will silently default to 'unknown'.",
                )
            )

        low, high = ivl_ts_bounds(find_child(substance_administration, "effectiveTime"))
        if low and high:
            low_dt = parse_comparable_datetime(low)
            high_dt = parse_comparable_datetime(high)
            if low_dt is not None and high_dt is not None and is_before(high, high_dt, low, low_dt):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        rule_id=f"{rule_prefix}-period-end-before-start",
                        segment=f"{segment_prefix}/substanceAdministration/effectiveTime",
                        message="A Medication Activity's dosing period end is before its start.",
                    )
                )
    return findings


def _rule_medications(section, now: datetime) -> list[ValidationFinding]:
    return _rule_medication_activities(_iter_medication_activities(section), "cda.medication", "Medications/...")


def _iter_allergy_observations(section):
    """Yield each Allergy-Intolerance Observation element found via the
    exact same act/entryRelationship[SUBJ] walk
    app.cda.allergies.build_allergy_intolerances() uses, so validation can
    never see a different set of entries than conversion does."""
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, ALLERGY_CONCERN_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            observation = find_child(relationship, "observation")
            if observation is None or not has_template_id(observation, ALLERGY_OBSERVATION_TEMPLATE_ID):
                continue
            yield observation


def _has_resolvable_allergen(observation) -> bool:
    """Same participant[@typeCode=CSM]/participantRole/playingEntity/code
    walk app.cda.allergies._resolve_allergen_code() uses, checking only for
    presence of a resolvable code (not building the negation text fallback,
    which this rule doesn't need)."""
    for participant in find_all(observation, "participant"):
        if participant.get("typeCode") != "CSM":
            continue
        participant_role = find_child(participant, "participantRole")
        playing_entity = find_child(participant_role, "playingEntity") if participant_role is not None else None
        code_element = find_child(playing_entity, "code") if playing_entity is not None else None
        if build_codeable_concept_from_cd(code_element) is not None:
            return True
    return False


def _rule_allergies(section, patient, now: datetime) -> list[ValidationFinding]:
    findings = []
    raw_birth = ts_value(find_child(patient, "birthTime")) if patient is not None else None
    birth_dt = parse_comparable_datetime(raw_birth) if raw_birth else None

    for observation in _iter_allergy_observations(section):
        negated = observation.get("negationInd") == "true"

        if not negated:
            if not _has_resolvable_allergen(observation):
                findings.append(
                    ValidationFinding(
                        severity="info",
                        rule_id="cda.allergy-missing-allergen",
                        segment="Allergies/.../observation/participant",
                        message="An asserted Allergy Observation has no resolvable allergen code - the converter will silently skip this entry.",
                    )
                )
                continue

        onset, _ = ivl_ts_bounds(find_child(observation, "effectiveTime"))
        onset_dt = parse_comparable_datetime(onset) if onset else None
        if onset_dt is not None:
            if not_in_future(onset, now) is False:
                findings.append(
                    ValidationFinding(
                        severity="warning",
                        rule_id="cda.allergy-onset-in-future",
                        segment="Allergies/.../observation/effectiveTime",
                        message="An Allergy Observation's onset date is in the future.",
                    )
                )
            if raw_birth and birth_dt is not None and is_before(onset, onset_dt, raw_birth, birth_dt):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        rule_id="cda.allergy-onset-before-birth",
                        segment="Allergies/.../observation/effectiveTime",
                        message="An Allergy Observation's onset date is before the patient's birthTime.",
                    )
                )

        for relationship in find_all(observation, "entryRelationship"):
            if relationship.get("typeCode") != "MFST":
                continue
            reaction_observation = find_child(relationship, "observation")
            if reaction_observation is None:
                continue
            if build_codeable_concept_from_cd(find_child(reaction_observation, "value")) is None:
                findings.append(
                    ValidationFinding(
                        severity="info",
                        rule_id="cda.allergy-reaction-missing-manifestation",
                        segment="Allergies/.../observation/entryRelationship[MFST]",
                        message="A Reaction Observation has no resolvable manifestation code - the converter will silently skip this reaction.",
                    )
                )
    return findings


def _iter_evn_immunization_activities(section):
    """Yield each EVN-mood Immunization Activity substanceAdministration
    element - the same walk (including the mood filter)
    app.cda.immunizations.build_immunizations() uses, so validation can
    never see a different set of entries than conversion does. INT-mood
    entries are out of scope for this slice (see that module's docstring)
    and are excluded here too, not flagged - the same "silently out of
    scope, not a data-quality issue" treatment an unrecognized section
    already gets."""
    for entry in find_all(section, "entry"):
        substance_administration = find_child(entry, "substanceAdministration")
        if substance_administration is None or not has_template_id(
            substance_administration, IMMUNIZATION_ACTIVITY_TEMPLATE_ID
        ):
            continue
        if substance_administration.get("moodCode") != "EVN":
            continue
        yield substance_administration


def _rule_immunizations(section, patient, now: datetime) -> list[ValidationFinding]:
    findings = []
    raw_birth = ts_value(find_child(patient, "birthTime")) if patient is not None else None
    birth_dt = parse_comparable_datetime(raw_birth) if raw_birth else None

    for substance_administration in _iter_evn_immunization_activities(section):
        consumable = find_child(substance_administration, "consumable")
        manufactured_product = find_child(consumable, "manufacturedProduct") if consumable is not None else None
        manufactured_material = (
            find_child(manufactured_product, "manufacturedMaterial") if manufactured_product is not None else None
        )
        code_element = find_child(manufactured_material, "code") if manufactured_material is not None else None
        if build_codeable_concept_from_cd(code_element) is None:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id="cda.immunization-missing-vaccine-code",
                    segment="Immunizations/.../substanceAdministration/consumable",
                    message="An Immunization Activity has no resolvable vaccine code - the converter will silently skip this entry.",
                )
            )
            continue

        if substance_administration.get("negationInd") != "true":
            status_element = find_child(substance_administration, "statusCode")
            status_code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
            if status_code and status_code not in IMMUNIZATION_STATUS_MAP:
                findings.append(
                    ValidationFinding(
                        severity="info",
                        rule_id="cda.immunization-status-unrecognized",
                        segment="Immunizations/.../substanceAdministration/statusCode",
                        message=f"statusCode {status_code!r} is not recognized - the converter will silently default to 'completed'.",
                    )
                )

        occurrence, _ = ivl_ts_bounds(find_child(substance_administration, "effectiveTime"))
        occurrence_dt = parse_comparable_datetime(occurrence) if occurrence else None
        if occurrence_dt is not None:
            if not_in_future(occurrence, now) is False:
                findings.append(
                    ValidationFinding(
                        severity="warning",
                        rule_id="cda.immunization-occurrence-in-future",
                        segment="Immunizations/.../substanceAdministration/effectiveTime",
                        message="An Immunization Activity's occurrence date is in the future.",
                    )
                )
            if raw_birth and birth_dt is not None and is_before(occurrence, occurrence_dt, raw_birth, birth_dt):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        rule_id="cda.immunization-occurrence-before-birth",
                        segment="Immunizations/.../substanceAdministration/effectiveTime",
                        message="An Immunization Activity's occurrence date is before the patient's birthTime.",
                    )
                )
    return findings


def _iter_vital_sign_observations(section):
    """Yield each Vital Sign Observation element found via the same
    organizer/component walk app.cda.vitals.build_vital_signs() uses, so
    validation can never see a different set of entries than conversion
    does."""
    for entry in find_all(section, "entry"):
        organizer = find_child(entry, "organizer")
        if organizer is None or not has_template_id(organizer, VITAL_SIGNS_ORGANIZER_TEMPLATE_ID):
            continue
        for component in find_all(organizer, "component"):
            observation = find_child(component, "observation")
            if observation is None or not has_template_id(observation, VITAL_SIGN_OBSERVATION_TEMPLATE_ID):
                continue
            yield observation


def _rule_vitals(section, now: datetime) -> list[ValidationFinding]:
    findings = []
    for observation in _iter_vital_sign_observations(section):
        if build_codeable_concept_from_cd(find_child(observation, "code")) is None:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id="cda.vitals-missing-code",
                    segment="Vital Signs/.../observation/code",
                    message="A Vital Sign Observation has no resolvable coded value - the converter will silently skip this entry.",
                )
            )
            continue

        occurrence, _ = ivl_ts_bounds(find_child(observation, "effectiveTime"))
        occurrence_dt = parse_comparable_datetime(occurrence) if occurrence else None
        if occurrence_dt is not None and not_in_future(occurrence, now) is False:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    rule_id="cda.vitals-effective-time-in-future",
                    segment="Vital Signs/.../observation/effectiveTime",
                    message="A Vital Sign Observation's effective time is in the future.",
                )
            )
    return findings


def _iter_result_observations(section):
    """Yield each Result Observation element found via the same
    organizer/component walk app.cda.results.build_diagnostic_reports()
    uses, so validation can never see a different set of entries than
    conversion does."""
    for entry in find_all(section, "entry"):
        organizer = find_child(entry, "organizer")
        if organizer is None or not has_template_id(organizer, RESULT_ORGANIZER_TEMPLATE_ID):
            continue
        for component in find_all(organizer, "component"):
            observation = find_child(component, "observation")
            if observation is None or not has_template_id(observation, RESULT_OBSERVATION_TEMPLATE_ID):
                continue
            yield observation


def _rule_results(section, now: datetime) -> list[ValidationFinding]:
    findings = []
    for observation in _iter_result_observations(section):
        if build_codeable_concept_from_cd(find_child(observation, "code")) is None:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id="cda.result-missing-code",
                    segment="Results/.../observation/code",
                    message="A Result Observation has no resolvable coded value - the converter will silently skip this entry.",
                )
            )
            continue

        status_element = find_child(observation, "statusCode")
        status_code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
        if status_code and status_code not in RESULT_STATUS_MAP:
            findings.append(
                ValidationFinding(
                    severity="info",
                    rule_id="cda.result-status-unrecognized",
                    segment="Results/.../observation/statusCode",
                    message=f"statusCode {status_code!r} is not recognized - the converter will silently default to 'unknown'.",
                )
            )

        occurrence, _ = ivl_ts_bounds(find_child(observation, "effectiveTime"))
        occurrence_dt = parse_comparable_datetime(occurrence) if occurrence else None
        if occurrence_dt is not None and not_in_future(occurrence, now) is False:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    rule_id="cda.result-effective-time-in-future",
                    segment="Results/.../observation/effectiveTime",
                    message="A Result Observation's effective time is in the future.",
                )
            )
    return findings


def _iter_procedure_entries(section):
    """Yield each Procedure Activity Procedure element found via the same
    walk app.cda.procedures.build_procedures() uses, so validation can
    never see a different set of entries than conversion does."""
    for entry in find_all(section, "entry"):
        procedure = find_child(entry, "procedure")
        if procedure is None or not has_template_id(procedure, PROCEDURE_TEMPLATE_ID):
            continue
        yield procedure


def _rule_procedures(section, now: datetime) -> list[ValidationFinding]:
    findings = []
    for procedure in _iter_procedure_entries(section):
        if procedure.get("negationInd") != "true":
            status_element = find_child(procedure, "statusCode")
            status_code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
            if status_code and status_code not in PROCEDURE_STATUS_MAP:
                findings.append(
                    ValidationFinding(
                        severity="info",
                        rule_id="cda.procedure-status-unrecognized",
                        segment="Procedures/.../procedure/statusCode",
                        message=f"statusCode {status_code!r} is not recognized - the converter will silently default to 'unknown'.",
                    )
                )

        occurrence, _ = ivl_ts_bounds(find_child(procedure, "effectiveTime"))
        occurrence_dt = parse_comparable_datetime(occurrence) if occurrence else None
        if occurrence_dt is not None and not_in_future(occurrence, now) is False:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    rule_id="cda.procedure-effective-time-in-future",
                    segment="Procedures/.../procedure/effectiveTime",
                    message="A Procedure's effective time is in the future.",
                )
            )
    return findings


def _iter_hospital_discharge_diagnosis_observations(section):
    """Yield each Problem Observation nested under a Hospital Discharge
    Diagnosis Act, via the same walk
    app.cda.hospital_discharge_diagnosis.build_hospital_discharge_diagnoses()
    uses, so validation can never see a different set of entries than
    conversion does."""
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            observation = find_child(relationship, "observation")
            if observation is None or not has_template_id(observation, PROBLEM_OBSERVATION_TEMPLATE_ID):
                continue
            yield observation


def _rule_hospital_discharge_diagnoses(section, patient, now: datetime) -> list[ValidationFinding]:
    return _rule_problem_observations(
        _iter_hospital_discharge_diagnosis_observations(section),
        "cda.hospital-discharge-diagnosis",
        "HospitalDischargeDiagnosis/...",
        patient,
        now,
    )


def _iter_discharge_medication_activities(section):
    """Yield each Medication Activity nested under a Discharge Medication
    Act, via the same walk
    app.cda.discharge_medications.build_discharge_medication_requests()
    uses, so validation can never see a different set of entries than
    conversion does."""
    for entry in find_all(section, "entry"):
        act = find_child(entry, "act")
        if act is None or not has_template_id(act, DISCHARGE_MEDICATION_ACT_TEMPLATE_ID):
            continue
        for relationship in find_all(act, "entryRelationship"):
            if relationship.get("typeCode") != "SUBJ":
                continue
            substance_administration = find_child(relationship, "substanceAdministration")
            if substance_administration is None or not has_template_id(
                substance_administration, MEDICATION_ACTIVITY_TEMPLATE_ID
            ):
                continue
            yield substance_administration


def _rule_discharge_medications(section, now: datetime) -> list[ValidationFinding]:
    return _rule_medication_activities(
        _iter_discharge_medication_activities(section), "cda.discharge-medication", "DischargeMedications/..."
    )


def _check_convertibility(document) -> list[ValidationFinding]:
    # Deferred import: app/cda/registry.py imports app/cda/ccd.py at module
    # load time; this module doesn't need to be part of that load-order
    # dance (nothing in app/cda/registry.py imports this module back), but
    # importing lazily here keeps this module import-order-independent of
    # registry.py the same way ccd.py already is.
    from app.cda.registry import get_document_builder

    try:
        builder = get_document_builder(document)
    except MappingError:
        return [
            ValidationFinding(
                severity="info",
                rule_id="cda.unsupported-document-type",
                message="No builder is registered for this document's templateId(s) - only generic checks were run.",
            )
        ]

    try:
        builder.build_bundle(document)
    except (MappingError, MissingSegmentError, ValidationError) as exc:
        return [
            ValidationFinding(
                severity="error",
                rule_id="cda.would-not-convert",
                message=f"This document would fail to convert to FHIR: {exc}",
            )
        ]
    except Exception:
        logger.exception("Unexpected error while checking convertibility for a C-CDA document")
        return [
            ValidationFinding(
                severity="error",
                rule_id="cda.convertibility-check-failed",
                message="An unexpected internal error occurred while checking whether this document would convert to FHIR.",
            )
        ]
    return []


def validate_document(document) -> ValidationReport:
    findings: list[ValidationFinding] = []
    now = datetime.now(timezone.utc)

    findings.extend(_rule_document_effective_time(document, now))

    patient = _find_patient(document)
    if patient is None:
        # No structural-absence finding needed here beyond this - the
        # convertibility check below raises the real MissingSegmentError
        # from build_patient_from_header and turns it into an error finding,
        # the same "don't re-derive a parallel required-fields table"
        # decision app/validation/engine.py already made for HL7v2.
        pass
    else:
        findings.extend(_rule_patient_name_missing(patient))
        findings.extend(_rule_patient_gender_unrecognized(patient))
        findings.extend(_rule_patient_birth_date(patient, now))

    component_of = find_child(document, "componentOf")
    encompassing_encounter = find_child(component_of, "encompassingEncounter") if component_of is not None else None
    if encompassing_encounter is not None:
        findings.extend(_rule_encounter(encompassing_encounter, now))

    problems_section = _find_problems_section(document)
    if problems_section is not None:
        findings.extend(_rule_problems(problems_section, patient, now))

    medications_section = _find_medications_section(document)
    if medications_section is not None:
        findings.extend(_rule_medications(medications_section, now))

    allergies_section = _find_allergies_section(document)
    if allergies_section is not None:
        findings.extend(_rule_allergies(allergies_section, patient, now))

    immunizations_section = _find_immunizations_section(document)
    if immunizations_section is not None:
        findings.extend(_rule_immunizations(immunizations_section, patient, now))

    vitals_section = _find_vitals_section(document)
    if vitals_section is not None:
        findings.extend(_rule_vitals(vitals_section, now))

    results_section = _find_results_section(document)
    if results_section is not None:
        findings.extend(_rule_results(results_section, now))

    procedures_section = _find_procedures_section(document)
    if procedures_section is not None:
        findings.extend(_rule_procedures(procedures_section, now))

    hospital_discharge_diagnosis_section = _find_hospital_discharge_diagnosis_section(document)
    if hospital_discharge_diagnosis_section is not None:
        findings.extend(_rule_hospital_discharge_diagnoses(hospital_discharge_diagnosis_section, patient, now))

    discharge_medications_section = _find_discharge_medications_section(document)
    if discharge_medications_section is not None:
        findings.extend(_rule_discharge_medications(discharge_medications_section, now))

    findings.extend(_check_convertibility(document))

    is_valid = not any(finding.severity == "error" for finding in findings)
    return ValidationReport(
        message_type="CDA",
        trigger_event=_resolve_trigger_event(document),
        is_valid=is_valid,
        findings=findings,
    )
