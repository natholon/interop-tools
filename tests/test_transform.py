import base64
from collections import Counter
from pathlib import Path

import pytest
from fhir.resources.R4B.appointment import Appointment
from fhir.resources.R4B.binary import Binary
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.claim import Claim, ClaimInsurance
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.coverage import Coverage
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient

from app.cda.pipeline import convert_cda_to_bundle
from app.edi.pipeline import convert_edi_to_bundle
from app.generators.registry import generate
from app.hl7.errors import MappingError
from app.hl7.pipeline import convert_hl7_to_bundle
from app.pipeline import convert_to_bundle
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


def test_ccd_round_trip_preserves_a_medication_requester():
    # The requester Practitioner was silently lost on the reverse trip
    # until _reverse_author_element existed - and the parametrized
    # resource-type-count test could not catch it, because the generator
    # emitted no medication author for it to lose.
    forward_xml = (FIXTURES / "ccd_medications_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    round_tripped = convert_cda_to_bundle(build_message_from_bundle(bundle, "CDA", "CCD", ""))

    practitioners = [
        e.resource for e in round_tripped.entry if e.resource.get_resource_type() == "Practitioner"
    ]
    assert [str(p.name[0].family) for p in practitioners] == ["Prescriber"]
    assert practitioners[0].identifier[0].value == "9988776655"

    requester = next(
        e.resource.requester
        for e in round_tripped.entry
        if e.resource.get_resource_type() == "MedicationRequest" and e.resource.requester
    )
    assert requester.reference == f"urn:uuid:{practitioners[0].id}"


def test_ccd_round_trip_preserves_a_device_author():
    # An assignedAuthoringDevice reverses back to assignedAuthoringDevice,
    # not to an assignedPerson - the Device is the author, and its model
    # and software names have nowhere to live on a Practitioner.
    forward_xml = (FIXTURES / "ccd_medications_device_author.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    message = build_message_from_bundle(bundle, "CDA", "CCD", "")
    assert "<assignedAuthoringDevice>" in message
    assert "<assignedPerson>" not in message

    round_tripped = convert_cda_to_bundle(message)
    devices = [e.resource for e in round_tripped.entry if e.resource.get_resource_type() == "Device"]
    assert len(devices) == 1
    assert [n.name for n in devices[0].deviceName] == ["Acme EHR 9000", "Acme Charting Suite"]
    assert devices[0].identifier[0].value == "EHR-1"
    assert not [e for e in round_tripped.entry if e.resource.get_resource_type() == "Practitioner"]


def test_ccd_round_trip_preserves_vital_signs_panel_and_members():
    forward_xml = (FIXTURES / "ccd_vitals_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_observations = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation"]
    original_panel = next(o for o in original_observations if o.code.coding[0].code == "85353-1")
    assert len(original_panel.hasMember) == 4

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    observations = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Observation"]
    round_tripped_panel = next(o for o in observations if o.code.coding and o.code.coding[0].code == "85353-1")
    assert len(round_tripped_panel.hasMember) == 4

    plain_codes = {"8867-4", "8310-5"}
    original_members = sorted(
        (o for o in original_observations if o.code.coding[0].code in plain_codes), key=lambda o: o.code.coding[0].code
    )
    members = sorted(
        (o for o in observations if o.code.coding and o.code.coding[0].code in plain_codes), key=lambda o: o.code.coding[0].code
    )
    assert len(original_members) == len(members) == 2
    for original, member in zip(original_members, members):
        assert member.code.coding[0].code == original.code.coding[0].code
        assert float(member.valueQuantity.value) == float(original.valueQuantity.value)
        assert member.valueQuantity.unit == original.valueQuantity.unit
        if original.interpretation:
            assert member.interpretation[0].coding[0].code == original.interpretation[0].coding[0].code

    # Blood Pressure Panel: reverses to two flat <observation> elements
    # (one per .component), then re-groups back into the identical panel
    # shape on the second forward pass.
    bp_panel = next(o for o in observations if o.code.coding and o.code.coding[0].code == "85354-9")
    assert bp_panel.valueQuantity is None
    bp_components = {c.code.coding[0].code: c for c in bp_panel.component}
    assert float(bp_components["8480-6"].valueQuantity.value) == 120
    assert float(bp_components["8462-4"].valueQuantity.value) == 80

    # Pulse Oximetry Panel: the primary reading's own value round-trips
    # exactly, and its one present optional component (flow rate) survives
    # too - both IG-documented synonymous codings are regenerated
    # regardless of which single one this fixture's own source used.
    pulse_ox_panel = next(o for o in observations if o.code.coding and {c.code for c in o.code.coding} == {"59408-5", "2708-6"})
    assert float(pulse_ox_panel.valueQuantity.value) == 97
    assert len(pulse_ox_panel.component) == 1
    assert pulse_ox_panel.component[0].code.coding[0].code == "3151-8"
    assert float(pulse_ox_panel.component[0].valueQuantity.value) == 2


def test_ccd_round_trip_preserves_result_report_and_observations():
    forward_xml = (FIXTURES / "ccd_results_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_report = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "DiagnosticReport")
    assert len(original_report.result) == 4

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    report = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DiagnosticReport")
    assert report.code.coding[0].code == original_report.code.coding[0].code
    assert report.status == original_report.status
    assert len(report.result) == 4

    original_codes = {"6690-2", "33747-0"}
    original_observations = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation" and e.resource.code.coding[0].code in original_codes),
        key=lambda o: o.code.coding[0].code,
    )
    observations = sorted(
        (
            e.resource
            for e in round_tripped_bundle.entry
            if e.resource.get_resource_type() == "Observation" and e.resource.code.coding and e.resource.code.coding[0].code in original_codes
        ),
        key=lambda o: o.code.coding[0].code,
    )
    assert len(original_observations) == len(observations) == 2

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

    # IVL_PQ (both bounds) round-trips as .valueRange, not .valueQuantity -
    # creatinine/color are outside original_codes above, so re-fetch them
    # directly from the full observation lists.
    all_round_tripped = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Observation"]
    all_original = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation"]
    creatinine_original = next(o for o in all_original if o.code.coding[0].code == "2160-0")
    creatinine = next(o for o in all_round_tripped if o.code.coding and o.code.coding[0].code == "2160-0")
    assert creatinine.valueQuantity is None
    assert float(creatinine.valueRange.low.value) == float(creatinine_original.valueRange.low.value) == 0.6
    assert float(creatinine.valueRange.high.value) == float(creatinine_original.valueRange.high.value) == 1.3

    # ED's own plain-text case reverses to ST (a disclosed, permanent
    # simplification - see _build_result_value_element's own docstring),
    # so it survives a second round trip stably even though the very first
    # reverse pass can't recover which xsi:type the source originally used.
    color = next(o for o in all_round_tripped if o.code.coding and o.code.coding[0].code == "5778-6")
    assert color.valueString == "Yellow"

    # The organizer-level Specimen itself round-trips too, referenced from
    # both the report and every result Observation that doesn't carry its
    # own override.
    specimens = {
        s.type.coding[0].code: s
        for s in (e.resource for e in round_tripped_bundle.entry)
        if s.get_resource_type() == "Specimen"
    }
    specimen = specimens["119297000"]
    assert report.specimen[0].reference == f"urn:uuid:{specimen.id}"
    assert float(specimen.collection.quantity.value) == 5
    assert specimen.note[0].text == "Drawn via venipuncture"
    assert specimen.collection.bodySite.coding[0].code == "368225008"

    # An observation-level <specimen> overrides the organizer default for
    # that one Observation - a real round-trip bug this fixture's own
    # culture entry now guards against: the reverse builder previously
    # regenerated only the organizer-level specimen, silently dropping the
    # override's own Specimen resource entirely (caught by a resource-type
    # count sweep across generated documents, not by any existing test).
    assert set(specimens) == {"119297000", "122575003"}
    culture = next(o for o in all_round_tripped if o.code.coding and o.code.coding[0].code == "33747-0")
    assert culture.specimen.reference == f"urn:uuid:{specimens['122575003'].id}"
    # Every other member still inherits the organizer-level default.
    assert creatinine.specimen.reference == f"urn:uuid:{specimen.id}"


def test_ccd_round_trip_preserves_original_text_as_codeable_concept_text():
    # ccd_procedures_basic.xml's own completed entry carries the
    # narrative-anchor originalText shape (<reference value="#Proc1"/>,
    # resolved forward against the section's own <text>) and its negated
    # entry the inline shape - both reverse to the *inline* shape, a
    # disclosed simplification (CodeableConcept.text keeps no record of
    # which it came from), so both are round-trip stable from here on.
    forward_xml = (FIXTURES / "ccd_procedures_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)

    procedures = {
        p.code.coding[0].code: p for p in (e.resource for e in round_tripped_bundle.entry) if p.get_resource_type() == "Procedure"
    }
    assert procedures["80146002"].code.text == "Appendectomy of the appendix"
    assert procedures["73761001"].code.text == "Screening colonoscopy"

    # Stable across a second cycle - the inline shape re-parses to the
    # identical .text rather than degrading further.
    twice = convert_cda_to_bundle(build_message_from_bundle(round_tripped_bundle, "CDA", "CCD", ""))
    twice_procedures = {
        p.code.coding[0].code: p for p in (e.resource for e in twice.entry) if p.get_resource_type() == "Procedure"
    }
    assert twice_procedures["80146002"].code.text == "Appendectomy of the appendix"
    assert twice_procedures["73761001"].code.text == "Screening colonoscopy"


def test_ccd_round_trip_escapes_xml_special_characters_in_original_text():
    # CodeableConcept.text is free text reaching a raw f-string builder -
    # the same escaping hazard app/transform/'s own adversarial review
    # already found once for names/displays (see CLAUDE.md).
    forward_xml = (FIXTURES / "ccd_procedures_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    procedure = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Procedure")
    procedure.code.text = 'Diseases of ear & mastoid <acute> "flagged"'

    round_tripped = convert_cda_to_bundle(build_message_from_bundle(bundle, "CDA", "CCD", ""))
    texts = {
        p.code.text for p in (e.resource for e in round_tripped.entry) if p.get_resource_type() == "Procedure" and p.code
    }
    assert 'Diseases of ear & mastoid <acute> "flagged"' in texts


def test_ccd_round_trip_preserves_procedure_fields():
    forward_xml = (FIXTURES / "ccd_procedures_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_procedures = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Procedure"),
        key=lambda p: p.code.coding[0].code,
    )
    assert len(original_procedures) == 2

    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)
    procedures = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Procedure"),
        key=lambda p: p.code.coding[0].code,
    )
    assert len(procedures) == 2

    for original, procedure in zip(original_procedures, procedures):
        assert procedure.status == original.status
        assert procedure.code.coding[0].code == original.code.coding[0].code
        assert procedure.performedDateTime == original.performedDateTime
        if original.performedPeriod:
            assert procedure.performedPeriod.start == original.performedPeriod.start
            assert procedure.performedPeriod.end == original.performedPeriod.end
        if original.bodySite:
            assert procedure.bodySite[0].coding[0].code == original.bodySite[0].coding[0].code
        if original.identifier:
            assert [i.value or i.system for i in procedure.identifier] == [
                i.value or i.system for i in original.identifier
            ]

    # The negated (not-done) procedure's own identifier is a root-only <id>
    # (no @extension) - the reverse path _reverse_identifier_root itself
    # never needed to handle, confirming Procedures' own dedicated
    # _reverse_generic_identifier covers all three build_identifier shapes.
    negated = next(p for p in procedures if p.status == "not-done")
    assert negated.identifier[0].value == "urn:oid:d1e2f3a4-0002-4a1a-8a1a-000000000002"
    assert negated.identifier[0].system == "urn:ietf:rfc:3986"


def test_ccd_round_trip_preserves_procedure_performer_and_participant():
    # ccd_procedures_basic.xml's own completed entry carries a
    # fully-populated performer (PractitionerRole wrapping a Practitioner
    # with a name, an Organization, and an address-only Location) and a
    # Service Delivery Location participant; its negated entry carries an
    # id-only performer with no name at all.
    forward_xml = (FIXTURES / "ccd_procedures_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)

    def _resolve(bundle_, reference: str):
        resource_id = reference.removeprefix("urn:uuid:")
        return next(e.resource for e in bundle_.entry if e.resource.id == resource_id)

    procedures = {p.code.coding[0].code: p for p in (e.resource for e in round_tripped_bundle.entry) if p.get_resource_type() == "Procedure"}
    appendectomy = procedures["80146002"]
    colonoscopy = procedures["73761001"]

    assert len(appendectomy.performer) == 1
    role = _resolve(round_tripped_bundle, appendectomy.performer[0].actor.reference)
    assert role.get_resource_type() == "PractitionerRole"
    practitioner = _resolve(round_tripped_bundle, role.practitioner.reference)
    assert practitioner.name[0].family == "Smith"
    assert practitioner.name[0].given == ["John"]
    assert practitioner.identifier[0].value == "333444555"
    organization = _resolve(round_tripped_bundle, role.organization.reference)
    assert organization.name == "General Hospital"
    performer_location = _resolve(round_tripped_bundle, role.location[0].reference)
    assert performer_location.address.city == "Portland"
    assert performer_location.address.line == ["100 Main St"]
    assert role.telecom[0].value == "+1-555-555-1234"

    sdloc = _resolve(round_tripped_bundle, appendectomy.location.reference)
    assert sdloc.get_resource_type() == "Location"
    assert sdloc.name == "Community Medical Center"
    assert sdloc.type[0].coding[0].code == "1060-3"
    assert sdloc.address.city == "Portland"
    assert sdloc.address.postalCode == "99123"

    # The id-only performer survives too - no fabricated name.
    assert len(colonoscopy.performer) == 1
    colonoscopy_role = _resolve(round_tripped_bundle, colonoscopy.performer[0].actor.reference)
    colonoscopy_practitioner = _resolve(round_tripped_bundle, colonoscopy_role.practitioner.reference)
    assert colonoscopy_practitioner.name is None
    assert colonoscopy_practitioner.identifier[0].value == "urn:oid:2.16.840.1.113883.19.5"


def test_ccd_round_trip_preserves_procedure_indication_note_and_recorder():
    # ccd_procedures_basic.xml's own completed entry carries an Indication
    # (-> reasonCode), a Comment Activity with its own nested author
    # (-> note, with authorReference resolving to a real Practitioner
    # distinct from the recorder below), and a direct-child <author>
    # (-> recorder) - all three genuinely optional, absent on the negated
    # entry, confirming they're not unconditionally regenerated.
    forward_xml = (FIXTURES / "ccd_procedures_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)

    def _resolve(bundle_, reference: str):
        resource_id = reference.removeprefix("urn:uuid:")
        return next(e.resource for e in bundle_.entry if e.resource.id == resource_id)

    procedures = {
        p.code.coding[0].code: p for p in (e.resource for e in round_tripped_bundle.entry) if p.get_resource_type() == "Procedure"
    }
    appendectomy = procedures["80146002"]
    colonoscopy = procedures["73761001"]

    assert len(appendectomy.reasonCode) == 1
    assert appendectomy.reasonCode[0].coding[0].code == "85189001"
    assert appendectomy.reasonCode[0].coding[0].display == "Acute appendicitis"

    assert len(appendectomy.note) == 1
    note = appendectomy.note[0]
    assert note.text == "Patient tolerated the procedure well, no complications."
    assert note.time.isoformat() == "2026-06-15T12:30:00-05:00"
    comment_author = _resolve(round_tripped_bundle, note.authorReference.reference)
    assert comment_author.name[0].family == "Commenter"
    assert comment_author.name[0].given == ["Jamie"]

    recorder_practitioner = _resolve(round_tripped_bundle, appendectomy.recorder.reference)
    assert recorder_practitioner.name[0].family == "Recorder"
    assert recorder_practitioner.name[0].given == ["Alice"]
    assert recorder_practitioner.id != comment_author.id

    assert colonoscopy.reasonCode is None
    assert colonoscopy.note is None
    assert colonoscopy.recorder is None


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


def test_list_supported_targets_includes_discharge_summary():
    assert ("CDA", "DISCHARGESUMMARY", "") in list_supported_targets()


def test_discharge_summary_round_trip_produces_a_convertible_document_again():
    forward_xml = (FIXTURES / "discharge_summary_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_resource_types = {e.resource.get_resource_type() for e in bundle.entry}
    assert "Encounter" in original_resource_types
    assert "Condition" in original_resource_types
    assert "MedicationRequest" in original_resource_types
    # The fixture's own Hospital Course/Plan of Treatment sections convert
    # to DocumentReference+Binary (see app/cda/narrative_sections.py), and
    # app/transform/cda_ccd.py::_build_narrative_section now regenerates
    # both on the reverse trip too - see this file's own
    # test_discharge_summary_round_trip_regenerates_narrative_sections for
    # the dedicated content-fidelity proof. Plan of Treatment's own
    # structured Planned Observation entry also produces a real CarePlan
    # (see app/cda/plan_of_treatment.py) alongside the narrative pair.
    assert {"DocumentReference", "Binary", "CarePlan"} <= original_resource_types
    original_document_reference_count = sum(
        1 for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    )
    assert original_document_reference_count == 2  # Hospital Course + Plan of Treatment

    document_text = build_message_from_bundle(bundle, "CDA", "DISCHARGESUMMARY", "")
    assert document_text.startswith('<?xml version="1.0"')
    assert "18842-5" in document_text

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    resource_types = {e.resource.get_resource_type() for e in round_tripped_bundle.entry}
    assert resource_types == {
        "Patient",
        "Encounter",
        "Condition",
        "MedicationRequest",
        "DocumentReference",
        "Binary",
        "CarePlan",
    }
    document_reference_count = sum(
        1 for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    )
    assert document_reference_count == 2

    original_encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
    encounter = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Encounter")
    assert encounter.class_fhir.code == original_encounter.class_fhir.code
    assert encounter.period.start == original_encounter.period.start
    assert encounter.period.end == original_encounter.period.end

    original_conditions = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Condition"),
        key=lambda c: c.code.coding[0].code,
    )
    conditions = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Condition"),
        key=lambda c: c.code.coding[0].code,
    )
    assert [c.code.coding[0].code for c in conditions] == [c.code.coding[0].code for c in original_conditions]


def test_discharge_summary_missing_patient_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "CDA", "DISCHARGESUMMARY", "")


def test_discharge_summary_round_trip_splits_discharge_diagnosis_from_plain_problems():
    # The real fixture carries one Condition with category="encounter-
    # diagnosis" (from the Hospital Discharge Diagnosis section) and one
    # plain Condition (from a bare Problem List section) - both must
    # round-trip into their own correct section, not get folded together.
    forward_xml = (FIXTURES / "discharge_summary_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_conditions = {c.code.coding[0].code: c for c in (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Condition")}
    assert original_conditions["385093006"].category[0].coding[0].code == "encounter-diagnosis"
    assert not original_conditions["38341003"].category

    document_text = build_message_from_bundle(bundle, "CDA", "DISCHARGESUMMARY", "")
    # The discharge-diagnosis Condition's own code must appear inside the
    # regenerated Hospital Discharge Diagnosis Act, not the plain Concern
    # Act - checked by finding which templateId segment its own code sits
    # closer to.
    hospital_discharge_idx = document_text.index("2.16.840.1.113883.10.20.22.2.24")
    problems_idx = document_text.index("2.16.840.1.113883.10.20.22.2.5.1")
    pneumonia_idx = document_text.index("385093006")
    hypertension_idx = document_text.index("38341003")
    assert hospital_discharge_idx < pneumonia_idx < problems_idx
    assert problems_idx < hypertension_idx

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    conditions = {c.code.coding[0].code: c for c in (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Condition")}
    assert conditions["385093006"].category[0].coding[0].code == "encounter-diagnosis"
    assert not conditions["38341003"].category

    # Discharge Medications now splits out too, via its own real
    # MedicationRequest.category == "discharge" marker (a standard code
    # from FHIR R4's own medicationrequest-category CodeSystem) - exactly
    # the way the Hospital Discharge Diagnosis Condition above does. This
    # previously asserted the opposite ("no FHIR-side marker at all, so it
    # always folds into the plain Medications section"), which described
    # an implementation choice as if it were a standards constraint.
    original_med = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "MedicationRequest")
    med = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "MedicationRequest")
    assert med.medicationCodeableConcept.coding[0].code == original_med.medicationCodeableConcept.coding[0].code
    assert original_med.category[0].coding[0].code == "discharge"
    assert med.category[0].coding[0].code == "discharge"
    assert "10183-2" in document_text  # Discharge Medications section LOINC code, now regenerated
    # This fixture's only MedicationRequest came from the Discharge
    # Medications section, so no plain Medications section is emitted.
    assert "10160-0" not in document_text


def test_discharge_summary_round_trip_regenerates_hospital_course_narrative():
    # Hospital Course's own real shape (plain mixed text, no <paragraph>
    # wrapper at all) - the LOINC code, title, and narrative text itself
    # all round-trip exactly.
    forward_xml = (FIXTURES / "discharge_summary_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_text = build_message_from_bundle(bundle, "CDA", "DISCHARGESUMMARY", "")
    assert "8648-8" in document_text
    assert "Hospital Course" in document_text
    assert "Patient admitted with fever and productive cough" in document_text

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    document_references = [
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    ]
    binaries = {e.resource.id: e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Binary"}
    hospital_course_doc = next(d for d in document_references if d.type.coding[0].code == "8648-8")
    assert hospital_course_doc.description == "Hospital Course"
    binary_id = hospital_course_doc.content[0].attachment.url[len("urn:uuid:") :]
    assert binaries[binary_id].data.decode("utf-8") == (
        "Patient admitted with fever and productive cough, diagnosed with community-acquired "
        "pneumonia. Treated with IV antibiotics and responded well. Afebrile for 48 hours prior "
        "to discharge, tolerating oral intake, ambulating without difficulty."
    )


def test_discharge_summary_plan_of_treatment_table_flattens_to_paragraphs_and_stays_stable():
    # Plan of Treatment's own real shape is a <table> - the row/column
    # structure isn't recoverable on the reverse trip (see
    # _build_narrative_section's own docstring), but the flattened,
    # pipe-joined text content is preserved exactly and stays stable across
    # a second round trip.
    forward_xml = (FIXTURES / "discharge_summary_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_references = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"]
    plan_doc = next(d for d in document_references if d.type.coding[0].code == "18776-5")
    binaries = {e.resource.id: e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary"}
    original_text = binaries[plan_doc.content[0].attachment.url[len("urn:uuid:") :]].data.decode("utf-8")
    assert original_text == (
        "Planned Activity | Planned Date\n"
        "Follow up with primary care physician | Aug 12, 2026\n"
        "Complete oral antibiotic course | Aug 15, 2026"
    )

    document_text = build_message_from_bundle(bundle, "CDA", "DISCHARGESUMMARY", "")
    assert "<table" not in document_text
    assert document_text.count("<paragraph>") >= 3

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    round_tripped_docs = [
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    ]
    round_tripped_binaries = {
        e.resource.id: e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Binary"
    }
    round_tripped_plan_doc = next(d for d in round_tripped_docs if d.type.coding[0].code == "18776-5")
    round_tripped_binary_id = round_tripped_plan_doc.content[0].attachment.url[len("urn:uuid:") :]
    assert round_tripped_binaries[round_tripped_binary_id].data.decode("utf-8") == original_text

    # A second reverse pass reproduces the identical flattened text again -
    # not a further-mutated one.
    second_document_text = build_message_from_bundle(round_tripped_bundle, "CDA", "DISCHARGESUMMARY", "")
    second_round_tripped_bundle = convert_cda_to_bundle(second_document_text)
    second_docs = [
        e.resource for e in second_round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    ]
    second_binaries = {
        e.resource.id: e.resource for e in second_round_tripped_bundle.entry if e.resource.get_resource_type() == "Binary"
    }
    second_plan_doc = next(d for d in second_docs if d.type.coding[0].code == "18776-5")
    second_binary_id = second_plan_doc.content[0].attachment.url[len("urn:uuid:") :]
    assert second_binaries[second_binary_id].data.decode("utf-8") == original_text


def test_discharge_summary_plan_of_treatment_care_plan_regenerates_as_structured_entry():
    # The fixture's own structured Planned Observation entry (see
    # app/cda/plan_of_treatment.py) produces a real CarePlan alongside the
    # narrative pair - both must survive a full round trip.
    forward_xml = (FIXTURES / "discharge_summary_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_care_plan = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CarePlan")

    document_text = build_message_from_bundle(bundle, "CDA", "DISCHARGESUMMARY", "")
    # Regenerated as a Planned Observation entry specifically (the
    # confirmed-primary shape - see _build_care_plan_activity_entry's own
    # docstring for why this builder can't recover whether the original
    # was a Planned Observation or Planned Procedure).
    assert "2.16.840.1.113883.10.20.22.4.44" in document_text

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    care_plan = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "CarePlan")
    assert care_plan.status == "active"
    assert care_plan.intent == "plan"
    assert len(care_plan.activity) == len(original_care_plan.activity)
    assert care_plan.activity[0].detail.code.coding[0].code == original_care_plan.activity[0].detail.code.coding[0].code
    assert care_plan.activity[0].detail.status == original_care_plan.activity[0].detail.status
    assert care_plan.activity[0].detail.scheduledString == original_care_plan.activity[0].detail.scheduledString


def test_narrative_section_regeneration_skips_document_reference_with_unrecognized_loinc_code():
    # A DocumentReference this app's own forward direction never produces
    # for a C-CDA source (e.g. an MDM-sourced one, with a LOINC code
    # outside the twelve narrative-section codes) must not be guessed into
    # one of them.
    patient = Patient(id="p1", name=[{"family": "Solo"}])
    binary = Binary(id="b1", contentType="text/plain", data=base64.b64encode(b"Some other document").decode("ascii"))
    doc = DocumentReference(
        id="d1",
        status="current",
        type={"coding": [{"system": "http://loinc.org", "code": "11488-4", "display": "Consult note"}]},
        content=[{"attachment": {"contentType": "text/plain", "url": "urn:uuid:b1"}}],
        subject={"reference": "urn:uuid:p1"},
    )
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [
        BundleEntry(fullUrl="urn:uuid:p1", resource=patient),
        BundleEntry(fullUrl="urn:uuid:d1", resource=doc),
        BundleEntry(fullUrl="urn:uuid:b1", resource=binary),
    ]
    document_text = build_message_from_bundle(bundle, "CDA", "DISCHARGESUMMARY", "")
    assert "11488-4" not in document_text
    assert "Some other document" not in document_text


def test_ccd_round_trip_never_splits_out_hospital_discharge_diagnosis_section():
    # CCD's own reverse builder must not pass include_discharge_specific_sections
    # - any Condition it encounters (even one carrying a stray
    # category="encounter-diagnosis", which this app's own CCD generator
    # never produces) stays folded into the plain Problems section.
    forward_xml = (FIXTURES / "ccd_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    assert "2.16.840.1.113883.10.20.22.2.24" not in document_text


def test_list_supported_targets_includes_history_and_physical():
    assert ("CDA", "HISTORYANDPHYSICAL", "") in list_supported_targets()


def test_history_and_physical_round_trip_produces_a_convertible_document_again():
    forward_xml = (FIXTURES / "history_and_physical_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_resource_types = {e.resource.get_resource_type() for e in bundle.entry}
    assert "Procedure" in original_resource_types
    # The fixture's own nine H&P-specific narrative sections (Reason for
    # Visit, HPI, Review of Systems, Physical Exam, General Status,
    # Assessment, Social History, Family History, Plan of Treatment) each
    # convert to a DocumentReference+Binary pair (see app/cda/narrative_
    # sections.py), and app/transform/cda_ccd.py::_build_narrative_section
    # now regenerates all nine on the reverse trip too. Social History's/
    # Family History's/Plan of Treatment's own structured entries also
    # produce a real Observation/FamilyMemberHistory/CarePlan alongside
    # their narrative pair (see app/cda/social_history.py/family_history.py/
    # plan_of_treatment.py).
    assert {"DocumentReference", "Binary", "Observation", "FamilyMemberHistory", "CarePlan"} <= original_resource_types
    original_document_reference_count = sum(
        1 for e in bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    )
    assert original_document_reference_count == 9

    document_text = build_message_from_bundle(bundle, "CDA", "HISTORYANDPHYSICAL", "")
    assert document_text.startswith('<?xml version="1.0"')
    assert "34117-2" in document_text

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    resource_types = {e.resource.get_resource_type() for e in round_tripped_bundle.entry}
    assert resource_types == {
        "Patient",
        "Procedure",
        "DocumentReference",
        "Binary",
        "Observation",
        "FamilyMemberHistory",
        "CarePlan",
    }
    document_reference_count = sum(
        1 for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    )
    assert document_reference_count == 9

    original_procedure = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Procedure")
    procedure = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Procedure")
    assert procedure.code.coding[0].code == original_procedure.code.coding[0].code
    assert procedure.status == original_procedure.status


def test_history_and_physical_missing_patient_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "CDA", "HISTORYANDPHYSICAL", "")


def test_history_and_physical_round_trip_regenerates_all_nine_narrative_sections_with_correct_loinc_codes():
    forward_xml = (FIXTURES / "history_and_physical_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    document_text = build_message_from_bundle(bundle, "CDA", "HISTORYANDPHYSICAL", "")

    round_tripped_bundle = convert_cda_to_bundle(document_text)
    document_references = [
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "DocumentReference"
    ]
    round_tripped_codes = {d.type.coding[0].code for d in document_references}
    assert round_tripped_codes == {
        "29299-5",  # Reason for Visit
        "10164-2",  # History of Present Illness
        "10187-3",  # Review of Systems
        "29545-1",  # Physical Exam
        "10210-3",  # General Status
        "51848-0",  # Assessment
        "29762-2",  # Social History
        "10157-6",  # Family History
        "18776-5",  # Plan of Treatment
    }

    # Physical Exam's own real shape is an ordered <list>/<item> - flattens
    # to one line per item and survives the round trip.
    binaries = {e.resource.id: e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Binary"}
    physical_exam_doc = next(d for d in document_references if d.type.coding[0].code == "29545-1")
    physical_exam_text = binaries[physical_exam_doc.content[0].attachment.url[len("urn:uuid:") :]].data.decode("utf-8")
    assert physical_exam_text == (
        "HEENT: Normal to examination.\n"
        "Heart: Regular rate and rhythm, no murmur.\n"
        "Right knee: Decreased range of motion, crepitus on flexion, no effusion."
    )

    # Social History's own real shape is a <table> - flattens to
    # pipe-joined rows and survives the round trip.
    social_history_doc = next(d for d in document_references if d.type.coding[0].code == "29762-2")
    social_history_text = binaries[social_history_doc.content[0].attachment.url[len("urn:uuid:") :]].data.decode(
        "utf-8"
    )
    assert social_history_text == (
        "Social History Element | Description\nTobacco smoking status | Never smoker\nAlcohol use | Occasional"
    )


def test_history_and_physical_round_trip_regenerates_structured_social_family_history_and_care_plan():
    # The fixture's own structured entries for Social History, Family
    # History, and Plan of Treatment (see app/cda/social_history.py/
    # family_history.py/plan_of_treatment.py) each produce a real
    # Observation/FamilyMemberHistory/CarePlan alongside the narrative pair
    # - all three must survive a full round trip, field values included.
    forward_xml = (FIXTURES / "history_and_physical_basic.xml").read_text()
    bundle = convert_cda_to_bundle(forward_xml)
    original_observation = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Observation")
    original_history = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "FamilyMemberHistory")
    original_care_plan = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "CarePlan")

    document_text = build_message_from_bundle(bundle, "CDA", "HISTORYANDPHYSICAL", "")
    round_tripped_bundle = convert_cda_to_bundle(document_text)

    observation = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Observation"
    )
    assert observation.code.coding[0].code == original_observation.code.coding[0].code
    assert observation.category[0].coding[0].code == "social-history"
    assert observation.valueCodeableConcept.coding[0].code == original_observation.valueCodeableConcept.coding[0].code

    history = next(
        e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "FamilyMemberHistory"
    )
    assert history.relationship.coding[0].code == original_history.relationship.coding[0].code
    assert history.sex.coding[0].code == original_history.sex.coding[0].code
    assert str(history.deceasedDate) == str(original_history.deceasedDate)
    assert history.condition[0].code.coding[0].code == original_history.condition[0].code.coding[0].code
    assert float(history.condition[0].onsetAge.value) == float(original_history.condition[0].onsetAge.value)
    assert history.condition[0].contributedToDeath == original_history.condition[0].contributedToDeath

    care_plan = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "CarePlan")
    assert care_plan.activity[0].detail.code.coding[0].code == original_care_plan.activity[0].detail.code.coding[0].code
    assert care_plan.activity[0].detail.status == original_care_plan.activity[0].detail.status


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


def test_list_supported_targets_includes_276_and_277():
    targets = list_supported_targets()
    assert ("EDI", "276", "") in targets
    assert ("EDI", "277", "") in targets


def test_edi_276_round_trip_preserves_tasks_for_subscriber_and_dependent():
    forward_x12 = (FIXTURES / "edi_276_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_tasks = [e.resource for e in bundle.entry if e.resource.get_resource_type() == "Task"]
    assert len(original_tasks) == 2

    message_text = build_message_from_bundle(bundle, "EDI", "276", "")
    assert "ST*276*" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    tasks = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Task"]
    assert len(tasks) == 2
    assert {t.status for t in tasks} == {"requested"}

    # Both patient loops (subscriber and dependent) must still resolve -
    # the one assertion specific to this builder's own new cross-cutting
    # logic (payer/provider via Task.owner/.requester, receiver via
    # exclusion, subscriber/dependent via Bundle order).
    patients = [e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient"]
    assert len(patients) == 2
    task_patient_ids = {t.for_fhir.reference.removeprefix("urn:uuid:") for t in tasks}
    assert task_patient_ids == {p.id for p in patients}


def test_edi_277_round_trip_preserves_business_status():
    forward_x12 = (FIXTURES / "edi_277_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_tasks = sorted(
        (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Task"),
        key=lambda t: t.identifier[0].value,
    )
    assert [t.status for t in original_tasks] == ["completed", "in-progress"]

    message_text = build_message_from_bundle(bundle, "EDI", "277", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    tasks = sorted(
        (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Task"),
        key=lambda t: t.identifier[0].value,
    )
    assert [t.status for t in tasks] == [t.status for t in original_tasks]
    for original, task in zip(original_tasks, tasks):
        assert task.businessStatus.coding[0].code == original.businessStatus.coding[0].code
        assert task.businessStatus.coding[1].code == original.businessStatus.coding[1].code


def test_edi_277_error_status_round_trips_failed_status():
    forward_x12 = (FIXTURES / "edi_277_error_status.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_task = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Task")
    assert original_task.status == "failed"

    message_text = build_message_from_bundle(bundle, "EDI", "277", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    task = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Task")
    assert task.status == "failed"


def test_edi_276_missing_task_raises_mapping_error():
    payer = Organization(id="payer1", name="Payer")
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:payer1", resource=payer)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "EDI", "276", "")


def test_edi_277_missing_task_raises_mapping_error():
    payer = Organization(id="payer1", name="Payer")
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [BundleEntry(fullUrl="urn:uuid:payer1", resource=payer)]
    with pytest.raises(MappingError):
        build_message_from_bundle(bundle, "EDI", "277", "")


def test_list_supported_targets_includes_278_request_and_response():
    targets = list_supported_targets()
    assert ("EDI", "278REQUEST", "") in targets
    assert ("EDI", "278RESPONSE", "") in targets


def test_edi_278_request_round_trip_preserves_claim_and_diagnoses():
    forward_x12 = (FIXTURES / "edi_278_request_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(original_claim.diagnosis) == 2
    assert not [e for e in bundle.entry if e.resource.get_resource_type() == "ClaimResponse"]

    message_text = build_message_from_bundle(bundle, "EDI", "278REQUEST", "")
    assert "ST*278*" in message_text
    assert "BHT*0007*13*" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    claim = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Claim")
    assert not [e for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "ClaimResponse"]
    original_codes = sorted(d.diagnosisCodeableConcept.coding[0].code for d in original_claim.diagnosis)
    codes = sorted(d.diagnosisCodeableConcept.coding[0].code for d in claim.diagnosis)
    assert codes == original_codes
    for original_dx, dx in zip(
        sorted(original_claim.diagnosis, key=lambda d: d.diagnosisCodeableConcept.coding[0].code),
        sorted(claim.diagnosis, key=lambda d: d.diagnosisCodeableConcept.coding[0].code),
    ):
        assert dx.diagnosisCodeableConcept.coding[0].system == original_dx.diagnosisCodeableConcept.coding[0].system


def test_edi_278_request_with_dependent_round_trip_preserves_both_patients():
    # The one assertion specific to this builder's own new cross-cutting
    # logic: unlike 270's own dependent (commonly carrying no member-
    # specific id), 278's real dependent loop does carry one, and it must
    # survive a full reverse-then-forward round trip, not get silently
    # dropped by copying 270's own include_id=False choice unexamined.
    forward_x12 = (FIXTURES / "edi_278_request_with_dependent.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_patient_ids = sorted(
        p.identifier[0].value for p in (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    assert len(original_patient_ids) == 2

    message_text = build_message_from_bundle(bundle, "EDI", "278REQUEST", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    patient_ids = sorted(
        p.identifier[0].value
        for p in (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    assert patient_ids == original_patient_ids


def test_edi_278_response_round_trip_preserves_outcome_and_auth_ref():
    forward_x12 = (FIXTURES / "edi_278_response_certified.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "ClaimResponse")
    assert original_response.outcome == "complete"
    assert original_response.preAuthRef == "AUTH0001"

    message_text = build_message_from_bundle(bundle, "EDI", "278RESPONSE", "")
    assert "BHT*0007*11*" in message_text
    assert "HCR*A1*AUTH0001" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    response = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "ClaimResponse")
    assert response.outcome == original_response.outcome
    assert response.preAuthRef == original_response.preAuthRef


def test_edi_278_response_denied_round_trips_partial_certification_data():
    forward_x12 = (FIXTURES / "edi_278_response_denied.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_response = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "ClaimResponse")

    message_text = build_message_from_bundle(bundle, "EDI", "278RESPONSE", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    response = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "ClaimResponse")
    assert response.outcome == original_response.outcome == "complete"


def test_edi_278_request_missing_claim_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "278REQUEST", "")


def test_edi_278_response_missing_claim_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "278RESPONSE", "")


def test_list_supported_targets_includes_835():
    assert ("EDI", "835", "") in list_supported_targets()


_ADJUSTMENT_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/payment-type"


def _claim_payments(payment_reconciliation):
    """Claim-payment details only - .detail[] also carries one entry per
    CAS adjustment triplet."""
    return [
        d for d in payment_reconciliation.detail
        if not any(c.system == _ADJUSTMENT_TYPE_SYSTEM for c in (d.type.coding or []))
    ]


def _claim_adjustments(payment_reconciliation):
    return [
        d for d in payment_reconciliation.detail
        if any(c.system == _ADJUSTMENT_TYPE_SYSTEM for c in (d.type.coding or []))
    ]


def test_edi_835_round_trip_preserves_payment_and_details():
    forward_x12 = (FIXTURES / "edi_835_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_pr = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation")
    assert len(_claim_payments(original_pr)) == 1
    assert len(_claim_adjustments(original_pr)) == 1

    message_text = build_message_from_bundle(bundle, "EDI", "835", "")
    assert "ST*835*" in message_text
    assert "BPR*" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    pr = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation")
    assert pr.paymentAmount.value == original_pr.paymentAmount.value
    assert pr.paymentDate == original_pr.paymentDate
    payments = _claim_payments(pr)
    assert len(payments) == 1
    assert payments[0].identifier.value == _claim_payments(original_pr)[0].identifier.value
    assert payments[0].amount.value == _claim_payments(original_pr)[0].amount.value

    # The CAS adjustment survives with its group code, reason code and
    # amount. Which claim it belonged to does not - PaymentReconciliation
    # has no service-line concept and .detail[] is flat, so the forward
    # direction never recorded it. Stable from here on, though: re-parsing
    # yields the identical flat list.
    adjustments = _claim_adjustments(pr)
    assert len(adjustments) == 1
    assert {c.code for c in adjustments[0].type.coding} == {"adjustment", "CO", "45"}
    assert adjustments[0].amount.value == _claim_adjustments(original_pr)[0].amount.value

    original_payer = next(
        e.resource
        for e in bundle.entry
        if e.resource.get_resource_type() == "Organization" and e.resource.id == original_pr.paymentIssuer.reference.removeprefix("urn:uuid:")
    )
    payer = next(
        e.resource
        for e in round_tripped_bundle.entry
        if e.resource.get_resource_type() == "Organization" and e.resource.id == pr.paymentIssuer.reference.removeprefix("urn:uuid:")
    )
    assert payer.name == original_payer.name
    assert payer.identifier[0].value == original_payer.identifier[0].value
    # Regression check for a real bug caught while building this reverse
    # slice: naively reusing edi_common.py's own reverse_nm1_qualifier
    # (whose fallback-marker prefix is NM1-scoped) would have silently
    # dropped this payer's own non-canonical "XV" qualifier - confirm the
    # identifier system still round-trips to the same disclosed fallback.
    assert payer.identifier[0].system == original_payer.identifier[0].system


def test_edi_835_multi_claim_round_trip_preserves_all_claims():
    forward_x12 = (FIXTURES / "edi_835_multi_claim.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_pr = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation")
    assert len(_claim_payments(original_pr)) == 2

    message_text = build_message_from_bundle(bundle, "EDI", "835", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    pr = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "PaymentReconciliation")
    assert len(_claim_payments(pr)) == 2
    original_ids = sorted(d.identifier.value for d in _claim_payments(original_pr))
    ids = sorted(d.identifier.value for d in _claim_payments(pr))
    assert ids == original_ids

    # Both claims' adjustments survive, even though the reverse builder
    # emits them all after the first claim - the forward direction records
    # no claim attribution to restore, so the flat list is what round-trips
    # and it round-trips whole.
    def reasons(payment_reconciliation):
        return sorted(
            c.code
            for d in _claim_adjustments(payment_reconciliation)
            for c in d.type.coding
            if c.system.endswith("claim-adjustment-reason-code")
        )

    assert reasons(pr) == reasons(original_pr) == ["45", "96"]


def test_edi_835_missing_payment_reconciliation_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "835", "")


def test_list_supported_targets_includes_837p():
    assert ("EDI", "837P", "") in list_supported_targets()


def test_edi_837p_round_trip_preserves_claim_items_and_diagnoses():
    forward_x12 = (FIXTURES / "edi_837p_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(original_claim.item) == 2
    assert len(original_claim.diagnosis) == 2

    message_text = build_message_from_bundle(bundle, "EDI", "837P", "")
    assert "ST*837*" in message_text
    assert "005010X222A2" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    claim = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(claim.item) == 2
    assert len(claim.diagnosis) == 2
    assert claim.total.value == original_claim.total.value

    for original_item, item in zip(original_claim.item, claim.item):
        assert item.productOrService.coding[0].code == original_item.productOrService.coding[0].code
        assert item.unitPrice.value == original_item.unitPrice.value
        assert item.diagnosisSequence == original_item.diagnosisSequence
        assert item.careTeamSequence == original_item.careTeamSequence
        assert item.servicedDate == original_item.servicedDate

    original_dx_codes = sorted(d.diagnosisCodeableConcept.coding[0].code for d in original_claim.diagnosis)
    dx_codes = sorted(d.diagnosisCodeableConcept.coding[0].code for d in claim.diagnosis)
    assert dx_codes == original_dx_codes

    # careTeam (rendering provider) must survive too, not just the item's
    # own careTeamSequence pointer to it.
    assert len(claim.careTeam) == len(original_claim.careTeam)


def test_edi_837p_with_dependent_round_trip_preserves_both_patients():
    # This fixture's dependent carries no member-specific id of its own
    # (unlike 278's own dependent, see prior_auth.py's own regression
    # test) - names, not identifiers, are the reliable comparison here.
    forward_x12 = (FIXTURES / "edi_837p_with_dependent.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_patient_names = sorted(
        f"{p.name[0].family} {p.name[0].given[0]}"
        for p in (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    assert len(original_patient_names) == 2

    message_text = build_message_from_bundle(bundle, "EDI", "837P", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    patient_names = sorted(
        f"{p.name[0].family} {p.name[0].given[0]}"
        for p in (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    assert patient_names == original_patient_names


def test_edi_837p_missing_claim_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "837P", "")


def test_list_supported_targets_includes_837i():
    assert ("EDI", "837I", "") in list_supported_targets()


def test_edi_837i_round_trip_preserves_claim_items_and_discharge_status():
    forward_x12 = (FIXTURES / "edi_837i_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(original_claim.item) == 2
    assert len(original_claim.diagnosis) == 3
    assert original_claim.supportingInfo[0].code.coding[0].code == "01"

    message_text = build_message_from_bundle(bundle, "EDI", "837I", "")
    assert "ST*837*" in message_text
    assert "005010X223A2" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    claim = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(claim.item) == 2
    assert len(claim.diagnosis) == 3
    assert claim.total.value == original_claim.total.value
    assert claim.supportingInfo[0].code.coding[0].code == "01"

    for original_item, item in zip(original_claim.item, claim.item):
        assert item.revenue.coding[0].code == original_item.revenue.coding[0].code
        if original_item.productOrService.coding:
            assert item.productOrService.coding[0].code == original_item.productOrService.coding[0].code
        assert item.unitPrice.value == original_item.unitPrice.value
        assert item.careTeamSequence == original_item.careTeamSequence
        assert item.servicedDate == original_item.servicedDate
        # 837I's own SV2 has no diagnosis-pointer composite at all, unlike
        # 837P's SV1-07 - confirm this reverse builder never fabricates one.
        assert item.diagnosisSequence is None

    original_dx_codes = sorted(d.diagnosisCodeableConcept.coding[0].code for d in original_claim.diagnosis)
    dx_codes = sorted(d.diagnosisCodeableConcept.coding[0].code for d in claim.diagnosis)
    assert dx_codes == original_dx_codes


def test_edi_837i_with_dependent_round_trip_preserves_revenue_code_only_item():
    # This fixture's own item has no procedure composite at all (a bare
    # room-and-board revenue-code line) - the "Revenue code X" text-only
    # fallback must survive the round trip, not just the coded case.
    forward_x12 = (FIXTURES / "edi_837i_with_dependent.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    assert original_claim.item[0].productOrService.coding is None
    assert original_claim.item[0].productOrService.text == "Revenue code 0120"

    message_text = build_message_from_bundle(bundle, "EDI", "837I", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    claim = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Claim")
    assert claim.item[0].productOrService.coding is None
    assert claim.item[0].productOrService.text == "Revenue code 0120"
    assert claim.item[0].revenue.coding[0].code == original_claim.item[0].revenue.coding[0].code

    original_patient_names = sorted(
        f"{p.name[0].family} {p.name[0].given[0]}"
        for p in (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    patient_names = sorted(
        f"{p.name[0].family} {p.name[0].given[0]}"
        for p in (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    assert patient_names == original_patient_names


def test_edi_837i_missing_claim_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "837I", "")


def test_list_supported_targets_includes_837d():
    assert ("EDI", "837D", "") in list_supported_targets()


def test_edi_837d_round_trip_preserves_tooth_information_and_no_diagnosis():
    # This fixture deliberately carries no HI segment at all (dental claims
    # commonly have none - see claim_837d.py's own module docstring) and
    # relies entirely on the claim-level DTP*472 default for both lines -
    # proving both the no-diagnosis path and the claim-level-DTP-becomes-
    # per-line-DTP simplification in one fixture.
    forward_x12 = (FIXTURES / "edi_837d_basic.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(original_claim.item) == 2
    assert not original_claim.diagnosis
    assert original_claim.item[0].bodySite.coding[0].code == "12"
    assert [c.coding[0].code for c in original_claim.item[0].subSite] == ["M", "O"]

    message_text = build_message_from_bundle(bundle, "EDI", "837D", "")
    assert "ST*837*" in message_text
    assert "005010X224A2" in message_text

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    claim = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(claim.item) == 2
    assert not claim.diagnosis
    assert claim.total.value == original_claim.total.value

    for original_item, item in zip(original_claim.item, claim.item):
        assert item.productOrService.coding[0].code == original_item.productOrService.coding[0].code
        assert item.productOrService.coding[0].system == original_item.productOrService.coding[0].system
        assert item.unitPrice.value == original_item.unitPrice.value
        assert item.careTeamSequence == original_item.careTeamSequence
        # Both lines resolve servicedDate from the one claim-level DTP*472
        # default in the original fixture, but this reverse builder always
        # regenerates a per-line DTP - the date itself must still match.
        assert item.servicedDate == original_item.servicedDate

    # Tooth body site/sub-site only survive on the first item, matching
    # the original fixture's own TOO segment placement.
    assert claim.item[0].bodySite.coding[0].code == "12"
    assert claim.item[0].bodySite.coding[0].system == original_claim.item[0].bodySite.coding[0].system
    assert [c.coding[0].code for c in claim.item[0].subSite] == ["M", "O"]
    assert claim.item[1].bodySite is None
    assert not claim.item[1].subSite

    # Rendering provider (careTeam) must survive too, not just the item's
    # own careTeamSequence pointer to it.
    assert len(claim.careTeam) == len(original_claim.careTeam)


def test_edi_837d_no_dependent_round_trip_preserves_diagnosis_pointer():
    # This fixture is the mirror case of edi_837d_basic.x12: it DOES carry
    # an HI segment and a resolvable SV3-11 diagnosis pointer, and its own
    # DTP*472 is per-line rather than claim-level.
    forward_x12 = (FIXTURES / "edi_837d_no_dependent.x12").read_text()
    bundle = convert_edi_to_bundle(forward_x12)
    original_claim = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(original_claim.diagnosis) == 1
    assert original_claim.item[0].diagnosisSequence == [1]

    message_text = build_message_from_bundle(bundle, "EDI", "837D", "")
    round_tripped_bundle = convert_edi_to_bundle(message_text)
    claim = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Claim")
    assert len(claim.diagnosis) == 1
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].code == (
        original_claim.diagnosis[0].diagnosisCodeableConcept.coding[0].code
    )
    assert claim.item[0].diagnosisSequence == [1]
    assert claim.item[0].servicedDate == original_claim.item[0].servicedDate

    original_patient_names = sorted(
        f"{p.name[0].family} {p.name[0].given[0]}"
        for p in (e.resource for e in bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    patient_names = sorted(
        f"{p.name[0].family} {p.name[0].given[0]}"
        for p in (e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    )
    assert patient_names == original_patient_names
    assert len(patient_names) == 1


def test_edi_837d_missing_claim_raises_mapping_error():
    empty_bundle = Bundle(id="test", type="collection")
    with pytest.raises(MappingError):
        build_message_from_bundle(empty_bundle, "EDI", "837D", "")


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


# Regression coverage for three real bugs an adversarial code-review pass
# caught in app/transform/ (this package had never had one, unlike the
# forward-direction code, which has repeatedly caught bugs this way) - none
# of the 200-300 seed fuzz sweeps every slice above was verified against
# would ever catch these, since the synthetic generators never happen to
# produce a name/display containing an XML special character or an X12
# reserved delimiter, and never happen to produce a JSON-round-tripped
# Decimal with a lost trailing zero.


def test_ccd_round_trip_escapes_xml_special_characters_in_free_text():
    # A Coding.display containing a literal "&" is extremely plausible for
    # real clinical text ("Diseases of the ear & mastoid process") - before
    # this fix, cda_ccd.py built XML via raw f-strings with no escaping at
    # all, so this produced unparseable XML (CdaParseError: not well-formed)
    # on the very next forward pass, silently breaking /api/transform for
    # any Bundle containing such a display. A patient name containing "&"/
    # "<"/">" hit the identical gap in <family>/<given> text content.
    patient = Patient(id="p1", name=[{"family": "O'Brien & Sons", "given": ["Pat<ricia>"]}])
    condition = Condition(
        id="c1",
        subject={"reference": "urn:uuid:p1"},
        code={"coding": [{"system": "http://snomed.info/sct", "code": "J209", "display": "Diseases of the ear & mastoid process"}]},
    )
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [
        BundleEntry(fullUrl="urn:uuid:p1", resource=patient),
        BundleEntry(fullUrl="urn:uuid:c1", resource=condition),
    ]

    message_text = build_message_from_bundle(bundle, "CDA", "CCD", "")
    assert "&amp;" in message_text
    assert "&lt;" in message_text and "&gt;" in message_text

    # The real assertion: this must actually re-parse, not just contain the
    # right escape sequences - a naive fix could still be self-inconsistent.
    round_tripped_bundle = convert_cda_to_bundle(message_text)
    rt_condition = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Condition")
    assert rt_condition.code.coding[0].display == "Diseases of the ear & mastoid process"
    rt_patient = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Patient")
    assert rt_patient.name[0].family == "O'Brien & Sons"
    assert rt_patient.name[0].given == ["Pat<ricia>"]


def test_edi_837p_sanitizes_x12_reserved_characters_in_organization_name():
    # An Organization.name containing a literal "*" is plausible (compound
    # business names, abbreviations). Before this fix, edi_common.py's
    # build_org_nm1 wrote it raw into NM1, splitting NM103 into two
    # elements and silently shifting every later positional field (the id
    # qualifier/value) out of place with no error raised anywhere - a
    # regenerated segment that parses into a materially different Bundle
    # than the one that produced it.
    billing = Organization(id="org1", name="Smith*Jones Medical Group")
    payer = Organization(id="org2", name="Acme Insurance")
    patient = Patient(id="p1", name=[{"family": "Doe", "given": ["Jane"]}])
    coverage = Coverage(
        id="cov1",
        status="active",
        beneficiary={"reference": "urn:uuid:p1"},
        payor=[{"reference": "urn:uuid:org2"}],
    )
    claim = Claim(
        id="claim1",
        status="active",
        use="claim",
        type={"text": "professional"},
        patient={"reference": "urn:uuid:p1"},
        created="2026-01-01",
        provider={"reference": "urn:uuid:org1"},
        priority={"text": "normal"},
        insurance=[ClaimInsurance(sequence=1, focal=True, coverage={"reference": "urn:uuid:cov1"})],
    )
    claim.insurer = {"reference": "urn:uuid:org2"}
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [
        BundleEntry(fullUrl="urn:uuid:org1", resource=billing),
        BundleEntry(fullUrl="urn:uuid:org2", resource=payer),
        BundleEntry(fullUrl="urn:uuid:p1", resource=patient),
        BundleEntry(fullUrl="urn:uuid:cov1", resource=coverage),
        BundleEntry(fullUrl="urn:uuid:claim1", resource=claim),
    ]

    message_text = build_message_from_bundle(bundle, "EDI", "837P", "")
    nm1_billing_segment = next(s for s in message_text.split("~") if s.startswith("NM1*85"))
    # Exactly 5 elements (NM1/85/2/name/nothing-else, no id qualifier/value
    # on this fixture) - a stray "*" inside the name would produce 6.
    assert nm1_billing_segment.split("*") == ["NM1", "85", "2", "Smith Jones Medical Group"]

    round_tripped_bundle = convert_edi_to_bundle(message_text)
    rt_billing = next(e.resource for e in round_tripped_bundle.entry if e.resource.get_resource_type() == "Organization" and "Smith" in (e.resource.name or ""))
    assert rt_billing.name == "Smith Jones Medical Group"


def test_edi_837p_preserves_trailing_zero_in_money_via_fixed_precision():
    # fhir.resources' own JSON (de)serialization silently drops a trailing
    # zero: Decimal("100.10") serializes to the bare JSON number 100.1,
    # which re-parses as Decimal("100.1") - reachable through the
    # documented Convert -> "Use Bundle above" -> Transform UI flow (and
    # the equivalent /api/convert -> /api/transform API flow) via
    # app/routes/convert.py's own json.loads()->Bundle.model_validate()
    # path. str(Decimal) would then silently emit "100.1" where the
    # original CLM02 was "100.10" - :.2f is immune, since it always
    # re-quantizes to 2 places regardless of the Decimal's own current
    # precision.
    import json

    billing = Organization(id="org1", name="Billing Group")
    payer = Organization(id="org2", name="Acme Insurance")
    patient = Patient(id="p1", name=[{"family": "Doe", "given": ["Jane"]}])
    coverage = Coverage(
        id="cov1", status="active", beneficiary={"reference": "urn:uuid:p1"}, payor=[{"reference": "urn:uuid:org2"}]
    )
    claim = Claim(
        id="claim1",
        status="active",
        use="claim",
        type={"text": "professional"},
        patient={"reference": "urn:uuid:p1"},
        created="2026-01-01",
        provider={"reference": "urn:uuid:org1"},
        priority={"text": "normal"},
        total={"value": "100.10", "currency": "USD"},
        insurance=[ClaimInsurance(sequence=1, focal=True, coverage={"reference": "urn:uuid:cov1"})],
    )
    claim.insurer = {"reference": "urn:uuid:org2"}
    bundle = Bundle(id="test", type="collection")
    bundle.entry = [
        BundleEntry(fullUrl="urn:uuid:org1", resource=billing),
        BundleEntry(fullUrl="urn:uuid:org2", resource=payer),
        BundleEntry(fullUrl="urn:uuid:p1", resource=patient),
        BundleEntry(fullUrl="urn:uuid:cov1", resource=coverage),
        BundleEntry(fullUrl="urn:uuid:claim1", resource=claim),
    ]

    # Simulate the real JSON round trip a Bundle takes through /api/convert's
    # own response and /api/transform's own bundle_json input.
    round_tripped_json = json.loads(bundle.model_dump_json())
    assert round_tripped_json["entry"][4]["resource"]["total"]["value"] == 100.1  # the precision loss itself
    bundle_after_json = Bundle.model_validate(round_tripped_json)

    message_text = build_message_from_bundle(bundle_after_json, "EDI", "837P", "")
    clm_segment = next(s for s in message_text.split("~") if s.startswith("CLM*"))
    assert "100.10" in clm_segment


# The generator key for a given reverse-transform target. Every HL7v2
# target's own (message_type, trigger_event) pair and every EDI target's
# own ("EDI", transaction_set) pair line up directly; only C-CDA needs a
# mapping, since its transform target_format is "CDA" while its generator
# message_type is also "CDA" but keyed by document type rather than a
# trigger.
_GENERATOR_KEY_OVERRIDES = {
    ("CDA", "CCD", ""): ("CDA", "CCD"),
    ("CDA", "DISCHARGESUMMARY", ""): ("CDA", "DISCHARGESUMMARY"),
    ("CDA", "HISTORYANDPHYSICAL", ""): ("CDA", "HISTORYANDPHYSICAL"),
}


def _generator_key(target_format: str, target_type: str, target_trigger: str) -> tuple[str, str]:
    override = _GENERATOR_KEY_OVERRIDES.get((target_format, target_type, target_trigger))
    if override:
        return override
    if target_format == "EDI":
        return "EDI", target_type
    return target_type, target_trigger


@pytest.mark.parametrize("target", list_supported_targets())
def test_reverse_transform_preserves_resource_type_counts(target):
    """Every reverse-transform target must round-trip a generated message
    back to a Bundle carrying the *same multiset of resource types* it
    started with.

    **Why this is a standing test rather than a run-it-manually sweep**,
    unlike this app's other broad sweeps (app/dedup.py's full-registry
    pass, the provenance pillar's own 1464-entry crosswalk sweep - both
    deliberately uncommitted since they mostly re-run coverage other
    tests already provide): this invariant is genuinely orthogonal to
    every per-slice round-trip test, all of which assert on specific
    named fields of specific resources they already know exist. None of
    them notices a resource *disappearing* wholesale - and this check
    found exactly that, a real pre-existing bug where an
    observation-level <specimen> override silently lost its own Specimen
    resource on 28 of 300 generated C-CDA documents while the entire test
    suite stayed green (see app/transform/cda_ccd.py::
    _build_result_observation_element and CLAUDE.md's own note).

    Deliberately kept to a handful of seeds per target - the whole
    parametrized set runs in well under a second, and the point is the
    invariant, not fuzz breadth (the per-slice 200-300-seed sweeps
    already cover that)."""
    target_format, target_type, target_trigger = target
    message_type, trigger_event = _generator_key(target_format, target_type, target_trigger)

    for seed in range(5):
        original = convert_to_bundle(generate(message_type, trigger_event, seed=seed))
        round_tripped = convert_to_bundle(
            build_message_from_bundle(original, target_format, target_type, target_trigger)
        )
        before = Counter(entry.resource.get_resource_type() for entry in original.entry)
        after = Counter(entry.resource.get_resource_type() for entry in round_tripped.entry)
        assert before == after, f"{target} seed={seed}: {before} != {after}"


def test_mdm_round_trip_preserves_unverified_availability_status():
    # TXA-19's "CA"/"OB"/"UN" ride on status.extension as an alternate
    # code, per the IG's own rule, so they are real data on the FHIR side
    # and must survive a round trip. This builder used to emit "AV"
    # unconditionally, which silently turned a cancelled document into an
    # available one.
    raw = (FIXTURES / "validation_mdm_availability_unverified.hl7").read_text()
    bundle = convert_to_bundle(raw)
    message = build_message_from_bundle(bundle, "HL7", "MDM", "T02")

    txa = next(line for line in message.split(chr(13)) if line.startswith("TXA"))
    assert txa.split("|")[19] == "CA"

    reparsed = convert_to_bundle(message)
    document_reference = next(
        e.resource for e in reparsed.entry if e.resource.get_resource_type() == "DocumentReference"
    )
    extension = document_reference.status__ext.extension[0]
    assert extension.valueCodeableConcept.coding[0].code == "CA"


def test_oru_round_trip_preserves_the_attending_practitioner_with_its_degree():
    # PV1-7 was mapped for ADT and ignored by ORU/MDM's minimal Encounter,
    # so the attending physician was dropped. "Minimal" means no lifecycle
    # to infer, not ignore what the PV1 carried - the same argument that
    # closed PV1-3 for this builder. XCN.7's degree only has somewhere to
    # go on a real Practitioner, which is why one is materialised.
    from app.generators.registry import generate

    source = generate("ORU", "R01", seed=4)
    bundle = convert_to_bundle(source)
    encounter = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Encounter")
    attending_id = encounter.participant[0].individual.reference.removeprefix("urn:uuid:")
    attending = next(e.resource for e in bundle.entry if e.resource.id == attending_id)
    assert attending.identifier[0].value == "5452"
    assert attending.name[0].family == "Reyes"
    assert attending.qualification[0].code.coding[0].code == "MD"

    message = build_message_from_bundle(bundle, "HL7", "ORU", "R01")
    pv1 = next(line for line in message.split("\r") if line.startswith("PV1"))
    assert pv1.split("|")[7] == "5452^Reyes^Betty^^^^MD"

    round_tripped = convert_to_bundle(message)
    encounter = next(e.resource for e in round_tripped.entry if e.resource.get_resource_type() == "Encounter")
    attending_id = encounter.participant[0].individual.reference.removeprefix("urn:uuid:")
    attending = next(e.resource for e in round_tripped.entry if e.resource.id == attending_id)
    assert attending.qualification[0].code.coding[0].code == "MD"
