"""Vital Signs section (templateId 2.16.840.1.113883.10.20.22.2.4.1) ->
Observation, per the C-CDA on FHIR IG's CF-vitals.md. No CSV mapping table
is published for this section, unlike Problems/Medications/Allergies/
Immunizations/Procedures - the field mapping comes from that page's own
tables and worked examples. The section templateId was confirmed against a
real HL7 C-CDA-Examples CCD, since the IG page gives only the LOINC code.

A Vital Signs Organizer (one per reading session) becomes a FHIR
Observation *panel* - `code` fixed to LOINC `85353-1`, `.hasMember`
referencing one Observation per vital sign. The organizer's own `/code` is
narrative-only in the source and is not carried anywhere. Every resource is
a separate top-level Bundle entry, never `.contained`.

**Blood Pressure and Pulse Oximetry panels.** C-CDA has no template for
either - a systolic/diastolic pair, or an O2 saturation with optional
concentration/flow-rate siblings, are ordinary Vital Sign Observations
sharing one organizer. Detection is purely by LOINC code within that flat
component list:

- **Blood Pressure** (`85354-9`): systolic `8480-6` + diastolic `8462-4`
  as exactly two `.component` entries, and **no** top-level
  `.valueQuantity` - the IG says not to send one. Both are required, so an
  incomplete pair is not grouped: it falls back to flat observations rather
  than being dropped.
- **Pulse Oximetry**: the O2 saturation reading (`59408-5` or the older
  synonymous `2708-6`) becomes the panel itself, carrying BOTH codings
  whichever the source used, and - unlike BP - keeping its own top-level
  `.valueQuantity`. Inhaled oxygen concentration (`3150-0`) and flow rate
  (`3151-8`) become components only when present ("only if values exist").
  One found with no primary reading to attach to falls back to flat.

Scope limit: only these two documented special cases are grouped. Every
other LOINC-coded vital maps 1:1."""

import uuid

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.observation import Observation, ObservationComponent
from fhir.resources.R4B.reference import Reference

from app.cda.common import (
    build_author_participant,
    build_codeable_concept_from_cd,
    build_identifiers,
    build_quantity_from_pq,
    effective_time_location,
    parse_partial_ts,
    record_coding,
)
from app.cda.parser import find_all, find_child, has_template_id, ivl_ts_bounds
from app.provenance.location import xpath_location

# Public (not module-private) - reused by app/cda/generator.py and
# app/cda/validation.py, same pattern as every other section module.
SECTION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.2.4.1"
# The "entries optional" sibling section (no trailing ".1"). A real HL7
# History and Physical example declares both this and SECTION_TEMPLATE_ID
# on one section, which is legal. Registered alongside it in registry.py
# and validation.py: a section declaring only the entries-optional
# templateId is a shape that really occurs (see procedures.py), and a
# single-templateId registration silently skips it.
SECTION_TEMPLATE_ID_ENTRIES_OPTIONAL = "2.16.840.1.113883.10.20.22.2.4"
ORGANIZER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.26"
OBSERVATION_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.4.27"

_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
_CATEGORY_CODE = "vital-signs"
# Vital Signs Panel - fixed per the IG's own "C-CDA Vital Signs Organizer
# to FHIR Observation Panel" table ("Set to 85353-1"), not derived from
# the source organizer's own /code. Public (not module-private) -
# app/transform/cda_ccd.py became a real reverse-direction consumer,
# using this fixed code to tell a panel Observation apart from an
# individual vital-sign Observation within one flat Bundle.entry list.
PANEL_CODE_SYSTEM = "http://loinc.org"
PANEL_CODE = "85353-1"

# C-CDA's statusCode is fixed to "completed" for both the Vital Signs
# Organizer and each Vital Sign Observation per the C-CDA spec itself (not
# just this IG's own disclosed default) - the IG's own tables state this
# plainly ("`final` (C-CDA is fixed to `completed`)"), so there's no
# ConceptMap to build, unlike Results'/Procedures' own genuinely-variable
# statusCode.
_FIXED_STATUS = "final"

# Public (not module-private) - app/cda/validation.py became a real
# second consumer, needing the identical "only a genuinely LOINC-coded
# value is trusted" check for its own incomplete-pair/orphaned-sibling
# detection rules.
LOINC_OID = "2.16.840.1.113883.6.1"

# Blood Pressure Panel (CF-vitals.md's own construction guidance, fetched
# directly) - see module docstring for the full shape. Public (not
# module-private) - both app/cda/validation.py (detecting an incomplete
# pair) and app/transform/cda_ccd.py (reversing a panel's own .component
# list back into two flat observations) became real second/third
# consumers, the same "promote once reused" discipline PANEL_CODE itself
# already established.
BP_SYSTOLIC_CODE = "8480-6"
BP_DIASTOLIC_CODE = "8462-4"
BP_PANEL_CODE = "85354-9"

# Pulse Oximetry Panel - see module docstring for the full shape. Public
# for the identical reason the Blood Pressure Panel constants above are.
PULSE_OX_PRIMARY_CODES = {"59408-5", "2708-6"}
PULSE_OX_CONCENTRATION_CODE = "3150-0"
PULSE_OX_FLOW_RATE_CODE = "3151-8"


def _category() -> CodeableConcept:
    return CodeableConcept(coding=[Coding(system=_CATEGORY_SYSTEM, code=_CATEGORY_CODE)])


def _record_fixed_status_and_category(recorder, resource_id: str) -> None:
    """status and category are both fixed per the C-CDA spec itself, for
    both the panel and every member Observation - no ConceptMap, no
    source field to point at, so both are recorded inferred with the
    identical disclosed reason regardless of which resource carries them."""
    recorder.record_inferred(
        resource_id,
        "status",
        'C-CDA fixes Vital Signs statusCode to "completed" per the spec itself -> FHIR "final", not read from any per-entry field.',
        _FIXED_STATUS,
    )
    recorder.record_inferred(
        resource_id,
        "category[0].coding[0].code",
        "Every Vital Signs Observation this app builds (panel or member) carries category=\"vital-signs\" unconditionally - not read from any C-CDA field.",
        _CATEGORY_CODE,
    )


def _member_base(index: int) -> str:
    """The i-th (0-based) component's own nested Observation - an organizer
    commonly wraps more than one member, so a bare "organizer/component/
    observation" alone would record the identical, ambiguous location for
    every member's own facts, the same collision risk Allergies' own
    multiple-reaction fix and 837I's own multi-HI-segment fix already
    addressed - disambiguated here proactively the same way."""
    return xpath_location("organizer", f"component[{index}]", "observation")


def _loinc_code(observation_element) -> str | None:
    """The observation's own <code>'s LOINC value, or None when absent or
    coded in some other system - only a genuinely LOINC-coded value is
    trusted for the Blood Pressure/Pulse Oximetry detection below, so a
    coincidentally identical code string in a different vocabulary can
    never false-match."""
    code_element = find_child(observation_element, "code")
    if code_element is None:
        return None
    if code_element.get("codeSystem") != LOINC_OID:
        return None
    return code_element.get("code")


def _apply_common_observation_fields(
    observation: Observation,
    observation_element,
    member_base: str,
    recorder=None,
    extra_resources: list | None = None,
) -> None:
    """effectiveDateTime/interpretation/method/bodySite/performer - the
    fields every Vital Sign Observation reads the same way regardless of
    whether it ends up as an ordinary flat member or the base of a Pulse
    Oximetry Panel (whose primary O2-saturation reading IS the panel, so
    it keeps its own author too). Extracted once _build_pulse_oximetry_panel
    became a second real consumer of the identical extraction
    _build_vital_sign_observation already had inline.

    `extra_resources` collects the performer Practitioner; pass None where
    there is nowhere to return one."""
    effective_time = find_child(observation_element, "effectiveTime")
    effective, _ = ivl_ts_bounds(effective_time)
    effective_dt = parse_partial_ts(effective)
    if effective_dt:
        observation.effectiveDateTime = effective_dt
        if recorder:
            recorder.record(
                observation.id,
                "effectiveDateTime",
                effective_time_location(f"{member_base}/effectiveTime", effective_time, "low"),
                effective_dt,
            )

    interpretation_element = find_child(observation_element, "interpretationCode")
    interpretation = build_codeable_concept_from_cd(interpretation_element)
    if interpretation:
        observation.interpretation = [interpretation]
        record_coding(
            recorder, observation.id, "interpretation[0]", f"{member_base}/interpretationCode", interpretation
        )

    method_element = find_child(observation_element, "methodCode")
    method = build_codeable_concept_from_cd(method_element)
    if method:
        observation.method = method
        record_coding(recorder, observation.id, "method", f"{member_base}/methodCode", method)

    body_site_element = find_child(observation_element, "targetSiteCode")
    body_site = build_codeable_concept_from_cd(body_site_element)
    if body_site:
        observation.bodySite = body_site
        record_coding(recorder, observation.id, "bodySite", f"{member_base}/targetSiteCode", body_site)

    # CF-vitals maps a Vital Sign Observation's /author to .performer as
    # well as to Provenance - one of the few author rows in the IG that
    # names a plain attribute. A side folded into a Blood Pressure Panel
    # keeps no Observation of its own to carry one, the same reason its
    # id, status and effectiveTime are not carried either.
    if extra_resources is None:
        return
    # allow_device=False: Observation.performer cannot reference a Device,
    # so a device-authored vital is left without a performer rather than
    # having its authoring system recorded as a person.
    author = build_author_participant(observation_element, member_base, allow_device=False, recorder=recorder)
    if author is None:
        return
    performer_reference, performer = author
    observation.performer = [performer_reference]
    extra_resources.append(performer)
    if recorder:
        recorder.record(
            observation.id,
            "performer[0].reference",
            xpath_location(member_base, "author", "assignedAuthor"),
            performer_reference.reference,
        )


def _build_vital_sign_observation(
    observation_element, patient_id: str, index: int, recorder=None,
    extra_resources: list | None = None
) -> Observation | None:
    code_element = find_child(observation_element, "code")
    code = build_codeable_concept_from_cd(code_element)
    if code is None:
        # No resolvable coded value - skip the entry, matching every other
        # section's own "no resolvable code -> skip" convention (Problems,
        # Medications, Allergies, Immunizations).
        return None

    observation_id = str(uuid.uuid4())
    observation = Observation(
        id=observation_id,
        status=_FIXED_STATUS,
        category=[_category()],
        code=code,
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )

    member_base = _member_base(index)

    # /id -> .identifier, per the IG's own CF-vitals mapping table. Every
    # entry-level id here was dropped before.
    identifiers = build_identifiers(
        find_all(observation_element, "id"),
        "urn:interop-tools:cda-vital-sign-id",
        resource_id=observation_id,
        location_prefix=xpath_location(member_base, "id"),
        recorder=recorder,
    )
    if identifiers:
        observation.identifier = identifiers
    if recorder:
        _record_fixed_status_and_category(recorder, observation_id)
        code_value = code_element.get("code")
        display_value = code_element.get("displayName")
        if code_value:
            recorder.record(observation_id, "code.coding[0].code", f"{member_base}/code/@code", code_value)
        if display_value:
            recorder.record(observation_id, "code.coding[0].display", f"{member_base}/code/@displayName", display_value)

    value_element = find_child(observation_element, "value")
    value = build_quantity_from_pq(value_element)
    if value:
        observation.valueQuantity = value
        if recorder:
            recorder.record(observation_id, "valueQuantity.value", f"{member_base}/value/@value", value_element.get("value"))
            if value.unit:
                recorder.record(observation_id, "valueQuantity.unit", f"{member_base}/value/@unit", value.unit)

    _apply_common_observation_fields(
        observation, observation_element, member_base, recorder=recorder, extra_resources=extra_resources
    )

    return observation


def _build_blood_pressure_panel(systolic, diastolic, patient_id: str, recorder=None) -> Observation | None:
    """systolic/diastolic are each (index, observation_element) pairs from
    the organizer's own flat component list - see build_vital_signs's own
    detection pass. Grouped into one Blood Pressure Panel Observation per
    CF-vitals.md's own construction guidance (fixed 85354-9 code, no
    top-level valueQuantity, exactly 2 components) rather than kept as two
    independent top-level Observations. Returns None when either side's
    own code or value doesn't resolve - resolves BOTH sides before
    building or recording anything (rather than failing mid-construction),
    so a failed grouping never leaves an orphaned, half-recorded panel
    behind; the caller falls both sides back to an ordinary flat Vital
    Sign Observation instead of silently dropping data."""
    resolved = []
    for index, observation_element in (systolic, diastolic):
        code_element = find_child(observation_element, "code")
        code = build_codeable_concept_from_cd(code_element)
        value_element = find_child(observation_element, "value")
        value = build_quantity_from_pq(value_element)
        if code is None or value is None:
            return None
        resolved.append((index, code_element, code, value_element, value))

    panel_id = str(uuid.uuid4())
    panel = Observation(
        id=panel_id,
        status=_FIXED_STATUS,
        category=[_category()],
        code=CodeableConcept(coding=[Coding(system=PANEL_CODE_SYSTEM, code=BP_PANEL_CODE)]),
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        component=[ObservationComponent(code=code, valueQuantity=value) for _, _, code, _, value in resolved],
    )
    if recorder:
        _record_fixed_status_and_category(recorder, panel_id)
        recorder.record_inferred(
            panel_id,
            "code.coding[0].code",
            'CF-vitals.md\'s own Blood Pressure Panel construction guidance fixes this to "85354-9" regardless of the source systolic/diastolic codes themselves.',
            BP_PANEL_CODE,
        )
        for component_index, (index, code_element, code, value_element, value) in enumerate(resolved):
            member_base = _member_base(index)
            component_path = f"component[{component_index}]"
            record_coding(recorder, panel_id, f"{component_path}.code", f"{member_base}/code", code)
            recorder.record(
                panel_id, f"{component_path}.valueQuantity.value", f"{member_base}/value/@value", value_element.get("value")
            )
            if value.unit:
                recorder.record(panel_id, f"{component_path}.valueQuantity.unit", f"{member_base}/value/@unit", value.unit)
    return panel


def _build_pulse_oximetry_panel(
    primary, concentration, flow_rate, patient_id: str, recorder=None, extra_resources: list | None = None
) -> Observation | None:
    """`primary` is a (index, observation_element) pair (the O2 saturation
    reading) - the base of the panel itself, not a separate member;
    `concentration`/`flow_rate` are the same shape or None when their own
    sibling LOINC code wasn't present in this organizer. Per CF-vitals.md's
    own construction guidance: the panel's own .code always carries BOTH
    IG-documented synonymous LOINC codes (59408-5/2708-6) regardless of
    which single one the source actually used, .valueQuantity is the O2
    saturation reading itself (unlike the Blood Pressure Panel, which
    carries no top-level value), and concentration/flow-rate become
    .component entries only when present. Returns None when the primary
    reading's own value doesn't resolve - falls back to an ordinary flat
    Vital Sign Observation the same way an incomplete BP pair does."""
    primary_index, primary_element = primary
    value_element = find_child(primary_element, "value")
    value = build_quantity_from_pq(value_element)
    if value is None:
        return None

    panel_id = str(uuid.uuid4())
    panel = Observation(
        id=panel_id,
        status=_FIXED_STATUS,
        category=[_category()],
        code=CodeableConcept(coding=[Coding(system=PANEL_CODE_SYSTEM, code=code) for code in sorted(PULSE_OX_PRIMARY_CODES)]),
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
        valueQuantity=value,
    )
    member_base = _member_base(primary_index)
    if recorder:
        _record_fixed_status_and_category(recorder, panel_id)
        recorder.record_inferred(
            panel_id,
            "code.coding[0].code",
            "CF-vitals.md's own Pulse Oximetry Panel construction guidance fixes this to carry both IG-documented "
            'synonymous LOINC codes ("59408-5" and "2708-6") regardless of which single one the source used.',
            "+".join(sorted(PULSE_OX_PRIMARY_CODES)),
        )
        recorder.record(panel_id, "valueQuantity.value", f"{member_base}/value/@value", value_element.get("value"))
        if value.unit:
            recorder.record(panel_id, "valueQuantity.unit", f"{member_base}/value/@unit", value.unit)

    # The primary O2-saturation reading IS the panel, so its own author
    # maps to .performer exactly as it would on a flat vital.
    _apply_common_observation_fields(
        panel, primary_element, member_base, recorder=recorder, extra_resources=extra_resources
    )

    components = []
    for entry in (concentration, flow_rate):
        if entry is None:
            continue
        entry_index, entry_element = entry
        code_element = find_child(entry_element, "code")
        code = build_codeable_concept_from_cd(code_element)
        entry_value_element = find_child(entry_element, "value")
        entry_value = build_quantity_from_pq(entry_value_element)
        if code is None or entry_value is None:
            # An optional concentration/flow-rate sibling with no
            # resolvable value has nothing useful to report either as a
            # component or as its own flat vital - matching the IG's own
            # "only if values exist" scoping, not a data-loss bug.
            continue
        components.append(ObservationComponent(code=code, valueQuantity=entry_value))
        if recorder:
            entry_member_base = _member_base(entry_index)
            component_path = f"component[{len(components) - 1}]"
            recorder.record(
                panel_id, f"{component_path}.code.coding[0].code", f"{entry_member_base}/code/@code", code_element.get("code")
            )
            recorder.record(
                panel_id,
                f"{component_path}.valueQuantity.value",
                f"{entry_member_base}/value/@value",
                entry_value_element.get("value"),
            )
            if entry_value.unit:
                recorder.record(
                    panel_id, f"{component_path}.valueQuantity.unit", f"{entry_member_base}/value/@unit", entry_value.unit
                )
    if components:
        panel.component = components

    return panel


def build_vital_signs(section, patient_id: str, recorder=None) -> list[Observation]:
    """One panel Observation per Vital Signs Organizer entry (its own
    .hasMember referencing one Observation per individual Vital Sign
    Observation - including a Blood Pressure/Pulse Oximetry Panel
    Observation as a single member, when either was detected, in place of
    the flat systolic/diastolic/O2-saturation-plus-siblings readings it
    was built from), plus each of those individual Observations - all
    returned as a flat list of separate, top-level resources. An organizer
    whose every child observation lacks a resolvable code produces no
    panel either (nothing to group), matching the "no resolvable code ->
    skip" convention at the organizer level too, not just the leaf level."""
    observations: list[Observation] = []
    # Practitioners materialised from a member observation's own <author>,
    # appended to the returned list once every organizer has been walked.
    extra_resources: list = []
    for entry in find_all(section, "entry"):
        organizer = find_child(entry, "organizer")
        if organizer is None or not has_template_id(organizer, ORGANIZER_TEMPLATE_ID):
            continue

        # First pass: bucket each component's own observation element by
        # LOINC code so the Blood Pressure/Pulse Oximetry Panel special
        # cases (see module docstring) can be detected before any of them
        # are built as ordinary flat members - first match wins per
        # bucket, a second occurrence of the same code falls through to
        # "plain" rather than silently being dropped.
        systolic = None
        diastolic = None
        pulse_ox_primary = None
        pulse_ox_concentration = None
        pulse_ox_flow_rate = None
        plain = []
        for index, component in enumerate(find_all(organizer, "component")):
            observation_element = find_child(component, "observation")
            if observation_element is None or not has_template_id(observation_element, OBSERVATION_TEMPLATE_ID):
                continue
            code = _loinc_code(observation_element)
            if code == BP_SYSTOLIC_CODE and systolic is None:
                systolic = (index, observation_element)
            elif code == BP_DIASTOLIC_CODE and diastolic is None:
                diastolic = (index, observation_element)
            elif code in PULSE_OX_PRIMARY_CODES and pulse_ox_primary is None:
                pulse_ox_primary = (index, observation_element)
            elif code == PULSE_OX_CONCENTRATION_CODE and pulse_ox_concentration is None:
                pulse_ox_concentration = (index, observation_element)
            elif code == PULSE_OX_FLOW_RATE_CODE and pulse_ox_flow_rate is None:
                pulse_ox_flow_rate = (index, observation_element)
            else:
                plain.append((index, observation_element))

        # An incomplete Blood Pressure pair, or a Pulse Oximetry
        # concentration/flow-rate reading with no primary O2 saturation
        # reading to attach to, has nothing to group into - falls back to
        # an ordinary flat Vital Sign Observation instead of being
        # silently dropped.
        if systolic and not diastolic:
            plain.append(systolic)
            systolic = None
        if diastolic and not systolic:
            plain.append(diastolic)
            diastolic = None
        if pulse_ox_primary is None:
            if pulse_ox_concentration:
                plain.append(pulse_ox_concentration)
                pulse_ox_concentration = None
            if pulse_ox_flow_rate:
                plain.append(pulse_ox_flow_rate)
                pulse_ox_flow_rate = None

        member_observations = []
        for index, observation_element in plain:
            observation = _build_vital_sign_observation(
                    observation_element, patient_id, index, recorder=recorder,
                    extra_resources=extra_resources,
                )
            if observation is not None:
                member_observations.append(observation)

        if systolic and diastolic:
            bp_panel = _build_blood_pressure_panel(systolic, diastolic, patient_id, recorder=recorder)
            if bp_panel is not None:
                member_observations.append(bp_panel)
            else:
                # Either side's own value failed to resolve - fall back to
                # plain rather than silently dropping the data.
                for index, observation_element in (systolic, diastolic):
                    observation = _build_vital_sign_observation(
                    observation_element, patient_id, index, recorder=recorder,
                    extra_resources=extra_resources,
                )
                    if observation is not None:
                        member_observations.append(observation)

        if pulse_ox_primary:
            pulse_ox_panel = _build_pulse_oximetry_panel(
                pulse_ox_primary,
                pulse_ox_concentration,
                pulse_ox_flow_rate,
                patient_id,
                recorder=recorder,
                extra_resources=extra_resources,
            )
            if pulse_ox_panel is not None:
                member_observations.append(pulse_ox_panel)
            else:
                index, observation_element = pulse_ox_primary
                observation = _build_vital_sign_observation(
                    observation_element, patient_id, index, recorder=recorder,
                    extra_resources=extra_resources,
                )
                if observation is not None:
                    member_observations.append(observation)
                # A concentration/flow-rate sibling with no resolvable
                # primary reading has nowhere to attach and is not
                # separately recovered as its own flat vital either - the
                # same "optional, skip if unusable" treatment
                # _build_pulse_oximetry_panel itself already applies.

        if not member_observations:
            continue

        panel_id = str(uuid.uuid4())
        panel = Observation(
            id=panel_id,
            status=_FIXED_STATUS,
            category=[_category()],
            code=CodeableConcept(coding=[Coding(system=PANEL_CODE_SYSTEM, code=PANEL_CODE)]),
            subject=Reference(reference=f"urn:uuid:{patient_id}"),
            hasMember=[Reference(reference=f"urn:uuid:{member.id}") for member in member_observations],
        )

        # organizer/id -> .identifier, per CF-vitals' own mapping table.
        organizer_identifiers = build_identifiers(
            find_all(organizer, "id"),
            "urn:interop-tools:cda-vitals-panel-id",
            resource_id=panel_id,
            location_prefix=xpath_location("organizer", "id"),
            recorder=recorder,
        )
        if organizer_identifiers:
            panel.identifier = organizer_identifiers

        if recorder:
            _record_fixed_status_and_category(recorder, panel_id)
            recorder.record_inferred(
                panel_id,
                "code.coding[0].code",
                'The IG\'s own "C-CDA Vital Signs Organizer to FHIR Observation Panel" table fixes this to "85353-1" - the organizer\'s own narrative-only /code is never read into this field.',
                PANEL_CODE,
            )

        # If organizer/effectiveTime is missing, the IG's own guidance says
        # to fall back to the earliest/latest observation effectiveTime -
        # disclosed and deferred (every real example fetched while
        # verifying this module carried its own organizer-level
        # effectiveTime, making this the rare case, not the common one).
        organizer_effective_time = find_child(organizer, "effectiveTime")
        organizer_effective, _ = ivl_ts_bounds(organizer_effective_time)
        panel_effective_dt = parse_partial_ts(organizer_effective)
        if panel_effective_dt:
            panel.effectiveDateTime = panel_effective_dt
            if recorder:
                recorder.record(
                    panel_id,
                    "effectiveDateTime",
                    effective_time_location("organizer/effectiveTime", organizer_effective_time, "low"),
                    panel_effective_dt,
                )

        observations.append(panel)
        observations.extend(member_observations)

    observations.extend(extra_resources)
    return observations
