import uuid

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.encounter import Encounter, EncounterLocation, EncounterParticipant
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner, PractitionerQualification
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.fhir_models.builders import (
    build_addresses,
    build_human_names,
    build_phone_telecom,
    hl7_sex_to_fhir_gender,
    parse_hl7_date,
    parse_hl7_datetime,
)
from app.hl7.parser import component_str, field_repetitions, field_str
from app.provenance.location import hl7_location

_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_PATIENT_CLASS_MAP = {"I": "IMP", "O": "AMB", "E": "EMER", "P": "PRENC"}


# HL7 table 0203, the IdentifierType value set the v2-to-FHIR IG binds
# CX.5 to for Identifier.type.
IDENTIFIER_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0203"
# HL7 table 0360, the DegreeLicenseCertificate value set the IG binds
# XCN.7 to for Practitioner.qualification.code.
DEGREE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0360"


def build_patient(pid, recorder=None) -> Patient:
    """PID -> Patient. Shared by every HL7 message type; PID is mapped
    identically regardless of message type or trigger event. `recorder`
    is optional (see app/provenance/recorder.py) - every existing caller
    keeps working unchanged when it's omitted."""
    patient_id = str(uuid.uuid4())
    identifiers = []
    for idx, repetition in enumerate(field_repetitions(pid, 3)):
        value = component_str(repetition, 1)
        if not value:
            continue
        assigning_authority = component_str(repetition, 4)
        system = assigning_authority or "urn:interop-tools:patient-id"
        identifier = Identifier(system=system, value=value)
        # CX.5 -> Identifier.type.coding.code, per the v2-to-FHIR IG's own
        # CX[Identifier] datatype map, which binds it to the IdentifierType
        # value set (HL7 table 0203). Leaving it unmapped made PID-3.5 the
        # single largest class of dropped HL7v2 data in this app.
        identifier_type = component_str(repetition, 5)
        if identifier_type:
            identifier.type = CodeableConcept(
                coding=[Coding(system=IDENTIFIER_TYPE_SYSTEM, code=identifier_type)]
            )
        identifiers.append(identifier)
        if recorder:
            index = len(identifiers) - 1
            if identifier_type:
                recorder.record(
                    patient_id,
                    f"identifier[{index}].type.coding[0].code",
                    hl7_location("PID", 3, repetition=idx, component=5),
                    identifier_type,
                )
            recorder.record(
                patient_id,
                f"identifier[{index}].value",
                hl7_location("PID", 3, repetition=idx, component=1),
                value,
            )
            # PID-3.4 genuinely drives .system, so it needs its own fact -
            # without it the crosswalk shows no source for .system at all,
            # and a completeness check can't tell "mapped but unrecorded"
            # apart from "dropped".
            if assigning_authority:
                recorder.record(
                    patient_id,
                    f"identifier[{index}].system",
                    hl7_location("PID", 3, repetition=idx, component=4),
                    system,
                )
            else:
                recorder.record_inferred(
                    patient_id,
                    f"identifier[{index}].system",
                    "PID-3.4 (Assigning Authority) was empty, so a local placeholder system is used.",
                    system,
                )

    patient = Patient(id=patient_id)
    if identifiers:
        patient.identifier = identifiers
    names = build_human_names(pid, resource_id=patient_id, recorder=recorder)
    if names:
        patient.name = names
    birth_date = parse_hl7_date(field_str(pid, 7))
    if birth_date:
        patient.birthDate = birth_date
        if recorder:
            recorder.record(patient_id, "birthDate", hl7_location("PID", 7), birth_date, source_value=field_str(pid, 7))
    sex = field_str(pid, 8)
    if sex:
        patient.gender = hl7_sex_to_fhir_gender(sex)
        if recorder:
            recorder.record(patient_id, "gender", hl7_location("PID", 8), patient.gender, source_value=sex)
    addresses = build_addresses(pid, resource_id=patient_id, recorder=recorder)
    if addresses:
        patient.address = addresses
    telecom = build_phone_telecom(pid, resource_id=patient_id, recorder=recorder)
    if telecom:
        patient.telecom = [telecom]
    return patient


# PL (person location) -> a chain of Location resources, per the
# v2-to-FHIR PL[Location] ConceptMap. Each populated component becomes its
# own Location carrying the value as .identifier plus a fixed .mode and
# .physicalType; the referencing resource points at the most granular one
# present, and each links upward via .partOf. Ordered most granular first,
# matching the ConceptMap's own [1]..[6] indexing.
#
# **Point of Care has no physicalType code.** The ConceptMap's fixed value
# for it is the literal placeholder "/extension??-poc/" - an unresolved
# item in the IG, not a code - and R4's location-physical-type value set
# has no point-of-care concept either. Omitted rather than substituting a
# plausible-looking code the IG never specified.
_PL_LEVELS: tuple[tuple[int, str, str | None], ...] = (
    (3, "Bed", "bd"),
    (2, "Room", "ro"),
    (8, "Floor", "lvl"),
    (1, "Point of Care", None),
    (7, "Building", "bu"),
    (4, "Facility", "si"),
)
_PL_PHYSICAL_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/location-physical-type"
_PL_MODE = "instance"
_PL_IDENTIFIER_SYSTEM = "urn:interop-tools:hl7v2-location-id"


def location_display(segment, field_num: int) -> str:
    """Human-readable text for a PL-shaped field, for a Reference.display
    beside the real Location reference.

    Joins every populated component least-specific first ("GENHOSP, C100,
    A"), the order locations are conventionally written in narrative. The
    IG gives no guidance for display text - it specifies the Location
    resources, not this string - so the ordering is this app's own choice,
    stated rather than implied.

    Previously this read only components 1-2 and mislabelled them
    ("facility" for what is actually Point of Care; Facility is component
    4), so "C100^^A^GENHOSP" displayed as just "C100"."""
    parts = []
    for component, _label, _code in reversed(_PL_LEVELS):
        value = field_str(segment, field_num, component=component)
        if value:
            parts.append(value)
    return ", ".join(parts)


def build_location_chain_from_pl(segment, field_num: int, recorder=None) -> list[Location]:
    """A PL field -> one Location per populated component, linked by
    .partOf, ordered most granular first. The caller references
    `chain[0]` (the most granular) and adds every returned Location to the
    Bundle. Empty list when the field is empty.

    **partOf order follows the IG's narrative, not its ConceptMap** - the
    two disagree, and only one of them is self-consistent. The narrative
    gives "Bed to Room to Floor to Point of Care to Building to Facility";
    the machine-readable ConceptMap instead links Point of Care straight
    to Facility and links Building to *itself* (`PL.7 ->
    [5].partOf.reference(Location[5])`), which is impossible - a Location
    cannot be its own parent. Treated as a defect in the ConceptMap and
    the narrative followed, with the discrepancy stated here rather than
    silently resolved."""
    levels = []
    for component, label, physical_type in _PL_LEVELS:
        value = field_str(segment, field_num, component=component)
        if value:
            levels.append((component, label, physical_type, value))
    if not levels:
        return []

    segment_id = field_str(segment, 0)
    chain: list[Location] = []
    for component, label, physical_type, value in levels:
        location = Location(
            id=str(uuid.uuid4()),
            mode=_PL_MODE,
            identifier=[Identifier(system=_PL_IDENTIFIER_SYSTEM, value=value)],
        )
        if physical_type:
            location.physicalType = CodeableConcept(
                coding=[Coding(system=_PL_PHYSICAL_TYPE_SYSTEM, code=physical_type)]
            )
        if recorder:
            location_str = hl7_location(segment_id, field_num, component=component)
            recorder.record(location.id, "identifier[0].value", location_str, value)
            recorder.record_inferred(
                location.id,
                "mode",
                f"Fixed to {_PL_MODE!r} for every PL level by the v2-to-FHIR PL[Location] map.",
                _PL_MODE,
            )
            if physical_type:
                recorder.record_inferred(
                    location.id,
                    "physicalType.coding[0].code",
                    f"{label} is fixed to {physical_type!r} by the v2-to-FHIR PL[Location] map.",
                    physical_type,
                )
        chain.append(location)

    for child, parent in zip(chain, chain[1:]):
        child.partOf = Reference(reference=f"urn:uuid:{parent.id}")
    return chain


def person_display(segment, field_num: int) -> str:
    """Build a human-readable display string from an XCN-shaped field (id^
    family^given, e.g. PV1-7, AIP-3). Shared across message types since XCN
    fields are formatted identically regardless of which segment they're in.
    Falls back to the id component (component 1) when family/given are both
    empty, so this stays consistent with build_practitioner_from_xcn's
    presence check ("id or family or given") - without the fallback, an
    id-only field would materialize a Practitioner but pair it with an empty
    display, which FHIR's Reference.display rejects (must be non-empty)."""
    family = field_str(segment, field_num, component=2)
    given = field_str(segment, field_num, component=3)
    name = ", ".join(part for part in (family, given) if part)
    return name or field_str(segment, field_num, component=1)


def build_practitioner_from_xcn(segment, field_num: int, recorder=None) -> Practitioner | None:
    """Build a real Practitioner resource from an XCN-shaped field (id^family^
    given^..., e.g. PV1-7, AIP-3). Returns None when the field is entirely
    empty. Shared so any future mapper needing a materialized (not just
    display-text) practitioner from an XCN field can reuse this rather than
    re-deriving it. `recorder` is optional (see app/provenance/recorder.py) -
    the segment id for hl7_location is read from the segment itself (field 0
    is always the segment's own name, e.g. "AIP"/"TXA") rather than passed in
    separately, since this function is generic across callers using
    different segment types for the identical XCN shape."""
    practitioner_id = field_str(segment, field_num, component=1)
    family = field_str(segment, field_num, component=2)
    given = field_str(segment, field_num, component=3)
    if not (practitioner_id or family or given):
        return None
    practitioner = Practitioner(id=str(uuid.uuid4()))
    segment_id = field_str(segment, 0)
    if practitioner_id:
        practitioner.identifier = [Identifier(system="urn:interop-tools:practitioner-id", value=practitioner_id)]
        if recorder:
            recorder.record(
                practitioner.id,
                "identifier[0].value",
                hl7_location(segment_id, field_num, component=1),
                practitioner_id,
            )
    if family or given:
        name = HumanName()
        if family:
            name.family = family
            if recorder:
                recorder.record(
                    practitioner.id, "name[0].family", hl7_location(segment_id, field_num, component=2), family
                )
        if given:
            name.given = [given]
            if recorder:
                recorder.record(
                    practitioner.id, "name[0].given[0]", hl7_location(segment_id, field_num, component=3), given
                )
        practitioner.name = [name]
    # XCN.7 -> qualification.code, per the v2-to-FHIR IG's own
    # XCN[Practitioner] datatype map (bound to DegreeLicenseCertificate).
    # Every XCN-derived Practitioner in this app dropped its degree before.
    degree = field_str(segment, field_num, component=7)
    if degree:
        practitioner.qualification = [
            PractitionerQualification(
                code=CodeableConcept(coding=[Coding(system=DEGREE_SYSTEM, code=degree)])
            )
        ]
        if recorder:
            recorder.record(
                practitioner.id,
                "qualification[0].code.coding[0].code",
                hl7_location(segment_id, field_num, component=7),
                degree,
            )
    return practitioner


def build_location_from_pl(segment, field_num: int, recorder=None) -> Location | None:
    """Build a real Location resource from a PL-shaped field (facility^room^
    bed^hospital, e.g. PV1-3, PV1-6, AIL-3). Returns None when the field is
    empty. Shared for the same reason as build_practitioner_from_xcn."""
    display = location_display(segment, field_num)
    if not display:
        return None
    location = Location(id=str(uuid.uuid4()), name=display)
    if recorder:
        # location_display() joins facility (component 1) + room (component
        # 2) into one string - the source location names the whole PL field
        # rather than picking just one component, since the recorded value
        # is the joined display, not either component alone.
        segment_id = field_str(segment, 0)
        recorder.record(location.id, "name", hl7_location(segment_id, field_num), display)
    return location


def resolve_encounter_class(pv1) -> Coding:
    """PV1-2 (patient class) -> Encounter.class Coding. Shared by every
    message type that derives an Encounter from a PV1 segment (ADT's
    admit/transfer/discharge encounters, ORU's optional result-reporting
    encounter) - the mapping from HL7's I/O/E/P codes to FHIR's
    IMP/AMB/EMER/PRENC is identical regardless of what triggered the
    message."""
    patient_class = field_str(pv1, 2).strip().upper()
    class_code = _PATIENT_CLASS_MAP.get(patient_class, "AMB")
    return Coding(system=_ENCOUNTER_CLASS_SYSTEM, code=class_code)


def build_visit_identifier(pv1) -> Identifier | None:
    """PV1-19 (visit number) -> an Identifier, or None when absent. Shared
    for the same reason as resolve_encounter_class."""
    visit_number = field_str(pv1, 19)
    if not visit_number:
        return None
    return Identifier(system="urn:interop-tools:visit-number", value=visit_number)


# v3 ParticipationType, the system PV1-7's own ATND code comes from.
# adt.py and siu.py each had their own copy before this became a third
# consumer.
PARTICIPATION_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"


def build_minimal_encounter(pv1, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
    """A minimal Encounter for message types whose PV1 (when present) gives
    context rather than an admit/discharge lifecycle event - ORU's optional
    result-reporting encounter and MDM's optional document-context encounter
    both need exactly this shape. Status is honestly "unknown" rather than
    guessed, since neither message type's trigger carries any real
    lifecycle signal the way ADT's does. This was independently duplicated
    once (byte-for-byte) between oru.py and mdm.py before being extracted
    here - the same kind of silent-duplication risk build_minimal_pv1_fields
    (in app/generators/base.py) was created to avoid on the generator side."""
    encounter_id = str(uuid.uuid4())
    encounter_class = resolve_encounter_class(pv1)
    encounter = Encounter(
        id=encounter_id,
        status="unknown",
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=encounter_class,
    )
    if recorder:
        recorder.record_inferred(
            encounter_id,
            "status",
            "This Encounter's own PV1 gives result/document context, not an admit-discharge lifecycle event - no real signal exists to infer a more specific status from, so it's always \"unknown\".",
            "unknown",
        )
        recorder.record(encounter_id, "class.code", hl7_location("PV1", 2), encounter_class.code, source_value=field_str(pv1, 2))
    visit_identifier = build_visit_identifier(pv1)
    if visit_identifier:
        encounter.identifier = [visit_identifier]
        if recorder:
            recorder.record(encounter_id, "identifier[0].value", hl7_location("PV1", 19), visit_identifier.value)

    # PV1-3 -> location[1].location(Location), per the v2-to-FHIR
    # PV1[Encounter] map. This encounter is minimal in the sense of having
    # no lifecycle to infer, not in the sense of ignoring the location the
    # PV1 actually carries - which was simply dropped before. `location[].
    # status` is left unset: the IG maps PV1-3 to it too, but nothing in a
    # result or document context says whether the patient is still there.
    if extra_resources is not None:
        locations = build_location_chain_from_pl(pv1, 3, recorder=recorder)
        if locations:
            extra_resources.extend(locations)
            encounter.location = [
                EncounterLocation(
                    location=build_reference_with_optional_display(locations[0].id, location_display(pv1, 3))
                )
            ]

        # PV1-7 -> participant.individual(Practitioner), the same
        # PV1[Encounter] row ADT already follows. Dropped here for the same
        # reason PV1-3 was: "minimal" means no lifecycle to infer, not
        # ignore what the PV1 carried. XCN's id and its XCN.7 degree only
        # have somewhere to go on a real Practitioner, which is why this
        # materialises one rather than building a display string.
        attending = build_practitioner_from_xcn(pv1, 7, recorder=recorder)
        if attending is not None:
            extra_resources.append(attending)
            attending_display = person_display(pv1, 7)
            encounter.participant = [
                EncounterParticipant(
                    type=[CodeableConcept(coding=[Coding(system=PARTICIPATION_TYPE_SYSTEM, code="ATND")])],
                    individual=build_reference_with_optional_display(attending.id, attending_display),
                )
            ]
            if recorder and attending_display:
                recorder.record(
                    encounter_id, "participant[0].individual.display", hl7_location("PV1", 7), attending_display
                )
    return encounter


def build_reference_with_optional_display(resource_id: str, display: str) -> Reference:
    """A Reference to a materialized resource, with `display` omitted
    (rather than passed as an empty string) when the display text couldn't
    be resolved - FHIR's Reference.display must be a non-empty string when
    present. First needed for SIU's AIP/AIL/AIG participants (an XCN field
    with only an id, no name, would otherwise pair a real Practitioner with
    an empty display and crash), reused by MDM's TXA-9/TXA-10 originator/
    authenticator references for the identical failure mode."""
    if display:
        return Reference(reference=f"urn:uuid:{resource_id}", display=display)
    return Reference(reference=f"urn:uuid:{resource_id}")


def assemble_bundle(msh, patient: Patient, *resources: Resource, recorder=None) -> Bundle:
    """Wrap a Patient plus any number of additional resources (an Encounter,
    an Appointment, ...) into a Bundle, with MSH-derived metadata. Shared by
    every message type's mapper. `recorder` is optional; when given,
    MSH-10/MSH-7 are recorded against `bundle.id` itself (not any entry's
    resource - see app/provenance/resolver.py's own bundle.id special case),
    since these two fields live on the Bundle, not on any resource within it."""
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")
    control_id = field_str(msh, 10)
    if control_id:
        bundle.identifier = Identifier(system="urn:interop-tools:message-control-id", value=control_id)
        if recorder:
            recorder.record(bundle.id, "identifier.value", hl7_location("MSH", 10), control_id)
    message_timestamp = parse_hl7_datetime(field_str(msh, 7))
    if message_timestamp:
        bundle.timestamp = message_timestamp
        if recorder:
            recorder.record(bundle.id, "timestamp", hl7_location("MSH", 7), message_timestamp, source_value=field_str(msh, 7))
    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{patient.id}", resource=patient)]
    bundle.entry.extend(BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources)
    return bundle
