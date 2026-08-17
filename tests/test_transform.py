from pathlib import Path

import pytest
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.patient import Patient

from app.cda.pipeline import convert_cda_to_bundle
from app.hl7.errors import MappingError
from app.hl7.pipeline import convert_hl7_to_bundle
from app.transform.pipeline import build_message_from_bundle
from app.transform.registry import get_builder, list_supported_targets

FIXTURES = Path(__file__).parent / "fixtures"


def test_list_supported_targets_includes_adt_a01():
    assert ("HL7", "ADT", "A01") in list_supported_targets()


def test_list_supported_targets_includes_ccd():
    assert ("CDA", "CCD", "") in list_supported_targets()


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
