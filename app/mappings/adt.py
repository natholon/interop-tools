import uuid
from abc import abstractmethod

import hl7
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.encounter import (
    Encounter,
    EncounterHospitalization,
    EncounterLocation,
    EncounterParticipant,
)
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.reference import Reference

from app.fhir_models.builders import parse_hl7_datetime
from app.hl7.errors import MappingError, MissingSegmentError
from app.hl7.parser import field_str, require_segment
from app.mappings.base import MessageMapper
from app.mappings.common import (
    assemble_bundle,
    build_patient,
    build_visit_identifier,
    location_display,
    person_display,
    resolve_encounter_class,
)

_PARTICIPATION_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
_DISCHARGE_DISPOSITION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0112"


def _drop_evn2_period_start_fallback(encounter: Encounter, pv1) -> None:
    """build_encounter_core falls back to EVN-2 for period.start whenever
    PV1-44 is absent - correct for admission-lifecycle triggers (A01/A02/
    A04/A05/A08) where EVN-2 genuinely is the encounter's own event time,
    but wrong for cancel-trigger messages (A11/A13): EVN-2 there is when the
    *cancel* notification itself was recorded, not any real start time for
    the (cancelled) encounter, so the fallback would mislabel the
    cancel-event time as an admission start. Resets period.start to PV1-44
    only, dropping the EVN-2 fallback; clears period entirely if nothing is
    left in it."""
    if encounter.period is None:
        return
    encounter.period.start = parse_hl7_datetime(field_str(pv1, 44))
    if encounter.period.start is None and encounter.period.end is None:
        encounter.period = None


def discharge_datetime(pv1, evn) -> str | None:
    """Resolve a discharge/end datetime from PV1-45, falling back to EVN-2."""
    value = parse_hl7_datetime(field_str(pv1, 45))
    if value:
        return value
    if evn is not None:
        return parse_hl7_datetime(field_str(evn, 2))
    return None


def build_encounter_core(pv1, evn, patient_id: str, status: str) -> Encounter:
    """Shared PV1/EVN -> Encounter mapping: class, identifier, current location,
    attending participant, and admit/discharge period. `status` is supplied by
    the caller since it depends on which trigger event is being mapped."""
    encounter = Encounter(
        id=str(uuid.uuid4()),
        status=status,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=resolve_encounter_class(pv1),
    )

    visit_identifier = build_visit_identifier(pv1)
    if visit_identifier:
        encounter.identifier = [visit_identifier]

    current_location_display = location_display(pv1, 3)
    if current_location_display:
        encounter.location = [EncounterLocation(location=Reference(display=current_location_display))]

    attending_display = person_display(pv1, 7)
    if attending_display:
        encounter.participant = [
            EncounterParticipant(
                type=[CodeableConcept(coding=[Coding(system=_PARTICIPATION_TYPE_SYSTEM, code="ATND")])],
                individual=Reference(display=attending_display),
            )
        ]

    period_start = parse_hl7_datetime(field_str(pv1, 44))
    if not period_start and evn is not None:
        period_start = parse_hl7_datetime(field_str(evn, 2))
    period_end = parse_hl7_datetime(field_str(pv1, 45))
    if period_start or period_end:
        period = Period()
        if period_start:
            period.start = period_start
        if period_end:
            period.end = period_end
        encounter.period = period

    return encounter


class BaseAdtMapper(MessageMapper):
    """Shared orchestration for ADT trigger events: require MSH/PID/PV1 (EVN is
    optional), build the Patient, delegate Encounter construction to the
    subclass (the part that actually differs per trigger event), then
    assemble the Bundle."""

    message_type = "ADT"

    @abstractmethod
    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        ...

    def to_bundle(self, message: hl7.Message) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        pv1 = require_segment(message, "PV1")
        try:
            evn = require_segment(message, "EVN")
        except MissingSegmentError:
            evn = None

        patient = build_patient(pid)
        encounter = self.build_encounter(pv1, evn, patient.id)
        return assemble_bundle(msh, patient, encounter)


class AdtA01Mapper(BaseAdtMapper):
    """A01 - Admit/visit notification. Encounter is open (in-progress)."""

    trigger_event = "A01"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        return build_encounter_core(pv1, evn, patient_id, status="in-progress")


class AdtA04Mapper(BaseAdtMapper):
    """A04 - Register a patient (typically outpatient). Encounter is open,
    same as A01; kept as its own mapper so register-specific handling has
    somewhere to go later without touching A01."""

    trigger_event = "A04"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        return build_encounter_core(pv1, evn, patient_id, status="in-progress")


class AdtA02Mapper(BaseAdtMapper):
    """A02 - Transfer a patient. Adds PV1-6 (prior location) ahead of the
    current PV1-3 location as location history."""

    trigger_event = "A02"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        encounter = build_encounter_core(pv1, evn, patient_id, status="in-progress")
        prior_display = location_display(pv1, 6)
        if prior_display:
            prior_location = EncounterLocation(location=Reference(display=prior_display), status="completed")
            if encounter.location:
                for loc in encounter.location:
                    loc.status = "active"
                encounter.location = [prior_location, *encounter.location]
            else:
                encounter.location = [prior_location]
        return encounter


class AdtA03Mapper(BaseAdtMapper):
    """A03 - Discharge/end visit. Requires a discharge date/time (PV1-45 or
    EVN-2 fallback) since a finished encounter with no end time is a real
    data-correctness problem, not something to guess at silently."""

    trigger_event = "A03"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        discharge_dt = discharge_datetime(pv1, evn)
        if not discharge_dt:
            raise MappingError("ADT^A03 (discharge) requires a discharge date/time (PV1-45 or EVN-2)")

        encounter = build_encounter_core(pv1, evn, patient_id, status="finished")
        period = encounter.period or Period()
        period.end = discharge_dt
        encounter.period = period

        disposition_code = field_str(pv1, 36)
        if disposition_code:
            encounter.hospitalization = EncounterHospitalization(
                dischargeDisposition=CodeableConcept(
                    coding=[Coding(system=_DISCHARGE_DISPOSITION_SYSTEM, code=disposition_code)]
                )
            )
        return encounter


class AdtA08Mapper(BaseAdtMapper):
    """A08 - Update patient information. Carries no explicit lifecycle signal
    and this app holds no persisted encounter state to update, so status is
    inferred: finished if a discharge time is present, in-progress otherwise."""

    trigger_event = "A08"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        status = "finished" if field_str(pv1, 45) else "in-progress"
        return build_encounter_core(pv1, evn, patient_id, status=status)


class AdtA05Mapper(BaseAdtMapper):
    """A05 - Pre-admit a patient. Encounter is planned - it hasn't started
    yet, unlike A01/A04's already-open encounter."""

    trigger_event = "A05"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        return build_encounter_core(pv1, evn, patient_id, status="planned")


class AdtA11Mapper(BaseAdtMapper):
    """A11 - Cancel Admit/Visit Notification: backs out an erroneous A01/A04.
    This converter is stateless - there's no persisted prior Encounter to
    actually cancel - so status="entered-in-error" is the only way to signal
    in the output that this Encounter represents a backed-out admission
    rather than a real one. This is a deliberate choice, not IG-mandated: the
    v2-to-FHIR IG's own A11 guidance applies no special handling at all (just
    "PV1 processing creates a new Encounter"), which would make an
    A11-derived Encounter indistinguishable from a plain A01 one in the FHIR
    output - judged not useful enough for a conversion tool whose purpose is
    to make exactly this kind of information visible."""

    trigger_event = "A11"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        encounter = build_encounter_core(pv1, evn, patient_id, status="entered-in-error")
        _drop_evn2_period_start_fallback(encounter, pv1)
        return encounter


class AdtA38Mapper(BaseAdtMapper):
    """A38 - Cancel Pre-Admit: backs out an erroneous A05. Same
    entered-in-error rationale and EVN-2 period.start hazard as A11/A13 - A38
    is A05's cancel-pattern sibling the same way A11 is A01/A04's and A13 is
    A03's. No discharge disposition handling (unlike A13): a pre-admit was
    never discharged, so PV1-36 has no relevance here."""

    trigger_event = "A38"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        encounter = build_encounter_core(pv1, evn, patient_id, status="entered-in-error")
        _drop_evn2_period_start_fallback(encounter, pv1)
        return encounter


class AdtA13Mapper(BaseAdtMapper):
    """A13 - Cancel Discharge: backs out an erroneous A03. Same
    entered-in-error rationale as A11. Unlike A03, a discharge date/time is
    not required - asserting entered-in-error doesn't depend on having one -
    but discharge disposition is still populated from PV1-36 when present,
    since the message backing out a discharge will typically still carry
    the fields that were in the erroneous A03. period.end is already
    PV1-45-only via build_encounter_core (it never falls back to EVN-2 for
    period.end, regardless of trigger, so no extra handling is needed
    there) - but period.start does need correcting, same as A11, via
    _drop_evn2_period_start_fallback."""

    trigger_event = "A13"

    def build_encounter(self, pv1, evn, patient_id: str) -> Encounter:
        encounter = build_encounter_core(pv1, evn, patient_id, status="entered-in-error")
        _drop_evn2_period_start_fallback(encounter, pv1)
        disposition_code = field_str(pv1, 36)
        if disposition_code:
            encounter.hospitalization = EncounterHospitalization(
                dischargeDisposition=CodeableConcept(
                    coding=[Coding(system=_DISCHARGE_DISPOSITION_SYSTEM, code=disposition_code)]
                )
            )
        return encounter
