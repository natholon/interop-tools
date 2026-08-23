"""Plan of Treatment section (templateId 2.16.840.1.113883.10.20.22.2.10,
titled "Plan of Care" on History and Physical - one shared template, not
two) -> DocumentReference+Binary (the narrative) plus one `CarePlan` with
one `.activity[]` per recognized planned entry.

**No C-CDA on FHIR page covers this section**, and the target resource is
not an obvious fit either: C-CDA allows a wide variety of entry classes
here, most meaning "an activity intended, not yet done".
**`CarePlan.activity[]` is the disclosed choice**: it is designed to hold a
heterogeneous list of planned activities without forcing each into its own
resource, and `CarePlanActivityDetail.kind` exists to hint at what an item
would materialize as without building it.

Every recognized shape carries the same code/statusCode/effectiveTime, so
one extraction serves them all; `_RECOGNIZED_ENTRY_SHAPES` maps each to its
wrapper element and its `kind`. Note two shapes share the `act` tag and two
share `substanceAdministration`, so dispatch is by templateId, not tag.

**`kind` is a judgement call, not a mapping.** The value set is fixed
(Appointment, CommunicationRequest, DeviceRequest, MedicationRequest,
NutritionOrder, Task, ServiceRequest, VisionPrescription) and no crosswalk
exists. Planned Encounter -> Appointment, Planned Medication/Immunization
Activity -> MedicationRequest and Instruction -> CommunicationRequest each
have one obvious counterpart. Everything else keeps the generic
ServiceRequest, including **Planned Supply**: the value set has no
SupplyRequest, and a C-CDA supply covers both durable equipment and
medication, so DeviceRequest would be right only half the time. The code,
status and schedule are carried either way; only the hint is coarse.

`.status`/`.intent` are likewise self-derived: the CarePlan is fixed
`status="active"`/`intent="plan"`, since nothing here says whether a plan
is still current. Per-activity `.status` comes from the entry's own
`statusCode` (not moodCode, which is uniformly RQO and carries no
distinction), falling back to `"unknown"`.

Scope limit: Planned Coverage (...22.4.129) is not recognized - it plans
insurance coverage rather than a clinical activity, and none of the `kind`
codes describes one."""

import uuid

from fhir.resources.R4B.careplan import CarePlan, CarePlanActivity, CarePlanActivityDetail
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.reference import Reference

from app.cda.common import build_codeable_concept_from_cd, effective_time_location, parse_partial_ts
from app.cda.narrative_sections import PLAN_OF_TREATMENT_TEMPLATE_ID, build_narrative_document_reference
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds
from app.provenance.location import xpath_location

SECTION_TEMPLATE_ID = PLAN_OF_TREATMENT_TEMPLATE_ID
PLANNED_OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.44"
PLANNED_PROCEDURE_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.41"
PLANNED_ENCOUNTER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.40"
PLANNED_MEDICATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.42"
PLANNED_IMMUNIZATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.120"
PLANNED_SUPPLY_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.43"
PLANNED_INTERVENTION_ACT_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.146"
INSTRUCTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.20"

_DEFAULT_ACTIVITY_KIND = "ServiceRequest"

# Disclosed, self-derived - see module docstring for why no official
# crosswalk exists to verify this against.
STATUS_MAP = {
    "active": "scheduled",
    "completed": "completed",
    "cancelled": "cancelled",
    "aborted": "cancelled",
    "suspended": "on-hold",
    "held": "on-hold",
}
_DEFAULT_STATUS = "unknown"

# (entry_tag, templateId, CarePlanActivityDetail.kind). Every wrapper
# element name and templateId was read off the published C-CDA
# StructureDefinitions; all of them carry the same code/statusCode/
# effectiveTime shape, which is why one extraction serves them all.
#
# `kind` is a hint at what a planned item *would* materialize as. The
# value set is fixed (Appointment, CommunicationRequest, DeviceRequest,
# MedicationRequest, NutritionOrder, Task, ServiceRequest,
# VisionPrescription) and no IG crosswalk exists, so three of these are
# unambiguous and three are disclosed judgement calls:
#   - Planned Encounter -> Appointment, Planned Medication/Immunization
#     Activity -> MedicationRequest, Instruction -> CommunicationRequest
#     each have one obvious counterpart.
#   - Planned Observation/Procedure/Intervention Act keep the generic
#     ServiceRequest default.
#   - **Planned Supply also keeps ServiceRequest**: the value set has no
#     SupplyRequest, and a C-CDA supply covers both durable equipment and
#     medication, so DeviceRequest would be right only half the time. The
#     code, status and schedule are still carried; only the hint is coarse.
_RECOGNIZED_ENTRY_SHAPES = (
    ("observation", PLANNED_OBSERVATION_TEMPLATE_ID, _DEFAULT_ACTIVITY_KIND),
    ("procedure", PLANNED_PROCEDURE_TEMPLATE_ID, _DEFAULT_ACTIVITY_KIND),
    ("encounter", PLANNED_ENCOUNTER_TEMPLATE_ID, "Appointment"),
    ("substanceAdministration", PLANNED_MEDICATION_TEMPLATE_ID, "MedicationRequest"),
    ("substanceAdministration", PLANNED_IMMUNIZATION_TEMPLATE_ID, "MedicationRequest"),
    ("supply", PLANNED_SUPPLY_TEMPLATE_ID, _DEFAULT_ACTIVITY_KIND),
    ("act", PLANNED_INTERVENTION_ACT_TEMPLATE_ID, _DEFAULT_ACTIVITY_KIND),
    ("act", INSTRUCTION_TEMPLATE_ID, "CommunicationRequest"),
)


def _resolve_status(element) -> str:
    status_element = find_child(element, "statusCode")
    code = (status_element.get("code") or "").strip().lower() if status_element is not None else ""
    return STATUS_MAP.get(code, _DEFAULT_STATUS)


def _build_activity_detail(
    planned_element, entry_tag: str, index: int, kind: str = _DEFAULT_ACTIVITY_KIND,
    resource_id: str | None = None, recorder=None,
):
    code_element = find_child(planned_element, "code")
    code = build_codeable_concept_from_cd(code_element)
    if code is None:
        return None

    status = _resolve_status(planned_element)
    detail = CarePlanActivityDetail(code=code, status=status, kind=kind)
    entry_base = xpath_location(f"entry[{index}]", entry_tag)

    if recorder and resource_id:
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(
                resource_id, f"activity[{index}].detail.code.coding[0].code", f"{entry_base}/code/@code", code_value
            )
        if display_value:
            recorder.record(
                resource_id,
                f"activity[{index}].detail.code.coding[0].display",
                f"{entry_base}/code/@displayName",
                display_value,
            )
        status_element = find_child(planned_element, "statusCode")
        raw_status = status_element.get("code") if status_element is not None else None
        if raw_status and raw_status.strip().lower() in STATUS_MAP:
            recorder.record(resource_id, f"activity[{index}].detail.status", f"{entry_base}/statusCode/@code", status)
        else:
            recorder.record_inferred(
                resource_id,
                f"activity[{index}].detail.status",
                f'statusCode was absent or not one of the disclosed recognized codes - defaults to "{_DEFAULT_STATUS}".',
                status,
            )
        recorder.record_inferred(
            resource_id,
            f"activity[{index}].detail.kind",
            f'Chosen from the entry\'s own template ("{entry_tag}"), not read from a source field - '
            "CarePlanActivityKind is a hint at what this planned item would materialize as, and no IG "
            "crosswalk defines it.",
            kind,
        )

    effective_time = find_child(planned_element, "effectiveTime")
    low, high = ivl_ts_bounds(effective_time)
    low_dt = parse_partial_ts(low)
    high_dt = parse_partial_ts(high)
    if low_dt and high_dt and low_dt != high_dt:
        detail.scheduledPeriod = Period(start=low_dt, end=high_dt)
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"activity[{index}].detail.scheduledPeriod.start",
                effective_time_location(f"{entry_base}/effectiveTime", effective_time, "low"),
                low_dt,
            )
            recorder.record(
                resource_id,
                f"activity[{index}].detail.scheduledPeriod.end",
                effective_time_location(f"{entry_base}/effectiveTime", effective_time, "high"),
                high_dt,
            )
    elif low_dt or high_dt:
        scheduled = low_dt or high_dt
        detail.scheduledString = scheduled
        if recorder and resource_id:
            recorder.record(
                resource_id,
                f"activity[{index}].detail.scheduledString",
                effective_time_location(f"{entry_base}/effectiveTime", effective_time, "low" if low_dt else "high"),
                scheduled,
            )

    return detail


def build_plan_of_treatment_resources(section, patient_id: str, recorder=None) -> list:
    """The narrative DocumentReference+Binary (always built) plus, when at
    least one recognized planned entry resolves, a single CarePlan wrapping
    one .activity[] per entry - matching narrative_sections.py's own
    disclosed "coexist, don't replace" design."""
    resources = list(build_narrative_document_reference(section, patient_id, recorder=recorder))

    care_plan_id = str(uuid.uuid4())
    activities = []
    for index, entry in enumerate(find_all(section, "entry")):
        for entry_tag, template_id, kind in _RECOGNIZED_ENTRY_SHAPES:
            planned_element = find_child(entry, entry_tag)
            if planned_element is None or not has_template_id(planned_element, template_id):
                continue
            detail = _build_activity_detail(
                planned_element, entry_tag, index, kind, resource_id=care_plan_id, recorder=recorder
            )
            if detail is not None:
                activities.append(CarePlanActivity(detail=detail))
            break

    if not activities:
        return resources

    care_plan = CarePlan(
        id=care_plan_id,
        status="active",
        intent="plan",
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        activity=activities,
    )
    if recorder:
        recorder.record_inferred(
            care_plan_id,
            "status",
            "Fixed to \"active\" - this converter has no source field indicating whether the plan has since been superseded.",
            "active",
        )
        recorder.record_inferred(
            care_plan_id,
            "intent",
            'Fixed to "plan" - every entry this section recognizes is inherently a planned (not-yet-done) activity.',
            "plan",
        )
    resources.append(care_plan)
    return resources
