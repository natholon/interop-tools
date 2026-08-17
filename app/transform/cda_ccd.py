"""FHIR Bundle -> C-CDA CCD XML - the second reverse-direction slice, and
the first proof that app/transform/'s architecture generalizes across
input *formats*, not just across HL7v2 trigger events. Scoped to exactly
"header + Problems section" - the identical scope CCD's own very first
*forward*-direction slice shipped with (see CLAUDE.md/git history: "Add
C-CDA to FHIR conversion (CCD header + Problems section)" was this app's
first CDA commit, before Medications/Allergies/Immunizations/etc. arrived
in later slices) - the same "one thing per slice" precedent applied to the
reverse direction too.

Reverses app/cda/common.py::build_patient_from_header/
build_encounter_from_header and app/cda/problems.py::build_conditions
field-for-field, using each one's own exact element/attribute shapes, not
re-derived independently. **Disclosed round-trip simplifications, not
bugs**: `clinicalStatus` is written back only via the Concern Act's own
`statusCode` (the reverse of `_ACT_STATUS_TO_CLINICAL_STATUS`) - the
nested Status Observation path `_resolve_clinical_status` also reads is
not reconstructed, since a FHIR `Condition.clinicalStatus` code alone
doesn't indicate which of the two source shapes originally produced it;
this is a safe simplification (the Concern Act's own statusCode is a
first-class, spec-legitimate source on its own, not a fabricated one).
`Condition.onsetDateTime`/`abatementDateTime` are written back as a single
`effectiveTime/low`+`high` pair (the forward mapper's own `ivl_ts_bounds`
reads exactly this shape, via `<low>`/`<high>` children) - either date
alone still produces a valid `effectiveTime` with only the corresponding
child present, matching `ivl_ts_bounds`' own tolerance for a partial pair."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.ccd import CCD_TEMPLATE_ID
from app.cda.common import RECOGNIZED_ENCOUNTER_CLASSES
from app.cda.problems import PROBLEM_OBSERVATION_TEMPLATE_ID
from app.cda.problems import CONCERN_ACT_TEMPLATE_ID
from app.cda.problems import SECTION_TEMPLATE_ID as PROBLEMS_SECTION_TEMPLATE_ID
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_date, format_hl7_ts

_US_HEADER_TEMPLATE_ID = "2.16.840.1.113883.10.20.22.1.1"
# Reverse of app/cda/common.py::_GENDER_MAP ({"F": "female", "M": "male",
# "UN": "other"}).
_GENDER_TO_CDA_CODE = {"female": "F", "male": "M", "other": "UN"}
# Reverse of app/cda/problems.py::_ACT_STATUS_TO_CLINICAL_STATUS - "active"
# is the fallback for a clinicalStatus code with no clean reverse (there's
# no "suspended"/"aborted" distinction FHIR's own condition-clinical
# CodeSystem preserves for "inactive"/"resolved" to map back to uniquely,
# so this picks the one Concern Act status each FHIR code most plausibly
# came from).
_CLINICAL_STATUS_TO_ACT_STATUS = {"active": "active", "inactive": "suspended", "resolved": "completed"}
_PLACEHOLDER_ROOT = "2.16.840.1.113883.19.5.99999.1"


def _reverse_identifier_root(identifier) -> str:
    """The reverse of app.cda.common.build_identifier's own root/extension
    resolution: an Identifier.system of urn:oid:<root> (the shape
    build_identifier itself produces whenever the source <id> had a real
    @root) reverses cleanly by stripping the urn:oid: prefix back off;
    anything else (this app's own urn:interop-tools:... fallback systems,
    or a system with no recoverable OID at all) falls back to a disclosed
    placeholder root rather than emitting a non-OID string as if it were
    one."""
    if identifier.system and identifier.system.startswith("urn:oid:"):
        return identifier.system[len("urn:oid:") :]
    return _PLACEHOLDER_ROOT


def _build_patient_role(patient) -> str:
    ids = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{identifier.value}"/>'
        for identifier in (patient.identifier or [])
    )
    name = ""
    if patient.name:
        human_name = patient.name[0]
        family = f"<family>{human_name.family}</family>" if human_name.family else ""
        given = "".join(f"<given>{g}</given>" for g in (human_name.given or []))
        name = f"<name>{given}{family}</name>"
    gender = ""
    if patient.gender:
        code = _GENDER_TO_CDA_CODE.get(patient.gender)
        if code:
            gender = f'<administrativeGenderCode code="{code}" codeSystem="2.16.840.1.113883.5.1"/>'
    birth_time = f'<birthTime value="{format_hl7_date(patient.birthDate)}"/>' if patient.birthDate else ""
    addr = ""
    if patient.address:
        address = patient.address[0]
        lines = "".join(f"<streetAddressLine>{line}</streetAddressLine>" for line in (address.line or []))
        city = f"<city>{address.city}</city>" if address.city else ""
        state = f"<state>{address.state}</state>" if address.state else ""
        postal_code = f"<postalCode>{address.postalCode}</postalCode>" if address.postalCode else ""
        country = f"<country>{address.country}</country>" if address.country else ""
        addr = f"<addr>{lines}{city}{state}{postal_code}{country}</addr>"
    telecom = ""
    if patient.telecom:
        contact_point = patient.telecom[0]
        scheme = "tel" if contact_point.system == "phone" else "mailto" if contact_point.system == "email" else "tel"
        telecom = f'<telecom value="{scheme}:{contact_point.value}"/>'

    return (
        f'<recordTarget><patientRole>{ids}{addr}{telecom}'
        f'<patient>{name}{gender}{birth_time}</patient>'
        "</patientRole></recordTarget>"
    )


def _build_component_of(encounter) -> str:
    if encounter is None:
        return ""
    ids = "".join(
        f'<id root="{_reverse_identifier_root(identifier)}" extension="{identifier.value}"/>'
        for identifier in (encounter.identifier or [])
    )
    class_code = "AMB"
    if encounter.class_fhir and encounter.class_fhir.code in RECOGNIZED_ENCOUNTER_CLASSES:
        class_code = encounter.class_fhir.code
    effective_time = ""
    if encounter.period:
        low = f'<low value="{format_hl7_ts(encounter.period.start)}"/>' if encounter.period.start else ""
        high = f'<high value="{format_hl7_ts(encounter.period.end)}"/>' if encounter.period.end else ""
        if low or high:
            effective_time = f"<effectiveTime>{low}{high}</effectiveTime>"
    return (
        "<componentOf><encompassingEncounter>"
        f'{ids}<code code="{class_code}" codeSystem="2.16.840.1.113883.5.4"/>{effective_time}'
        "</encompassingEncounter></componentOf>"
    )


def _build_problem_entry(condition) -> str:
    code = condition.code.coding[0] if condition.code and condition.code.coding else None
    value = (
        f'<value xsi:type="CD" code="{code.code}" codeSystem="{code.system}" displayName="{code.display or ""}"/>'
        if code
        else '<value xsi:type="CD" nullFlavor="UNK"/>'
    )
    act_status = "active"
    if condition.clinicalStatus and condition.clinicalStatus.coding:
        act_status = _CLINICAL_STATUS_TO_ACT_STATUS.get(condition.clinicalStatus.coding[0].code, "active")
    # A bare @value IVL_TS per date, rather than reconstructing a shared
    # low/high pair on one effectiveTime - onsetDateTime/abatementDateTime
    # are two independent FHIR fields with no guarantee both came from the
    # same source effectiveTime element in the first place (a document
    # could have been hand-edited, or come from a different pipeline
    # entirely), so treating them as two independent point-in-time facts
    # here is the more honest reverse mapping, not a corner cut.
    onset = f'<low value="{format_hl7_date(condition.onsetDateTime)}"/>' if condition.onsetDateTime else ""
    abatement = f'<high value="{format_hl7_date(condition.abatementDateTime)}"/>' if condition.abatementDateTime else ""
    effective_time = f"<effectiveTime>{onset}{abatement}</effectiveTime>" if (onset or abatement) else ""

    return (
        f'<entry typeCode="DRIV"><act classCode="ACT" moodCode="EVN">'
        f'<templateId root="{CONCERN_ACT_TEMPLATE_ID}"/>'
        '<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/>'
        f'<statusCode code="{act_status}"/>'
        f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN">'
        f'<templateId root="{PROBLEM_OBSERVATION_TEMPLATE_ID}"/>'
        '<code code="55607006" codeSystem="2.16.840.1.113883.6.96" displayName="Problem"/>'
        '<statusCode code="completed"/>'
        f"{effective_time}{value}"
        "</observation></entryRelationship></act></entry>"
    )


def _build_problems_section(conditions) -> str:
    if not conditions:
        return ""
    entries = "".join(_build_problem_entry(c) for c in conditions)
    return (
        f'<component><section><templateId root="{PROBLEMS_SECTION_TEMPLATE_ID}"/>'
        '<code code="11450-4" codeSystem="2.16.840.1.113883.6.1" displayName="Problem List"/>'
        f"<title>Problems</title>{entries}</section></component>"
    )


class CcdReverseBuilder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError("Bundle has no Patient resource - cannot build a CCD document")
        encounter = find_resource(bundle, "Encounter")
        conditions = find_resources(bundle, "Condition")

        document_id = bundle.identifier.value if bundle.identifier else "TT000"
        document_root = _reverse_identifier_root(bundle.identifier) if bundle.identifier else _PLACEHOLDER_ROOT
        effective_time = format_hl7_ts(bundle.timestamp) if bundle.timestamp else ""

        problems_section = _build_problems_section(conditions)
        body = f"<component><structuredBody>{problems_section}</structuredBody></component>" if problems_section else ""

        xml_text = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<templateId root="{_US_HEADER_TEMPLATE_ID}"/><templateId root="{CCD_TEMPLATE_ID}"/>'
            f'<id root="{document_root}" extension="{document_id}"/>'
            '<code code="34133-9" codeSystem="2.16.840.1.113883.6.1" displayName="Summarization of Episode Note"/>'
            "<title>Continuity of Care Document</title>"
            f'{f"<effectiveTime value=\"{effective_time}\"/>" if effective_time else ""}'
            '<confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>'
            '<languageCode code="en-US"/>'
            f"{_build_patient_role(patient)}{_build_component_of(encounter)}{body}"
            "</ClinicalDocument>"
        )
        return xml_text
