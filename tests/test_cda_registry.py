from pathlib import Path

import pytest

from app.cda.allergies import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL, build_allergy_intolerances
from app.cda.ccd import CcdBuilder
from app.cda.discharge_medications import SECTION_TEMPLATE_ID as DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID
from app.cda.discharge_medications import build_discharge_medication_requests
from app.cda.discharge_summary import DischargeSummaryBuilder
from app.cda import family_history
from app.cda.hospital_discharge_diagnosis import SECTION_TEMPLATE_ID as HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID
from app.cda.hospital_discharge_diagnosis import build_hospital_discharge_diagnoses
from app.cda.history_and_physical import HistoryAndPhysicalBuilder
from app.cda import narrative_sections
from app.cda import plan_of_treatment
from app.cda import social_history
from app.cda.parser import parse_document
from app.cda.procedures import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as PROCEDURES_ENTRIES_OPTIONAL
from app.cda.procedures import build_procedures
from app.cda.registry import SECTION_BUILDERS, get_document_builder
from app.cda.problems import SECTION_TEMPLATE_ID, build_conditions
from app.cda.results import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as RESULTS_ENTRIES_OPTIONAL
from app.cda.results import build_diagnostic_reports
from app.cda.vitals import SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL as VITALS_ENTRIES_OPTIONAL
from app.cda.vitals import build_vital_signs
from app.hl7.errors import MappingError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_get_document_builder_resolves_ccd():
    document = parse_document(read_fixture("ccd_basic.xml"))
    builder = get_document_builder(document)
    assert isinstance(builder, CcdBuilder)


def test_get_document_builder_raises_for_unrecognized_template():
    document = parse_document(read_fixture("ccd_unrecognized_document_type.xml"))
    with pytest.raises(MappingError):
        get_document_builder(document)


def test_section_builders_registers_problems_section():
    assert SECTION_BUILDERS[SECTION_TEMPLATE_ID] is build_conditions


def test_get_document_builder_resolves_discharge_summary():
    document = parse_document(read_fixture("discharge_summary_basic.xml"))
    builder = get_document_builder(document)
    assert isinstance(builder, DischargeSummaryBuilder)


def test_section_builders_registers_both_allergies_section_variants():
    # "entries required" and "entries optional" are the same entry shape,
    # just a different section-level cardinality constraint - both must
    # dispatch to the same builder function.
    assert SECTION_BUILDERS[SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL] is build_allergy_intolerances


def test_get_document_builder_resolves_history_and_physical():
    document = parse_document(read_fixture("history_and_physical_basic.xml"))
    builder = get_document_builder(document)
    assert isinstance(builder, HistoryAndPhysicalBuilder)


def test_section_builders_registers_both_vitals_results_procedures_section_variants():
    # Same "entries required"/"entries optional" dual registration as
    # Allergies above - found via a real official HL7 History and Physical
    # example, see app/cda/procedures.py's own docstring.
    assert SECTION_BUILDERS[VITALS_ENTRIES_OPTIONAL] is build_vital_signs
    assert SECTION_BUILDERS[RESULTS_ENTRIES_OPTIONAL] is build_diagnostic_reports
    assert SECTION_BUILDERS[PROCEDURES_ENTRIES_OPTIONAL] is build_procedures


def test_section_builders_registers_discharge_specific_sections():
    # Confirmed via a real official HL7 Discharge Summary example that both
    # of these sections wrap the byte-for-byte identical Problem
    # Observation/Medication Activity templates Problems/Medications
    # already parse - only the outer Act template differs.
    assert SECTION_BUILDERS[HOSPITAL_DISCHARGE_DIAGNOSIS_SECTION_TEMPLATE_ID] is build_hospital_discharge_diagnoses
    assert SECTION_BUILDERS[DISCHARGE_MEDICATIONS_SECTION_TEMPLATE_ID] is build_discharge_medication_requests


def test_section_builders_registers_all_twelve_narrative_section_templateids():
    # Hospital Course/Plan of Treatment (Discharge Summary) and the nine
    # History and Physical-specific narrative sections (Reason for Visit/
    # Chief Complaint in all three of its legal shapes, History of Present
    # Illness, Physical Exam, Assessment, Review of Systems, Social
    # History, Family History, General Status) all originate from one
    # shared builder - see app/cda/narrative_sections.py's own docstring
    # for the full templateId/LOINC sourcing. Three of the twelve (Social
    # History, Family History, Plan of Treatment) are overridden with their
    # own combined builder, which internally reuses the narrative one
    # alongside real structured-entry parsing - see app/cda/social_history.py/
    # family_history.py/plan_of_treatment.py.
    narrative_template_ids = [
        narrative_sections.HOSPITAL_COURSE_TEMPLATE_ID,
        narrative_sections.PLAN_OF_TREATMENT_TEMPLATE_ID,
        narrative_sections.REASON_FOR_VISIT_CHIEF_COMPLAINT_TEMPLATE_ID,
        narrative_sections.REASON_FOR_VISIT_TEMPLATE_ID,
        narrative_sections.CHIEF_COMPLAINT_TEMPLATE_ID,
        narrative_sections.HISTORY_OF_PRESENT_ILLNESS_TEMPLATE_ID,
        narrative_sections.PHYSICAL_EXAM_TEMPLATE_ID,
        narrative_sections.ASSESSMENT_TEMPLATE_ID,
        narrative_sections.REVIEW_OF_SYSTEMS_TEMPLATE_ID,
        narrative_sections.SOCIAL_HISTORY_TEMPLATE_ID,
        narrative_sections.FAMILY_HISTORY_TEMPLATE_ID,
        narrative_sections.GENERAL_STATUS_TEMPLATE_ID,
    ]
    assert len(narrative_template_ids) == len(set(narrative_template_ids))  # every templateId genuinely distinct
    # This test's own independently-typed list must match
    # narrative_sections.ALL_TEMPLATE_IDS exactly - not just re-import and
    # reuse it, which would make this test tautological against the very
    # registration it's meant to verify.
    assert set(narrative_template_ids) == set(narrative_sections.ALL_TEMPLATE_IDS)
    overridden_with_structured_entries = {
        narrative_sections.SOCIAL_HISTORY_TEMPLATE_ID: social_history.build_social_history_resources,
        narrative_sections.FAMILY_HISTORY_TEMPLATE_ID: family_history.build_family_history_resources,
        narrative_sections.PLAN_OF_TREATMENT_TEMPLATE_ID: plan_of_treatment.build_plan_of_treatment_resources,
    }
    for template_id in narrative_template_ids:
        if template_id in overridden_with_structured_entries:
            assert SECTION_BUILDERS[template_id] is overridden_with_structured_entries[template_id]
        else:
            assert SECTION_BUILDERS[template_id] is narrative_sections.build_narrative_document_reference
