"""MDM (Medical Document Management) -> FHIR mapping.

T02/T04/T06/T08/T10/T11 all produce identical output here, matching ORU's
pattern (BaseOruMapper handling R01/R30/R31/R32/R40 identically): the
official v2-to-FHIR IG ships exactly one MDM ConceptMap (MDM_T02 -> Bundle)
and treats the TXA segment map as trigger-agnostic, so "new document" (T02),
"status update" (T04), "addendum" (T06), "edit notification" (T08),
"replacement notification" (T10), and "cancel notification" (T11) all
convert through the same TXA/OBX shape - this stateless converter doesn't
model the different document-lifecycle semantics those triggers imply
upstream, only the document data itself.

T10/T11 are semantically status-change events (replace/cancel) and were
initially a candidate for trigger-specific FHIR status handling (e.g. T10 ->
"superseded", T11 -> "entered-in-error", mirroring ADT A11/A13's deliberate
cancel-pattern override) - but unlike A11/A13 (where the IG's silence was a
deliberate reason to *add* fidelity the IG doesn't provide), here the
decision was to defer to the IG as the authoritative source: the segment-txa-
to-documentreference ConceptMap (build.fhir.org/ig/HL7/v2-to-fhir) has no
trigger-event-specific guidance at all, and TXA-13 (Unique Document Number of
the Original - the field that would carry T10's "replaces" pointer) has no
mapped FHIR target in that ConceptMap. So status stays 100% TXA-19-driven for
every trigger, and TXA-13 is left unmapped, same as every other IG-silent
field in this module - a real difference in disclosed rationale from A11/A13
even though the *code* ends up looking the same as T02/T04/T06.

TXA -> DocumentReference field map (per the v2-to-FHIR IG's
segment-txa-to-documentreference ConceptMap, verified via build.fhir.org):
TXA-2 Document Type -> type, TXA-3 Document Content Presentation ->
content.attachment.contentType, TXA-6 Origination Date/Time -> date, TXA-9
Originator -> author (Practitioner), TXA-10 Assigned Authenticator ->
authenticator (Practitioner), TXA-12 Unique Document Number ->
masterIdentifier, TXA-16 Unique Document File Name -> identifier, TXA-18
Document Confidentiality Status -> securityLabel, TXA-19 Document
Availability Status -> status, TXA-25 Document Title -> description.

Two things the IG maps but this converter deliberately does NOT attempt,
both disclosed rather than guessed at:
- TXA-17 (Document Completion Status) -> docStatus: the IG doesn't publish a
  verified code crosswalk from TXA-17's local/table values to FHIR's
  4-code CompositionStatus value set, and passing an unverified raw code
  through risked emitting a non-compliant value - omitted rather than guess.
- TXA-19 (Document Availability Status) -> status: only "AV" (Available) is
  a verified mapping (-> "current"); every other/absent value also defaults
  to "current" (matching the IG's own default), rather than guessing at a
  fuller crosswalk this app couldn't verify.

Document *content* itself is carried by OBX segments following TXA, not by
TXA itself - the IG doesn't show a worked content-transfer example, so this
app's approach (concatenate every TX/FT-typed OBX-5 value into a single
plaintext body, base64-encode it into a separate Binary resource referenced
via DocumentReference.content.attachment.url) is a disclosed extension of
the IG's intent, not something the IG hands you directly. A message with no
usable OBX content still converts successfully - it just gets no Binary and
an attachment with no url, which is valid FHIR (Attachment has no required
fields).
"""

import base64
import uuid

from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.binary import Binary
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.documentreference import DocumentReference, DocumentReferenceContent, DocumentReferenceContext
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.fhir_models.builders import build_codeable_concept_from_cwe, parse_hl7_datetime
from app.hl7.errors import MissingSegmentError
from app.hl7.parser import field_str, optional_segments, raw_field_str, require_segment
from app.mappings.base import MessageMapper
from app.mappings.common import (
    assemble_bundle,
    build_minimal_encounter,
    build_patient,
    build_practitioner_from_xcn,
    build_reference_with_optional_display,
    person_display,
)
from app.provenance.location import hl7_location

_CONFIDENTIALITY_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"

# TXA-3 (Document Content Presentation, HL7 table 0191) -> a real MIME type.
# No IG-published crosswalk exists for this - a disclosed, local judgment
# call (same category as SIU's _AIG_LOCATION_TYPE_CODES), covering the most
# common real-world codes; anything unrecognized falls back to text/plain
# rather than leaving contentType unset (it's a required-by-the-IG 1..1
# target field).
_CONTENT_PRESENTATION_TO_MIME_TYPE = {
    "TEXT": "text/plain",
    "FORMATTED": "text/plain",
    "HTML": "text/html",
    "XML": "application/xml",
    "RTF": "application/rtf",
    "PDF": "application/pdf",
    "JPEG": "image/jpeg",
}
_DEFAULT_CONTENT_TYPE = "text/plain"

# Value types whose OBX-5 is treated as a line of the document body text.
_TEXT_VALUE_TYPES = {"TX", "FT"}


def _resolve_content_type(txa) -> str:
    code = field_str(txa, 3).strip().upper()
    return _CONTENT_PRESENTATION_TO_MIME_TYPE.get(code, _DEFAULT_CONTENT_TYPE)


def _resolve_status(txa) -> str:
    """TXA-19 -> DocumentReference.status. Only "AV" (Available) is a
    verified mapping; everything else (including absent) defaults to
    "current" too, matching the IG's own stated default - see module
    docstring for why a fuller crosswalk isn't attempted."""
    return "current"


def _build_binary_from_obx(obx_segments, txa, recorder=None) -> Binary | None:
    # TX/FT is unstructured free text, not HL7-composite - raw_field_str
    # (whole field) is used rather than field_str (component 1 only), which
    # would otherwise silently truncate any line containing a literal '^'.
    text_lines = [
        raw_field_str(obx, 5) for obx in obx_segments if field_str(obx, 2).strip().upper() in _TEXT_VALUE_TYPES
    ]
    text_lines = [line for line in text_lines if line]
    if not text_lines:
        return None
    body = "\n".join(text_lines)
    binary_id = str(uuid.uuid4())
    binary = Binary(
        id=binary_id,
        contentType=_resolve_content_type(txa),
        data=base64.b64encode(body.encode("utf-8")).decode("ascii"),
    )
    if recorder:
        # Mirrors SIU's own NTE-join precedent (Appointment.comment) for a
        # field built by concatenating an unknown number of repeating
        # segments into one FHIR value with no per-segment boundary left in
        # the output - the location string discloses the segment count
        # rather than fabricating a fake per-segment breakdown.
        location = hl7_location("OBX", 5) if len(text_lines) == 1 else f"OBX-5 (×{len(text_lines)} segments)"
        recorder.record(binary_id, "data", location, body)
    return binary


def _build_xcn_practitioner_reference(
    segment, field_num: int, recorder=None
) -> tuple[Practitioner | None, Reference | None]:
    """Build a materialized Practitioner + matching Reference for an XCN
    field (TXA-9 originator, TXA-10 authenticator), same pattern as SIU's
    AIP participants: a real resource (build_practitioner_from_xcn) paired
    with a human-readable display (person_display, falling back to the XCN
    id when there's no name) via the shared
    build_reference_with_optional_display."""
    practitioner = build_practitioner_from_xcn(segment, field_num, recorder=recorder)
    if practitioner is None:
        return None, None
    reference = build_reference_with_optional_display(practitioner.id, person_display(segment, field_num))
    return practitioner, reference


def build_document_reference(
    txa, patient_id: str, encounter_id: str | None, binary_id: str | None, recorder=None
) -> tuple[DocumentReference, list[Resource]]:
    """TXA -> DocumentReference. Returns the DocumentReference plus any extra
    resources materialized for it (originator/authenticator Practitioners)."""
    document_reference_id = str(uuid.uuid4())
    status = _resolve_status(txa)
    content_type = _resolve_content_type(txa)
    attachment = Attachment(contentType=content_type)
    if binary_id:
        attachment.url = f"urn:uuid:{binary_id}"

    document_reference = DocumentReference(
        id=document_reference_id,
        status=status,
        content=[DocumentReferenceContent(attachment=attachment)],
        subject=Reference(reference=f"urn:uuid:{patient_id}"),
    )
    if recorder:
        # _resolve_status never actually reads TXA-19's own value (see its
        # docstring) - it always returns "current" regardless of what's
        # there, so this is inferred, not a direct read, even in the one
        # case ("AV") where the result happens to match a verified mapping.
        recorder.record_inferred(
            document_reference_id,
            "status",
            'TXA-19 (Document Availability Status) has no fuller verified crosswalk beyond "AV"->"current" - every value, including absent, defaults to "current" (the IG\'s own stated default), not read from the field\'s actual value.',
            status,
        )
        recorder.record(
            document_reference_id,
            "content[0].attachment.contentType",
            hl7_location("TXA", 3),
            content_type,
            source_value=field_str(txa, 3),
        )

    doc_type = build_codeable_concept_from_cwe(
        txa, 2, resource_id=document_reference_id, relative_path="type", recorder=recorder
    )
    if doc_type:
        document_reference.type = doc_type

    master_id = field_str(txa, 12)
    if master_id:
        document_reference.masterIdentifier = Identifier(system="urn:interop-tools:document-number", value=master_id)
        if recorder:
            recorder.record(document_reference_id, "masterIdentifier.value", hl7_location("TXA", 12), master_id)

    file_name = field_str(txa, 16)
    if file_name:
        document_reference.identifier = [Identifier(system="urn:interop-tools:document-file-name", value=file_name)]
        if recorder:
            recorder.record(document_reference_id, "identifier[0].value", hl7_location("TXA", 16), file_name)

    origination_dt = parse_hl7_datetime(field_str(txa, 6))
    if origination_dt:
        document_reference.date = origination_dt
        if recorder:
            recorder.record(
                document_reference_id, "date", hl7_location("TXA", 6), origination_dt, source_value=field_str(txa, 6)
            )

    confidentiality_code = field_str(txa, 18)
    if confidentiality_code:
        document_reference.securityLabel = [
            CodeableConcept(coding=[Coding(system=_CONFIDENTIALITY_SYSTEM, code=confidentiality_code)])
        ]
        if recorder:
            recorder.record(
                document_reference_id,
                "securityLabel[0].coding[0].code",
                hl7_location("TXA", 18),
                confidentiality_code,
            )

    title = raw_field_str(txa, 25)
    if title:
        document_reference.description = title
        if recorder:
            recorder.record(document_reference_id, "description", hl7_location("TXA", 25), title)

    if encounter_id:
        document_reference.context = DocumentReferenceContext(
            encounter=[Reference(reference=f"urn:uuid:{encounter_id}")]
        )

    extra_resources: list[Resource] = []
    originator, author_ref = _build_xcn_practitioner_reference(txa, 9, recorder=recorder)
    if originator is not None:
        document_reference.author = [author_ref]
        extra_resources.append(originator)
        if recorder and author_ref.display:
            # TXA-9 produces two independent facts, same "one source field,
            # two FHIR destinations" case SIU's own AIP-3 already
            # established: the materialized Practitioner's own name (already
            # recorded inside build_practitioner_from_xcn above) and this
            # DocumentReference's own author[0].display string.
            recorder.record(document_reference_id, "author[0].display", hl7_location("TXA", 9), author_ref.display)

    # If TXA-10 identifies the same real person as TXA-9 (e.g. a physician
    # who both dictates and co-signs a note), reuse the same materialized
    # Practitioner rather than building a second, near-identical one -
    # same rationale as ORU's OBX-16 performer_cache dedup.
    originator_id = field_str(txa, 9, component=1)
    authenticator_id = field_str(txa, 10, component=1)
    if originator is not None and authenticator_id and authenticator_id == originator_id:
        authenticator_display = person_display(txa, 10)
        document_reference.authenticator = build_reference_with_optional_display(originator.id, authenticator_display)
        if recorder and authenticator_display:
            recorder.record(
                document_reference_id, "authenticator.display", hl7_location("TXA", 10), authenticator_display
            )
    else:
        authenticator, authenticator_ref = _build_xcn_practitioner_reference(txa, 10, recorder=recorder)
        if authenticator is not None:
            document_reference.authenticator = authenticator_ref
            extra_resources.append(authenticator)
            if recorder and authenticator_ref.display:
                recorder.record(
                    document_reference_id, "authenticator.display", hl7_location("TXA", 10), authenticator_ref.display
                )

    return document_reference, extra_resources


class BaseMdmMapper(MessageMapper):
    """Shared (in fact total - see module docstring) behavior for every MDM
    trigger event in scope. Requires MSH/PID/TXA; PV1 is optional (builds a
    minimal Encounter when present, same as ORU)."""

    message_type = "MDM"

    def to_bundle(self, message, recorder=None) -> Bundle:
        msh = require_segment(message, "MSH")
        pid = require_segment(message, "PID")
        txa = require_segment(message, "TXA")
        obx_segments = optional_segments(message, "OBX")

        try:
            pv1 = require_segment(message, "PV1")
        except MissingSegmentError:
            pv1 = None

        patient = build_patient(pid, recorder=recorder)
        encounter = build_minimal_encounter(pv1, patient.id, recorder=recorder) if pv1 is not None else None
        encounter_id = encounter.id if encounter is not None else None

        binary = _build_binary_from_obx(obx_segments, txa, recorder=recorder)
        document_reference, extra_resources = build_document_reference(
            txa, patient.id, encounter_id, binary.id if binary is not None else None, recorder=recorder
        )

        resources_in_order = (
            ([encounter] if encounter is not None else [])
            + [document_reference]
            + ([binary] if binary is not None else [])
            + extra_resources
        )
        return assemble_bundle(msh, patient, *resources_in_order, recorder=recorder)


class MdmT02Mapper(BaseMdmMapper):
    """T02 - Original document notification and content."""

    trigger_event = "T02"


class MdmT04Mapper(BaseMdmMapper):
    """T04 - Document status change notification and content."""

    trigger_event = "T04"


class MdmT06Mapper(BaseMdmMapper):
    """T06 - Document addendum notification and content."""

    trigger_event = "T06"


class MdmT08Mapper(BaseMdmMapper):
    """T08 - Document edit notification and content."""

    trigger_event = "T08"


class MdmT10Mapper(BaseMdmMapper):
    """T10 - Document replacement notification and content."""

    trigger_event = "T10"


class MdmT11Mapper(BaseMdmMapper):
    """T11 - Document cancel notification."""

    trigger_event = "T11"
