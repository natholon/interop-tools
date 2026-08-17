from pathlib import Path

import pytest
from fhir.resources.R4B.appointment import Appointment
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient

from app.cda.pipeline import convert_cda_to_bundle
from app.edi.pipeline import convert_edi_to_bundle
from app.generators.registry import generate
from app.hl7.errors import MappingError
from app.hl7.pipeline import convert_hl7_to_bundle
from app.transform.pipeline import build_message_from_bundle
from app.transform.registry import get_builder, list_supported_targets

FIXTURES = Path(__file__).parent / "fixtures"


def test_list_supported_targets_includes_adt_a01():
    assert ("HL7", "ADT", "A01") in list_supported_targets()


def test_list_supported_targets_includes_all_six_adt_triggers():
    targets = list_supported_targets()
    for trigger in ("A01", "A02", "A03", "A04", "A05", "A08"):
        assert ("HL7", "ADT", trigger) in targets


def test_list_supported_targets_includes_all_three_adt_cancel_triggers():
    targets = list_supported_targets()
    for trigger in ("A11", "A13", "A38"):
        assert ("HL7", "ADT", trigger) in targets


def test_list_supported_targets_includes_siu_s12():
    assert ("HL7", "SIU", "S12") in list_supported_targets()


def test_list_supported_targets_includes_all_six_siu_triggers():
    targets = list_supported_targets()
    for trigger in ("S12", "S13", "S14", "S15", "S17", "S26"):
        assert ("HL7", "SIU", trigger) in targets


def test_list_supported_targets_includes_oru_r01():
    assert ("HL7", "ORU", "R01") in list_supported_targets()


def test_list_supported_targets_includes_all_five_oru_triggers():
    targets = list_supported_targets()
    for trigger in ("R01", "R30", "R31", "R32", "R40"):
        assert ("HL7", "ORU", trigger) in targets


def test_list_supported_targets_includes_mdm_t02():
    assert ("HL7", "MDM", "T02") in list_supported_targets()


def test_list_supported_targets_includes_all_six_mdm_triggers():
    targets = list_supported_targets()
    for trigger in ("T02", "T04", "T06", "T08", "T10", "T11"):
        assert ("HL7", "MDM", trigger) in targets


def test_list_supported_targets_includes_ccd():
    assert ("CDA", "CCD", "") in list_supported_targets()


def test_list_supported_targets_includes_270():
    assert ("EDI", "270", "") in list_supported_targets()


def test_list_supported_targets_includes_271():
    assert ("EDI", "271", "") in list_supported_targets()


def test_get_builder_raises_mapping_error_for_unregistered_target():
    with pytest.raises(MappingError):
        get_builder("HL7", "ADT", "A99")


def test_get_builder_normalizes_case_and_whitespace():
    builder_lower = get_builder("hl7", "adt", "a01")
    builder_upper = get_builder("HL7", "ADT", "A01")
    assert builder_lower is builder_upper


def test_adt_a01_round_trips_patient_and_encounter_fields():
    forward_text = (
        "MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20260811120000||ADT^A01|MSG00001|P|2.5\r"
        "EVN|A01|20260811120000\r"
        "PID|1||123456^^^HOSP^MR||Doe^Jane^Q||19620305|F|||123 Main St^Apt 4^Springfield^IL^62704^USA||(555)555-1234\r"
        "PV1|1|I|W123^456^A^HOSP||||1234^Smith^John^^^^MD||||||||||||V0001|||||||||||||||||||||||||20260811120000|\r"
    )
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A01")

    assert message_text.startswith("MSH|^~\\&|")
    assert "||ADT^A01|" in message_text
    segments = message_text.strip("\r").split("\r")
    segment_ids = [s.split("|")[0] for s in segments]
    assert segment_ids == ["MSH", "EVN", "PID", "PV1"]

    pid = next(s for s in segments if s.startswith("PID|"))
    pid_fields = pid.split("|")
    assert pid_fields[3] == "123456^^^HOSP^MR"
    assert pid_fields[5] == "Doe^Jane^Q"
    assert pid_fields[7] == "19620305"
    assert pid_fields[8] == "F"
    assert pid_fields[11] == "123 Main St^Apt 4^Springfield^IL^62704^USA"
    assert pid_fields[13] == "(555)555-1234"

    pv1 = next(s for s in segments if s.startswith("PV1|"))
    pv1_fields = pv1.split("|")
    assert pv1_fields[2] == "I"
    assert pv1_fields[19] == "V0001"


def test_round_trip_produces_a_convertible_message_again():
    # Not just "text was produced" - the generated text must itself
    # successfully convert back through the real forward pipeline, proving
    # this is a genuine round trip, not just string formatting.
    forward_text = (
        "MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20260811120000||ADT^A01|MSG00001|P|2.5\r"
        "EVN|A01|20260811120000\r"
        "PID|1||123456^^^HOSP^MR||Doe^Jane^Q||19620305|F|||123 Main St^Apt 4^Springfield^IL^62704^USA||(555)555-1234\r"
        "PV1|1|I|W123^456^A^HOSP||||1234^Smith^John^^^^MD||||||||||||V0001|||||||||||||||||||||||||20260811120000|\r"
    )
    bundle = convert_hl7_to_bundle(forward_text)
    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A01")

    round_tripped_bundle = convert_hl7_to_bundle(message_text)

    patient = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    assert patient.name[0].family == "Doe"
    assert patient.name[0].given == ["Jane", "Q"]
    assert patient.gender == "female"
    assert patient.birthDate.isoformat() == "1962-03-05"

    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.class_fhir.code == "IMP"
    assert encounter.identifier[0].value == "V0001"


def test_missing_patient_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "HL7", "ADT", "A01")


def test_encounter_is_optional():
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A01")

    segments = message_text.strip("\r").split("\r")
    segment_ids = [s.split("|")[0] for s in segments]
    assert segment_ids == ["MSH", "EVN", "PID", "PV1"]
    pid = next(s for s in segments if s.startswith("PID|"))
    assert "Solo" in pid


def test_multiple_given_names_map_to_separate_xpn_components():
    patient = Patient(id="p1", name=[{"family": "Multi", "given": ["Ann", "Beth"]}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A01")
    pid = next(s for s in message_text.split("\r") if s.startswith("PID|"))
    assert pid.split("|")[5] == "Multi^Ann^Beth"


@pytest.mark.parametrize("trigger,fixture", [("A02", "adt_a02_basic.hl7"), ("A04", "adt_a04_basic.hl7"), ("A05", "adt_a05_basic.hl7")])
def test_adt_trigger_round_trips_and_preserves_class_and_status(trigger, fixture):
    # A01/A02/A04/A05/A08 all reuse the identical base builder - this
    # parametrized test proves each one produces the correct EVN-1/MSH-9
    # trigger code and a Bundle-consistent round trip, not just that some
    # text was produced.
    forward_text = (FIXTURES / fixture).read_text()
    bundle = convert_hl7_to_bundle(forward_text)
    original_encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", trigger)
    assert f"||ADT^{trigger}|" in message_text
    segments = message_text.strip("\r").split("\r")
    evn = next(s for s in segments if s.startswith("EVN|"))
    assert evn.split("|")[1] == trigger

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    round_tripped_encounter = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter"
    )
    assert round_tripped_encounter.class_fhir.code == original_encounter.class_fhir.code
    assert round_tripped_encounter.status == original_encounter.status


def test_adt_a02_round_trips_prior_and_current_location():
    forward_text = (FIXTURES / "adt_a02_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A02")
    pv1 = next(s for s in message_text.split("\r") if s.startswith("PV1|"))
    fields = pv1.split("|")
    assert fields[3]  # current location (PV1-3)
    assert fields[6]  # prior location (PV1-6)
    assert fields[3] != fields[6]

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")
    statuses = {loc.status for loc in encounter.location}
    assert statuses == {"active", "completed"}


def test_adt_a03_round_trips_discharge_disposition_and_status():
    forward_text = (FIXTURES / "adt_a03_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)
    original_encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A03")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")

    assert encounter.status == "finished"
    assert encounter.period.end is not None
    assert (
        encounter.hospitalization.dischargeDisposition.coding[0].code
        == original_encounter.hospitalization.dischargeDisposition.coding[0].code
    )


def test_adt_a03_missing_discharge_time_raises_mapping_error():
    patient = Patient(id="p1", name=[{"family": "NoDischarge"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "ADT", "A03")


@pytest.mark.parametrize("fixture,expected_status", [("adt_a08_finished.hl7", "finished"), ("adt_a08_in_progress.hl7", "in-progress")])
def test_adt_a08_round_trips_inferred_status(fixture, expected_status):
    # A08's own status is inferred purely from whether PV1-45 is present -
    # the reverse builder needs no A08-specific logic at all, since
    # whatever discharge time the source Encounter carries (or doesn't)
    # gets faithfully re-emitted, and re-parsing re-infers the same status.
    forward_text = (FIXTURES / fixture).read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A08")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.status == expected_status


@pytest.mark.parametrize(
    "trigger,fixture",
    [("A11", "adt_a11_basic.hl7"), ("A13", "adt_a13_with_discharge.hl7"), ("A38", "adt_a38_basic.hl7")],
)
def test_adt_cancel_trigger_round_trips_and_preserves_entered_in_error_status(trigger, fixture):
    # A11/A13/A38 needed zero cancel-trigger-specific reverse logic, the
    # same "no A08-specific code needed" discovery the earlier ADT breadth
    # pass made: _build_pv1/_build_evn already faithfully re-emit whatever
    # the source Encounter carries, and the forward mapper's own
    # _drop_evn2_period_start_fallback ignores EVN-2 for period.start on
    # every cancel trigger regardless of what this builder writes there, so
    # a plain trigger_event swap round-trips correctly without needing to
    # special-case the EVN-2 hazard on the way back out.
    forward_text = (FIXTURES / fixture).read_text()
    bundle = convert_hl7_to_bundle(forward_text)
    original_encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", trigger)
    assert f"||ADT^{trigger}|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.status == "entered-in-error" == original_encounter.status
    original_period_start = original_encounter.period.start if original_encounter.period else None
    period_start = encounter.period.start if encounter.period else None
    assert period_start == original_period_start


def test_adt_a13_round_trips_discharge_disposition():
    forward_text = (FIXTURES / "adt_a13_with_discharge.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)
    original_encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")

    message_text = build_message_from_bundle(bundle, "HL7", "ADT", "A13")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")

    assert (
        encounter.hospitalization.dischargeDisposition.coding[0].code
        == original_encounter.hospitalization.dischargeDisposition.coding[0].code
    )


def test_ccd_round_trip_produces_a_convertible_document_again():
    # The real fixture used to prove the forward direction, run in reverse
    # then forward again - the genuine round-trip proof, not just "XML was
    # produced," mirroring test_round_trip_produces_a_convertible_message_
    # again's discipline for the HL7v2 slice.
    forward_xml = (FIXTURES / "ccd_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")

    assert document_text.startswith('<?xml version="1.0"')
    round_tripped_bundle = convert_cda_to_bundle(document_text)

    patient = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    assert patient.name[0].family == "Betterhalf"
    assert patient.name[0].given == ["Eve"]
    assert patient.gender == "female"
    assert patient.birthDate.isoformat() == "1975-05-01"
    assert patient.address[0].city == "Beaverton"
    # A real OID-shaped identifier.system must round-trip exactly, not
    # collapse into a disclosed placeholder root - see
    # app/transform/cda_ccd.py::_reverse_identifier_root.
    assert patient.identifier[0].system == "urn:oid:2.16.840.1.113883.19.5"
    assert patient.identifier[0].value == "998991"

    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.class_fhir.code == "AMB"
    assert encounter.period.start is not None
    assert encounter.period.end is not None

    conditions = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Condition"]
    displays = {c.code.coding[0].display for c in conditions}
    assert displays == {"Hypertensive disorder", "Type 2 diabetes mellitus"}


def test_ccd_round_trip_preserves_coding_system_not_just_code():
    # Regression test for a real, pre-existing bug caught while adding the
    # Medications section: the Problems value-building originally wrote
    # coding.system (a FHIR system URL) directly into the CDA codeSystem
    # attribute instead of reversing it back to an OID, producing garbage
    # like "urn:oid:http://snomed.info/sct" on a second round trip.
    forward_xml = (FIXTURES / "ccd_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_condition = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Condition")

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    condition = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Condition")

    assert condition.code.coding[0].system == original_condition.code.coding[0].system == "http://snomed.info/sct"


def test_ccd_round_trip_preserves_medication_fields():
    forward_xml = (FIXTURES / "ccd_medications_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_requests = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "MedicationRequest"),
        key=lambda m: m.medicationCodeableConcept.coding[0].code,
    )
    assert len(original_requests) == 2

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    requests = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "MedicationRequest"),
        key=lambda m: m.medicationCodeableConcept.coding[0].code,
    )
    assert len(requests) == 2

    for original, request in zip(original_requests, requests):
        assert request.status == original.status
        assert request.intent == original.intent
        assert request.medicationCodeableConcept.coding[0].code == original.medicationCodeableConcept.coding[0].code
        assert (
            request.medicationCodeableConcept.coding[0].system == original.medicationCodeableConcept.coding[0].system
        )
        original_dosage = original.dosageInstruction[0] if original.dosageInstruction else None
        dosage = request.dosageInstruction[0] if request.dosageInstruction else None
        if original_dosage is None:
            assert dosage is None
            continue
        assert dosage.patientInstruction == original_dosage.patientInstruction
        if original_dosage.route:
            assert dosage.route.coding[0].code == original_dosage.route.coding[0].code
        if original_dosage.doseAndRate:
            assert dosage.doseAndRate[0].doseQuantity.value == original_dosage.doseAndRate[0].doseQuantity.value
            assert dosage.doseAndRate[0].doseQuantity.unit == original_dosage.doseAndRate[0].doseQuantity.unit
        if original_dosage.timing:
            assert (
                dosage.timing.repeat.boundsPeriod.start == original_dosage.timing.repeat.boundsPeriod.start
            )
            assert dosage.timing.repeat.boundsPeriod.end == original_dosage.timing.repeat.boundsPeriod.end


def test_ccd_round_trip_produces_no_medications_section_when_negated():
    # ccd_medications_negated.xml has a negationInd="true" entry, which the
    # forward mapper skips entirely - so the reverse builder must never see
    # a MedicationRequest for it, and this document has no other Medication
    # Activity entries, so no Medications section should be emitted at all.
    forward_xml = (FIXTURES / "ccd_medications_negated.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    assert not [e for e in bundle.entry if e.resource.get_resource_type() == "MedicationRequest"]

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    assert not [e for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "MedicationRequest"]


def test_ccd_round_trip_preserves_allergy_fields():
    forward_xml = (FIXTURES / "ccd_allergies_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "AllergyIntolerance")

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    allergy = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "AllergyIntolerance")

    assert allergy.code.coding[0].code == original.code.coding[0].code
    assert allergy.code.coding[0].system == original.code.coding[0].system == "http://snomed.info/sct"
    assert allergy.type == original.type == "allergy"
    assert allergy.category == original.category == ["food"]
    assert allergy.clinicalStatus.coding[0].code == original.clinicalStatus.coding[0].code == "active"
    assert allergy.criticality == original.criticality == "high"
    assert allergy.onsetDateTime == original.onsetDateTime
    assert allergy.recordedDate == original.recordedDate
    assert allergy.reaction[0].manifestation[0].coding[0].code == original.reaction[0].manifestation[0].coding[0].code
    assert allergy.reaction[0].severity == original.reaction[0].severity == "moderate"


def test_ccd_round_trip_preserves_no_known_allergies_negation():
    # A negated allergy with no resolvable allergen degrades to the IG's
    # own generic "No known allergies" text - this is the one shape this
    # builder can fully round-trip exactly, since there's no coded allergen
    # to lose in the first place (see cda_ccd.py's own docstring for the
    # further-lossy case where a negated allergy DID carry a resolvable
    # allergen).
    forward_xml = (FIXTURES / "ccd_allergies_no_known.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "AllergyIntolerance")
    assert original.code.text == "No known allergies"
    assert not original.code.coding

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    allergy = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "AllergyIntolerance")
    assert allergy.code.text == "No known allergies"
    assert not allergy.code.coding


def test_ccd_round_trip_preserves_immunization_fields():
    forward_xml = (FIXTURES / "ccd_immunizations_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_immunizations = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Immunization"),
        key=lambda i: i.vaccineCode.coding[0].code,
    )
    # The fixture's third entry (INT mood, planned Tdap) must never convert
    # at all - only the two EVN-mood entries (administered, refused) do.
    assert len(original_immunizations) == 2

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    immunizations = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Immunization"),
        key=lambda i: i.vaccineCode.coding[0].code,
    )
    assert len(immunizations) == 2

    for original, immunization in zip(original_immunizations, immunizations):
        assert immunization.status == original.status
        assert immunization.vaccineCode.coding[0].code == original.vaccineCode.coding[0].code
        assert immunization.vaccineCode.coding[0].system == original.vaccineCode.coding[0].system
        assert immunization.lotNumber == original.lotNumber
        if original.route:
            assert immunization.route.coding[0].code == original.route.coding[0].code
        if original.doseQuantity is not None:
            assert immunization.doseQuantity.value == original.doseQuantity.value
            assert immunization.doseQuantity.unit == original.doseQuantity.unit


def test_ccd_round_trip_preserves_vital_signs_panel_and_members():
    forward_xml = (FIXTURES / "ccd_vitals_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_observations = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation"]
    original_panel = next(o for o in original_observations if o.code.coding[0].code == "85353-1")
    assert len(original_panel.hasMember) == 2

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    observations = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Observation"]
    panel = next(o for o in observations if o.code.coding[0].code == "85353-1")
    assert len(panel.hasMember) == 2

    original_members = sorted(
        (o for o in original_observations if o.code.coding[0].code != "85353-1"), key=lambda o: o.code.coding[0].code
    )
    members = sorted(
        (o for o in observations if o.code.coding[0].code != "85353-1"), key=lambda o: o.code.coding[0].code
    )
    for original, member in zip(original_members, members):
        assert member.code.coding[0].code == original.code.coding[0].code
        assert float(member.valueQuantity.value) == float(original.valueQuantity.value)
        assert member.valueQuantity.unit == original.valueQuantity.unit
        if original.interpretation:
            assert member.interpretation[0].coding[0].code == original.interpretation[0].coding[0].code


def test_ccd_round_trip_preserves_result_report_and_observations():
    forward_xml = (FIXTURES / "ccd_results_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_report = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DiagnosticReport")
    assert len(original_report.result) == 2

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    report = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DiagnosticReport")
    assert report.code.coding[0].code == original_report.code.coding[0].code
    assert report.status == original_report.status
    assert len(report.result) == 2

    original_observations = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation"),
        key=lambda o: o.code.coding[0].code,
    )
    observations = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Observation"),
        key=lambda o: o.code.coding[0].code,
    )
    assert len(observations) == 2

    # One PQ-valued observation with a referenceRange, one ST-valued
    # free-text observation - exercising two of _build_observation_value's
    # own xsi:type branches in the same fixture.
    pq_original = next(o for o in original_observations if o.valueQuantity is not None)
    pq = next(o for o in observations if o.valueQuantity is not None)
    assert float(pq.valueQuantity.value) == float(pq_original.valueQuantity.value)
    assert pq.valueQuantity.unit == pq_original.valueQuantity.unit
    assert pq.interpretation[0].coding[0].code == pq_original.interpretation[0].coding[0].code
    assert float(pq.referenceRange[0].low.value) == float(pq_original.referenceRange[0].low.value)
    assert float(pq.referenceRange[0].high.value) == float(pq_original.referenceRange[0].high.value)

    st_original = next(o for o in original_observations if o.valueString is not None)
    st = next(o for o in observations if o.valueString is not None)
    assert st.valueString == st_original.valueString


def test_ccd_missing_patient_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "CDA", "CCD", "")


def test_ccd_encounter_and_problems_are_optional():
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    resource_types = {e.resource.get_resource_type() for e in round_tripped_bundle.entry}
    assert resource_types == {"Patient"}


def test_edi_270_round_trip_with_dependent_produces_a_convertible_interchange_again():
    # The real fixture used to prove the forward direction (with a
    # dependent), run in reverse then forward again.
    forward_x12 = (FIXTURES / "edi_270_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)

    message_text = build_message_from_bundle(bundle, "EDI", "270", "")

    assert message_text.startswith("ISA*")
    round_tripped_bundle = convert_edi_to_bundle(message_text)

    organizations = {o.resource.name: o.resource for o in round_tripped_bundle.entry if o.resource.get_resource_type() == "Organization"}
    assert "ACME HEALTH PLAN" in organizations
    payer = organizations["ACME HEALTH PLAN"]
    assert payer.identifier[0].value == "PAYERID001"

    patients = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 2
    names = {(p.name[0].family, p.name[0].given[0]) for p in patients}
    assert ("DOE", "JANE") in names
    assert ("DOE", "JIMMY") in names

    coverage = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Coverage")
    request = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityRequest"
    )
    # The dependent (Jimmy) must still resolve as "the patient", not the
    # subscriber (Jane) - the precedence rule the real forward fixture's
    # own mapping test already establishes, now proven to survive a full
    # reverse-then-forward round trip.
    assert request.patient.reference == coverage.beneficiary.reference
    assert coverage.beneficiary.reference != coverage.subscriber.reference


def test_edi_270_round_trip_without_dependent():
    forward_x12 = (FIXTURES / "edi_270_no_dependent.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)

    message_text = build_message_from_bundle(bundle, "EDI", "270", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)

    patients = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 1
    coverage = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Coverage")
    assert coverage.beneficiary.reference == coverage.subscriber.reference


def test_edi_270_missing_patient_raises_mapping_error():
    payer = Organization(id="payer1", name="Payer")
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:payer1", resource=payer)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "EDI", "270", "")


def test_edi_270_missing_payer_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "270", "")


def test_edi_271_round_trip_produces_a_convertible_interchange_again():
    forward_x12 = (FIXTURES / "edi_271_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)

    message_text = build_message_from_bundle(bundle, "EDI", "271", "")

    assert message_text.startswith("ISA*")
    round_tripped_bundle = convert_edi_to_bundle(message_text)

    response = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse"
    )
    assert response.outcome == "complete"
    assert response.insurance[0].inforce is True
    descriptions = {item.description for item in response.insurance[0].item}
    assert "Gold Plan" in descriptions


def test_edi_271_rejection_round_trips_outcome_and_disposition():
    forward_x12 = (FIXTURES / "edi_271_rejected.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)

    message_text = build_message_from_bundle(bundle, "EDI", "271", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)

    response = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "CoverageEligibilityResponse"
    )
    assert response.outcome == "error"
    assert response.disposition is not None


def test_edi_271_missing_patient_raises_mapping_error():
    payer = Organization(id="payer1", name="Payer")
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:payer1", resource=payer)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "EDI", "271", "")


def test_edi_271_missing_payer_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "271", "")


def test_siu_s12_round_trip_preserves_appointment_fields():
    forward_text = (FIXTURES / "siu_s12_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)
    original = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")

    message_text = build_message_from_bundle(bundle, "HL7", "SIU", "S12")
    assert "||SIU^S12|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    appointment = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Appointment")

    assert appointment.status == original.status == "booked"
    assert appointment.start == original.start
    assert appointment.end == original.end
    assert appointment.minutesDuration == original.minutesDuration
    assert appointment.reasonCode[0].coding[0].code == original.reasonCode[0].coding[0].code
    assert appointment.appointmentType.coding[0].code == original.appointmentType.coding[0].code
    assert appointment.serviceType[0].coding[0].code == original.serviceType[0].coding[0].code
    assert appointment.comment == original.comment
    assert len(appointment.participant) == len(original.participant)


def test_siu_s12_round_trip_preserves_practitioner_location_and_device():
    forward_text = (FIXTURES / "siu_s12_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "SIU", "S12")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)

    resource_types = {e.resource.get_resource_type() for e in round_tripped_bundle.entry}
    assert resource_types == {"Patient", "Appointment", "Practitioner", "Location", "Device"}

    practitioner = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Practitioner")
    assert practitioner.name[0].family == "Smith"
    assert practitioner.name[0].given == ["John"]

    device = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Device")
    assert device.deviceName[0].name == "Portable X-Ray"
    assert device.type.coding[0].code == "EQUIPMENT"


def test_siu_s12_missing_patient_raises_mapping_error():
    appointment = Appointment(id="a1", status="booked", participant=[])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:a1", resource=appointment)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "SIU", "S12")


def test_siu_s12_missing_appointment_raises_mapping_error():
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "SIU", "S12")


@pytest.mark.parametrize(
    "trigger,fixture,expected_status",
    [
        ("S13", "siu_s13_basic.hl7", "booked"),
        ("S14", "siu_s14_basic.hl7", "booked"),
        ("S15", "siu_s15_basic.hl7", "cancelled"),
        ("S17", "siu_s17_basic.hl7", "entered-in-error"),
        ("S26", "siu_s26_basic.hl7", "noshow"),
    ],
)
def test_siu_trigger_round_trips_and_preserves_status(trigger, fixture, expected_status):
    # S12/S13/S14/S26 (booked-timing-required) and S15/S17 (untimed) each
    # reuse the identical base builder, split only by _validate - this
    # confirms both branches produce a correct round trip.
    forward_text = (FIXTURES / fixture).read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "SIU", trigger)
    assert f"||SIU^{trigger}|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    original = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Appointment")
    appointment = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Appointment")
    assert appointment.status == expected_status == original.status


def test_siu_s26_missing_start_time_raises_mapping_error():
    # S26 is one of the booked-timing-required triggers, mirroring S12's
    # own requirement - an Appointment with no start can't round-trip
    # through a trigger the forward mapper itself would reject.
    appointment = Appointment(id="a1", status="noshow", participant=[])
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [
        BundleEntry(fullUrl="urn:uuid:p1", resource=patient),
        BundleEntry(fullUrl="urn:uuid:a1", resource=appointment),
    ]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "SIU", "S26")


def test_oru_r01_round_trip_preserves_report_and_observation_grouping():
    forward_text = (FIXTURES / "oru_r01_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "ORU", "R01")
    assert "||ORU^R01|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    reports = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DiagnosticReport"),
        key=lambda r: r.code.coding[0].code,
    )
    assert [r.code.coding[0].code for r in reports] == ["CBC", "GLU"]
    cbc_report = reports[0]
    assert cbc_report.status == "final"
    assert len(cbc_report.result) == 2

    glu_report = reports[1]
    assert glu_report.status == "preliminary"
    assert len(glu_report.result) == 1


def test_oru_r01_round_trip_preserves_observation_values_and_performer():
    forward_text = (FIXTURES / "oru_r01_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "ORU", "R01")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)

    observations = {
        e.resource.code.coding[0].code: e.resource
        for e in round_tripped_bundle.entry
        if e.resource.get_resource_type() == "Observation"
    }
    wbc = observations["WBC"]
    assert float(wbc.valueQuantity.value) == 7.2
    assert wbc.valueQuantity.unit == "10*3/uL"
    assert wbc.interpretation[0].coding[0].code == "N"
    assert wbc.performer is not None

    glucose = observations["GLUCOSE"]
    assert float(glucose.valueQuantity.value) == 95.0
    assert glucose.referenceRange[0].text == "70-100"

    practitioner = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Practitioner"
    )
    assert practitioner.name[0].family == "Rivera"
    assert practitioner.name[0].given == ["Ana"]


@pytest.mark.parametrize(
    "trigger,fixture",
    [("R30", "oru_r30_basic.hl7"), ("R31", "oru_r31_basic.hl7"), ("R32", "oru_r32_basic.hl7"), ("R40", "oru_r40_basic.hl7")],
)
def test_oru_trigger_round_trips_and_preserves_report_grouping(trigger, fixture):
    # R01/R30/R31/R32/R40 all reuse the identical base builder - this
    # confirms the trigger-string swap alone is enough for each sibling.
    forward_text = (FIXTURES / fixture).read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "ORU", trigger)
    assert f"||ORU^{trigger}|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    original_reports = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "DiagnosticReport"),
        key=lambda r: r.code.coding[0].code,
    )
    round_tripped_reports = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DiagnosticReport"),
        key=lambda r: r.code.coding[0].code,
    )
    assert [r.code.coding[0].code for r in round_tripped_reports] == [r.code.coding[0].code for r in original_reports]
    assert [len(r.result) for r in round_tripped_reports] == [len(r.result) for r in original_reports]


def test_oru_r01_missing_patient_raises_mapping_error():
    report = DiagnosticReport(id="r1", status="final", code={"coding": [{"code": "X"}]})
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:r1", resource=report)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "ORU", "R01")


def test_oru_r01_missing_diagnostic_report_raises_mapping_error():
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "ORU", "R01")


def test_mdm_t02_round_trip_preserves_document_fields():
    forward_text = (FIXTURES / "mdm_t02_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "MDM", "T02")
    assert "||MDM^T02|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    original_doc = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    doc = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference")

    assert doc.type.coding[0].code == original_doc.type.coding[0].code
    assert doc.masterIdentifier.value == original_doc.masterIdentifier.value
    assert doc.date == original_doc.date
    assert doc.description == original_doc.description
    assert doc.securityLabel[0].coding[0].code == original_doc.securityLabel[0].coding[0].code

    originator = next(
        e.resource
        for e in round_tripped_bundle.entry
        if e.resource.get_resource_type() == "Practitioner" and e.resource.name[0].family == "Chen"
    )
    assert originator.name[0].given == ["Wei"]
    authenticator = next(
        e.resource
        for e in round_tripped_bundle.entry
        if e.resource.get_resource_type() == "Practitioner" and e.resource.name[0].family == "Alvarez"
    )
    assert authenticator.name[0].given == ["Rosa"]


def test_mdm_t02_round_trip_preserves_document_body():
    forward_text = (FIXTURES / "mdm_t02_basic.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "MDM", "T02")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)

    original_binary = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary")
    binary = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Binary")
    assert binary.data == original_binary.data


def test_mdm_t02_same_author_authenticator_dedups_practitioner_on_round_trip():
    forward_text = (FIXTURES / "mdm_t02_same_author_authenticator.hl7").read_text()
    bundle = convert_hl7_to_bundle(forward_text)
    assert len([e for e in bundle.entry if e.resource.get_resource_type() == "Practitioner"]) == 1

    message_text = build_message_from_bundle(bundle, "HL7", "MDM", "T02")
    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    assert len([e for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Practitioner"]) == 1

    original_doc = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    doc = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    assert doc.identifier[0].value == original_doc.identifier[0].value


def test_mdm_t02_missing_patient_raises_mapping_error():
    doc = DocumentReference(
        id="d1", status="current", content=[{"attachment": {}}], subject={"reference": "urn:uuid:p1"}
    )
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:d1", resource=doc)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "MDM", "T02")


def test_mdm_t02_missing_document_reference_raises_mapping_error():
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:p1", resource=patient)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "HL7", "MDM", "T02")


@pytest.mark.parametrize("trigger", ["T04", "T06", "T08", "T10", "T11"])
def test_mdm_trigger_round_trips_and_preserves_document_type(trigger):
    # T02/T04/T06/T08/T10/T11 all reuse the identical base builder - this
    # confirms the trigger-string swap alone is enough for each sibling,
    # the same fixture-free (generator-backed) shape the equivalent ADT/ORU
    # breadth-pass tests use when no dedicated per-trigger fixture exists.
    forward_text = generate("MDM", trigger, seed=7)
    bundle = convert_hl7_to_bundle(forward_text)

    message_text = build_message_from_bundle(bundle, "HL7", "MDM", trigger)
    assert f"||MDM^{trigger}|" in message_text

    round_tripped_bundle = convert_hl7_to_bundle(message_text)
    original_doc = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    doc = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference")
    assert doc.masterIdentifier.value == original_doc.masterIdentifier.value
