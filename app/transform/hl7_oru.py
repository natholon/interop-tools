"""FHIR Bundle -> HL7v2 ORU - the sixth reverse-direction slice, and the
second proof (after SIU's `Appointment`) that this architecture handles a
genuinely different FHIR shape - `DiagnosticReport` + `Observation`, with
a real *grouping* relationship (`DiagnosticReport.result[]` referencing
only its own `Observation`s) that neither ADT's Encounter nor SIU's
flat-participant-list Appointment needed to reconstruct. Originally R01
alone; **R30/R31/R32/R40 shipped as an immediate follow-up breadth pass**,
the same "one-line trigger-string change" the module's own original
docstring disclosed as a natural next step - confirmed genuinely trivial,
not just assumed: `app/mappings/oru.py::BaseOruMapper` handles all five
triggers with byte-for-byte identical forward logic (they differ only in
upstream ordering-workflow semantics this stateless converter never
modeled), so the reverse direction mirrors that exactly via a
`_BaseOruBuilder` + five one-line `trigger_event` subclasses, the same
`_BaseAdtBuilder` shape `hl7_adt.py` already established for its own
five-trigger breadth pass.

Reverses `app/mappings/oru.py::build_observation`/`build_diagnostic_report`
field-for-field: `OBR-4`/`-7`/`-8`/`-22`/`-25` (code/effective timing/
issued/status, CWE + status map both reused in reverse from
`app/transform/hl7_common.py`), and one `OBX` per `Observation` referenced
by that report's own `DiagnosticReport.result[]` - **not** a flat scan of
every `Observation` in the Bundle, mirroring `group_segments_by_leader`'s
own forward-direction grouping guarantee (a report only emits `OBX`
segments for its own results, never another report's). `OBX-2`/`-3`/`-5`/
`-6`/`-7`/`-8`/`-11`/`-14`/`-16` reverse `Observation.value[x]`/`.code`/
`.referenceRange[0].text`/`.interpretation`/`.status`/
`.effectiveDateTime`/`.performer`.

**A real, disclosed round-trip fidelity gap specific to this slice**:
`Observation.status`'s reverse can't always recover the exact original
OBX-11 code - `_RESULT_STATUS_MAP`'s forward direction maps *two* distinct
HL7 codes (`"D"`/`"W"`) to the identical FHIR `"entered-in-error"` status,
so the reverse table picks one disclosed representative (`"D"`) rather
than guessing which of the two a given Observation originally carried -
the same "can't always recover a many-to-one forward mapping's original
input" limitation `edi_271.py`'s own `.disposition` free-text gap already
discloses for a different field. `OBR-2`/`-3` (placer/filler numbers) and
`OBX-1`'s own semantic value have no FHIR-side home at all (the forward
mapper never reads `DiagnosticReport.identifier`/`Observation.identifier`
for these - neither resource type gets an `.identifier` from OBR/OBX at
all), so `OBX-1` is regenerated as a simple per-report sequence number and
`OBR-2`/`-3` are left empty, the same "no source field, disclosed
placeholder or omission" precedent every earlier slice already
established for MSH-3/4/5/6 and similar."""

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_ts
from app.transform.hl7_common import build_minimal_pv1, build_msh, build_pid, reverse_cwe

# Reverse of app/mappings/oru.py::_RESULT_STATUS_MAP - "D" is the disclosed
# representative for "entered-in-error" (that FHIR status also maps back
# from HL7's own "W", but the reverse can't recover which of the two a
# given resource originally carried).
_STATUS_TO_RESULT_CODE = {
    "amended": "A",
    "corrected": "C",
    "entered-in-error": "D",
    "final": "F",
    "preliminary": "P",
    "cancelled": "X",
}
_DEFAULT_RESULT_CODE = "F"


def _reverse_status(status: str | None) -> str:
    if not status:
        return _DEFAULT_RESULT_CODE
    return _STATUS_TO_RESULT_CODE.get(status, _DEFAULT_RESULT_CODE)


def _build_obx_value(observation) -> tuple[str, str, str]:
    """Returns (OBX-2 value type, OBX-5 value, OBX-6 units)."""
    if observation.valueQuantity is not None:
        value = observation.valueQuantity.value
        return "NM", str(value), observation.valueQuantity.unit or ""
    if observation.valueCodeableConcept is not None:
        return "CWE", reverse_cwe(observation.valueCodeableConcept), ""
    if observation.valueDateTime is not None:
        return "DTM", format_hl7_ts(observation.valueDateTime), ""
    if observation.valueString is not None:
        return "ST", observation.valueString, ""
    return "", "", ""


def _build_obx(index: int, observation, practitioners_by_id: dict) -> str:
    value_type, value, units = _build_obx_value(observation)
    fields: dict[int, str] = {1: str(index), 11: _reverse_status(observation.status)}
    if value_type:
        fields[2] = value_type
    code = reverse_cwe(observation.code)
    if code:
        fields[3] = code
    if value:
        fields[5] = value
    if units:
        fields[6] = units
    if observation.referenceRange:
        text = observation.referenceRange[0].text
        if text:
            fields[7] = text
    if observation.interpretation and observation.interpretation[0].coding:
        code_value = observation.interpretation[0].coding[0].code
        if code_value:
            fields[8] = code_value
    if observation.effectiveDateTime:
        fields[14] = format_hl7_ts(observation.effectiveDateTime)

    if observation.performer:
        # OBX-16 (Responsible Observer) - reverse via the referenced
        # Practitioner's own identifier/name, the same XCN shape AIP-3
        # already uses.
        performer_id = observation.performer[0].reference.removeprefix("urn:uuid:")
        practitioner = practitioners_by_id.get(performer_id)
        if practitioner is not None:
            identifier = practitioner.identifier[0].value if practitioner.identifier else ""
            name = practitioner.name[0] if practitioner.name else None
            family = (name.family or "") if name else ""
            given = name.given[0] if name and name.given else ""
            fields[16] = f"{identifier}^{family}^{given}"

    return segment("OBX", fields, 16)


def _build_obr(index: int, report) -> str:
    fields: dict[int, str] = {1: str(index), 25: _reverse_status(report.status)}
    code = reverse_cwe(report.code)
    if code:
        fields[4] = code
    if report.effectivePeriod is not None:
        if report.effectivePeriod.start:
            fields[7] = format_hl7_ts(report.effectivePeriod.start)
        if report.effectivePeriod.end:
            fields[8] = format_hl7_ts(report.effectivePeriod.end)
    elif report.effectiveDateTime:
        fields[7] = format_hl7_ts(report.effectiveDateTime)
    if report.issued:
        fields[22] = format_hl7_ts(report.issued)
    return segment("OBR", fields, 25)


class _BaseOruBuilder(MessageBuilder):
    trigger_event: str

    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError(f"Bundle has no Patient resource - cannot build an ORU^{self.trigger_event} message")
        reports = find_resources(bundle, "DiagnosticReport")
        if not reports:
            raise MappingError(
                f"Bundle has no DiagnosticReport resource - cannot build an ORU^{self.trigger_event} message"
            )
        encounter = find_resource(bundle, "Encounter")
        observations_by_id = {o.id: o for o in find_resources(bundle, "Observation")}
        practitioners_by_id = {p.id: p for p in find_resources(bundle, "Practitioner")}

        msh, _msh_dt = build_msh(bundle, "ORU", self.trigger_event)
        pv1 = build_minimal_pv1(encounter)
        pid = build_pid(patient)

        segments = [msh]
        if pv1:
            segments.append(pv1)
        segments.append(pid)

        for report_index, report in enumerate(reports, start=1):
            segments.append(_build_obr(report_index, report))
            for obx_index, result_ref in enumerate(report.result or [], start=1):
                observation_id = result_ref.reference.removeprefix("urn:uuid:")
                observation = observations_by_id.get(observation_id)
                if observation is None:
                    continue
                segments.append(_build_obx(obx_index, observation, practitioners_by_id))

        return "\r".join(segments) + "\r"


class OruR01Builder(_BaseOruBuilder):
    trigger_event = "R01"


class OruR30Builder(_BaseOruBuilder):
    trigger_event = "R30"


class OruR31Builder(_BaseOruBuilder):
    trigger_event = "R31"


class OruR32Builder(_BaseOruBuilder):
    trigger_event = "R32"


class OruR40Builder(_BaseOruBuilder):
    trigger_event = "R40"
