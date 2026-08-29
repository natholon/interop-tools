from pathlib import Path

import pytest

from app.edi.claim_837p import Edi837pBuilder
from app.edi.pipeline import convert_edi_to_bundle
from app.edi.parser import first_transaction_set, parse_interchange
from app.hl7.errors import MappingError, MissingSegmentError

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _build_bundle(fixture_name: str):
    interchange = parse_interchange(read_fixture(fixture_name))
    transaction_set = first_transaction_set(interchange)
    return Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)


def _entries_by_type(bundle):
    by_type = {}
    for entry in bundle.entry:
        by_type.setdefault(entry.resource.get_resource_type(), []).append(entry.resource)
    return by_type


def test_basic_fixture_maps_claim_with_two_service_lines():
    bundle = _build_bundle("edi_837p_basic.x12")
    by_type = _entries_by_type(bundle)

    assert bundle.identifier.value == "0123"  # BHT03

    billing_provider = by_type["Practitioner"][0]
    assert billing_provider.name[0].family == "KILDARE"
    payer = by_type["Organization"][0]
    assert payer.name == "KEY INSURANCE COMPANY"
    patient = by_type["Patient"][0]
    assert patient.name[0].family == "SMITH"
    coverage = by_type["Coverage"][0]

    claim = by_type["Claim"][0]
    assert claim.use == "claim"
    assert claim.status == "active"
    assert claim.type.coding[0].code == "professional"
    assert claim.patient.reference == f"urn:uuid:{patient.id}"
    assert claim.provider.reference == f"urn:uuid:{billing_provider.id}"
    assert claim.insurer.reference == f"urn:uuid:{payer.id}"
    assert claim.insurance[0].coverage.reference == f"urn:uuid:{coverage.id}"
    assert coverage.beneficiary.reference == f"urn:uuid:{patient.id}"
    assert coverage.subscriber.reference == f"urn:uuid:{patient.id}"  # no dependent in this fixture
    assert claim.identifier[0].value == "26407789"  # CLM01
    assert float(claim.total.value) == 79.04  # CLM02
    assert claim.created.isoformat() == "2026-08-13T10:23:00+00:00"

    assert len(claim.diagnosis) == 2
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].code == "J209"
    assert claim.diagnosis[0].diagnosisCodeableConcept.coding[0].system == "http://hl7.org/fhir/sid/icd-10-cm"
    assert claim.diagnosis[1].diagnosisCodeableConcept.coding[0].code == "E119"

    # NM1*82 rendering provider is the same person as the billing provider
    # in this fixture (a common real-world shape for a sole proprietor) -
    # still materializes as its own Practitioner resource and its own
    # careTeam entry, not deduped against the billing provider.
    rendering_practitioners = by_type["Practitioner"]
    assert len(rendering_practitioners) == 2
    assert claim.careTeam[0].role.coding[0].code == "primary"
    care_team_provider_id = claim.careTeam[0].provider.reference

    assert len(claim.item) == 2
    line1, line2 = claim.item
    assert line1.sequence == 1
    assert line1.productOrService.coding[0].code == "99213"
    assert float(line1.unitPrice.value) == 43
    assert float(line1.quantity.value) == 1
    assert line1.servicedDate.isoformat() == "2026-08-10"
    assert line1.diagnosisSequence == [1, 2]  # SV1-07 "1:2"
    assert line1.locationCodeableConcept.coding[0].code == "11"  # CLM05-1
    assert line1.careTeamSequence == [1]
    assert claim.careTeam[0].sequence == 1
    assert care_team_provider_id in {f"urn:uuid:{p.id}" for p in rendering_practitioners}

    assert line2.sequence == 2
    assert line2.productOrService.coding[0].code == "90782"
    assert line2.diagnosisSequence == [1]  # SV1-07 "1"


def test_with_dependent_fixture_patient_is_the_dependent():
    bundle = _build_bundle("edi_837p_with_dependent.x12")
    by_type = _entries_by_type(bundle)

    billing_provider = by_type["Organization"][0]  # NM102="2" -> Organization, not Practitioner
    assert billing_provider.name == "GENERAL HOSPITAL"
    assert "Practitioner" not in by_type  # no rendering provider (NM1*82) in this fixture

    patients = {p.name[0].given[0]: p for p in by_type["Patient"]}
    assert set(patients.keys()) == {"JANE", "TED"}
    dependent = patients["TED"]
    subscriber = patients["JANE"]
    assert dependent.gender == "male"
    assert dependent.birthDate.isoformat() == "2015-05-01"

    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{dependent.id}"
    assert claim.careTeam is None

    coverage = by_type["Coverage"][0]
    assert coverage.beneficiary.reference == f"urn:uuid:{dependent.id}"
    assert coverage.subscriber.reference == f"urn:uuid:{subscriber.id}"

    assert len(claim.item) == 1
    assert claim.item[0].careTeamSequence is None


def test_missing_bht_raises_missing_segment_error():
    interchange = parse_interchange(read_fixture("edi_837p_missing_bht.x12"))
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="BHT"):
        Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)


def _minimal_raw(body: str, control: str = "0001") -> str:
    body_segment_count = len([s for s in body.split("~") if s])
    se01 = body_segment_count + 2  # ST + body segments + SE itself, inclusive
    return (
        "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
        "*260813*1200*^*00501*000000001*0*P*:~"
        "GS*HC*SENDERID*RECEIVERID*20260813*1200*1*X*005010X222A2~"
        f"ST*837*{control}*005010X222A2~"
        f"{body}"
        f"SE*{se01}*{control}~"
        "GE*1*1~"
        "IEA*1*000000001~"
    )


def test_missing_clm_raises_missing_segment_error():
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MissingSegmentError, match="CLM"):
        Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_clm02_unresolvable_raises_mapping_error():
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "CLM*26407789****11:B:1*Y*A*Y*I~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="CLM02"):
        Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_bht04_unresolvable_raises_mapping_error():
    body = (
        "BHT*0019*00*0999~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    with pytest.raises(MappingError, match="BHT04"):
        Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)


def test_dependent_loop_without_nm1_falls_back_to_subscriber():
    # A 2000C loop can be present (HL03="23") but missing its own NM1 - the
    # same "malformed-but-plausible dependent loop" gate every other EDI
    # family's own dependent/patient loop resolver already applies (see
    # resolve_eligibility_parties/resolve_claim_status_loops/
    # resolve_prior_auth_loops). Built proactively with this gate from the
    # start (matching claim_status.py/prior_auth.py's own precedent), not
    # discovered after the fact.
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*1~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "HL*3*2*23*0~"
        "PAT*19~"
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    assert len(by_type["Patient"]) == 1  # dependent loop dropped - only the subscriber materializes
    claim = by_type["Claim"][0]
    assert claim.patient.reference == f"urn:uuid:{by_type['Patient'][0].id}"


def test_diagnosis_pointer_out_of_range_is_skipped():
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~"
        "HI*ABK:J209~"
        "LX*1~"
        "SV1*HC:99213*43*UN*1***1:9~"  # pointer 9 doesn't resolve - only 1 diagnosis exists
        "DTP*472*D8*20260810~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item[0].diagnosisSequence == [1]


def test_service_line_without_sv1_is_skipped():
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~"
        "LX*1~"
        "DTP*472*D8*20260810~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item is None


def test_wrongly_qualified_dtp_is_not_used_as_serviced_date():
    # A code review caught that the service-line DTP lookup originally
    # took "the first DTP in the group" regardless of DTP01 (Date/Time
    # Qualifier) - a 2400 loop carrying a differently-qualified DTP (e.g.
    # "463" Prescription Date) ahead of the real "472" Service Date one
    # would have silently mislabeled it. Fixed by filtering on DTP01="472"
    # specifically (find_dtp_by_qualifier).
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~"
        "HI*ABK:J209~"
        "LX*1~"
        "SV1*HC:99213*43*UN*1***1~"
        "DTP*463*D8*20200101~"  # Prescription Date, not Service Date - must be ignored
        "DTP*472*D8*20260810~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert claim.item[0].servicedDate.isoformat() == "2026-08-10"


def test_item_sequence_numbers_stay_contiguous_when_a_middle_line_has_no_sv1():
    # A code review caught that item.sequence was assigned via
    # enumerate() over every LX group *before* filtering out groups with
    # no SV1, so a malformed middle line produced non-contiguous sequence
    # numbers (e.g. [1, 3] instead of [1, 2]). Fixed by numbering only the
    # items actually built.
    body = (
        "BHT*0019*00*0999*20260813*1023*CH~"
        "HL*1**20*1~"
        "NM1*85*1*KILDARE*BEN****XX*1999996666~"
        "HL*2*1*22*0~"
        "NM1*IL*1*SMITH*JANE****MI*111223333~"
        "NM1*PR*2*KEY INSURANCE COMPANY*****PI*999996666~"
        "CLM*26407789*79.04***11:B:1*Y*A*Y*I~"
        "HI*ABK:J209~"
        "LX*1~"
        "SV1*HC:99213*43*UN*1***1~"
        "LX*2~"
        # No SV1 for this line - malformed, must be skipped.
        "LX*3~"
        "SV1*HC:90782*15*UN*1***1~"
    )
    raw = _minimal_raw(body)
    interchange = parse_interchange(raw)
    transaction_set = first_transaction_set(interchange)
    bundle = Edi837pBuilder().build_bundle(transaction_set, interchange.delimiters)
    by_type = _entries_by_type(bundle)
    claim = by_type["Claim"][0]
    assert [item.sequence for item in claim.item] == [1, 2]


def test_bundle_round_trips_through_json():
    from fhir.resources.R4B.bundle import Bundle

    bundle = _build_bundle("edi_837p_basic.x12")
    round_tripped = Bundle.model_validate_json(bundle.model_dump_json())
    assert round_tripped.type == bundle.type
    assert len(round_tripped.entry) == len(bundle.entry)


def _by_type(bundle, resource_type):
    return [e.resource for e in bundle.entry if e.resource.get_resource_type() == resource_type]


def test_n3_n4_map_to_each_party_own_address():
    # N3/N4 follow the NM1 they describe, so the billing provider's address
    # must not land on the payer - both are in the same 2000B/2000A walk.
    bundle = convert_edi_to_bundle(read_fixture("edi_837p_basic.x12"))
    payer = next(o for o in _by_type(bundle, "Organization") if o.name == "KEY INSURANCE COMPANY")
    assert payer.address[0].line == ["3333 OCEAN ST"]
    assert (payer.address[0].city, payer.address[0].state, payer.address[0].postalCode) == (
        "SOUTH MIAMI",
        "FL",
        "33000",
    )
    billing = _by_type(bundle, "Practitioner")[0]
    assert billing.address[0].line == ["234 SEAWAY ST"]
    assert billing.address[0].city == "MIAMI"


def test_per_contact_numbers_map_to_telecom():
    raw = read_fixture("edi_837p_basic.x12").replace(
        "N3*3333 OCEAN ST~", "N3*3333 OCEAN ST~PER*IC*JERRY*TE*3055552222*FX*3055553333~"
    )
    payer = next(
        o for o in _by_type(convert_edi_to_bundle(raw), "Organization") if o.name == "KEY INSURANCE COMPANY"
    )
    assert [(t.system, t.value) for t in payer.telecom] == [
        ("phone", "3055552222"),
        ("fax", "3055553333"),
    ]


def test_a_party_with_no_address_segments_gets_no_address():
    raw = "".join(
        seg + "~" for seg in read_fixture("edi_837p_basic.x12").split("~") if not seg.startswith(("N3", "N4"))
    )
    assert all(o.address is None for o in _by_type(convert_edi_to_bundle(raw), "Organization"))


def _coverage(raw: str):
    return next(e.resource for e in convert_edi_to_bundle(raw).entry if e.resource.get_resource_type() == "Coverage")


def test_sbr_maps_payer_order_and_filing_indicator():
    # SBR09 is the Claim Filing Indicator - element 9, the position the real
    # X12.org examples use. Coverage.type binds at preferred strength, so an
    # X12 code on a disclosed system is conformant there.
    coverage = _coverage(read_fixture("edi_837p_basic.x12"))
    assert coverage.order == 1
    assert coverage.type.coding[0].code == "CI"


def test_pat_supplies_the_relationship_when_sbr_does_not():
    # A dependent's own 2000C loop states the relationship on PAT01, not on
    # the subscriber's SBR02.
    coverage = _coverage(read_fixture("edi_837p_with_dependent.x12"))
    assert coverage.relationship.coding[0].code == "child"


def test_pat01_wins_over_sbr02_for_a_dependent_claim():
    # Coverage.relationship is the *beneficiary's* relationship to the
    # subscriber, which is what PAT01 states. SBR02 states the
    # *subscriber's* own relationship to the insured, so reading it for a
    # dependent claim reported the child as "self".
    raw = read_fixture("edi_837p_with_dependent.x12").replace("SBR*P********CI~", "SBR*P*18*******CI~")
    assert _coverage(raw).relationship.coding[0].code == "child"


def test_sbr02_supplies_the_relationship_when_the_subscriber_is_the_patient():
    raw = read_fixture("edi_837p_basic.x12").replace("SBR*P********CI~", "SBR*P*18*******CI~")
    assert _coverage(raw).relationship.coding[0].code == "self"


def test_a_dependent_claim_with_no_pat_states_no_relationship():
    # SBR02 describes the subscriber, so with no PAT there is nothing that
    # describes the dependent - saying "self" would assert the child is the
    # subscriber.
    assert _coverage(read_fixture("edi_837i_with_dependent.x12")).relationship is None


def test_unknown_relationship_code_leaves_the_field_unset():
    # "21" is X12's own Unknown - flattening it to "other" would assert a
    # relationship the message never stated.
    raw = read_fixture("edi_837p_with_dependent.x12").replace("PAT*19~", "PAT*21~")
    assert _coverage(raw).relationship is None


def test_unknown_payer_responsibility_leaves_order_unset():
    raw = read_fixture("edi_837p_basic.x12").replace("SBR*P********CI~", "SBR*U********CI~")
    assert _coverage(raw).order is None


def test_nm1_middle_name_and_suffix_map_onto_the_human_name():
    # NM105 is the middle name, which FHIR carries as a second .given
    # entry - it has no separate middle-name field - and NM107 the suffix.
    raw = read_fixture("edi_837p_basic.x12").replace(
        "NM1*IL*1*SMITH*JANE****MI*111223333~", "NM1*IL*1*SMITH*JANE*Q**JR*MI*111223333~"
    )
    patient = next(
        e.resource for e in convert_edi_to_bundle(raw).entry if e.resource.get_resource_type() == "Patient"
    )
    assert patient.name[0].given == ["JANE", "Q"]
    assert patient.name[0].suffix == ["JR"]


def test_ref_ei_maps_to_a_tax_id_identifier_beside_the_npi():
    # REF*EI is the party's Employer Identification Number, on FHIR's own
    # USEIN naming system - it must sit alongside the NPI, not replace it.
    billing = next(
        e.resource
        for e in convert_edi_to_bundle(read_fixture("edi_837p_basic.x12")).entry
        if e.resource.get_resource_type() == "Practitioner"
    )
    systems = {i.system: i.value for i in billing.identifier}
    assert systems["http://hl7.org/fhir/sid/us-npi"] == "1999996666"
    assert systems["urn:oid:2.16.840.1.113883.4.4"] == "123456789"


def test_sv1_unit_of_measure_lands_on_the_quantity():
    claim = next(
        e.resource
        for e in convert_edi_to_bundle(read_fixture("edi_837p_basic.x12")).entry
        if e.resource.get_resource_type() == "Claim"
    )
    assert claim.item[0].quantity.code == "UN"
    assert claim.item[0].quantity.system == "urn:interop-tools:x12-unit-of-measure"


def test_prv_taxonomy_lands_on_the_care_team_qualification():
    # Only a PXC-qualified PRV03 is read - PRV02 names which code list
    # PRV03 is drawn from, and taxonomy is the only one with a canonical
    # FHIR system.
    raw = read_fixture("edi_837p_basic.x12").replace(
        "NM1*82*1*KILDARE*BEN****XX*1999996666~",
        "NM1*82*1*KILDARE*BEN****XX*1999996666~PRV*PE*PXC*207Q00000X~",
    )
    claim = next(
        e.resource for e in convert_edi_to_bundle(raw).entry if e.resource.get_resource_type() == "Claim"
    )
    assert claim.careTeam[0].qualification.coding[0].system == "http://nucc.org/provider-taxonomy"
    assert claim.careTeam[0].qualification.coding[0].code == "207Q00000X"


def test_a_non_taxonomy_prv_is_left_alone():
    raw = read_fixture("edi_837p_basic.x12").replace(
        "NM1*82*1*KILDARE*BEN****XX*1999996666~",
        "NM1*82*1*KILDARE*BEN****XX*1999996666~PRV*PE*ZZ*SOMETHING~",
    )
    claim = next(
        e.resource for e in convert_edi_to_bundle(raw).entry if e.resource.get_resource_type() == "Claim"
    )
    assert claim.careTeam[0].qualification is None

