from app.edi.common import build_diagnosis_codeable_concepts
from app.edi.parser import Delimiters

_DELIMITERS = Delimiters(element="*", component=":", repetition="^", segment_terminator="~")


def test_abk_qualifier_resolves_to_icd10():
    hi = ["HI", "ABK:J209"]
    concepts = build_diagnosis_codeable_concepts(hi, _DELIMITERS)
    assert len(concepts) == 1
    assert concepts[0].coding[0].system == "http://hl7.org/fhir/sid/icd-10-cm"
    assert concepts[0].coding[0].code == "J209"


def test_bf_qualifier_resolves_to_icd9_not_icd10():
    # X12 code list 1270: "BF" is the legacy ICD-9-CM Other Diagnosis
    # qualifier - "ABF" is the ICD-10-CM one. An earlier version of this
    # table (introduced in Phase 3's prior_auth.py before being promoted
    # here) mapped "BF" itself to ICD-10-CM, which is wrong.
    hi = ["HI", "BF:1831"]
    concepts = build_diagnosis_codeable_concepts(hi, _DELIMITERS)
    assert len(concepts) == 1
    assert concepts[0].coding[0].system == "http://hl7.org/fhir/sid/icd-9-cm"
    assert concepts[0].coding[0].code == "1831"


def test_multiple_diagnoses_preserve_composite_order():
    hi = ["HI", "ABK:J209", "ABF:E119", "ABF:I10"]
    concepts = build_diagnosis_codeable_concepts(hi, _DELIMITERS)
    assert [c.coding[0].code for c in concepts] == ["J209", "E119", "I10"]


def test_unrecognized_qualifier_falls_back_to_disclosed_local_system():
    hi = ["HI", "ZZ:99999"]
    concepts = build_diagnosis_codeable_concepts(hi, _DELIMITERS)
    assert concepts[0].coding[0].system == "urn:interop-tools:x12-hi-qualifier:ZZ"


def test_none_hi_segment_returns_empty_list():
    assert build_diagnosis_codeable_concepts(None, _DELIMITERS) == []


def test_scan_stops_at_first_empty_position():
    hi = ["HI", "ABK:J209", ""]
    concepts = build_diagnosis_codeable_concepts(hi, _DELIMITERS)
    assert len(concepts) == 1
