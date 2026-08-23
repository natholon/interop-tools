from pathlib import Path

import pytest
from fhir.resources.R4B.bundle import Bundle

from app.cda.errors import CdaParseError
from app.cda.pipeline import convert_cda_to_bundle
from app.hl7.errors import MappingError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _entries_by_type(bundle):
    entries = {}
    for entry in bundle.entry:
        entries.setdefault(entry.resource.get_resource_type(), []).append(entry)
    return entries


def _resolve_reference(bundle, reference: str):
    """Resolves a "urn:uuid:{id}" Reference.reference string to the real
    resource it points at within `bundle` - used by tests that need to
    follow a chain of materialized resources (e.g. Procedure.performer ->
    PractitionerRole -> Practitioner/Organization/Location)."""
    resource_id = reference.removeprefix("urn:uuid:")
    return next(entry.resource for entry in bundle.entry if entry.resource.id == resource_id)


def test_basic_fixture_maps_patient_encounter_and_conditions():
    bundle = convert_cda_to_bundle(read_fixture("ccd_basic.xml"))

    assert bundle.type == "collection"
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    encounter = entries["Encounter"][0].resource
    conditions = [e.resource for e in entries["Condition"]]

    assert patient.identifier[0].value == "998991"
    assert patient.name[0].family == "Betterhalf"
    assert patient.name[0].given == ["Eve"]
    assert patient.gender == "female"
    assert patient.birthDate.isoformat() == "1975-05-01"
    assert patient.address[0].city == "Beaverton"
    assert patient.address[0].state == "OR"
    assert patient.telecom[0].value == "+1-555-555-2003"

    assert encounter.class_fhir.code == "AMB"
    assert encounter.identifier[0].value == "ENC001"
    assert encounter.subject.reference == f"urn:uuid:{patient.id}"
    assert encounter.period.start is not None
    assert encounter.period.end is not None

    assert len(conditions) == 2
    displays = {c.code.coding[0].display for c in conditions}
    assert displays == {"Hypertensive disorder", "Type 2 diabetes mellitus"}
    for condition in conditions:
        assert condition.subject.reference == f"urn:uuid:{patient.id}"
        assert condition.clinicalStatus.coding[0].code == "active"
        assert condition.onsetDateTime is not None

    assert bundle.identifier.value == "TT988"
    assert bundle.timestamp is not None


def test_minimal_fixture_omits_optional_resources_and_fields():
    bundle = convert_cda_to_bundle(read_fixture("ccd_minimal.xml"))

    entries = _entries_by_type(bundle)
    assert list(entries.keys()) == ["Patient"]
    patient = entries["Patient"][0].resource
    assert patient.name[0].family == "Imal"
    assert patient.identifier is None
    assert patient.gender is None
    assert patient.birthDate is None
    assert patient.address is None


def test_malformed_fixture_raises_cda_parse_error():
    with pytest.raises(CdaParseError):
        convert_cda_to_bundle(read_fixture("ccd_malformed.xml"))


def test_unrecognized_document_type_raises_mapping_error():
    with pytest.raises(MappingError):
        convert_cda_to_bundle(read_fixture("ccd_unrecognized_document_type.xml"))


def test_negated_problem_produces_no_condition():
    bundle = convert_cda_to_bundle(read_fixture("ccd_problem_negated.xml"))
    entries = _entries_by_type(bundle)
    assert "Condition" not in entries


def test_status_observation_overrides_act_status():
    bundle = convert_cda_to_bundle(read_fixture("ccd_problem_status_observation_overrides_act_status.xml"))
    entries = _entries_by_type(bundle)
    condition = entries["Condition"][0].resource
    # The Concern Act's own statusCode says "active"; the nested Status
    # Observation says "Resolved" (SNOMED 413322009) - the nested
    # observation must win per the IG.
    assert condition.clinicalStatus.coding[0].code == "resolved"


def test_effective_time_variants_resolve_onset_and_abatement_per_shape():
    bundle = convert_cda_to_bundle(read_fixture("ccd_effective_time_variants.xml"))
    entries = _entries_by_type(bundle)
    conditions = {c.resource.code.coding[0].display: c.resource for c in entries["Condition"]}

    bare_value = conditions["Acute bronchitis"]
    assert bare_value.onsetDateTime.isoformat() == "2022-03-01"
    assert bare_value.abatementDateTime.isoformat() == "2022-03-01"

    low_only = conditions["Asthma"]
    assert low_only.onsetDateTime.isoformat() == "2022-06-15"
    assert low_only.abatementDateTime is None

    low_and_high = conditions["Viral sinusitis"]
    assert low_and_high.onsetDateTime.isoformat() == "2021-01-10"
    assert low_and_high.abatementDateTime.isoformat() == "2021-02-09"

    null_flavor = conditions["Anemia"]
    assert null_flavor.onsetDateTime is None
    assert null_flavor.abatementDateTime is None


def test_unrecognized_section_is_silently_skipped():
    bundle = convert_cda_to_bundle(read_fixture("ccd_unrecognized_section_present.xml"))
    entries = _entries_by_type(bundle)
    # Encounters has no registered section builder - its entry must not
    # appear as any resource, but the sibling recognized Problems section
    # must still be processed normally.
    assert set(entries.keys()) == {"Patient", "Condition"}
    assert entries["Condition"][0].resource.code.coding[0].display == "Osteoarthritis"


def test_problem_edge_cases_multi_subj_missing_value_and_wrong_type_code():
    bundle = convert_cda_to_bundle(read_fixture("ccd_problem_edge_cases.xml"))
    entries = _entries_by_type(bundle)
    conditions = entries.get("Condition", [])
    displays = {c.resource.code.coding[0].display for c in conditions}
    # One Concern Act with two entryRelationship[SUBJ] children -> both
    # problems mapped. A Problem Observation with no <value> at all is
    # skipped, not crashed on. A REFR (not SUBJ) relationship wrapping a
    # Problem-Observation-templated element must not be treated as an
    # asserted problem.
    assert displays == {"Asthma", "Anemia"}


def test_header_multiplicities_and_fallbacks():
    bundle = convert_cda_to_bundle(read_fixture("ccd_header_multiplicities.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    encounter = entries["Encounter"][0].resource

    # A root-only <id> (no @extension) is still a complete identifier per
    # the HL7 II datatype - the root itself is the identifier value.
    assert patient.identifier[0].value == "urn:oid:7c2d4e6f-0000-0000-0000-000000000002"
    assert bundle.identifier.value == "urn:oid:8f3c6b2a-0000-0000-0000-000000000001"

    # The source's own use codes decide: "L" (legal) is the official name
    # and "P" is a pseudonym. Position alone used to decide, which called
    # the second name "old" - a former name - when the document had said
    # it was a pseudonym.
    assert [n.use for n in patient.name] == ["official", "nickname"]
    assert [n.family for n in patient.name] == ["Plicity", "Named"]
    assert len(patient.address) == 2
    assert [t.value for t in patient.telecom] == ["+1-555-555-3001", "+1-555-555-3002"]

    # Both encounter <id> elements must be captured, not just the first.
    assert [i.value for i in encounter.identifier] == ["LOCALENC1", "NATIONALENC1"]
    # An unrecognized encounter class code falls back to the disclosed default.
    assert encounter.class_fhir.code == "AMB"

    # ClinicalDocument/effectiveTime is date-only here - Bundle.timestamp is
    # FHIR "instant" (no date-only form), so it must be omitted rather than
    # crash resource construction.
    assert bundle.timestamp is None


def test_medications_basic_fixture_maps_structured_and_free_text_dosing():
    bundle = convert_cda_to_bundle(read_fixture("ccd_medications_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    requests = {r.resource.medicationCodeableConcept.coding[0].display: r.resource for r in entries["MedicationRequest"]}
    assert len(requests) == 2
    for request in requests.values():
        assert request.subject.reference == f"urn:uuid:{patient.id}"
        # A plain-Medications-sourced request never populates .category -
        # that's precisely what makes "discharge" a reliable marker for
        # the Discharge Medications section (see app/cda/
        # discharge_medications.py and its reverse-direction split).
        assert request.category is None

    structured = requests["Lisinopril 10 MG Oral Tablet"]
    assert structured.status == "active"
    assert structured.intent == "order"  # moodCode INT
    dosage = structured.dosageInstruction[0]
    assert dosage.route.coding[0].display == "ORAL"
    assert float(dosage.doseAndRate[0].doseQuantity.value) == 10
    assert dosage.doseAndRate[0].doseQuantity.unit == "mg"
    assert dosage.timing.repeat.boundsPeriod.start.isoformat() == "2026-07-01"
    assert dosage.timing.repeat.boundsPeriod.end.isoformat() == "2026-10-01"
    assert dosage.patientInstruction is None

    free_text = requests["Amoxicillin 500 MG Oral Capsule"]
    assert free_text.status == "completed"
    assert free_text.intent == "plan"  # moodCode EVN
    assert free_text.dosageInstruction[0].patientInstruction == (
        "Take one capsule by mouth three times daily until gone"
    )
    assert free_text.dosageInstruction[0].route is None


def test_negated_medication_produces_no_medication_request():
    bundle = convert_cda_to_bundle(read_fixture("ccd_medications_negated.xml"))
    entries = _entries_by_type(bundle)
    assert "MedicationRequest" not in entries


def test_allergies_basic_fixture_maps_full_shape():
    bundle = convert_cda_to_bundle(read_fixture("ccd_allergies_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    allergy = entries["AllergyIntolerance"][0].resource

    assert allergy.patient.reference == f"urn:uuid:{patient.id}"
    assert allergy.code.coding[0].display == "Eggs (edible)"
    assert allergy.clinicalStatus.coding[0].code == "active"
    assert allergy.clinicalStatus.coding[0].system == "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
    assert allergy.type == "allergy"
    assert allergy.category == ["food"]
    assert allergy.criticality == "high"
    assert allergy.onsetDateTime.isoformat() == "1998-06-01"
    assert allergy.recordedDate.isoformat() == "2014-01-04"

    assert len(allergy.reaction) == 1
    reaction = allergy.reaction[0]
    assert reaction.manifestation[0].coding[0].display == "Wheal"
    assert reaction.severity == "moderate"
    assert reaction.onset.isoformat() == "1998-06-01"


def test_no_known_allergies_produces_text_only_code():
    bundle = convert_cda_to_bundle(read_fixture("ccd_allergies_no_known.xml"))
    entries = _entries_by_type(bundle)
    allergy = entries["AllergyIntolerance"][0].resource
    assert allergy.code.text == "No known allergies"
    assert allergy.code.coding is None
    assert allergy.clinicalStatus.coding[0].code == "active"


def test_immunizations_basic_fixture_maps_administered_and_refused_skips_planned():
    bundle = convert_cda_to_bundle(read_fixture("ccd_immunizations_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    immunizations = {e.resource.vaccineCode.coding[0].display: e.resource for e in entries["Immunization"]}

    # Only the two EVN-mood entries convert - the INT-mood (planned Tdap)
    # entry is out of scope for this slice and must be silently skipped.
    assert len(immunizations) == 2
    for immunization in immunizations.values():
        assert immunization.patient.reference == f"urn:uuid:{patient.id}"

    administered = immunizations["influenza virus vaccine, unspecified formulation"]
    assert administered.status == "completed"
    assert administered.occurrenceDateTime.isoformat() == "2010-08-15"
    assert administered.lotNumber == "1"
    assert administered.route.coding[0].display == "INTRAMUSCULAR"
    assert float(administered.doseQuantity.value) == 0.5
    assert administered.doseQuantity.unit == "mL"

    refused = immunizations["zoster vaccine, live"]
    assert refused.status == "not-done"
    assert refused.occurrenceString == "Unknown"
    assert refused.occurrenceDateTime is None


def test_vitals_basic_fixture_maps_panel_and_members():
    bundle = convert_cda_to_bundle(read_fixture("ccd_vitals_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    observations = [e.resource for e in entries["Observation"]]
    # The outer Vital Signs Panel, heart rate, temperature (both plain),
    # plus the Blood Pressure Panel and Pulse Oximetry Panel groupings.
    assert len(observations) == 5

    panel = next(o for o in observations if o.code.coding[0].code == "85353-1")
    members = [o for o in observations if o.id != panel.id]
    assert len(members) == 4
    assert {ref.reference for ref in panel.hasMember} == {f"urn:uuid:{m.id}" for m in members}
    assert panel.status == "final"
    assert panel.category[0].coding[0].code == "vital-signs"
    assert panel.subject.reference == f"urn:uuid:{patient.id}"
    assert panel.effectiveDateTime.isoformat() == "2026-08-05T09:00:00-05:00"

    heart_rate = next(m for m in members if m.code.coding[0].code == "8867-4")
    assert float(heart_rate.valueQuantity.value) == 76
    assert heart_rate.valueQuantity.unit == "/min"
    assert heart_rate.interpretation[0].coding[0].code == "N"

    temperature = next(m for m in members if m.code.coding[0].code == "8310-5")
    assert float(temperature.valueQuantity.value) == 98.6
    assert temperature.valueQuantity.unit == "[degF]"
    assert temperature.interpretation is None

    # Blood Pressure Panel: systolic+diastolic grouped as .component, no
    # top-level valueQuantity.
    bp_panel = next(m for m in members if m.code.coding[0].code == "85354-9")
    assert bp_panel.valueQuantity is None
    assert bp_panel.status == "final"
    bp_components = {c.code.coding[0].code: c for c in bp_panel.component}
    assert float(bp_components["8480-6"].valueQuantity.value) == 120
    assert bp_components["8480-6"].valueQuantity.unit == "mm[Hg]"
    assert float(bp_components["8462-4"].valueQuantity.value) == 80

    # Pulse Oximetry Panel: the O2 saturation reading itself becomes the
    # panel (with a top-level valueQuantity, unlike the BP Panel), always
    # carrying both IG-documented synonymous LOINC codings regardless of
    # which one the source used (this fixture uses 59408-5 only), plus the
    # one present optional sibling (flow rate) as a .component.
    pulse_ox_panel = next(m for m in members if {c.code for c in m.code.coding} == {"59408-5", "2708-6"})
    assert float(pulse_ox_panel.valueQuantity.value) == 97
    assert pulse_ox_panel.valueQuantity.unit == "%"
    assert len(pulse_ox_panel.component) == 1
    assert pulse_ox_panel.component[0].code.coding[0].code == "3151-8"
    assert float(pulse_ox_panel.component[0].valueQuantity.value) == 2


def test_results_basic_fixture_maps_report_and_observations():
    bundle = convert_cda_to_bundle(read_fixture("ccd_results_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    report = entries["DiagnosticReport"][0].resource
    specimen = entries["Specimen"][0].resource
    observations = {o.resource.code.coding[0].code: o.resource for o in entries["Observation"]}

    assert report.status == "final"
    assert report.code.coding[0].display == "Complete blood count panel"
    assert report.subject.reference == f"urn:uuid:{patient.id}"
    assert {ref.reference for ref in report.result} == {f"urn:uuid:{o.id}" for o in observations.values()}
    assert report.category[0].coding[0].code == "laboratory"
    assert report.specimen[0].reference == f"urn:uuid:{specimen.id}"

    # Specimen: built from the organizer-level <specimen>, its own
    # collection.bodySite from the sibling Specimen Collection Procedure
    # (fixed SNOMED code 17636008).
    assert specimen.type.coding[0].code == "119297000"
    assert float(specimen.collection.quantity.value) == 5
    assert specimen.collection.quantity.unit == "mL"
    assert specimen.note[0].text == "Drawn via venipuncture"
    assert specimen.collection.bodySite.coding[0].code == "368225008"

    wbc = observations["6690-2"]
    assert float(wbc.valueQuantity.value) == 6.8
    assert wbc.valueQuantity.unit == "10*3/uL"
    assert float(wbc.referenceRange[0].low.value) == 4.5
    assert float(wbc.referenceRange[0].high.value) == 11.0
    assert wbc.category[0].coding[0].code == "laboratory"
    # The organizer-level Specimen propagates as the default for every
    # result Observation that doesn't carry its own.
    assert wbc.specimen.reference == f"urn:uuid:{specimen.id}"

    culture = observations["33747-0"]
    assert culture.valueString == "No growth after 48 hours"
    assert culture.valueQuantity is None

    # IVL_PQ with both bounds present -> valueRange, not valueQuantity.
    creatinine = observations["2160-0"]
    assert creatinine.valueQuantity is None
    assert float(creatinine.valueRange.low.value) == 0.6
    assert float(creatinine.valueRange.high.value) == 1.3

    # ED's own plain-text (narrative-reference) case maps to valueString,
    # the same as ST.
    color = observations["5778-6"]
    assert color.valueString == "Yellow"


def test_procedures_basic_fixture_maps_completed_and_negated_entries():
    bundle = convert_cda_to_bundle(read_fixture("ccd_procedures_basic.xml"))
    entries = _entries_by_type(bundle)
    patient = entries["Patient"][0].resource
    procedures = {p.resource.code.coding[0].code: p.resource for p in entries["Procedure"]}
    assert len(procedures) == 2

    appendectomy = procedures["80146002"]
    assert appendectomy.status == "completed"
    assert appendectomy.subject.reference == f"urn:uuid:{patient.id}"
    # originalText -> CodeableConcept.text, per the C-CDA on FHIR IG.
    # The appendectomy uses the narrative-reference shape (resolved via
    # build_sectioned_bundle's own resolve_narrative_references pre-pass,
    # with the <content> markup flattened); the colonoscopy below uses the
    # inline shape - both real-world shapes exercised in one fixture.
    assert appendectomy.code.text == "Appendectomy of the appendix"
    assert appendectomy.performedDateTime.isoformat() == "2026-06-15T12:00:00-05:00"
    assert appendectomy.performedPeriod is None
    assert appendectomy.bodySite[0].coding[0].display == "Appendix structure"
    assert appendectomy.identifier[0].value == "PROC001"

    colonoscopy = procedures["73761001"]
    assert colonoscopy.code.text == "Screening colonoscopy"
    # negationInd="true" overrides statusCode unconditionally.
    assert colonoscopy.status == "not-done"
    assert colonoscopy.performedDateTime is None
    assert colonoscopy.performedPeriod.start.isoformat() == "2026-02-01"
    assert colonoscopy.performedPeriod.end.isoformat() == "2026-02-01"
    # Root-only id (no @extension) falls back to the urn:oid identifier shape.
    assert colonoscopy.identifier[0].system == "urn:ietf:rfc:3986"
    assert colonoscopy.identifier[0].value == "urn:oid:d1e2f3a4-0002-4a1a-8a1a-000000000002"

    # performer -> Procedure.performer.actor -> a real PractitionerRole
    # wrapping Practitioner + Organization + Location (from the performer's
    # own address).
    assert len(appendectomy.performer) == 1
    role = _resolve_reference(bundle, appendectomy.performer[0].actor.reference)
    assert role.get_resource_type() == "PractitionerRole"
    practitioner = _resolve_reference(bundle, role.practitioner.reference)
    assert practitioner.name[0].family == "Smith"
    assert practitioner.name[0].given == ["John"]
    assert practitioner.identifier[0].value == "333444555"
    organization = _resolve_reference(bundle, role.organization.reference)
    assert organization.name == "General Hospital"
    performer_location = _resolve_reference(bundle, role.location[0].reference)
    assert performer_location.address.city == "Portland"
    assert role.telecom[0].value == "+1-555-555-1234"

    # participant[@typeCode=LOC] -> Procedure.location - a separate
    # Location, unrelated to the performer's own machinery.
    sdloc = _resolve_reference(bundle, appendectomy.location.reference)
    assert sdloc.get_resource_type() == "Location"
    assert sdloc.name == "Community Medical Center"
    assert sdloc.type[0].coding[0].code == "1060-3"
    assert sdloc.address.city == "Portland"
    assert sdloc.address.postalCode == "99123"

    # A performer with only an id and no assignedPerson/name at all still
    # materializes a Practitioner (id-only), matching a real fetched
    # example's own shape.
    assert len(colonoscopy.performer) == 1
    colonoscopy_role = _resolve_reference(bundle, colonoscopy.performer[0].actor.reference)
    colonoscopy_practitioner = _resolve_reference(bundle, colonoscopy_role.practitioner.reference)
    assert colonoscopy_practitioner.name is None
    assert colonoscopy_practitioner.identifier[0].value == "urn:oid:2.16.840.1.113883.19.5"

    # entryRelationship[typeCode=RSON] -> a nested Indication Observation's
    # own <value> -> Procedure.reasonCode.
    assert len(appendectomy.reasonCode) == 1
    assert appendectomy.reasonCode[0].coding[0].code == "85189001"
    assert appendectomy.reasonCode[0].coding[0].display == "Acute appendicitis"

    # entryRelationship[typeCode=SUBJ, inversionInd=true] -> a Comment
    # Activity act -> Procedure.note, with its own nested author (a plain
    # Practitioner, not a PractitionerRole - see module docstring for why).
    assert len(appendectomy.note) == 1
    note = appendectomy.note[0]
    assert note.text == "Patient tolerated the procedure well, no complications."
    assert note.time.isoformat() == "2026-06-15T12:30:00-05:00"
    comment_author = _resolve_reference(bundle, note.authorReference.reference)
    assert comment_author.get_resource_type() == "Practitioner"
    assert comment_author.name[0].family == "Commenter"
    assert comment_author.name[0].given == ["Jamie"]

    # <author> as a direct child of the procedure element (Author
    # Participation) -> Procedure.recorder, a plain Practitioner reference
    # distinct from the Comment Activity's own author.
    recorder_practitioner = _resolve_reference(bundle, appendectomy.recorder.reference)
    assert recorder_practitioner.get_resource_type() == "Practitioner"
    assert recorder_practitioner.name[0].family == "Recorder"
    assert recorder_practitioner is not comment_author

    # The negated Colonoscopy entry carries none of these - confirming
    # they're each genuinely optional, not always populated.
    assert colonoscopy.reasonCode is None
    assert colonoscopy.note is None
    assert colonoscopy.recorder is None


def test_discharge_summary_maps_header_and_all_six_of_its_sections():
    bundle = convert_cda_to_bundle(read_fixture("discharge_summary_basic.xml"))
    entries = _entries_by_type(bundle)

    # Hospital Discharge Diagnosis and Discharge Medications sections wrap
    # the byte-for-byte identical Problem Observation/Medication Activity
    # templates Problems/Medications already parse (see
    # app/cda/hospital_discharge_diagnosis.py and
    # app/cda/discharge_medications.py) - both must convert now, alongside
    # the plain Problems-shaped Condition. Hospital Course and Plan of
    # Treatment are narrative-only sections that each produce a
    # DocumentReference+Binary pair (see app/cda/narrative_sections.py) -
    # Plan of Treatment's own structured Planned Observation entry also
    # produces a real CarePlan (see app/cda/plan_of_treatment.py).
    assert set(entries.keys()) == {
        "Patient",
        "Encounter",
        "Condition",
        "MedicationRequest",
        "DocumentReference",
        "Binary",
        "CarePlan",
    }
    conditions = {c.resource.code.coding[0].display: c.resource for c in entries["Condition"]}
    assert set(conditions) == {"Hypertensive disorder", "Community-acquired pneumonia"}

    discharge_diagnosis = conditions["Community-acquired pneumonia"]
    assert discharge_diagnosis.category[0].coding[0].code == "encounter-diagnosis"
    problem_list_condition = conditions["Hypertensive disorder"]
    assert problem_list_condition.category is None

    medication_request = entries["MedicationRequest"][0].resource
    assert medication_request.medicationCodeableConcept.coding[0].display == "Amoxicillin 500 MG Oral Capsule"
    # A Discharge-Medications-sourced request is marked with the standard
    # "discharge" code from FHIR R4's own medicationrequest-category
    # CodeSystem - the marker the reverse direction splits on, mirroring
    # the Hospital Discharge Diagnosis Condition's own category above. A
    # plain-Medications-sourced request never populates .category at all
    # (see test_medications_basic_fixture_* for that side).
    assert medication_request.category[0].coding[0].code == "discharge"
    assert (
        medication_request.category[0].coding[0].system
        == "http://terminology.hl7.org/CodeSystem/medicationrequest-category"
    )

    encounter = entries["Encounter"][0].resource
    assert encounter.class_fhir.code == "IMP"
    assert encounter.period.start.isoformat() == "2026-07-28T08:00:00-05:00"
    assert encounter.period.end.isoformat() == "2026-08-05T10:00:00-05:00"

    document_references = {dr.resource.type.coding[0].code: dr.resource for dr in entries["DocumentReference"]}
    assert set(document_references) == {"8648-8", "18776-5"}
    binaries_by_id = {b.resource.id: b.resource for b in entries["Binary"]}

    hospital_course = document_references["8648-8"]
    assert hospital_course.description == "Hospital Course"
    hospital_course_binary_id = hospital_course.content[0].attachment.url.removeprefix("urn:uuid:")
    assert "community-acquired pneumonia" in binaries_by_id[hospital_course_binary_id].data.decode("utf-8").lower()

    plan_of_treatment = document_references["18776-5"]
    assert plan_of_treatment.description == "Plan of Care"
    plan_binary_id = plan_of_treatment.content[0].attachment.url.removeprefix("urn:uuid:")
    plan_text = binaries_by_id[plan_binary_id].data.decode("utf-8")
    # Table row/column association preserved, not flattened into one
    # unlabeled run - see extract_narrative_text's own docstring.
    assert "Planned Activity | Planned Date" in plan_text
    assert "Follow up with primary care physician | Aug 12, 2026" in plan_text

    # The section's own structured Planned Observation entry produces a
    # real CarePlan alongside the narrative DocumentReference+Binary - both
    # representations coexist (see app/cda/plan_of_treatment.py).
    care_plan = entries["CarePlan"][0].resource
    assert care_plan.status == "active"
    assert care_plan.intent == "plan"
    assert len(care_plan.activity) == 1
    activity_detail = care_plan.activity[0].detail
    assert activity_detail.code.coding[0].display == "Follow-up visit"
    assert activity_detail.status == "scheduled"
    assert activity_detail.kind == "ServiceRequest"


def test_history_and_physical_maps_header_recognized_section_and_all_nine_narrative_sections():
    bundle = convert_cda_to_bundle(read_fixture("history_and_physical_basic.xml"))
    entries = _entries_by_type(bundle)

    # Reason for Visit, History of Present Illness, Review of Systems,
    # Physical Exam, General Status, Assessment, Social History, Family
    # History, and Plan of Treatment are all H&P-specific narrative
    # sections - each now converts to its own DocumentReference+Binary pair
    # (see app/cda/narrative_sections.py) - alongside the Procedures section
    # (using the "entries optional" templateId, the exact shape a real
    # official HL7 History and Physical example was found using). Social
    # History's/Family History's/Plan of Treatment's own structured entries
    # also produce a real Observation/FamilyMemberHistory/CarePlan
    # alongside their narrative pair (see app/cda/social_history.py/
    # family_history.py/plan_of_treatment.py).
    assert set(entries.keys()) == {
        "Patient",
        "Procedure",
        "DocumentReference",
        "Binary",
        "Observation",
        "FamilyMemberHistory",
        "CarePlan",
    }
    procedure = entries["Procedure"][0].resource
    assert procedure.code.coding[0].display == "Knee arthroscopy"
    assert procedure.status == "completed"

    smoking_status = entries["Observation"][0].resource
    assert smoking_status.code.coding[0].display == "Tobacco smoking status NHIS"
    assert smoking_status.category[0].coding[0].code == "social-history"
    assert smoking_status.status == "final"
    assert smoking_status.valueCodeableConcept.coding[0].display == "Never smoker"

    family_member_history = entries["FamilyMemberHistory"][0].resource
    assert family_member_history.relationship.coding[0].code == "FTH"
    assert family_member_history.sex.coding[0].code == "M"
    assert family_member_history.deceasedDate == "1998"
    assert len(family_member_history.condition) == 1
    condition = family_member_history.condition[0]
    assert condition.code.coding[0].display == "Coronary arteriosclerosis"
    assert condition.onsetAge.value == 58
    assert condition.onsetAge.unit == "a"
    assert condition.contributedToDeath is True

    care_plan = entries["CarePlan"][0].resource
    assert care_plan.status == "active"
    assert care_plan.intent == "plan"
    assert len(care_plan.activity) == 1
    activity_detail = care_plan.activity[0].detail
    assert activity_detail.code.coding[0].display == "Total knee replacement"
    assert activity_detail.status == "scheduled"

    document_references = entries["DocumentReference"]
    assert len(document_references) == 9
    binaries_by_id = {b.resource.id: b.resource for b in entries["Binary"]}

    by_code = {dr.resource.type.coding[0].code: dr.resource for dr in document_references}
    expected_codes = {
        "29299-5": "Reason for Visit",
        "10164-2": "History of Present Illness",
        "10187-3": "Review of Systems",
        "29545-1": "Physical Examination",
        "10210-3": "General Status",
        "51848-0": "Assessment",
        "29762-2": "Social History",
        "10157-6": "Family History",
        "18776-5": "Plan of Care",
    }
    assert set(by_code) == set(expected_codes)
    for code, title in expected_codes.items():
        document_reference = by_code[code]
        assert document_reference.type.coding[0].system == "http://loinc.org"
        assert document_reference.description == title

    def narrative_text(loinc_code: str) -> str:
        document_reference = by_code[loinc_code]
        binary_id = document_reference.content[0].attachment.url.removeprefix("urn:uuid:")
        return binaries_by_id[binary_id].data.decode("utf-8")

    # Reason for Visit - the fixture's own original narrative section,
    # already covered before this section became a nine-section fixture.
    assert narrative_text("29299-5") == "Pre-operative evaluation prior to elective knee replacement."

    # History of Present Illness - two <paragraph>s, one line each.
    hpi_text = narrative_text("10164-2")
    assert "worsening over the past two years" in hpi_text
    assert "failed to provide adequate relief" in hpi_text

    # Physical Exam - an ordered <list>, one line per <item>.
    physical_exam_text = narrative_text("29545-1")
    assert "HEENT: Normal to examination." in physical_exam_text
    assert "Right knee: Decreased range of motion, crepitus on flexion, no effusion." in physical_exam_text

    # Social History and Plan of Treatment - table row/column association
    # preserved, not flattened into one unlabeled run (see
    # extract_narrative_text's own docstring).
    social_history_text = narrative_text("29762-2")
    assert "Social History Element | Description" in social_history_text
    assert "Tobacco smoking status | Never smoker" in social_history_text

    plan_text = narrative_text("18776-5")
    assert "Planned Activity | Planned Date" in plan_text
    assert "Total knee arthroplasty | Sep 2, 2026" in plan_text


def test_bundle_round_trips_through_json():
    bundle = convert_cda_to_bundle(read_fixture("ccd_basic.xml"))
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)
