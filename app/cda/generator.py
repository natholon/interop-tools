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
from app.cda.discharge_summary import DISCHARGE_SUMMARY_TEMPLATE_ID
from app.cda.immunizations import IMMUNIZATION_ACTIVITY_TEMPLATE_ID
from app.cda.immunizations import SECTION_TEMPLATE_ID as IMMUNIZATIONS_SECTION_TEMPLATE_ID
from app.cda.immunizations import STATUS_MAP as IMMUNIZATION_STATUS_MAP
from app.cda.medications import FREE_TEXT_SIG_TEMPLATE_ID, MEDICATION_ACTIVITY_TEMPLATE_ID, STATUS_MAP
from app.cda.medications import SECTION_TEMPLATE_ID as MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.parser import parse_document
from app.cda.problems import (
    CONCERN_ACT_TEMPLATE_ID,
    PROBLEM_OBSERVATION_TEMPLATE_ID,
    STATUS_OBSERVATION_CODE,
    STATUS_OBSERVATION_VALUE_TO_CLINICAL_STATUS,
)
from app.cda.problems import SECTION_TEMPLATE_ID as PROBLEMS_SECTION_TEMPLATE_ID
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
    start, end = random_time_range(rng, min_days=-30, max_days=10)
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
    start, end = random_time_range(rng, min_days=-300, max_days=10)
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
        start, end = random_time_range(rng, min_days=-60, max_days=30)
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
    start, end = random_time_range(rng, min_days=-300, max_days=10)
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
    # ~80% EVN (administered/refused - what this section converts), ~20%
    # INT (planned - deliberately out of scope, direct fuzz coverage of
    # build_immunizations()'s mood-based skip).
    mood_code = "EVN" if maybe(rng, 0.8) else "INT"
    negated = mood_code == "EVN" and maybe(rng, 0.15)
    negation_attr = ' negationInd="true"' if negated else ""
    # Mostly a recognized statusCode (exercising STATUS_MAP's branches),
    # rarely an unrecognized one, exercising _resolve_status's default.
    status_code = rng.choice(list(IMMUNIZATION_STATUS_MAP)) if maybe(rng, 0.85) else "draft"
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

    return (
        f'<entry typeCode="DRIV"><substanceAdministration classCode="SBADM" moodCode="{mood_code}"{negation_attr}>'
        f'<templateId root="{IMMUNIZATION_ACTIVITY_TEMPLATE_ID}"/><id root="{sub_id}"/>'
        f'<statusCode code="{status_code}"/>{effective_time}{dosing}'
        '<consumable><manufacturedProduct classCode="MANU">'
        '<templateId root="2.16.840.1.113883.10.20.22.4.54"/>'
        f'<manufacturedMaterial><code code="{code}" codeSystem="2.16.840.1.113883.12.292" codeSystemName="CVX" displayName="{display}"/>{lot_number}</manufacturedMaterial>'
        "</manufacturedProduct></consumable>"
        "</substanceAdministration></entry>"
    )


def _random_immunizations_section(rng: random.Random) -> str | None:
    if not maybe(rng, 0.85):
        return None
    start, end = random_time_range(rng, min_days=-300, max_days=10)
    entries = "".join(_random_immunization_entry(rng, start, end) for _ in range(rng.randint(1, 3)))
    return (
        f'<component><section><templateId root="{IMMUNIZATIONS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11369-6" codeSystem="2.16.840.1.113883.6.1" displayName="History of immunizations"/>'
        f"<title>Immunizations</title>{entries}</section></component>"
    )


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
) -> str:
    """Shared body for every "header + generic sections" C-CDA document
    type this generator produces - CCD and Discharge Summary are both
    exactly this shape (see app.cda.common.build_sectioned_bundle, the
    conversion-side counterpart of this same "extract once a second real
    consumer exists" pattern). `force_encounter` exists because a real
    Discharge Summary is inherently tied to one hospitalization and so
    (unlike CCD, where an encompassingEncounter is genuinely optional)
    almost always carries one - see app/cda/discharge_summary.py."""
    ids = "".join(_random_id_element(rng) for _ in range(1))
    # A ~10-day window around "now" (rather than always-past) exercises the
    # (warning-severity) "document date in the future" rule about half the
    # time without any error-severity consequence.
    doc_dt = random_datetime_near_now(rng, min_days=-5, max_days=5)
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
    sections = problems_section + medications_section + allergies_section + immunizations_section
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
    parse_document(xml_text)  # self-check: a generator bug should raise, not return broken XML
    return xml_text


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
    )
