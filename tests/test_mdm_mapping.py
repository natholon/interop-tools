import base64
from pathlib import Path

import pytest

from app.hl7.errors import MissingSegmentError
from app.hl7.parser import parse_message
from app.mappings.mdm import MdmT02Mapper, MdmT04Mapper, MdmT06Mapper, MdmT08Mapper, MdmT10Mapper, MdmT11Mapper

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _entries_by_type(bundle) -> dict:
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_every_field():
    message = parse_message(read_fixture("mdm_t02_basic.hl7"))
    bundle = MdmT02Mapper().to_bundle(message)
    by_type = _entries_by_type(bundle)

    # Patient + Encounter (PV1 present) + DocumentReference + Binary (OBX
    # content present) + 2 materialized Practitioners (originator, authenticator)
    assert len(bundle.entry) == 6
    assert len(by_type["Practitioner"]) == 2

    document_reference = by_type["DocumentReference"][0]
    assert document_reference.status == "current"
    assert document_reference.type.coding[0].code == "CN"
    assert document_reference.type.coding[0].display == "Consultation Note"
    assert document_reference.content[0].attachment.contentType == "text/plain"
    assert document_reference.masterIdentifier.value == "DOC-000123"
    assert document_reference.date.isoformat() == "2026-08-12T10:50:00+00:00"
    assert document_reference.description == "Cardiology Consult Note"
    assert document_reference.securityLabel[0].coding[0].code == "R"

    patient = by_type["Patient"][0]
    encounter = by_type["Encounter"][0]
    binary = by_type["Binary"][0]
    assert document_reference.subject.reference == f"urn:uuid:{patient.id}"
    assert document_reference.context.encounter[0].reference == f"urn:uuid:{encounter.id}"
    assert document_reference.content[0].attachment.url == f"urn:uuid:{binary.id}"

    originator = next(p for p in by_type["Practitioner"] if p.name[0].family == "Chen")
    authenticator = next(p for p in by_type["Practitioner"] if p.name[0].family == "Alvarez")
    assert document_reference.author[0].reference == f"urn:uuid:{originator.id}"
    assert document_reference.author[0].display == "Chen, Wei"
    assert document_reference.authenticator.reference == f"urn:uuid:{authenticator.id}"
    assert document_reference.authenticator.display == "Alvarez, Rosa"

    assert binary.contentType == "text/plain"
    assert binary.data.decode() == (
        "Patient seen for cardiology consult.\nNo acute distress; recommend follow-up in 2 weeks."
    )


def test_minimal_fixture_omits_optional_pieces():
    message = parse_message(read_fixture("mdm_t02_minimal.hl7"))
    bundle = MdmT02Mapper().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert "Encounter" not in by_type
    assert "Binary" not in by_type
    assert "Practitioner" not in by_type

    document_reference = by_type["DocumentReference"][0]
    assert document_reference.status == "current"
    assert document_reference.content[0].attachment.url is None
    assert document_reference.author is None
    assert document_reference.context is None


def test_obx_content_with_caret_is_not_truncated():
    # Regression test: TX is unstructured free text, not HL7 composite - a
    # literal '^' in the text used to get silently truncated because
    # _build_binary_from_obx read it via field_str (component 1 only)
    # instead of the whole field.
    message = parse_message(read_fixture("mdm_t02_obx_with_caret.hl7"))
    bundle = MdmT02Mapper().to_bundle(message)
    binary = next(e.resource for e in bundle.entry if e.resource.get_resource_type() == "Binary")
    assert binary.data.decode() == "Grade II^ tear noted on exam; recommend follow-up"


def test_same_originator_and_authenticator_deduplicates_to_one_practitioner():
    # TXA-9 (originator) and TXA-10 (authenticator) identifying the same
    # real-world XCN id (e.g. a physician who both dictates and co-signs a
    # note) must materialize ONE Practitioner referenced by both
    # DocumentReference.author and .authenticator, not two near-identical
    # ones - same rationale as ORU's OBX-16 performer dedup. This fixture
    # also exercises TXA-16 (Unique Document File Name) -> identifier.
    message = parse_message(read_fixture("mdm_t02_same_author_authenticator.hl7"))
    bundle = MdmT02Mapper().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert len(by_type["Practitioner"]) == 1
    practitioner = by_type["Practitioner"][0]
    document_reference = by_type["DocumentReference"][0]
    assert document_reference.author[0].reference == f"urn:uuid:{practitioner.id}"
    assert document_reference.authenticator.reference == f"urn:uuid:{practitioner.id}"
    assert document_reference.identifier[0].value == "consult_note_911234.txt"


def test_missing_txa_raises_missing_segment_error():
    message = parse_message(read_fixture("mdm_t02_missing_txa.hl7"))
    with pytest.raises(MissingSegmentError):
        MdmT02Mapper().to_bundle(message)


@pytest.mark.parametrize("mapper_cls", [MdmT04Mapper, MdmT06Mapper, MdmT08Mapper, MdmT10Mapper, MdmT11Mapper])
def test_other_triggers_produce_same_shape_as_t02(mapper_cls):
    # T02/T04/T06/T08/T10/T11 are field-identical per the v2-to-FHIR IG's
    # single, trigger-agnostic TXA ConceptMap - including T10/T11, which are
    # semantically status-change events but have no trigger-specific target
    # in the IG's own ConceptMap (see app/mappings/mdm.py's module docstring).
    message = parse_message(read_fixture("mdm_t02_basic.hl7"))
    bundle = mapper_cls().to_bundle(message)
    by_type = _entries_by_type(bundle)

    assert len(bundle.entry) == 6
    assert by_type["DocumentReference"][0].status == "current"
