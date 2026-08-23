"""Synthetic CCD (Continuity of Care Document) generator - the app/cda/
mirror of app/generators/{adt,siu,oru,mdm}.py, but living inside app/cda/
rather than app/generators/: app/cda/ is already the one-stop package for
everything CDA-specific (parser + conversion), and there's only one C-CDA
document type so far, so a separate app/generators/cda.py with its own
sub-registry would be premature relative to HL7v2's real 4-type/20-trigger
registry need.

Reuses app.generators.base's format-agnostic primitives (maybe(),
random_person_name(), random_address(), random_phone(), random_sex(),
random_identifier(), random_datetime_near_now()/format_hl7_datetime()) -
format_hl7_datetime()'s YYYYMMDDHHMMSS output is exactly CDA's own @value
TS digit shape (CDA and HL7v2 TS fields share the identical digit shape,
already established for parse_hl7_date/parse_hl7_datetime reuse in
app/cda/common.py). Net-new here is only the SNOMED problem-code pool and
the XML-shaped field builders.

Built via f-string templates, the same construction style as this
project's hand-authored tests/fixtures/ccd_*.xml, rather than
xml.etree.ElementTree tree-building - consistent with how the HL7v2
generator builds segments via plain string joins rather than the hl7
library. Self-checked by parsing the result back via
app.cda.parser.parse_document() before returning, mirroring the HL7v2
generators' parse_message() self-check - a generator bug should raise
CdaParseError, not return broken XML. Full-conversion-success is left to
the test suite's round-trip check (tests/test_generate_cda.py), matching
the HL7v2 generators' precedent exactly.
"""

import random
import xml.etree.ElementTree as ET

from app.cda.allergies import (
    ALLERGY_CONCERN_ACT_TEMPLATE_ID,
    ALLERGY_OBSERVATION_TEMPLATE_ID,
    ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID,
    CLINICAL_STATUS_MAP as ALLERGY_CLINICAL_STATUS_MAP,
    CRITICALITY_MAP,
    CRITICALITY_OBSERVATION_TEMPLATE_ID,
    REACTION_OBSERVATION_TEMPLATE_ID,
    SEVERITY_MAP,
    SEVERITY_OBSERVATION_TEMPLATE_ID,
)
from app.cda.allergies import SECTION_TEMPLATE_ID as ALLERGIES_SECTION_TEMPLATE_ID
from app.cda.allergies import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
from app.cda.ccd import CCD_TEMPLATE_ID
from app.cda.discharge_medications import DISCHARGE_MEDICATION_ACT_TEMPLATE_ID
from app.cda.discharge_medications import SECTION_TEMPLATE_ID as DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.discharge_summary import DISCHARGE_SUMMARY_TEMPLATE_ID
from app.cda.history_and_physical import HISTORY_AND_PHYSICAL_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import SECTION_TEMPLATE_ID as HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID
from app.cda.immunizations import IMMUNIZATION_ACTIVITY_TEMPLATE_ID
from app.cda.medications import STATUS_MAP as MEDICATION_STATUS_MAP
from app.cda.immunizations import SECTION_TEMPLATE_ID as IMMUNIZATIONS_SECTION_TEMPLATE_ID
from app.cda.immunizations import STATUS_MAP as IMMUNIZATION_STATUS_MAP
from app.cda.medications import FREE_TEXT_SIG_TEMPLATE_ID, MEDICATION_ACTIVITY_TEMPLATE_ID, STATUS_MAP
from app.cda.medications import SECTION_TEMPLATE_ID as MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.family_history import AGE_OBSERVATION_TEMPLATE_ID, DEATH_OBSERVATION_TEMPLATE_ID
from app.cda.family_history import ORGANIZER_TEMPLATE_ID as FAMILY_HISTORY_ORGANIZER_TEMPLATE_ID
from app.cda.family_history import OBSERVATION_TEMPLATE_ID as FAMILY_HISTORY_OBSERVATION_TEMPLATE_ID
from app.cda.narrative_sections import (
    ASSESSMENT_TEMPLATE_ID,
    FAMILY_HISTORY_TEMPLATE_ID,
    GENERAL_STATUS_TEMPLATE_ID,
    HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
    HOSPITAL_COURSE_TEMPLATE_ID,
    PHYSICAL_EXAM_TEMPLATE_ID,
    PLAN_OF_TREATMENT_TEMPLATE_ID,
    REASON_FOR_VISIT_TEMPLATE_ID,
    REVIEW_OF_SYSTEMS_TEMPLATE_ID,
    SOCIAL_HISTORY_TEMPLATE_ID,
)
from app.cda.parser import CDA_NS, parse_document
from app.cda.plan_of_treatment import PLANNED_OBSERVATION_TEMPLATE_ID, PLANNED_PROCEDURE_TEMPLATE_ID
from app.cda.social_history import OBSERVATION_TEMPLATE_ID as SOCIAL_HISTORY_OBSERVATION_TEMPLATE_ID
from app.cda.social_history import SMOKING_STATUS_TEMPLATE_ID
from app.cda.problems import (
    CONCERN_ACT_TEMPLATE_ID,
    PROBLEM_OBSERVATION_TEMPLATE_ID,
    STATUS_OBSERVATION_CODE,
    STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS,
)
from app.cda.problems import SECTION_TEMPLATE_ID as PROBLEMS_SECTION_TEMPLATE_ID
from app.cda.procedures import PROCEDURE_TEMPLATE_ID
from app.cda.procedures import SECTION_TEMPLATE_ID as PROCEDURES_SECTION_TEMPLATE_ID
from app.cda.procedures import (
    SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as PROCEDURES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL,
)
from app.cda.procedures import STATUS_MAP as PROCEDURE_STATUS_MAP
from app.cda.procedures import SERVICE_DELIVERY_LOCATION_TEMPLATE_ID
from app.cda.procedures import INDICATION_TEMPLATE_ID, COMMENT_ACTIVITY_TEMPLATE_ID
from app.cda.results import ORGANIZER_TEMPLATE_ID as RESULT_ORGANIZER_TEMPLATE_ID
from app.cda.results import OBSERVATION_TEMPLATE_ID as RESULT_OBSERVATION_TEMPLATE_ID
from app.cda.results import SECTION_TEMPLATE_ID as RESULTS_SECTION_TEMPLATE_ID
from app.cda.results import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as RESULTS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
from app.cda.results import STATUS_MAP as RESULT_STATUS_MAP
from app.cda.vitals import ORGANIZER_TEMPLATE_ID as VITAL_SIGNS_ORGANIZER_TEMPLATE_ID
from app.cda.vitals import OBSERVATION_TEMPLATE_ID as VITAL_SIGN_OBSERVATION_TEMPLATE_ID
from app.cda.vitals import SECTION_TEMPLATE_ID as VITALS_SECTION_TEMPLATE_ID
from app.cda.vitals import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as VITALS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL
from app.generators.base import (
    format_hl7_datetime,
    maybe,
    random_address,
    random_datetime_near_now,
    random_identifier,
    random_person_name,
    random_phone,
    random_sex,
    random_time_range,
)

_US_HEADER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.1"
_HEX_DIGITS = "0123456789abcdef"

# A representative SNOMED CT problem pool - overlaps with the codes used in
# tests/fixtures/ccd_*.xml plus a few more, for realistic fuzz variety.
_PROBLEM_CODES = [
    ("38341003", "Hypertensive disorder"),
    ("44054006", "Type 2 diabetes mellitus"),
    ("195967001", "Asthma"),
    ("271737000", "Anemia"),
    ("396275006", "Osteoarthritis"),
    ("444814009", "Viral sinusitis"),
    ("10509002", "Acute bronchitis"),
    ("367498001", "Seasonal allergic rhinitis"),
    ("370143000", "Major depressive disorder"),
    ("235595009", "Gastroesophageal reflux disease"),
    ("55822004", "Hyperlipidemia"),
    ("37796009", "Migraine"),
]
_RECOGNIZED_ENCOUNTER_CLASS_CODES = ["AMB", "EMER", "IMP"]
_UNRECOGNIZED_ENCOUNTER_CLASS_CODES = ["XYZ", "ZZZ", "UNK"]

# A representative RxNorm medication pool - overlaps with the codes used in
# tests/fixtures/ccd_medications_*.xml plus a few more, for realistic fuzz
# variety.
_MEDICATION_CODES = [
    ("314076", "Lisinopril 10 MG Oral Tablet"),
    ("308191", "Amoxicillin 500 MG Oral Capsule"),
    ("197361", "Ibuprofen 200 MG Oral Tablet"),
    ("206805", "Atorvastatin 20 MG Oral Tablet"),
    ("310965", "Metformin 500 MG Oral Tablet"),
    ("197806", "Levothyroxine 50 MCG Oral Tablet"),
    ("745679", "Albuterol 90 MCG Inhalant Solution"),
]
_MEDICATION_ROUTES = [("C38288", "ORAL"), ("C38276", "INTRAVENOUS"), ("C38299", "TOPICAL")]
_SIG_TEXTS = [
    "Take one tablet by mouth once daily",
    "Take one capsule by mouth three times daily until gone",
    "Apply to affected area twice daily",
    "Inhale two puffs every 4 to 6 hours as needed",
]

# Allergy-Intolerance Observation value pool - each code drives both
# _TYPE_MAP and _CATEGORY_MAP in app/cda/allergies.py; 419199007 is
# deliberately included even though it's type-mapped but NOT category-
# mapped, for direct fuzz coverage of that partial-mapping gap.
_ALLERGY_TYPE_CODES = ["235719002", "414285001", "416098002", "419199007", "59037007"]
_ALLERGEN_CODES = [
    ("102263004", "Eggs (edible)"),
    ("91935009", "Peanut"),
    ("7984002", "Penicillin"),
    ("387406002", "Sulfonamide"),
    ("111088007", "Latex"),
    ("227037002", "Shellfish"),
    ("763875007", "Tree nut"),
]
_REACTION_CODES = [
    ("247472004", "Wheal"),
    ("271807003", "Skin rash"),
    ("39579001", "Anaphylaxis"),
    ("422587007", "Nausea"),
    ("21522001", "Abdominal pain"),
]

# A representative CVX vaccine-code pool (codeSystem 2.16.840.1.113883.12.292).
_VACCINE_CODES = [
    ("88", "influenza virus vaccine, unspecified formulation"),
    ("187", "zoster vaccine, live"),
    ("115", "Tdap"),
    ("133", "pneumococcal conjugate vaccine, 13 valent"),
    ("08", "hepatitis B vaccine, pediatric or pediatric/adolescent dosage"),
    ("03", "MMR"),
]
_IMMUNIZATION_ROUTES = [("C28161", "INTRAMUSCULAR"), ("C38299", "TOPICAL"), ("C38304", "SUBCUTANEOUS")]

# Vital Sign Observation codes (LOINC), each with a realistic unit and
# value range - overlaps with the codes a real C-CDA-Examples CCD used
# while researching this section, plus a few more for fuzz variety.
_VITAL_SIGN_CODES = [
    ("8302-2", "Body height", "cm", (150.0, 200.0)),
    ("3141-9", "Body weight", "kg", (45.0, 120.0)),
    ("8480-6", "Systolic blood pressure", "mm[Hg]", (95.0, 160.0)),
    ("8462-4", "Diastolic blood pressure", "mm[Hg]", (55.0, 100.0)),
    ("8867-4", "Heart rate", "/min", (55.0, 110.0)),
    ("8310-5", "Body temperature", "Cel", (36.0, 39.0)),
    ("9279-1", "Respiratory rate", "/min", (12.0, 22.0)),
    ("2708-6", "Oxygen saturation", "%", (90.0, 100.0)),
]
_INTERPRETATION_CODES = ["N", "H", "L"]

# Result Observation codes (LOINC), each with a realistic unit and value
# range - a representative basic metabolic/CBC-shaped pool.
_RESULT_OBSERVATION_CODES = [
    ("718-7", "Hemoglobin", "g/dL", (11.0, 16.0)),
    ("6690-2", "Leukocytes", "10*3/uL", (4.0, 11.0)),
    ("777-3", "Platelets", "10*3/uL", (150.0, 400.0)),
    ("2160-0", "Creatinine", "mg/dL", (0.6, 1.3)),
    ("2345-7", "Glucose", "mg/dL", (70.0, 140.0)),
    ("2951-2", "Sodium", "mmol/L", (135.0, 145.0)),
]
_RESULT_PANEL_CODES = [
    ("58410-2", "CBC panel"),
    ("24323-8", "Comprehensive metabolic panel"),
    ("57021-8", "CBC W Auto Differential panel"),
]

# Procedure Activity Procedure codes (SNOMED CT) and body-site codes -
# overlaps with the code the real C-CDA-Examples CCD used while
# researching this section, plus a few more for fuzz variety.
_PROCEDURE_CODES = [
    ("80146002", "Excision of appendix"),
    ("73761001", "Colonoscopy"),
    ("232717009", "Coronary artery bypass graft"),
    ("18946005", "Fracture reduction"),
    ("174040003", "Excisional biopsy of skin lesion"),
]
_BODY_SITE_CODES = [
    ("66019005", "Colon structure"),
    ("80144004", "Appendix structure"),
    ("51185008", "Thoracic structure"),
    ("53120007", "Upper limb structure"),
]

# Blood Pressure Panel / Pulse Oximetry Panel codes (LOINC) - direct fuzz
# coverage of app/cda/vitals.py's own grouping detection, generated
# deliberately (not left to chance via _VITAL_SIGN_CODES' own coincidental
# overlap with these same codes).
_BP_SYSTOLIC = ("8480-6", "Systolic blood pressure", "mm[Hg]", 95.0, 160.0)
_BP_DIASTOLIC = ("8462-4", "Diastolic blood pressure", "mm[Hg]", 55.0, 100.0)
_PULSE_OX_PRIMARY = ("59408-5", "Oxygen saturation", "%", 90.0, 100.0)
_PULSE_OX_CONCENTRATION = ("3150-0", "Inhaled oxygen concentration", "%", 21.0, 100.0)
_PULSE_OX_FLOW_RATE = ("3151-8", "Inhaled oxygen flow rate", "L/min", 1.0, 15.0)

# Specimen codes (SNOMED CT) - direct fuzz coverage of app/cda/results.py's
# own specimen attachment (organizer-level and observation-level).
_SPECIMEN_CODES = [
    ("119297000", "Blood specimen"),
    ("122575003", "Urine specimen"),
    ("258500001", "Nasopharyngeal swab"),
]

# Performer/participant pools - direct fuzz coverage of app/cda/
# procedures.py's own performer (Practitioner+PractitionerRole+
# Organization+Location) and participant (Service Delivery Location)
# handling.
_PERFORMER_NAMES = [("John", "Smith"), ("Maria", "Garcia"), ("Wei", "Chen")]
_ORGANIZATION_NAMES = ["General Hospital", "Valley Health Clinic", "Riverside Surgical Center"]
_SERVICE_DELIVERY_LOCATION_NAMES = ["Medical Ward", "Same Day Surgery Unit", "Outpatient Procedure Suite"]


def _pretty_print(root) -> str:
    """Indent a generated document so a human can actually read it - the
    f-string templates below produce one unbroken ~16KB line otherwise.

    Uses ElementTree.indent() rather than xml.dom.minidom.toprettyxml():
    minidom injects whitespace into *mixed* content, which would corrupt
    the narrative <text> blocks (a <paragraph> with inline <content>
    anchors is exactly that shape). ET.indent leaves any element that
    already has text content alone, so narrative survives untouched -
    verified, not assumed: across 180 generated documents (all three
    document types, 691 narrative <text> blocks) the converted Bundle is
    byte-identical before and after indenting, and
    extract_narrative_text() returns the same string for every block.

    ET.tostring() drops the XML declaration, so it's re-added here to
    match what the templates themselves emit."""
    ET.indent(root, space="  ")
    # Templates declare the CDA namespace as the default one; register it
    # so ET.tostring doesn't rewrite every tag with an "ns0:" prefix.
    ET.register_namespace("", CDA_NS)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _random_uuid_like(rng: random.Random) -> str:
    """A UUID-shaped root string, driven entirely by `rng` (unlike
    uuid.uuid4(), which uses its own unseeded internal randomness and would
    silently break this generator's "same seed -> same output byte-for-
    byte" guarantee - every generator function in this project must take
    all its randomness from the passed-in rng, never a global source)."""

    def group(n: int) -> str:
        return "".join(rng.choice(_HEX_DIGITS) for _ in range(n))

    return f"{group(8)}-{group(4)}-{group(4)}-{group(4)}-{group(12)}"


def _random_id_element(rng: random.Random) -> str:
    """Randomly root-only or root+extension (~50/50) - direct fuzz coverage
    of both II identifier shapes app/cda/common.py::_build_identifier
    handles."""
    root = _random_uuid_like(rng)
    if maybe(rng, 0.5):
        return f'<id root="{root}"/>'
    return f'<id root="{root}" extension="{random_identifier(rng, digits=6)}"/>'


def _random_name_element(rng: random.Random, sex: str | None, use: str) -> str:
    family, given = random_person_name(rng, sex=sex)
    return f'<name use="{use}"><given>{given}</given><family>{family}</family></name>'


def _random_addr_element(rng: random.Random, use: str) -> str:
    line1, city, state, zip_code = random_address(rng)
    return (
        f'<addr use="{use}"><streetAddressLine>{line1}</streetAddressLine>'
        f"<city>{city}</city><state>{state}</state>"
        f'<postalCode>{zip_code}</postalCode><country>US</country></addr>'
    )


def _random_telecom_element(rng: random.Random, use: str) -> str:
    return f'<telecom use="{use}" value="tel:{random_phone(rng)}"/>'


def _random_ivl_ts(rng: random.Random, start, end) -> str:
    """One of the three legal IVL_TS shapes (bare @value, low-only,
    low+high), split ~20/40/40 - direct fuzz coverage of all three shapes
    app.cda.parser.ivl_ts_bounds() handles. `end` is always start + a
    positive duration (from random_time_range), so the low+high shape can
    never accidentally produce an inverted (high < low) interval."""
    choice = rng.random()
    if choice < 0.2:
        return f'<effectiveTime value="{format_hl7_datetime(start)}"/>'
    if choice < 0.6:
        return f'<effectiveTime><low value="{format_hl7_datetime(start)}"/></effectiveTime>'
    return (
        f'<effectiveTime><low value="{format_hl7_datetime(start)}"/>'
        f'<high value="{format_hl7_datetime(end)}"/></effectiveTime>'
    )


def _random_encounter(rng: random.Random, force: bool = False) -> str | None:
    if not force and not maybe(rng, 0.5):
        return None
    ids = "".join(_random_id_element(rng) for _ in range(rng.choice((1, 2))))
    if maybe(rng, 0.85):
        class_code = rng.choice(_RECOGNIZED_ENCOUNTER_CLASS_CODES)
    else:
        class_code = rng.choice(_UNRECOGNIZED_ENCOUNTER_CLASS_CODES)
    # A wide-ish window (including a bit of future) gives fuzz coverage of
    # the "period start in the future" warning without ever risking start >
    # end (random_time_range always derives end = start + a positive
    # duration), so this can never trip the (error-severity) "period end
    # before start" rule.
    start, end = random_time_range(rng, min_days=-30, max_days=0)
    return (
        f'<componentOf><encompassingEncounter>{ids}'
        f'<code code="{class_code}" codeSystem="2.16.840.1.113883.5.4"/>'
        f"{_random_ivl_ts(rng, start, end)}"
        f"</encompassingEncounter></componentOf>"
    )


def _random_problem_entry(rng: random.Random) -> str:
    act_id = _random_uuid_like(rng)
    obs_id = _random_uuid_like(rng)
    act_status = "active" if maybe(rng, 0.7) else rng.choice(("suspended", "aborted", "completed"))
    code, display = rng.choice(_PROBLEM_CODES)
    negated = maybe(rng, 0.1)

    # Onset/abatement stay within the last ~300 days through 10 days into
    # the future - always safely after even the earliest possible generated
    # birthTime (at least ~364 days ago, since random_datetime_near_now adds
    # up to +23:59 on top of the -365-day floor before truncating to a date
    # - see _random_patient below), so this can never trip the (error-
    # severity) "onset before birth" rule, while still occasionally landing
    # in the future to exercise the (warning-severity) "onset in the
    # future" rule.
    start, end = random_time_range(rng, min_days=-300, max_days=0)
    effective_time = _random_ivl_ts(rng, start, end)

    if negated:
        value = '<value xsi:type="CD" nullFlavor="NA"/>'
    else:
        value = f'<value xsi:type="CD" code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
    negation_attr = ' negationInd="true"' if negated else ""

    # ~50/50: rely on the Concern Act's own statusCode alone, or add a
    # nested Status Observation (typeCode="REFR") - direct fuzz coverage of
    # the two-vocabulary clinicalStatus resolution path in
    # app/cda/problems.py::_resolve_clinical_status.
    status_observation = ""
    if maybe(rng, 0.5):
        status_code = rng.choice(list(STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS))
        status_observation = (
            '<entryRelationship typeCode="REFR"><observation classCode="OBS" moodCode="EVN">'
            f'<code code="{STATUS_OBSERVATION_CODE}" codeSystem="2.16.840.1.113883.6.1" displayName="Status"/>'
            '<statusCode code="completed"/>'
            f'<value xsi:type="CD" code="{status_code}" codeSystem="2.16.840.1.113883.6.96"/>'
            "</observation></entryRelationship>"
        )

    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{CONCERN_ACT_TEMPLATE_ID}"/><id root="{act_id}"/>'
        '<code code="CONC" codeSystem="2.16.840.1.113883.5.6" displayName="Concern"/>'
        f'<statusCode code="{act_status}"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN"{negation_attr}>'
        f'<templateId root="{PROBLEM_OBSERVATION_TEMPLATE_ID}"/><id root="{obs_id}"/>'
        '<code code="55607006" codeSystem="2.16.840.1.113883.6.96" displayName="Problem"/>'
        '<statusCode code="completed"/>'
        f"{effective_time}{value}{status_observation}"
        "</observation></entryRelationship></act></entry>"
    )


def _random_problems_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.85):
        return None
    entries = "".join(_random_problem_entry(rng) for _ in range(rng.randint(1, 3)))
    return (
        f'<component><section><templateId root="{PROBLEMS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11450-4" codeSystem="2.16.840.1.113883.6.1" displayName="Problem List"/>'
        f"<title>Problems</title>{entries}</section></component>"
    )


def _random_hospital_discharge_diagnosis_entry(rng: random.Random) -> str:
    # Wraps the identical Problem Observation shape _random_problem_entry
    # builds, inside a Hospital Discharge Diagnosis Act rather than
    # Problems' own Concern Act - see app/cda/hospital_discharge_diagnosis.py.
    act_id = _random_uuid_like(rng)
    obs_id = _random_uuid_like(rng)
    code, display = rng.choice(_PROBLEM_CODES)
    start, end = random_time_range(rng, min_days=-14, max_days=0)
    effective_time = _random_ivl_ts(rng, start, end)
    value = f'<value xsi:type="CD" code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{HOSPITAL_DISCHARGE_DIAGNOSIS_ACT_TEMPLATE_ID}"/><id root="{act_id}"/>'
        '<code code="11535-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital Discharge Diagnosis"/>'
        '<statusCode code="active"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{PROBLEM_OBSERVATION_TEMPLATE_ID}"/><id root="{obs_id}"/>'
        '<code code="55607006" codeSystem="2.16.840.1.113883.6.96" displayName="Problem"/>'
        '<statusCode code="completed"/>'
        f"{effective_time}{value}"
        "</observation></entryRelationship></act></entry>"
    )


def _random_hospital_discharge_diagnosis_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.7):
        return None
    entries = "".join(_random_hospital_discharge_diagnosis_entry(rng) for _ in range(rng.randint(1, 2)))
    return (
        f'<component><section><templateId root="{HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11535-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital Discharge Diagnosis"/>'
        f"<title>Discharge Diagnosis</title>{entries}</section></component>"
    )


def _random_medication_entry(rng: random.Random) -> str:
    subad_id = _random_uuid_like(rng)
    mood_code = "INT" if maybe(rng, 0.5) else "EVN"
    # Mostly recognized statusCode values (exercising every row of
    # STATUS_MAP at least occasionally), rarely an unrecognized one -
    # direct fuzz coverage of _resolve_status's "unknown" fallback branch.
    if maybe(rng, 0.85):
        status_code = rng.choice(list(STATUS_MAP))
    else:
        status_code = rng.choice(("new", "held"))
    negated = maybe(rng, 0.1)
    negation_attr = ' negationInd="true"' if negated else ""
    code, display = rng.choice(_MEDICATION_CODES)

    # Structured dosing (route/dose/rate/effectiveTime) and free-text SIG
    # are alternatives in real C-CDA, not always both present - split
    # ~45/35/20 across structured-only, free-text-only, and neither, direct
    # fuzz coverage of _build_dosage's "no dosage info at all -> None"
    # branch alongside its two populated branches.
    dosing_choice = rng.random()
    dosing = ""
    if dosing_choice < 0.45:
        route_code, route_display = rng.choice(_MEDICATION_ROUTES)
        start, end = random_time_range(rng, min_days=-60, max_days=0)
        dose_value = rng.choice((5, 10, 20, 25, 50, 100, 200, 500))
        rate = f'<rateQuantity value="{rng.choice((1, 2, 5))}" unit="mL/h"/>' if maybe(rng, 0.15) else ""
        dosing = (
            f"{_random_ivl_ts(rng, start, end)}"
            f'<routeCode code="{route_code}" codeSystem="2.16.840.1.113883.3.26.1.1" displayName="{route_display}"/>'
            f'<doseQuantity value="{dose_value}" unit="mg"/>{rate}'
        )
    elif dosing_choice < 0.80:
        sig_text = rng.choice(_SIG_TEXTS)
        dosing = (
            f'<entryRelationship typeCode="COMP"><substanceAdministration classCode="SBADM" moodCode="{mood_code}">'
            f'<templateId root="{FREE_TEXT_SIG_TEMPLATE_ID}"/>'
            '<code code="76662-6" codeSystem="2.16.840.1.113883.6.1" displayName="Medication Instructions"/>'
            f"<text>{sig_text}</text>"
            '<consumable><manufacturedProduct classCode="MANU">'
            '<manufacturedLabeledDrug nullFlavor="NA"/>'
            "</manufacturedProduct></consumable>"
            "</substanceAdministration></entryRelationship>"
        )

    return (
        f'<entry typeCode="DRIV"><substanceAdministration classCode="SBADM" moodCode="{mood_code}"{negation_attr}>'
        f'<templateId root="{MEDICATION_ACTIVITY_TEMPLATE_ID}"/><id root="{subad_id}"/>'
        f'<statusCode code="{status_code}"/>'
        f"{dosing}"
        '<consumable><manufacturedProduct classCode="MANU">'
        f'<templateId root="2.16.840.1.113883.10.20.22.4.23"/>'
        f'<manufacturedMaterial><code code="{code}" codeSystem="2.16.840.1.113883.6.88" displayName="{display}"/></manufacturedMaterial>'
        "</manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
    )


def _random_medications_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.85):
        return None
    entries = "".join(_random_medication_entry(rng) for _ in range(rng.randint(1, 3)))
    return (
        f'<component><section><templateId root="{MEDICATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="10160-0" codeSystem="2.16.840.1.113883.6.1" displayName="History of medication use"/>'
        f"<title>Medications</title>{entries}</section></component>"
    )


def _random_discharge_medication_entry(rng: random.Random) -> str:
    # Wraps the identical Medication Activity shape _random_medication_entry
    # builds, inside a Discharge Medication Act rather than a bare
    # substanceAdministration - see app/cda/discharge_medications.py.
    subad_id = _random_uuid_like(rng)
    code, display = rng.choice(_MEDICATION_CODES)
    status_code = rng.choice(list(STATUS_MAP)) if maybe(rng, 0.85) else "new"
    dosing = ""
    if maybe(rng, 0.7):
        route_code, route_display = rng.choice(_MEDICATION_ROUTES)
        dose_value = rng.choice((5, 10, 20, 25, 50, 100, 200, 500))
        dosing = (
            f'<routeCode code="{route_code}" codeSystem="2.16.840.1.113883.3.26.1.1" displayName="{route_display}"/>'
            f'<doseQuantity value="{dose_value}" unit="mg"/>'
        )
    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{DISCHARGE_MEDICATION_ACT_TEMPLATE_ID}"/>'
        '<code code="10183-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital discharge medication"/>'
        '<statusCode code="completed"/>'
        f'<entryRelationship typeCode="SUBJ"><substanceAdministration classCode="SBADM" moodCode="EVN">'
        f'<templateId root="{MEDICATION_ACTIVITY_TEMPLATE_ID}"/><id root="{subad_id}"/>'
        f'<statusCode code="{status_code}"/>'
        f"{dosing}"
        '<consumable><manufacturedProduct classCode="MANU">'
        f'<manufacturedMaterial><code code="{code}" codeSystem="2.16.840.1.113883.6.88" displayName="{display}"/></manufacturedMaterial>'
        "</manufacturedProduct></consumable>"
        "</substanceAdministration></entryRelationship></act></entry>"
    )


def _random_discharge_medications_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.7):
        return None
    entries = "".join(_random_discharge_medication_entry(rng) for _ in range(rng.randint(1, 2)))
    return (
        f'<component><section><templateId root="{DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="10183-2" codeSystem="2.16.840.1.113883.6.1" displayName="Hospital Discharge Medications"/>'
        f"<title>Discharge Medications</title>{entries}</section></component>"
    )


def _random_reaction_entry(rng: random.Random, start, end) -> str:
    code, display = rng.choice(_REACTION_CODES)
    reaction_id = _random_uuid_like(rng)
    severity = ""
    if maybe(rng, 0.6):
        severity_code = rng.choice(list(SEVERITY_MAP))
        severity = (
            f'<entryRelationship typeCode="SUBJ" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{SEVERITY_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="SEV" codeSystem="2.16.840.1.113883.5.4"/><statusCode code="completed"/>'
            f'<value xsi:type="CD" code="{severity_code}" codeSystem="2.16.840.1.113883.6.96"/>'
            "</observation></entryRelationship>"
        )
    return (
        f'<entryRelationship typeCode="MFST" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{REACTION_OBSERVATION_TEMPLATE_ID}"/><id root="{reaction_id}"/>'
        '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/><statusCode code="completed"/>'
        f"{_random_ivl_ts(rng, start, end)}"
        f'<value xsi:type="CD" code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
        f"{severity}"
        "</observation></entryRelationship>"
    )


def _random_allergy_entry(rng: random.Random) -> str:
    act_id = _random_uuid_like(rng)
    obs_id = _random_uuid_like(rng)
    type_code = rng.choice(_ALLERGY_TYPE_CODES)
    negated = maybe(rng, 0.1)

    # Same window/rationale as _random_problem_entry - always safely after
    # even the earliest possible generated birthTime.
    start, end = random_time_range(rng, min_days=-300, max_days=0)
    effective_time = _random_ivl_ts(rng, start, end)

    # A negated entry can still carry a resolvable allergen (-> "no known
    # allergy to X" text) or a nullFlavor one (-> "no known allergies") -
    # direct fuzz coverage of both _resolve_allergen_code negation branches.
    if negated and maybe(rng, 0.3):
        participant = (
            '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
            '<code nullFlavor="NA"/>'
            "</playingEntity></participantRole></participant>"
        )
    else:
        allergen_code, allergen_display = rng.choice(_ALLERGEN_CODES)
        participant = (
            '<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
            f'<code code="{allergen_code}" codeSystem="2.16.840.1.113883.6.96" displayName="{allergen_display}"/>'
            "</playingEntity></participantRole></participant>"
        )
    negation_attr = ' negationInd="true"' if negated else ""

    author = f'<author><time value="{format_hl7_datetime(start)}"/></author>' if maybe(rng, 0.6) else ""

    # ~50/50: rely on the fixed "active" default, or add a nested Status
    # Observation - direct fuzz coverage of _resolve_clinical_status's two
    # branches, mirroring Problems' identical split.
    status_observation = ""
    if maybe(rng, 0.5):
        status_code = rng.choice(list(ALLERGY_CLINICAL_STATUS_MAP))
        status_observation = (
            f'<entryRelationship typeCode="REFR"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{ALLERGY_STATUS_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="33999-4" codeSystem="2.16.840.1.113883.6.1" displayName="Status"/>'
            '<statusCode code="completed"/>'
            f'<value xsi:type="CD" code="{status_code}" codeSystem="2.16.840.1.113883.6.96"/>'
            "</observation></entryRelationship>"
        )

    criticality = ""
    if maybe(rng, 0.4):
        criticality_code = rng.choice(list(CRITICALITY_MAP))
        criticality = (
            f'<entryRelationship typeCode="SUBJ" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{CRITICALITY_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="82606-5" codeSystem="2.16.840.1.113883.6.1" displayName="Criticality"/>'
            '<statusCode code="completed"/>'
            f'<value xsi:type="CD" code="{criticality_code}" codeSystem="2.16.840.1.113883.5.1063"/>'
            "</observation></entryRelationship>"
        )

    reaction = _random_reaction_entry(rng, start, end) if maybe(rng, 0.5) else ""

    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{ALLERGY_CONCERN_ACT_TEMPLATE_ID}"/><id root="{act_id}"/>'
        '<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/><statusCode code="active"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN"{negation_attr}>'
        f'<templateId root="{ALLERGY_OBSERVATION_TEMPLATE_ID}"/><id root="{obs_id}"/>'
        '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/><statusCode code="completed"/>'
        f'{effective_time}<value xsi:type="CD" code="{type_code}" codeSystem="2.16.840.1.113883.6.96"/>'
        f"{author}{participant}{status_observation}{criticality}{reaction}"
        "</observation></entryRelationship></act></entry>"
    )


def _random_allergies_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.85):
        return None
    # ~25% "entries optional" templateId instead of "entries required" -
    # both wrap the identical entry shape (see app/cda/allergies.py), but
    # a real Discharge Summary example used the "entries optional" variant
    # specifically, and app/cda/validation.py once recognized only the
    # "entries required" one for its rule dispatch (a bug caught by code
    # review, not this generator, since it never emitted the other variant
    # until now) - this exists so that gap can't silently reopen unnoticed.
    section_template_id = (
        ALLERGIES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL if maybe(rng, 0.25) else ALLERGIES_SECTION_TEMPLATE_ID
    )
    entries = "".join(_random_allergy_entry(rng) for _ in range(rng.randint(1, 3)))
    return (
        f'<component><section><templateId root="{section_template_id}"/>'
        '<code code="48765-2" codeSystem="2.16.840.1.113883.6.1" displayName="Allergies and adverse reactions"/>'
        f"<title>Allergies</title>{entries}</section></component>"
    )


def _random_immunization_entry(rng: random.Random, start, end) -> str:
    sub_id = _random_uuid_like(rng)
    # ~80% EVN (administered/refused) -> Immunization, ~20% INT (planned)
    # -> MedicationRequest. Both convert, via different field maps.
    mood_code = "EVN" if maybe(rng, 0.8) else "INT"
    # negationInd means different things per mood: on EVN it overrides
    # status to not-done, on INT it maps to doNotPerform either way - so
    # INT gets an explicit "false" sometimes too, which EVN never needs.
    if mood_code == "EVN":
        negation_attr = ' negationInd="true"' if maybe(rng, 0.15) else ""
    else:
        negation_attr = f' negationInd="{"true" if maybe(rng, 0.3) else "false"}"' if maybe(rng, 0.5) else ""
    # Mostly a recognized statusCode, rarely an unrecognized one to
    # exercise each mood's own fallback. The two moods resolve status
    # through different ConceptMaps.
    status_pool = IMMUNIZATION_STATUS_MAP if mood_code == "EVN" else MEDICATION_STATUS_MAP
    status_code = rng.choice(list(status_pool)) if maybe(rng, 0.85) else "draft"
    code, display = rng.choice(_VACCINE_CODES)

    effective_time = _random_ivl_ts(rng, start, end) if maybe(rng, 0.7) else ""

    dosing = ""
    if maybe(rng, 0.5):
        route_code, route_display = rng.choice(_IMMUNIZATION_ROUTES)
        dose_value = rng.choice((0.25, 0.5, 1.0))
        dosing = (
            f'<routeCode code="{route_code}" codeSystem="2.16.840.1.113883.3.26.1.1" displayName="{route_display}"/>'
            f'<doseQuantity value="{dose_value}" unit="mL"/>'
        )

    lot_number = f"<lotNumberText>{random_identifier(rng, 6)}</lotNumberText>" if maybe(rng, 0.6) else ""
    # repeatNumber only has a target on the INT side.
    repeat_number = (
        f'<repeatNumber value="{rng.randint(1, 4)}"/>' if mood_code == "INT" and maybe(rng, 0.5) else ""
    )

    return (
        f'<entry typeCode="DRIV"><substanceAdministration classCode="SBADM" moodCode="{mood_code}"{negation_attr}>'
        f'<templateId root="{IMMUNIZATION_ACTIVITY_TEMPLATE_ID}"/><id root="{sub_id}"/>'
        f'<statusCode code="{status_code}"/>{effective_time}{repeat_number}{dosing}'
        '<consumable><manufacturedProduct classCode="MANU">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.54"/>'
        f'<manufacturedMaterial><code code="{code}" codeSystem="2.16.840.1.113883.12.292" codeSystemName="CVX" displayName="{display}"/>{lot_number}</manufacturedMaterial>'
        "</manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
    )


def _random_immunizations_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.85):
        return None
    start, end = random_time_range(rng, min_days=-300, max_days=0)
    entries = "".join(_random_immunization_entry(rng, start, end) for _ in range(rng.randint(1, 3)))
    return (
        f'<component><section><templateId root="{IMMUNIZATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11369-6" codeSystem="2.16.840.1.113883.6.1" displayName="History of immunizations"/>'
        f"<title>Immunizations</title>{entries}</section></component>"
    )


def _random_vital_sign_observation_from_code(rng: random.Random, start, code: str, display: str, unit: str, low: float, high: float) -> str:
    obs_id = _random_uuid_like(rng)
    value = round(rng.uniform(low, high), 1)
    interpretation = ""
    if maybe(rng, 0.3):
        interpretation = (
            f'<interpretationCode code="{rng.choice(_INTERPRETATION_CODES)}" codeSystem="2.16.840.1.113883.5.83"/>'
        )
    return (
        f'<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{VITAL_SIGN_OBSERVATION_TEMPLATE_ID}"/><id root="{obs_id}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{display}"/>'
        '<statusCode code="completed"/>'
        f'<effectiveTime value="{format_hl7_datetime(start)}"/>'
        f'<value xsi:type="PQ" value="{value}" unit="{unit}"/>{interpretation}'
        "</observation></component>"
    )


def _random_vital_sign_observation(rng: random.Random, start) -> str:
    code, display, unit, (low, high) = rng.choice(_VITAL_SIGN_CODES)
    return _random_vital_sign_observation_from_code(rng, start, code, display, unit, low, high)


def _random_vital_signs_organizer(rng: random.Random) -> str:
    org_id = _random_uuid_like(rng)
    start, _ = random_time_range(rng, min_days=-60, max_days=0)
    count = rng.randint(1, 4)
    components = "".join(_random_vital_sign_observation(rng, start) for _ in range(count))

    # Direct fuzz coverage of app/cda/vitals.py's own Blood Pressure Panel
    # grouping - both sides present ~40% of the time, with a further ~10%
    # chance of dropping the diastolic side to exercise the "incomplete
    # pair falls back to plain" branch.
    if maybe(rng, 0.4):
        components += _random_vital_sign_observation_from_code(rng, start, *_BP_SYSTOLIC)
        if maybe(rng, 0.9):
            components += _random_vital_sign_observation_from_code(rng, start, *_BP_DIASTOLIC)

    # Direct fuzz coverage of the Pulse Oximetry Panel grouping - the
    # primary O2 saturation reading ~30% of the time, each optional
    # sibling independently present about half the time.
    if maybe(rng, 0.3):
        components += _random_vital_sign_observation_from_code(rng, start, *_PULSE_OX_PRIMARY)
        if maybe(rng, 0.5):
            components += _random_vital_sign_observation_from_code(rng, start, *_PULSE_OX_CONCENTRATION)
        if maybe(rng, 0.5):
            components += _random_vital_sign_observation_from_code(rng, start, *_PULSE_OX_FLOW_RATE)

    return (
        f'<entry typeCode="DRIV"><organizer classCode="CLUSTER" moodCode="EVN">'
        f'<templateId root="{VITAL_SIGNS_ORGANIZER_TEMPLATE_ID}"/><id root="{org_id}"/>'
        '<code code="46680005" codeSystem="2.16.840.1.113883.6.96" displayName="Vital signs"/>'
        '<statusCode code="completed"/>'
        f'<effectiveTime value="{format_hl7_datetime(start)}"/>'
        f"{components}"
        "</organizer></entry>"
    )


def _random_vital_signs_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.7):
        return None
    # ~25% "entries optional" templateId instead of "entries required" -
    # both wrap the identical entry shape, but a real official HL7 History
    # and Physical example was found declaring both templateIds together
    # on its own Vital Signs section (see app/cda/vitals.py), and
    # Procedures' sibling example used ONLY the entries-optional one
    # standalone - the exact class of gap this app's Allergies section
    # already shipped once (see _random_allergies_section above). Exists so
    # that gap can't silently reopen for Vitals/Results/Procedures either.
    section_template_id = (
        VITALS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL if maybe(rng, 0.25) else VITALS_SECTION_TEMPLATE_ID
    )
    entries = "".join(_random_vital_signs_organizer(rng) for _ in range(rng.randint(1, 2)))
    return (
        f'<component><section><templateId root="{section_template_id}"/>'
        '<code code="8716-3" codeSystem="2.16.840.1.113883.6.1" displayName="Vital signs"/>'
        f"<title>Vital Signs</title>{entries}</section></component>"
    )


def _random_specimen(rng: random.Random) -> str:
    """A <specimen> element - direct fuzz coverage of app/cda/results.py's
    own _build_specimen, at whichever attachment level the caller wires
    this into (organizer-level default vs. an individual observation's own
    override - see _random_result_organizer/_random_result_observation)."""
    spec_id = _random_uuid_like(rng)
    code, display = rng.choice(_SPECIMEN_CODES)
    return (
        '<specimen typeCode="SPC"><specimenRole classCode="SPEC">'
        f'<id root="{spec_id}"/>'
        '<specimenPlayingEntity classCode="ENT">'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
        f'<quantity value="{rng.randint(1, 20)}" unit="mL"/>'
        "</specimenPlayingEntity></specimenRole></specimen>"
    )


def _random_result_value(rng: random.Random, value: float, unit: str) -> str:
    """~75% the existing plain PQ shape, ~15% IVL_PQ (split evenly across
    low-only/high-only/both), ~10% ED-as-plain-text - direct fuzz coverage
    of app/cda/results.py's own IVL_PQ/ED value-type branches."""
    roll = rng.random()
    if roll < 0.75:
        return f'<value xsi:type="PQ" value="{value}" unit="{unit}"/>'
    if roll < 0.90:
        low = round(value * 0.8, 1)
        high = round(value * 1.2, 1)
        shape = rng.choice(["low", "high", "both"])
        if shape == "low":
            return f'<value xsi:type="IVL_PQ"><low value="{low}" unit="{unit}"/></value>'
        if shape == "high":
            return f'<value xsi:type="IVL_PQ"><high value="{high}" unit="{unit}"/></value>'
        return f'<value xsi:type="IVL_PQ"><low value="{low}" unit="{unit}"/><high value="{high}" unit="{unit}"/></value>'
    return f'<value xsi:type="ED">{rng.choice(["Clear", "Cloudy", "Yellow", "Amber"])}</value>'


def _random_result_observation(rng: random.Random, start) -> str:
    code, display, unit, (low, high) = rng.choice(_RESULT_OBSERVATION_CODES)
    obs_id = _random_uuid_like(rng)
    value = round(rng.uniform(low, high), 1)
    # Mostly recognized statusCode values (exercising every row of
    # STATUS_MAP at least occasionally), rarely an unrecognized one -
    # direct fuzz coverage of _resolve_status's "unknown" fallback branch,
    # same split as Medications'/Procedures' own status fuzzing.
    status_code = rng.choice(list(RESULT_STATUS_MAP)) if maybe(rng, 0.85) else "nullified"
    interpretation = ""
    if maybe(rng, 0.3):
        interpretation = (
            f'<interpretationCode code="{rng.choice(_INTERPRETATION_CODES)}" codeSystem="2.16.840.1.113883.5.83"/>'
        )
    # A reference range is genuinely optional per result - direct fuzz
    # coverage of _build_reference_range's own present/absent branch.
    reference_range = ""
    if maybe(rng, 0.5):
        low_bound = round(value * 0.8, 1)
        high_bound = round(value * 1.2, 1)
        reference_range = (
            '<referenceRange><observationRange><value xsi:type="IVL_PQ">'
            f'<low value="{low_bound}" unit="{unit}"/><high value="{high_bound}" unit="{unit}"/>'
            "</value></observationRange></referenceRange>"
        )
    # ~15% chance this individual observation carries its own <specimen>,
    # overriding the organizer-level default for just this one result -
    # direct fuzz coverage of that attachment-level override.
    specimen = _random_specimen(rng) if maybe(rng, 0.15) else ""
    value_element = _random_result_value(rng, value, unit)
    return (
        f'<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{RESULT_OBSERVATION_TEMPLATE_ID}"/><id root="{obs_id}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{display}"/>'
        f'<statusCode code="{status_code}"/>'
        f'<effectiveTime value="{format_hl7_datetime(start)}"/>'
        f"{value_element}{interpretation}{reference_range}{specimen}"
        "</observation></component>"
    )


def _random_result_organizer(rng: random.Random) -> str:
    org_id = _random_uuid_like(rng)
    panel_code, panel_display = rng.choice(_RESULT_PANEL_CODES)
    start, _ = random_time_range(rng, min_days=-90, max_days=0)
    status_code = rng.choice(list(RESULT_STATUS_MAP)) if maybe(rng, 0.85) else "nullified"
    count = rng.randint(1, 3)
    components = "".join(_random_result_observation(rng, start) for _ in range(count))
    # ~35% chance the organizer itself carries a <specimen>, becoming the
    # default for every result observation that doesn't have its own -
    # direct fuzz coverage of that attachment level.
    specimen = _random_specimen(rng) if maybe(rng, 0.35) else ""
    return (
        f'<entry typeCode="DRIV"><organizer classCode="BATTERY" moodCode="EVN">'
        f'<templateId root="{RESULT_ORGANIZER_TEMPLATE_ID}"/><id root="{org_id}"/>'
        f'<code code="{panel_code}" codeSystem="2.16.840.1.113883.6.1" displayName="{panel_display}"/>'
        f'<statusCode code="{status_code}"/>'
        f'<effectiveTime value="{format_hl7_datetime(start)}"/>'
        f"{specimen}{components}"
        "</organizer></entry>"
    )


def _random_results_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.7):
        return None
    # ~25% "entries optional" templateId - see _random_vital_signs_section's
    # own comment for why.
    section_template_id = (
        RESULTS_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL if maybe(rng, 0.25) else RESULTS_SECTION_TEMPLATE_ID
    )
    entries = "".join(_random_result_organizer(rng) for _ in range(rng.randint(1, 2)))
    return (
        f'<component><section><templateId root="{section_template_id}"/>'
        '<code code="30954-2" codeSystem="2.16.840.1.113883.6.1" displayName="Relevant diagnostic tests and/or laboratory data"/>'
        f"<title>Results</title>{entries}</section></component>"
    )


def _random_performer(rng: random.Random) -> str:
    """A <performer> element - direct fuzz coverage of app/cda/
    procedures.py's own Practitioner+PractitionerRole+Organization+
    Location construction."""
    perf_id = _random_uuid_like(rng)
    # ~70% a real assignedPerson/name, else id-only - direct fuzz coverage
    # of _build_practitioner_from_assigned_entity's own "id or name, skip
    # only when neither resolves" presence rule (a real fetched example
    # showed a performer with only an id and no name at all).
    person = ""
    if maybe(rng, 0.7):
        given, family = rng.choice(_PERFORMER_NAMES)
        person = f"<assignedPerson><name><given>{given}</given><family>{family}</family></name></assignedPerson>"
    organization = ""
    if maybe(rng, 0.6):
        org_name = rng.choice(_ORGANIZATION_NAMES)
        organization = f"<representedOrganization><name>{org_name}</name></representedOrganization>"
    addr = ""
    if maybe(rng, 0.4):
        addr = (
            "<addr><streetAddressLine>100 Main St</streetAddressLine><city>Portland</city>"
            '<state>OR</state><postalCode>97201</postalCode><country>US</country></addr>'
        )
    telecom = '<telecom use="WP" value="tel:+1-555-555-1234"/>' if maybe(rng, 0.5) else ""
    return (
        '<performer typeCode="PRF"><assignedEntity>'
        f'<id root="2.16.840.1.113883.4.6" extension="{perf_id[:9]}"/>'
        f"{addr}{telecom}{person}{organization}"
        "</assignedEntity></performer>"
    )


def _random_participant_location(rng: random.Random) -> str:
    """A <participant typeCode="LOC"> element - direct fuzz coverage of
    app/cda/procedures.py's own Service Delivery Location handling."""
    location_name = rng.choice(_SERVICE_DELIVERY_LOCATION_NAMES)
    return (
        '<participant typeCode="LOC"><participantRole classCode="SDLOC">'
        f'<templateId root="{SERVICE_DELIVERY_LOCATION_TEMPLATE_ID}"/>'
        '<code code="1060-3" codeSystem="2.16.840.1.113883.6.259" displayName="Medical Ward"/>'
        "<addr><streetAddressLine>1007 Health Drive</streetAddressLine><city>Portland</city>"
        '<state>OR</state><postalCode>99123</postalCode><country>US</country></addr>'
        '<telecom use="WP" value="tel:+1-555-555-1030"/>'
        f'<playingEntity classCode="PLC"><name>{location_name}</name></playingEntity>'
        "</participantRole></participant>"
    )


def _random_procedure_author(rng: random.Random) -> str:
    """A direct-child <author> (Author Participation) - direct fuzz
    coverage of app/cda/procedures.py's own _build_procedure_recorder ->
    Procedure.recorder."""
    given, family = random_person_name(rng)
    return (
        "<author><time value=\"20260615113000-0500\"/><assignedAuthor>"
        f'<id root="2.16.840.1.113883.19.5" extension="{_random_uuid_like(rng)[:9]}"/>'
        f"<assignedPerson><name><given>{given}</given><family>{family}</family></name></assignedPerson>"
        "</assignedAuthor></author>"
    )


def _random_indication(rng: random.Random) -> str:
    """entryRelationship[typeCode=RSON] wrapping an Indication Observation
    - direct fuzz coverage of app/cda/procedures.py's own
    _build_reason_codes -> Procedure.reasonCode. Reuses _PROBLEM_CODES -
    an Indication is a clinical finding, the identical SNOMED vocabulary
    Problems already uses, not a Procedure-specific code list."""
    code, display = rng.choice(_PROBLEM_CODES)
    return (
        '<entryRelationship typeCode="RSON"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{INDICATION_TEMPLATE_ID}"/>'
        '<code code="75321-0" codeSystem="2.16.840.1.113883.6.1" displayName="Clinical finding"/>'
        f'<value xsi:type="CD" code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
        "</observation></entryRelationship>"
    )


_COMMENT_TEXTS = [
    "Patient tolerated the procedure well, no complications.",
    "Follow-up scheduled to assess healing.",
    "Mild discomfort reported post-procedure, resolved without intervention.",
]


def _random_comment_activity(rng: random.Random) -> str:
    """entryRelationship[typeCode=SUBJ, inversionInd=true] wrapping a
    Comment Activity act - direct fuzz coverage of app/cda/procedures.py's
    own _build_notes -> Procedure.note, including its own nested author
    (~70% present, mirroring _random_performer's own name-vs-id-only
    split, since the mapper builds a Practitioner only when the nested
    author resolves)."""
    text = rng.choice(_COMMENT_TEXTS)
    author = ""
    if maybe(rng, 0.7):
        given, family = random_person_name(rng)
        author = (
            f'<author><templateId root="2.16.840.1.113883.10.20.22.4.119"/>'
            '<time value="20260615123000-0500"/><assignedAuthor>'
            f'<id root="2.16.840.1.113883.19.5" extension="{_random_uuid_like(rng)[:9]}"/>'
            f"<assignedPerson><name><given>{given}</given><family>{family}</family></name></assignedPerson>"
            "</assignedAuthor></author>"
        )
    return (
        '<entryRelationship typeCode="SUBJ" inversionInd="true"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{COMMENT_ACTIVITY_TEMPLATE_ID}"/>'
        '<code code="48767-8" codeSystem="2.16.840.1.113883.6.1" displayName="Annotation Comment"/>'
        f"<text>{text}</text>{author}"
        "</act></entryRelationship>"
    )


def _random_procedure_entry(rng: random.Random) -> str:
    proc_id = _random_uuid_like(rng)
    code, display = rng.choice(_PROCEDURE_CODES)
    negated = maybe(rng, 0.1)
    negation_attr = ' negationInd="true"' if negated else ""
    # Mostly recognized statusCode values, rarely an unrecognized one -
    # direct fuzz coverage of _resolve_status's "unknown" fallback branch.
    status_code = rng.choice(list(PROCEDURE_STATUS_MAP)) if maybe(rng, 0.85) else "held"

    # Point-in-time effectiveTime (~60%) or a low/high period (~40%) -
    # direct fuzz coverage of both branches app/cda/procedures.py's own
    # _build_procedure needs to distinguish (use performedDateTime only
    # when effectiveTime@value is populated, else performedPeriod).
    if maybe(rng, 0.6):
        point_in_time, _ = random_time_range(rng, min_days=-400, max_days=0)
        effective_time = f'<effectiveTime value="{format_hl7_datetime(point_in_time)}"/>'
    else:
        start, end = random_time_range(rng, min_days=-400, max_days=0)
        effective_time = (
            f'<effectiveTime><low value="{format_hl7_datetime(start)}"/>'
            f'<high value="{format_hl7_datetime(end)}"/></effectiveTime>'
        )

    body_site = ""
    if maybe(rng, 0.5):
        site_code, site_display = rng.choice(_BODY_SITE_CODES)
        body_site = f'<targetSiteCode code="{site_code}" codeSystem="2.16.840.1.113883.6.96" displayName="{site_display}"/>'

    # ~50% chance of a <performer>, ~40% chance of a <participant
    # typeCode="LOC"> - direct fuzz coverage of app/cda/procedures.py's
    # own performer/participant handling.
    performer = _random_performer(rng) if maybe(rng, 0.5) else ""
    participant = _random_participant_location(rng) if maybe(rng, 0.4) else ""
    # ~50% chance of a direct-child <author> (recorder), ~50% chance of an
    # Indication, ~40% chance of a Comment Activity - each independently
    # gated, direct fuzz coverage of app/cda/procedures.py's own
    # _build_procedure_recorder/_build_reason_codes/_build_notes.
    author = _random_procedure_author(rng) if maybe(rng, 0.5) else ""
    indication = _random_indication(rng) if maybe(rng, 0.5) else ""
    comment = _random_comment_activity(rng) if maybe(rng, 0.4) else ""

    return (
        f'<entry typeCode="DRIV"><procedure classCode="PROC" moodCode="EVN"{negation_attr}>'
        f'<templateId root="{PROCEDURE_TEMPLATE_ID}"/><id root="{proc_id}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
        f'<statusCode code="{status_code}"/>'
        f"{effective_time}{body_site}{performer}{participant}{author}{indication}{comment}"
        "</procedure></entry>"
    )


def _random_procedures_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.7):
        return None
    # ~25% "entries optional" templateId - see _random_vital_signs_section's
    # own comment for why; Procedures is the section a real official example
    # was actually caught using ONLY this variant for (see app/cda/
    # procedures.py), so this branch is exercising a genuinely observed
    # real-world shape, not just a defensive guess.
    section_template_id = (
        PROCEDURES_SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL if maybe(rng, 0.25) else PROCEDURES_SECTION_TEMPLATE_ID
    )
    entries = "".join(_random_procedure_entry(rng) for _ in range(rng.randint(1, 3)))
    return (
        f'<component><section><templateId root="{section_template_id}"/>'
        '<code code="47519-4" codeSystem="2.16.840.1.113883.6.1" displayName="History of procedures"/>'
        f"<title>Procedures</title>{entries}</section></component>"
    )


# Small, obviously-synthetic text pools for the narrative-only sections
# (see app/cda/narrative_sections.py) - one rng.choice() per generated
# document, mirroring this generator's own established "small pool, not
# full free-text randomization" style for every other free-text field.
_HOSPITAL_COURSE_TEXTS = [
    "Patient admitted with fever and productive cough, treated with IV antibiotics and responded well. Afebrile for 48 hours prior to discharge.",
    "Uncomplicated post-operative course. Ambulating independently, tolerating a regular diet, pain well controlled on oral medication.",
]
_REASON_FOR_VISIT_TEXTS = [
    "Pre-operative evaluation prior to elective surgery.",
    "Follow-up evaluation of chronic condition.",
    "New patient visit for evaluation of recent symptoms.",
]
_HPI_TEXTS = [
    "Patient reports symptoms worsening over the past several weeks despite conservative management.",
    "Patient presents with a several-day history of symptoms, now improving with supportive care.",
]
_REVIEW_OF_SYSTEMS_TEXTS = [
    "Denies fever, chills, or recent weight loss. All other systems reviewed and negative.",
    "Positive for fatigue and mild joint pain. Denies chest pain, shortness of breath, or gastrointestinal symptoms.",
]
_GENERAL_STATUS_TEXTS = [
    "Alert and oriented, no acute distress.",
    "Well-appearing, in no apparent distress, ambulating without assistance.",
]
_PHYSICAL_EXAM_ITEMS = [
    "HEENT: Normal to examination.",
    "Heart: Regular rate and rhythm, no murmur.",
    "Lungs: Clear to auscultation bilaterally.",
    "Abdomen: Soft, non-tender, non-distended.",
    "Extremities: No edema, normal range of motion.",
]
_ASSESSMENT_ITEMS = [
    "Condition stable, continue current management.",
    "Improving on current treatment regimen.",
    "Candidate for further intervention as previously discussed.",
]
_SOCIAL_HISTORY_ROWS = [
    ("Tobacco smoking status", "Never smoker"),
    ("Tobacco smoking status", "Former smoker"),
    ("Alcohol use", "Occasional"),
    ("Alcohol use", "None"),
]
_FAMILY_HISTORY_TEXTS = [
    "Mother: hypertension. Father: coronary artery disease.",
    "No known family history of significant illness.",
]
_PLAN_OF_TREATMENT_ROWS = [
    ("Follow up with primary care physician", "1-2 weeks"),
    ("Complete prescribed medication course", "As directed"),
]

# Structured-entry pools for Social History/Family History/Plan of
# Treatment (see app/cda/social_history.py/family_history.py/
# plan_of_treatment.py) - each entry's own /code, not the narrative text
# pools above, direct fuzz coverage of the structured-entry parsing
# alongside the narrative one every generated document already exercises.
_SMOKING_STATUS_VALUES = [
    ("266919005", "Never smoker"),
    ("8517006", "Former smoker"),
    ("449868002", "Current every day smoker"),
]
_FAMILY_RELATIONSHIPS = [
    ("FTH", "father"),
    ("MTH", "mother"),
    ("BRO", "brother"),
    ("SIS", "sister"),
]
_PLANNED_OBSERVATION_CODES = [
    ("62959-2", "Colonoscopy"),
    ("185389009", "Follow-up visit"),
]


def _random_narrative_section(
    rng: random.Random,
    template_id: str,
    code: str,
    code_display: str,
    title: str,
    text_options: list[str],
    extra_entries: str = "",
) -> str:
    """A plain-paragraph narrative section - the shape Hospital Course/
    Reason for Visit/History of Present Illness/Review of Systems/General
    Status all use in real documents (confirmed against the real HL7
    C-CDA-Examples documents - see app/cda/narrative_sections.py's own
    docstring). Always returns real narrative text (this app's own
    established "guarantee convertibility by construction" contract) -
    presence/absence of the whole *section* is decided by the caller via
    maybe(), not by this function returning None. `extra_entries` (default
    "", every existing caller unaffected) is real structured `<entry>` XML
    - only Family History's own caller passes one, direct fuzz coverage of
    app/cda/family_history.py's structured-entry parsing alongside the
    narrative text this function always produces."""
    text = rng.choice(text_options)
    return (
        f'<component><section><templateId root="{template_id}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{code_display}"/>'
        f"<title>{title}</title><text><paragraph>{text}</paragraph></text>{extra_entries}</section></component>"
    )


def _random_list_narrative_section(
    rng: random.Random, template_id: str, code: str, code_display: str, title: str, item_pool: list[str]
) -> str:
    """An ordered-<list> narrative section - the shape Physical Exam/
    Assessment both use in real documents."""
    items = rng.sample(item_pool, k=rng.randint(2, min(3, len(item_pool))))
    items_xml = "".join(f"<item>{item}</item>" for item in items)
    return (
        f'<component><section><templateId root="{template_id}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{code_display}"/>'
        f'<title>{title}</title><text><list listType="ordered">{items_xml}</list></text></section></component>'
    )


def _random_table_narrative_section(
    rng: random.Random,
    template_id: str,
    code: str,
    code_display: str,
    title: str,
    header_cells: tuple[str, str],
    row_pool: list[tuple[str, str]],
    extra_entries: str = "",
) -> str:
    """A `<table>` narrative section - the shape Social History/Family
    History/Plan of Treatment commonly use in real documents (see
    app/cda/narrative_sections.py's own extract_narrative_text docstring
    for why this app's own narrative extraction has to be table-aware at
    all). Two rows, direct fuzz coverage of the row/column-preserving
    extraction path every generated Discharge Summary/History and Physical
    now exercises. `extra_entries` (default "", every existing caller
    unaffected) is real structured `<entry>` XML - Social History's and
    Plan of Treatment's own callers each pass one, direct fuzz coverage of
    app/cda/social_history.py's/plan_of_treatment.py's structured-entry
    parsing alongside the narrative table this function always produces."""
    rows = rng.sample(row_pool, k=min(2, len(row_pool)))
    header = f"<tr><th>{header_cells[0]}</th><th>{header_cells[1]}</th></tr>"
    body = "".join(f"<tr><td>{left}</td><td>{right}</td></tr>" for left, right in rows)
    table = f"<table><thead>{header}</thead><tbody>{body}</tbody></table>"
    return (
        f'<component><section><templateId root="{template_id}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{code_display}"/>'
        f"<title>{title}</title><text>{table}</text>{extra_entries}</section></component>"
    )


def _random_social_history_entry(rng: random.Random) -> str:
    """A structured Social History Observation entry (see
    app/cda/social_history.py) - direct fuzz coverage of the structured-
    entry parsing alongside the narrative text every generated Social
    History section already carries. Splits between the Smoking-Status-
    specific templateId and the generic Social History Observation one,
    exercising both templateIds build_social_history_resources()
    recognizes."""
    point_in_time, _ = random_time_range(rng, min_days=-400, max_days=0)
    effective_time = f'<effectiveTime value="{format_hl7_datetime(point_in_time)[:8]}"/>'
    if maybe(rng, 0.6):
        code, display = rng.choice(_SMOKING_STATUS_VALUES)
        return (
            f'<entry><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{SMOKING_STATUS_TEMPLATE_ID}"/>'
            '<code code="72166-2" codeSystem="2.16.840.1.113883.6.1" displayName="Tobacco smoking status NHIS"/>'
            f'<statusCode code="completed"/>{effective_time}'
            f'<value xsi:type="CD" code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
            "</observation></entry>"
        )
    return (
        f'<entry><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{SOCIAL_HISTORY_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="160573003" codeSystem="2.16.840.1.113883.6.96" displayName="Alcohol intake"/>'
        f'<statusCode code="completed"/>{effective_time}'
        f'<value xsi:type="PQ" value="{rng.randint(0, 14)}" unit="/wk"/>'
        "</observation></entry>"
    )


def _random_family_history_entry(rng: random.Random) -> str:
    """A structured Family History Organizer entry (see
    app/cda/family_history.py) - one relative, one condition, with the
    Age Observation and Family History Death Observation nested
    entryRelationships, plus the relative's own sdtc:deceasedInd/
    deceasedTime extension, each independently maybe()-gated - direct fuzz
    coverage of every branch _build_condition/_build_family_member_history
    needs to distinguish."""
    relationship_code, relationship_display = rng.choice(_FAMILY_RELATIONSHIPS)
    condition_code, condition_display = rng.choice(_PROBLEM_CODES)
    sex = random_sex(rng)
    gender = f'<administrativeGenderCode code="{sex}" codeSystem="2.16.840.1.113883.5.1"/>' if maybe(rng, 0.6) else ""

    contributed_to_death = maybe(rng, 0.2)
    death_relationship = ""
    deceased_extension = ""
    if contributed_to_death:
        death_relationship = (
            '<entryRelationship typeCode="CAUS"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{DEATH_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/><statusCode code="completed"/>'
            '<value xsi:type="CD" code="419099009" codeSystem="2.16.840.1.113883.6.96" displayName="Dead"/>'
            "</observation></entryRelationship>"
        )
        # Direct fuzz coverage of _build_family_member_history's own
        # deceasedDate-over-deceasedBoolean choice-type preference (see
        # that function's own docstring) - a real deceasedTime is present
        # ~70% of the time contributed_to_death fires, the rest exercising
        # the deceasedInd-with-no-date fallback branch instead.
        deceased_extension = '<sdtc:deceasedInd value="true"/>'
        if maybe(rng, 0.7):
            deceased_extension += f'<sdtc:deceasedTime value="{rng.randint(1950, 2020)}"/>'

    age_relationship = ""
    if maybe(rng, 0.6):
        age_relationship = (
            '<entryRelationship typeCode="SUBJ" inversionInd="true"><observation classCode="OBS" moodCode="EVN">'
            f'<templateId root="{AGE_OBSERVATION_TEMPLATE_ID}"/>'
            '<code code="445518008" codeSystem="2.16.840.1.113883.6.96" displayName="Age At Onset"/>'
            f'<statusCode code="completed"/><value xsi:type="PQ" value="{rng.randint(20, 90)}" unit="a"/>'
            "</observation></entryRelationship>"
        )

    return (
        f'<entry><organizer classCode="CLUSTER" moodCode="EVN">'
        f'<templateId root="{FAMILY_HISTORY_ORGANIZER_TEMPLATE_ID}"/><statusCode code="completed"/>'
        '<subject><relatedSubject classCode="PRS" xmlns:sdtc="urn:hl7-org:sdtc">'
        f'<code code="{relationship_code}" displayName="{relationship_display}" codeSystemName="HL7 FamilyMember" codeSystem="2.16.840.1.113883.5.111"/>'
        f"<subject>{gender}{deceased_extension}</subject>"
        "</relatedSubject></subject>"
        '<component><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{FAMILY_HISTORY_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="75323-6" codeSystem="2.16.840.1.113883.6.1" displayName="Condition"/>'
        '<statusCode code="completed"/>'
        f'<value xsi:type="CD" code="{condition_code}" codeSystem="2.16.840.1.113883.6.96" displayName="{condition_display}"/>'
        f"{death_relationship}{age_relationship}"
        "</observation></component>"
        "</organizer></entry>"
    )


def _random_plan_of_treatment_entry(rng: random.Random) -> str:
    """A structured Planned Observation or Planned Procedure entry (see
    app/cda/plan_of_treatment.py) - splits between the two recognized
    entry shapes, direct fuzz coverage of both."""
    point_in_time, _ = random_time_range(rng, min_days=1, max_days=90)
    status_code = rng.choice(("active", "completed", "cancelled", "suspended")) if maybe(rng, 0.85) else "new"
    if maybe(rng, 0.5):
        code, display = rng.choice(_PLANNED_OBSERVATION_CODES)
        return (
            f'<entry><observation classCode="OBS" moodCode="RQO">'
            f'<templateId root="{PLANNED_OBSERVATION_TEMPLATE_ID}"/>'
            f'<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{display}"/>'
            f'<statusCode code="{status_code}"/>'
            f'<effectiveTime><center value="{format_hl7_datetime(point_in_time)[:8]}"/></effectiveTime>'
            "</observation></entry>"
        )
    code, display = rng.choice(_PROCEDURE_CODES)
    return (
        f'<entry><procedure classCode="PROC" moodCode="RQO">'
        f'<templateId root="{PLANNED_PROCEDURE_TEMPLATE_ID}"/>'
        f'<code code="{code}" codeSystem="2.16.840.1.113883.6.96" displayName="{display}"/>'
        f'<statusCode code="{status_code}"/>'
        f'<effectiveTime value="{format_hl7_datetime(point_in_time)[:8]}"/>'
        "</procedure></entry>"
    )


def _random_hospital_course_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.7):
        return None
    return _random_narrative_section(
        rng, HOSPITAL_COURSE_TEMPLATE_ID, "8648-8", "Hospital Course", "Hospital Course", _HOSPITAL_COURSE_TEXTS
    )


def _random_plan_of_treatment_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.8):
        return None
    entries = _random_plan_of_treatment_entry(rng) if maybe(rng, 0.6) else ""
    return _random_table_narrative_section(
        rng,
        PLAN_OF_TREATMENT_TEMPLATE_ID,
        "18776-5",
        "Plan of Treatment",
        "Plan of Care",
        ("Planned Activity", "Timeframe"),
        _PLAN_OF_TREATMENT_ROWS,
        extra_entries=entries,
    )


def _random_hp_narrative_sections(rng: random.Random) -> str:
    """History and Physical's own eight remaining required narrative
    sections (Plan of Treatment/Plan of Care is shared with Discharge
    Summary - see _random_plan_of_treatment_section - so it isn't repeated
    here). Each section is independently maybe()-gated, matching this
    generator's own established per-section presence policy."""
    sections = ""
    if maybe(rng, 0.8):
        sections += _random_narrative_section(
            rng, REASON_FOR_VISIT_TEMPLATE_ID, "29299-5", "Reason for visit", "Reason for Visit", _REASON_FOR_VISIT_TEXTS
        )
    if maybe(rng, 0.7):
        sections += _random_narrative_section(
            rng,
            HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
            "10164-2",
            "History of present illness",
            "History of Present Illness",
            _HPI_TEXTS,
        )
    if maybe(rng, 0.7):
        sections += _random_narrative_section(
            rng,
            REVIEW_OF_SYSTEMS_TEMPLATE_ID,
            "10187-3",
            "Review of systems",
            "Review of Systems",
            _REVIEW_OF_SYSTEMS_TEXTS,
        )
    if maybe(rng, 0.7):
        sections += _random_list_narrative_section(
            rng, PHYSICAL_EXAM_TEMPLATE_ID, "29545-1", "Physical findings", "Physical Examination", _PHYSICAL_EXAM_ITEMS
        )
    if maybe(rng, 0.7):
        sections += _random_narrative_section(
            rng, GENERAL_STATUS_TEMPLATE_ID, "10210-3", "General status", "General Status", _GENERAL_STATUS_TEXTS
        )
    if maybe(rng, 0.7):
        sections += _random_list_narrative_section(
            rng, ASSESSMENT_TEMPLATE_ID, "51848-0", "Assessment", "Assessment", _ASSESSMENT_ITEMS
        )
    if maybe(rng, 0.7):
        social_history_entries = _random_social_history_entry(rng) if maybe(rng, 0.6) else ""
        sections += _random_table_narrative_section(
            rng,
            SOCIAL_HISTORY_TEMPLATE_ID,
            "29762-2",
            "Social history",
            "Social History",
            ("Social History Element", "Description"),
            _SOCIAL_HISTORY_ROWS,
            extra_entries=social_history_entries,
        )
    if maybe(rng, 0.7):
        family_history_entries = _random_family_history_entry(rng) if maybe(rng, 0.6) else ""
        sections += _random_narrative_section(
            rng,
            FAMILY_HISTORY_TEMPLATE_ID,
            "10157-6",
            "Family history",
            "Family History",
            _FAMILY_HISTORY_TEXTS,
            extra_entries=family_history_entries,
        )
    return sections


def _random_patient(rng: random.Random) -> str:
    sex = random_sex(rng) if maybe(rng) else None
    ids = "".join(_random_id_element(rng) for _ in range(rng.choice((1, 2))))
    addr = _random_addr_element(rng, "HP") if maybe(rng, 0.55) else ""
    telecom = _random_telecom_element(rng, "HP") if maybe(rng, 0.55) else ""
    names = _random_name_element(rng, sex, "L")
    if maybe(rng, 0.2):
        names += _random_name_element(rng, sex, "P")
    gender = ""
    if sex and maybe(rng, 0.6):
        gender = f'<administrativeGenderCode code="{sex}" codeSystem="2.16.840.1.113883.5.1"/>'
    birth_time = ""
    if maybe(rng, 0.6):
        # At least ~364 days ago - see _random_problem_entry's onset window
        # comment for why this lower bound on patient age matters.
        dob = random_datetime_near_now(rng, min_days=-365 * 80, max_days=-365)
        birth_time = f'<birthTime value="{dob.strftime("%Y%m%d")}"/>'
    return (
        f"<recordTarget><patientRole>{ids}{addr}{telecom}"
        f"<patient>{names}{gender}{birth_time}</patient>"
        "</patientRole></recordTarget>"
    )


def _generate_sectioned_document(
    rng: random.Random,
    document_template_id: str,
    doc_code: str,
    doc_code_display: str,
    title: str,
    force_encounter: bool = False,
    include_discharge_specific_sections: bool = False,
    include_hospital_course: bool = False,
    include_plan_of_treatment: bool = False,
    include_hp_narrative_sections: bool = False,
) -> str:
    """Shared body for every "header + generic sections" C-CDA document
    type this generator produces - CCD, Discharge Summary, and History and
    Physical are all exactly this shape (see
    app.cda.common.build_sectioned_bundle, the conversion-side counterpart
    of this same "extract once a second real consumer exists" pattern).
    `force_encounter` exists because a real Discharge Summary is inherently
    tied to one hospitalization and so (unlike CCD, where an
    encompassingEncounter is genuinely optional) almost always carries one -
    see app/cda/discharge_summary.py. `include_discharge_specific_sections`
    exists because Hospital Discharge Diagnosis/Discharge Medications only
    make real-world sense on a Discharge Summary (SECTION_BUILDERS itself
    has no document-type awareness - any document type carrying one of
    these sections would convert it - but generating them for CCD/H&P would
    produce unrealistic synthetic data no real sender would emit). The
    three narrative-section flags follow the identical reasoning: Hospital
    Course only makes sense on a Discharge Summary, the eight H&P-specific
    narrative sections only on a History and Physical, and Plan of
    Treatment (shared, see app/cda/narrative_sections.py's own docstring)
    on both but never CCD."""
    ids = "".join(_random_id_element(rng) for _ in range(1))
    # A ~10-day window around "now" (rather than always-past) exercises the
    # (warning-severity) "document date in the future" rule about half the
    # time without any error-severity consequence.
    doc_dt = random_datetime_near_now(rng, min_days=-5, max_days=0)
    if maybe(rng, 0.7):
        effective_time = f'<effectiveTime value="{format_hl7_datetime(doc_dt)}"/>'
    else:
        # Date-only - direct fuzz coverage of the Bundle.timestamp-is-
        # FHIR-instant fix (must convert cleanly with no timestamp, never
        # crash on a date-only ClinicalDocument/effectiveTime).
        effective_time = f'<effectiveTime value="{doc_dt.strftime("%Y%m%d")}"/>'

    encounter = _random_encounter(rng, force=force_encounter) or ""
    problems_section = _random_problems_section(rng) or ""
    medications_section = _random_medications_section(rng) or ""
    allergies_section = _random_allergies_section(rng) or ""
    immunizations_section = _random_immunizations_section(rng) or ""
    vitals_section = _random_vital_signs_section(rng) or ""
    results_section = _random_results_section(rng) or ""
    procedures_section = _random_procedures_section(rng) or ""
    discharge_diagnosis_section = (
        _random_hospital_discharge_diagnosis_section(rng) or "" if include_discharge_specific_sections else ""
    )
    discharge_medications_section = (
        _random_discharge_medications_section(rng) or "" if include_discharge_specific_sections else ""
    )
    hospital_course_section = _random_hospital_course_section(rng) or "" if include_hospital_course else ""
    plan_of_treatment_section = _random_plan_of_treatment_section(rng) or "" if include_plan_of_treatment else ""
    hp_narrative_sections = _random_hp_narrative_sections(rng) if include_hp_narrative_sections else ""
    sections = (
        problems_section
        + medications_section
        + allergies_section
        + immunizations_section
        + vitals_section
        + results_section
        + procedures_section
        + discharge_diagnosis_section
        + discharge_medications_section
        + hospital_course_section
        + plan_of_treatment_section
        + hp_narrative_sections
    )
    body = f"<component><structuredBody>{sections}</structuredBody></component>" if sections else ""

    xml_text = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<templateId root="{_US_HEADER_TEMPLATE_ID}"/><templateId root="{document_template_id}"/>'
        f"{ids}"
        f'<code code="{doc_code}" codeSystem="2.16.840.1.113883.6.1" displayName="{doc_code_display}"/>'
        f"<title>{title}</title>"
        f"{effective_time}"
        '<confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>'
        '<languageCode code="en-US"/>'
        f"{_random_patient(rng)}{encounter}{body}"
        "</ClinicalDocument>"
    )
    return _pretty_print(parse_document(xml_text))  # parse = self-check: a generator bug should raise, not return broken XML


def generate_ccd(rng: random.Random) -> str:
    return _generate_sectioned_document(
        rng,
        CCD_TEMPLATE_ID,
        doc_code="34133-9",
        doc_code_display="Summarization of Episode Note",
        title="Continuity of Care Document",
    )


def generate_discharge_summary(rng: random.Random) -> str:
    return _generate_sectioned_document(
        rng,
        DISCHARGE_SUMMARY_TEMPLATE_ID,
        doc_code="18842-5",
        doc_code_display="Discharge Summary",
        title="Discharge Summary",
        force_encounter=True,
        include_discharge_specific_sections=True,
        include_hospital_course=True,
        include_plan_of_treatment=True,
    )


def generate_history_and_physical(rng: random.Random) -> str:
    # componentOf/encompassingEncounter is genuinely optional per the real
    # fetched example (an H&P can precede any admission, e.g. a pre-op
    # visit) - same "not force_encounter" treatment as CCD, unlike
    # Discharge Summary's inherent hospitalization tie.
    return _generate_sectioned_document(
        rng,
        HISTORY_AND_PHYSICAL_TEMPLATE_ID,
        doc_code="34117-2",
        doc_code_display="History and physical note",
        title="History and Physical Note",
        include_plan_of_treatment=True,
        include_hp_narrative_sections=True,
    )
