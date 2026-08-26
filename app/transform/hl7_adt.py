"""FHIR Bundle -> HL7v2 ADT (A01/A02/A03/A04/A05/A08/A11/A13/A38).

Reverses `app/mappings/common.py::build_patient` and
`app/mappings/adt.py::build_encounter_core` field-for-field, using each
one's exact field positions and component shapes rather than re-deriving
them - so a change to the forward mapping has an obvious counterpart here.
Shared PID/MSH construction lives in `hl7_common.py`.

**All triggers share one `_build_pv1`/`_build_evn`.** `_build_pv1` reflects
whatever the source `Encounter` actually contains - PV1-6 when a
`status="completed"` location is present, PV1-36 when `.hospitalization`
is, PV1-44/45 from `.period` - rather than branching on trigger. None of
the forward direction's per-trigger differences depend on a FHIR field the
reverse would not already be populating: A08's status inference, for one,
comes back out consistently with no A08-specific code, since re-parsing a
message with PV1-45 re-infers `status="finished"` on its own.

**A03 is the one exception**: it requires a discharge time to exist, so
`AdtA03Builder` overrides `_validate` to raise the way `AdtA03Mapper` does,
rather than emitting an A03 with no PV1-45.

Disclosed round-trip fidelity gaps:
- MSH-3/4/5/6 (sending/receiving application and facility) have no FHIR
  home at all, so they get fixed placeholder values. Empty is not an
  option - HL7v2 requires them.
- `location_display` (PV1-3/PV1-6) and `person_display` (PV1-7) collapse
  several components into one display string on the way in. Reversing a
  display string into components is ambiguous, so both take the same
  deliberate choice: the whole string goes in component 1, the rest stay
  empty, rather than guessing at a split point."""

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_ts
from app.transform.hl7_common import (
    CLASS_TO_PATIENT_CLASS,
    build_msh,
    build_pid,
    reverse_pl_field,
    reverse_pv1_doctor_fields,
    reverse_pv1_encounter_fields,
)



def _build_pv1(encounter, locations_by_id: dict | None = None, practitioners_by_id: dict | None = None) -> str:
    locations_by_id = locations_by_id or {}
    practitioners_by_id = practitioners_by_id or {}
    fields: dict[int, str] = {1: "1"}
    if encounter is None:
        return segment("PV1", fields, 45)

    if encounter.class_fhir and encounter.class_fhir.code:
        fields[2] = CLASS_TO_PATIENT_CLASS.get(encounter.class_fhir.code, "O")

    if encounter.location:
        # A02's own prior-location entry carries status="completed" (see
        # app/mappings/adt.py::AdtA02Mapper) - PV1-6, not PV1-3. Every
        # other trigger's own single location entry has no such marker and
        # is the current location - PV1-3.
        prior = next((loc for loc in encounter.location if loc.status == "completed"), None)
        current = next((loc for loc in encounter.location if loc.status != "completed"), None)
        if prior is not None and prior.location:
            fields[6] = reverse_pl_field(prior.location, locations_by_id)
        if current is not None and current.location:
            fields[3] = reverse_pl_field(current.location, locations_by_id)

    fields.update(reverse_pv1_doctor_fields(encounter, practitioners_by_id))
    fields.update(reverse_pv1_encounter_fields(encounter))

    if encounter.identifier:
        visit_number = encounter.identifier[0].value
        if visit_number:
            fields[19] = visit_number

    if encounter.hospitalization and encounter.hospitalization.dischargeDisposition:
        coding = encounter.hospitalization.dischargeDisposition.coding
        if coding and coding[0].code:
            fields[36] = coding[0].code

    if encounter.period:
        if encounter.period.start:
            fields[44] = format_hl7_ts(encounter.period.start)
        if encounter.period.end:
            fields[45] = format_hl7_ts(encounter.period.end)

    return segment("PV1", fields, 45)


class _BaseAdtBuilder(MessageBuilder):
    trigger_event: str

    def _validate(self, encounter) -> None:
        """Overridden by AdtA03Builder - every other trigger has no
        structural requirement beyond "a Patient exists"."""

    def _build_evn(self, encounter, msh_dt: str) -> str:
        evn_dt = msh_dt
        if encounter is not None and encounter.period and encounter.period.start:
            evn_dt = format_hl7_ts(encounter.period.start) or msh_dt
        return segment("EVN", {1: self.trigger_event, 2: evn_dt}, 2)

    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError(f"Bundle has no Patient resource - cannot build an ADT^{self.trigger_event} message")
        encounter = find_resource(bundle, "Encounter")
        self._validate(encounter)

        msh, msh_dt = build_msh(bundle, "ADT", self.trigger_event)
        evn = self._build_evn(encounter, msh_dt)
        pid = build_pid(patient)
        locations_by_id = {loc.id: loc for loc in find_resources(bundle, "Location")}
        practitioners_by_id = {p.id: p for p in find_resources(bundle, "Practitioner")}
        pv1 = _build_pv1(encounter, locations_by_id, practitioners_by_id)

        return "\r".join([msh, evn, pid, pv1]) + "\r"


class AdtA01Builder(_BaseAdtBuilder):
    trigger_event = "A01"


class AdtA02Builder(_BaseAdtBuilder):
    trigger_event = "A02"


class AdtA04Builder(_BaseAdtBuilder):
    trigger_event = "A04"


class AdtA05Builder(_BaseAdtBuilder):
    trigger_event = "A05"


class AdtA08Builder(_BaseAdtBuilder):
    trigger_event = "A08"


class AdtA03Builder(_BaseAdtBuilder):
    trigger_event = "A03"

    def _validate(self, encounter) -> None:
        if encounter is None or not encounter.period or not encounter.period.end:
            raise MappingError(
                "ADT^A03 (discharge) requires an Encounter with period.end - "
                "cannot build a discharge message with no discharge time"
            )


class AdtA11Builder(_BaseAdtBuilder):
    trigger_event = "A11"


class AdtA38Builder(_BaseAdtBuilder):
    trigger_event = "A38"


class AdtA13Builder(_BaseAdtBuilder):
    trigger_event = "A13"
