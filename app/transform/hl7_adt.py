"""FHIR Bundle -> HL7v2 ADT - the first reverse-direction slice in this app
(A01 alone, originally in its own hl7_adt_a01.py module, chosen for the
same reason ADT^A01 was this app's very first forward-direction slice: the
simplest, best-understood shape). Renamed/expanded to app/transform/
hl7_adt.py, mirroring app/mappings/adt.py's own single-file-per-message-
type shape, once A02/A03/A04/A05/A08 became real second/third/... slices
of the identical Patient+Encounter shape - this project's own established
"When adding a new trigger event for an already-supported message type,
add a subclass in that type's module rather than a new file" rule, applied
to the reverse direction for the first time.

Reverses app/mappings/common.py::build_patient/app/mappings/adt.py::
build_encounter_core field-for-field, using each one's own exact field
positions/component shapes - not re-deriving them independently, so a
future change to the forward mapping's field shape has an obvious reverse
counterpart to update. Shared PID/MSH construction lives in
app/transform/hl7_common.py (promoted there once app/transform/hl7_siu.py
became a second real consumer of the identical PID reversal). **A real,
disclosed round-trip fidelity gap, not a bug**: several HL7v2 fields the
forward mapper reads have no FHIR-side home at all (MSH-3/4/5/6 sending/
receiving application-facility, PV1-2's full patient-class nuance beyond
the four codes FHIR's ActEncounterCode binding covers), and two forward-
mapped fields are collapsed into a single *display string* rather than
kept as separate components (app.mappings.common.location_display for
PV1-3/PV1-6, person_display for PV1-7) - reversing a display string back
into structured components is inherently lossy/ambiguous, so this builder
makes the same one deliberate choice both fields share: place the whole
display string in the field's first component and leave the rest empty,
rather than guessing at a split point. Fields with no FHIR-side source at
all get a fixed, disclosed placeholder value (matching this app's own
generator's own "always produce valid output" precedent) rather than
being left empty, since MSH's own structural fields are required by the
HL7v2 standard itself.

**All five triggers this module now covers (A01/A02/A04/A05/A08) share one
`_build_pv1`/`_build_evn` implementation, not five separate ones** - a
deliberate simplification found once A02/A03/A08's own reverse
requirements were actually worked out: `_build_pv1` reflects whatever the
source `Encounter` resource actually contains (PV1-6 prior location
whenever a `status="completed"` location entry is present, PV1-36
discharge disposition whenever `.hospitalization` is present, PV1-44/45
whenever `.period.start`/`.end` are present) rather than branching on
which trigger is being built - since none of A01/A02/A04/A05/A08's own
forward-direction differences turn out to depend on a FHIR field the
reverse direction wouldn't already be populating anyway (A08's own status
inference, for example, is automatically consistent on the way back out:
re-parsing a message with PV1-45 present will re-infer `status="finished"`
without this module needing to know that rule exists). **A03 is the one
genuine exception**: it requires a discharge time to exist at all (the
forward mapper's own `AdtA03Mapper` raises without one), so
`AdtA03Builder` overrides `_validate` to raise the same way, rather than
silently emitting an A03 message with no PV1-45."""

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, format_hl7_ts
from app.transform.hl7_common import build_msh, build_pid

# Reverse of app/mappings/common.py::_PATIENT_CLASS_MAP - "AMB" is the
# fallback on the forward side, so it's also the safest default here for
# an Encounter.class code this table doesn't recognize.
_CLASS_TO_PATIENT_CLASS = {"IMP": "I", "AMB": "O", "EMER": "E", "PRENC": "P"}


def _build_pv1(encounter) -> str:
    fields: dict[int, str] = {1: "1"}
    if encounter is None:
        return segment("PV1", fields, 45)

    if encounter.class_fhir and encounter.class_fhir.code:
        fields[2] = _CLASS_TO_PATIENT_CLASS.get(encounter.class_fhir.code, "O")

    if encounter.location:
        # A02's own prior-location entry carries status="completed" (see
        # app/mappings/adt.py::AdtA02Mapper) - PV1-6, not PV1-3. Every
        # other trigger's own single location entry has no such marker and
        # is the current location - PV1-3.
        prior = next((loc for loc in encounter.location if loc.status == "completed"), None)
        current = next((loc for loc in encounter.location if loc.status != "completed"), None)
        if prior is not None and prior.location and prior.location.display:
            fields[6] = prior.location.display
        if current is not None and current.location and current.location.display:
            fields[3] = current.location.display

    if encounter.participant:
        display = encounter.participant[0].individual.display if encounter.participant[0].individual else None
        if display:
            fields[7] = display

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
        pv1 = _build_pv1(encounter)

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
