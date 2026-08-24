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
    PARTICIPATION_TYPE_SYSTEM,
    assemble_bundle,
    build_location_chain_from_pl,
    build_patient,
    build_practitioner_from_xcn,
    build_reference_with_optional_display,
    build_visit_identifier,
    location_display,
    person_display,
    resolve_encounter_class,
)
from app.provenance.location import hl7_location

_DISCHARGE_DISPOSITION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0112"


def _drop_evn2_period_start_fallback(encounter: Encounter, pv1, recorder=None) -> None:
    """build_encounter_core falls back to EVN-2 for period.start whenever
    PV1-44 is absent - correct for admission-lifecycle triggers (A01/A02/
    A04/A05/A08) where EVN-2 genuinely is the encounter's own event time,
    but wrong for cancel-trigger messages (A11/A13): EVN-2 there is when the
    *cancel* notification itself was recorded, not any real start time for
    the (cancelled) encounter, so the fallback would mislabel the
    cancel-event time as an admission start. Resets period.start to PV1-44
    only, dropping the EVN-2 fallback; clears period entirely if nothing is
    left in it.

    `recorder` corrects (not duplicates) whatever build_encounter_core
    already recorded for period.start, since this function runs strictly
    after it and can change or remove that field's real value - see
    app/provenance/recorder.py's own module docstring for why `record()`
    is last-write-wins rather than append-only, precisely for this case."""
    if encounter.period is None:
        return
    encounter.period.start = parse_hl7_datetime(field_str(pv1, 44))
    if recorder:
        if encounter.period.start:
            recorder.record(
                encounter.id, "period.start", hl7_location("PV1", 44), encounter.period.start, source_value=field_str(pv1, 44)
            )
        else:
            recorder.forget(encounter.id, "period.start")
    if encounter.period.start is None and encounter.period.end is None:
        encounter.period = None
        if recorder:
            recorder.forget_prefix(encounter.id, "period.")


def discharge_datetime(pv1, evn) -> str | None:
    """Resolve a discharge/end datetime from PV1-45, falling back to EVN-2."""
    value = parse_hl7_datetime(field_str(pv1, 45))
    if value:
        return value
    if evn is not None:
        return parse_hl7_datetime(field_str(evn, 2))
    return None


def build_encounter_core(
    pv1, evn, patient_id: str, status: str, status_reason: str, recorder=None, extra_resources=None
) -> Encounter:
    """Shared PV1/EVN -> Encounter mapping: class, identifier, current location,
    attending participant, and admit/discharge period. `status`/`status_reason`
    are supplied by the caller since both depend on which trigger event is
    being mapped - `status` is never read directly from a field's own value
    for any ADT trigger (even A08's "presence of PV1-45" check is about
    whether the field is populated, not what it says), so it's always
    recorded as `derivation="inferred"`, with each trigger supplying its
    own specific `status_reason` string."""
    encounter_id = str(uuid.uuid4())
    encounter_class = resolve_encounter_class(pv1)
    encounter = Encounter(
        id=encounter_id,
        status=status,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        class_fhir=encounter_class,
    )
    if recorder:
        recorder.record_inferred(encounter_id, "status", status_reason, status)
        recorder.record(encounter_id, "class.code", hl7_location("PV1", 2), encounter_class.code, source_value=field_str(pv1, 2))

    visit_identifier = build_visit_identifier(pv1)
    if visit_identifier:
        encounter.identifier = [visit_identifier]
        if recorder:
            recorder.record(encounter_id, "identifier[0].value", hl7_location("PV1", 19), visit_identifier.value)

    # PV1-3 -> a chain of real Location resources (one per populated PL
    # component, linked by .partOf), per the v2-to-FHIR PL[Location] map.
    # Encounter.location references the most granular one; the display
    # string rides alongside for readers who don't resolve references.
    current_locations = build_location_chain_from_pl(pv1, 3, recorder=recorder)
    if current_locations:
        if extra_resources is not None:
            extra_resources.extend(current_locations)
        encounter.location = [
            EncounterLocation(
                location=build_reference_with_optional_display(
                    current_locations[0].id, location_display(pv1, 3)
                )
            )
        ]

    # PV1-7 -> participant.individual(Practitioner), per the v2-to-FHIR
    # PV1[Encounter] map. This used to build a display string only, so
    # everything XCN carries beyond family/given - the id, and the degree
    # XCN.7 maps to qualification.code - had nowhere to go and was dropped.
    # A real Practitioner is also more useful to a consumer, which is the
    # same reasoning SIU's AIP and ORU's OBX-16 already follow.
    attending = build_practitioner_from_xcn(pv1, 7, recorder=recorder)
    attending_display = person_display(pv1, 7)
    if attending is not None:
        if extra_resources is not None:
            extra_resources.append(attending)
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

    period_start = parse_hl7_datetime(field_str(pv1, 44))
    period_start_location = hl7_location("PV1", 44)
    period_start_raw = field_str(pv1, 44)
    if not period_start and evn is not None:
        period_start = parse_hl7_datetime(field_str(evn, 2))
        period_start_location = hl7_location("EVN", 2)
        period_start_raw = field_str(evn, 2)
    period_end = parse_hl7_datetime(field_str(pv1, 45))
    if period_start or period_end:
        period = Period()
        if period_start:
            period.start = period_start
            if recorder:
                recorder.record(encounter_id, "period.start", period_start_location, period_start, source_value=period_start_raw)
        if period_end:
            period.end = period_end
            if recorder:
                recorder.record(encounter_id, "period.end", hl7_location("PV1", 45), period_end, source_value=field_str(pv1, 45))
        encounter.period = period

    return encounter


class BaseAdtMapper(MessageMapper):
    """Shared orchestration for ADT trigger events: require MSH/PID/PV1 (EVN is
    optional), build the Patient, delegate Encounter construction to the
    subclass (the part that actually differs per trigger event), then
    assemble the Bundle."""

    message_type = "ADT"

    @abstractmethod
    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        ...

    def to_bundle(self, message: hl7.Message, recorder=None) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        pv1 = require_segment(message, "PV1")
        try:
            evn = require_segment(message, "EVN")
        except MissingSegmentError:
            evn = None

        patient = build_patient(pid, recorder=recorder)
        # Locations built from PV1-3/PV1-6 are separate top-level
        # resources the Encounter only references, so the subclass
        # appends them here for assemble_bundle to pick up.
        extra_resources: list = []
        encounter = self.build_encounter(pv1, evn, patient.id, recorder=recorder, extra_resources=extra_resources)
        return assemble_bundle(msh, patient, encounter, *extra_resources, recorder=recorder)


class AdtA01Mapper(BaseAdtMapper):
    """A01 - Admit/visit notification. Encounter is open (in-progress)."""

    trigger_event = "A01"

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        return build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="in-progress",
            status_reason="ADT^A01 (Admit/visit notification) always maps to status=in-progress; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )


class AdtA04Mapper(BaseAdtMapper):
    """A04 - Register a patient (typically outpatient). Encounter is open,
    same as A01; kept as its own mapper so register-specific handling has
    somewhere to go later without touching A01."""

    trigger_event = "A04"

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        return build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="in-progress",
            status_reason="ADT^A04 (Register) always maps to status=in-progress, the same as A01; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )


class AdtA02Mapper(BaseAdtMapper):
    """A02 - Transfer a patient. Adds PV1-6 (prior location) ahead of the
    current PV1-3 location as location history."""

    trigger_event = "A02"

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        encounter = build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="in-progress",
            status_reason="ADT^A02 (Transfer) always maps to status=in-progress; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )
        prior_locations = build_location_chain_from_pl(pv1, 6, recorder=recorder)
        if prior_locations:
            if extra_resources is not None:
                extra_resources.extend(prior_locations)
            prior_location = EncounterLocation(
                location=build_reference_with_optional_display(
                    prior_locations[0].id, location_display(pv1, 6)
                ),
                status="completed",
            )
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

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        discharge_dt = discharge_datetime(pv1, evn)
        if not discharge_dt:
            raise MappingError("ADT^A03 (discharge) requires a discharge date/time (PV1-45 or EVN-2)")

        encounter = build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="finished",
            status_reason="ADT^A03 (Discharge) always maps to status=finished; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )
        period = encounter.period or Period()
        period.end = discharge_dt
        encounter.period = period
        if recorder:
            # discharge_datetime() prefers PV1-45, falling back to EVN-2 - re-derive
            # which one actually fired (the function itself doesn't say) so the
            # correction below points at the real source, not always PV1-45.
            pv1_45_raw = field_str(pv1, 45)
            if pv1_45_raw:
                recorder.record(encounter.id, "period.end", hl7_location("PV1", 45), discharge_dt, source_value=pv1_45_raw)
            elif evn is not None:
                recorder.record(
                    encounter.id, "period.end", hl7_location("EVN", 2), discharge_dt, source_value=field_str(evn, 2)
                )

        disposition_code = field_str(pv1, 36)
        if disposition_code:
            encounter.hospitalization = EncounterHospitalization(
                dischargeDisposition=CodeableConcept(
                    coding=[Coding(system=_DISCHARGE_DISPOSITION_SYSTEM, code=disposition_code)]
                )
            )
            if recorder:
                recorder.record(
                    encounter.id,
                    "hospitalization.dischargeDisposition.coding[0].code",
                    hl7_location("PV1", 36),
                    disposition_code,
                )
        return encounter


class AdtA08Mapper(BaseAdtMapper):
    """A08 - Update patient information. Carries no explicit lifecycle signal
    and this app holds no persisted encounter state to update, so status is
    inferred: finished if a discharge time is present, in-progress otherwise."""

    trigger_event = "A08"

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        status = "finished" if field_str(pv1, 45) else "in-progress"
        return build_encounter_core(
            pv1,
            evn,
            patient_id,
            status=status,
            status_reason="Inferred from whether PV1-45 (discharge date/time) is present, not its value: finished if present, in-progress otherwise.",
            recorder=recorder,
            extra_resources=extra_resources,
        )


class AdtA05Mapper(BaseAdtMapper):
    """A05 - Pre-admit a patient. Encounter is planned - it hasn't started
    yet, unlike A01/A04's already-open encounter."""

    trigger_event = "A05"

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        return build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="planned",
            status_reason="ADT^A05 (Pre-admit) always maps to status=planned; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )


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

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        encounter = build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="entered-in-error",
            status_reason="ADT^A11 (Cancel Admit) always maps to status=entered-in-error, a deliberate choice to make a backed-out admission visible; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )
        _drop_evn2_period_start_fallback(encounter, pv1, recorder=recorder)
        return encounter


class AdtA38Mapper(BaseAdtMapper):
    """A38 - Cancel Pre-Admit: backs out an erroneous A05. Same
    entered-in-error rationale and EVN-2 period.start hazard as A11/A13 - A38
    is A05's cancel-pattern sibling the same way A11 is A01/A04's and A13 is
    A03's. No discharge disposition handling (unlike A13): a pre-admit was
    never discharged, so PV1-36 has no relevance here."""

    trigger_event = "A38"

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        encounter = build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="entered-in-error",
            status_reason="ADT^A38 (Cancel Pre-Admit) always maps to status=entered-in-error, the same rationale as A11; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )
        _drop_evn2_period_start_fallback(encounter, pv1, recorder=recorder)
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

    def build_encounter(self, pv1, evn, patient_id: str, recorder=None, extra_resources=None) -> Encounter:
        encounter = build_encounter_core(
            pv1,
            evn,
            patient_id,
            status="entered-in-error",
            status_reason="ADT^A13 (Cancel Discharge) always maps to status=entered-in-error, the same rationale as A11; not read from any PV1 field.",
            recorder=recorder,
            extra_resources=extra_resources,
        )
        _drop_evn2_period_start_fallback(encounter, pv1, recorder=recorder)
        disposition_code = field_str(pv1, 36)
        if disposition_code:
            encounter.hospitalization = EncounterHospitalization(
                dischargeDisposition=CodeableConcept(
                    coding=[Coding(system=_DISCHARGE_DISPOSITION_SYSTEM, code=disposition_code)]
                )
            )
            if recorder:
                recorder.record(
                    encounter.id,
                    "hospitalization.dischargeDisposition.coding[0].code",
                    hl7_location("PV1", 36),
                    disposition_code,
                )
        return encounter
