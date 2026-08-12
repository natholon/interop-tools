"""Cross-document-type C-CDA shared logic - the app/mappings/common.py
equivalent for the XML input format. Header structure (recordTarget/
patientRole, componentOf/encompassingEncounter) and coded-value shape
(CD/value[xsi:type=CD]) are standardized across C-CDA document types
generally, not CCD-specific, so this lives here rather than in ccd.py - a
future Discharge Summary/H&P builder reuses it directly, same reasoning as
app/mappings/common.py::build_patient being shared across every HL7v2
message type."""

import uuid

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.cda.parser import find_all, find_child, coded_value, ivl_ts_bounds, ts_value
from app.fhir_models.builders import parse_hl7_date, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError

# HL7 AdministrativeGender codeSystem (2.16.840.1.113883.5.1) uses F/M/UN,
# not HL7v2 PID-8's M/F/O - a genuinely different (if similarly-shaped)
# vocabulary, so this is its own table rather than reusing
# app/fhir_models/builders.py::hl7_sex_to_fhir_gender's _GENDER_MAP. "UN"
# (undifferentiated) maps to FHIR's "other", the closer semantic fit versus
# "unknown" (which means "not recorded" rather than "recorded as neither").
_GENDER_MAP = {"F": "female", "M": "male", "UN": "other"}

# Disclosed, extensible OID -> FHIR canonical system URI table (same
# category as app/mappings/mdm.py's TXA-3 MIME crosswalk) - covers the code
# systems this slice's Problems mapping actually needs (SNOMED CT) plus a
# couple of other common ones likely to appear in real C-CDA documents.
# Anything unrecognized falls back to a urn:oid: URI, matching FHIR's own
# convention for representing an untranslated OID rather than guessing.
_OID_TO_FHIR_SYSTEM = {
    "2.16.840.1.113883.6.96": "http://snomed.info/sct",
    "2.16.840.1.113883.6.1": "http://loinc.org",
    "2.16.840.1.113883.6.88": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "2.16.840.1.113883.6.90": "http://hl7.org/fhir/sid/icd-10-cm",
}
_CD_FALLBACK_SYSTEM = "urn:interop-tools:coded-value"

_TELECOM_SCHEME_TO_SYSTEM = {"tel": "phone", "mailto": "email", "fax": "fax"}
# A representative subset of HL7 AddressUse/TelecomUse codes - disclosed,
# extensible; an unrecognized use code is simply omitted rather than guessed.
_TELECOM_USE_MAP = {"HP": "home", "WP": "work", "MC": "mobile"}


def parse_partial_ts(raw_value: str | None) -> str | None:
    """CDA TS/IVL_TS @value strings use the identical HL7 TS digit shape
    (YYYYMMDD[HHMM[SS][+/-ZZZZ]]) as HL7v2 TS fields, so the existing
    parse_hl7_datetime/parse_hl7_date are directly reusable. Onset-style
    dates are commonly date-only (8 digits) - tries the full-timestamp
    parse first, falls back to date-only, matching the same "try datetime,
    fall back to date" combinator app/validation/common.py needed for the
    identical reason. Unlike that module, this only needs the resulting
    FHIR-formatted string (for direct assignment to a dateTime/date field),
    not a comparable Python datetime. Public (not module-private) since
    every section builder needing an onset/abatement/effective date reuses
    this, not just the header builders in this module."""
    if not raw_value:
        return None
    return parse_hl7_datetime(raw_value) or parse_hl7_date(raw_value)


def build_codeable_concept_from_cd(element) -> CodeableConcept | None:
    """Build a CodeableConcept from a CD-shaped element (a <code .../> or
    <value xsi:type="CD" .../> - both carry the same @code/@codeSystem/
    @displayName attributes). Returns None when the element is absent or
    has no @code, matching the established "no code -> None" convention
    (see app/fhir_models/builders.py::build_codeable_concept_from_cwe).
    Does not attempt to resolve originalText/reference back into narrative
    <text> - falls back to "code only, no display" when @displayName is
    absent, which real-world senders commonly omit in favor of that
    narrative-reference pattern."""
    result = coded_value(element)
    if result is None:
        return None
    code, display_name, code_system_oid = result
    system = _OID_TO_FHIR_SYSTEM.get(code_system_oid) or (
        f"urn:oid:{code_system_oid}" if code_system_oid else _CD_FALLBACK_SYSTEM
    )
    coding = Coding(system=system, code=code)
    if display_name:
        coding.display = display_name
    return CodeableConcept(coding=[coding])


def _build_identifier(id_element, fallback_system: str) -> Identifier | None:
    if id_element is None:
        return None
    extension = id_element.get("extension")
    root = id_element.get("root")
    if extension:
        system = f"urn:oid:{root}" if root else fallback_system
        return Identifier(system=system, value=extension)
    if root:
        # An II with a root but no extension is still a complete identifier
        # per the HL7 datatype spec - the root itself IS the identifier
        # value, not "no identifier". Represented the same way FHIR's own
        # CDA-on-FHIR mapping guidance represents a bare root.
        return Identifier(system="urn:ietf:rfc:3986", value=f"urn:oid:{root}")
    return None


def _build_identifiers(id_elements, fallback_system: str) -> list[Identifier]:
    identifiers = []
    for id_element in id_elements:
        identifier = _build_identifier(id_element, fallback_system)
        if identifier:
            identifiers.append(identifier)
    return identifiers


def _build_patient_identifiers(patient_role) -> list[Identifier]:
    return _build_identifiers(find_all(patient_role, "id"), "urn:interop-tools:cda-patient-id")


def _build_patient_names(patient_element) -> list[HumanName]:
    names = []
    for i, name_element in enumerate(find_all(patient_element, "name")):
        family_element = find_child(name_element, "family")
        family = (family_element.text or "").strip() if family_element is not None else ""
        given_parts = [g.text.strip() for g in find_all(name_element, "given") if g.text and g.text.strip()]
        if not family and not given_parts:
            continue
        name = HumanName(use="official" if i == 0 else "old")
        if family:
            name.family = family
        if given_parts:
            name.given = given_parts
        names.append(name)
    return names


def _build_patient_addresses(patient_role) -> list[Address]:
    addresses = []
    for addr_element in find_all(patient_role, "addr"):
        lines = [e.text.strip() for e in find_all(addr_element, "streetAddressLine") if e.text and e.text.strip()]
        city_element = find_child(addr_element, "city")
        state_element = find_child(addr_element, "state")
        postal_code_element = find_child(addr_element, "postalCode")
        country_element = find_child(addr_element, "country")
        city = (city_element.text or "").strip() if city_element is not None else ""
        state = (state_element.text or "").strip() if state_element is not None else ""
        postal_code = (postal_code_element.text or "").strip() if postal_code_element is not None else ""
        country = (country_element.text or "").strip() if country_element is not None else ""
        if not any([lines, city, state, postal_code, country]):
            continue
        address = Address()
        if lines:
            address.line = lines
        if city:
            address.city = city
        if state:
            address.state = state
        if postal_code:
            address.postalCode = postal_code
        if country:
            address.country = country
        addresses.append(address)
    return addresses


def _build_patient_telecoms(patient_role) -> list[ContactPoint]:
    telecoms = []
    for telecom_element in find_all(patient_role, "telecom"):
        raw_value = telecom_element.get("value")
        if not raw_value:
            continue
        scheme, _, value = raw_value.partition(":")
        contact_point = ContactPoint(value=value or raw_value)
        system = _TELECOM_SCHEME_TO_SYSTEM.get(scheme.lower())
        if system:
            contact_point.system = system
        use = _TELECOM_USE_MAP.get(telecom_element.get("use", ""))
        if use:
            contact_point.use = use
        telecoms.append(contact_point)
    return telecoms


def build_patient_from_header(document) -> Patient:
    """recordTarget/patientRole/patient -> Patient. Raises
    MissingSegmentError when the header lacks a patientRole/patient at
    all - reused from app.hl7.errors since the meaning ("a required
    structural piece is absent") is format-agnostic."""
    record_target = find_child(document, "recordTarget")
    patient_role = find_child(record_target, "patientRole") if record_target is not None else None
    if patient_role is None:
        raise MissingSegmentError("recordTarget/patientRole is missing")
    patient_element = find_child(patient_role, "patient")
    if patient_element is None:
        raise MissingSegmentError("recordTarget/patientRole/patient is missing")

    patient = Patient(id=str(uuid.uuid4()))

    identifiers = _build_patient_identifiers(patient_role)
    if identifiers:
        patient.identifier = identifiers

    names = _build_patient_names(patient_element)
    if names:
        patient.name = names

    gender_element = find_child(patient_element, "administrativeGenderCode")
    if gender_element is not None:
        code = (gender_element.get("code") or "").strip().upper()
        if code:
            patient.gender = _GENDER_MAP.get(code, "unknown")

    birth_time = find_child(patient_element, "birthTime")
    if birth_time is not None:
        birth_date = parse_hl7_date(ts_value(birth_time) or "")
        if birth_date:
            patient.birthDate = birth_date

    addresses = _build_patient_addresses(patient_role)
    if addresses:
        patient.address = addresses

    telecoms = _build_patient_telecoms(patient_role)
    if telecoms:
        patient.telecom = telecoms

    return patient


_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_DEFAULT_ENCOUNTER_CLASS = "AMB"
# componentOf/encompassingEncounter/code draws from ActEncounterCode
# (2.16.840.1.113883.5.4), the same source vocabulary FHIR's own
# Encounter.class binding (v3-ActCode) mirrors - a genuine near-direct
# mapping, not a guess, with a disclosed default when absent/unrecognized.
# Public (not module-private) - reused by app/cda/validation.py so its
# "unrecognized encounter class" rule can never drift from what this
# mapper actually treats as recognized.
RECOGNIZED_ENCOUNTER_CLASSES = {"AMB", "EMER", "IMP", "ACUTE", "NONAC", "PRENC", "SS", "VR"}


def build_encounter_from_header(document, patient_id: str) -> Encounter | None:
    """componentOf/encompassingEncounter -> Encounter, or None when absent
    (a CCD isn't necessarily bound to one encounter - it's a summary
    document). Status is honestly "unknown" rather than inferred - unlike
    ADT's real-time admit/discharge triggers, a CCD gives no reliable
    lifecycle signal to infer status from, same rationale as ORU's/MDM's
    minimal PV1-derived Encounters (app/mappings/oru.py,
    app/mappings/mdm.py -> app/mappings/common.py::build_minimal_encounter)."""
    component_of = find_child(document, "componentOf")
    encompassing_encounter = find_child(component_of, "encompassingEncounter") if component_of is not None else None
    if encompassing_encounter is None:
        return None

    class_code = _DEFAULT_ENCOUNTER_CLASS
    code_element = find_child(encompassing_encounter, "code")
    if code_element is not None:
        raw_code = (code_element.get("code") or "").strip().upper()
        if raw_code in RECOGNIZED_ENCOUNTER_CLASSES:
            class_code = raw_code

    encounter = Encounter(
        id=str(uuid.uuid4()),
        status="unknown",
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=Coding(system=_ENCOUNTER_CLASS_SYSTEM, code=class_code),
    )

    identifiers = _build_identifiers(find_all(encompassing_encounter, "id"), "urn:interop-tools:cda-encounter-id")
    if identifiers:
        encounter.identifier = identifiers

    period_start, period_end = (
        (parse_partial_ts(v) for v in ivl_ts_bounds(find_child(encompassing_encounter, "effectiveTime")))
    )
    if period_start or period_end:
        period = Period()
        if period_start:
            period.start = period_start
        if period_end:
            period.end = period_end
        encounter.period = period

    return encounter


def assemble_bundle(document, patient: Patient, *resources: Resource) -> Bundle:
    """Wrap a Patient plus any number of additional resources into a
    Bundle, with ClinicalDocument-derived metadata (id -> Bundle.identifier,
    effectiveTime -> Bundle.timestamp) - the CDA-header equivalent of
    app/mappings/common.py::assemble_bundle, which reads MSH fields instead.
    Bundle.type is "collection", matching every existing HL7v2 pipeline's
    output shape - a proper FHIR-Document-shaped Bundle(type="document")
    with a Composition is deliberately out of scope for this slice (see
    CLAUDE.md)."""
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")

    identifier = _build_identifier(find_child(document, "id"), "urn:interop-tools:cda-document-id")
    if identifier:
        bundle.identifier = identifier

    # Bundle.timestamp is FHIR "instant", which (unlike Condition's
    # onset/abatement dateTime) has NO date-only form - it requires full
    # date+time+timezone precision. parse_partial_ts's date-only fallback
    # would produce a value the instant regex rejects, crashing resource
    # construction on a perfectly convertible document whose
    # ClinicalDocument/effectiveTime happens to be date-only. Use
    # parse_hl7_datetime directly (returns None rather than a date-only
    # string), matching app/mappings/common.py::assemble_bundle's own
    # deliberate choice not to date-only-fallback for this same reason.
    timestamp = parse_hl7_datetime(ts_value(find_child(document, "effectiveTime")) or "")
    if timestamp:
        bundle.timestamp = timestamp

    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{patient.id}", resource=patient)]
    bundle.entry.extend(BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources)
    return bundle
