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


def test_list_supported_targets_includes_siu_s12():
    assert ("HL7", "SIU", "S12") in list_supported_targets()


def test_list_supported_targets_includes_oru_r01():
    assert ("HL7", "ORU", "R01") in list_supported_targets()


def test_list_supported_targets_includes_all_five_oru_triggers():
    targets = list_supported_targets()
    for trigger in ("R01", "R30", "R31", "R32", "R40"):
        assert ("HL7", "ORU", trigger) in targets


def test_list_supported_targets_includes_mdm_t02():
    assert ("HL7", "MDM", "T02") in list_supported_targets()


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
