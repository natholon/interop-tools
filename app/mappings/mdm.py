"""MDM (Medical Document Management) -> FHIR mapping.

T02/T04/T06/T08/T10/T11 all produce identical output, the same degenerate
case as ORU's R01/R30/R31/R32/R40: the v2-to-FHIR IG ships exactly one MDM
ConceptMap (MDM_T02 -> Bundle) and treats the TXA segment map as
trigger-agnostic. This stateless converter models the document data, not
the lifecycle semantics those triggers imply upstream.

**T10/T11 look like they should override status** (replace/cancel, the way
ADT's A11/A13 deliberately do) **and deliberately do not.** With A11/A13 the
IG's silence was a reason to add fidelity it does not provide; here the IG
is treated as authoritative, because the segment-txa-to-documentreference
ConceptMap has no trigger-specific guidance and TXA-13 (Unique Document
Number of the Original - what would carry T10's "replaces" pointer) has no
mapped target in it. So status stays TXA-19-driven for every trigger and
TXA-13 stays unmapped.

TXA -> DocumentReference, per that ConceptMap:

    TXA-2  Document Type            -> type
    TXA-3  Content Presentation     -> content.attachment.contentType
    TXA-6  Origination Date/Time    -> date
    TXA-9  Originator               -> author (Practitioner)
    TXA-10 Assigned Authenticator   -> authenticator (Practitioner)
    TXA-12 Unique Document Number   -> masterIdentifier
    TXA-16 Unique Document File Name-> identifier
    TXA-18 Confidentiality Status   -> securityLabel
    TXA-19 Availability Status      -> status
    TXA-25 Document Title           -> description

Two fields the IG maps that this converter does not attempt:
- **TXA-17** (Document Completion Status) -> docStatus. No verified
  crosswalk exists from TXA-17's table values to FHIR's 4-code
  CompositionStatus, and passing a raw code through risks emitting a
  non-conformant value.
- **TXA-19** -> status has only one verified row, `"AV"` -> `"current"`.
  Anything else defaults to `"current"` (the IG's own default) rather than
  guessing. The IG's other TXA-19 rule *is* implemented: `"CA"`/`"OB"`/
  `"UN"` ride along on `status` as an alternate-codes extension.

Document *content* comes from OBX segments following TXA, not TXA itself.
The IG shows no worked content-transfer example, so concatenating every
TX/FT-typed OBX-5 into one plaintext body and base64-ing it into a separate
Binary (referenced by `content.attachment.url`) is a disclosed extension of
its intent. A message with no usable OBX still converts - it just gets no
Binary and an attachment with no url, which is valid FHIR.
"""

import base64
import uuid

from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.binary import Binary
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.documentreference import DocumentReference, DocumentReferenceContent, DocumentReferenceContext
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.fhirprimitiveextension import FHIRPrimitiveExtension
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


# The one TXA-19 value with a verified target. Everything else defaults to
# the same status, so only this one is a genuine read.
_VERIFIED_AVAILABILITY_STATUS = {"AV": "current"}

# The IG's other TXA-19 rule: "CA", "OB" and "UN" carry no status assignment
# of their own, but the code itself is placed on status as an alternate-codes
# extension. The URL is assigned verbatim by the segment map; the value is
# ID[CodeableConcept], which maps ID.1 to coding.code and assigns no system -
# so none is invented here.
ALTERNATE_CODES_EXTENSION = "http://hl7.org/fhir/StructureDefinition/alternate-codes"
_ALTERNATE_AVAILABILITY_STATUS = {"CA", "OB", "UN"}


def _resolve_status(txa) -> tuple[str, str | None]:
    """TXA-19 -> (DocumentReference.status, the TXA-19 value it was read
    from, or None when the value was not read).

    Only "AV" (Available) is a verified mapping; everything else,
    including absent, defaults to "current" as well - the IG's own stated
    default, see the module docstring for why a fuller crosswalk is not
    attempted. Returning *which* of those happened matters: previously
    this always returned the literal, so an "AV" that really did drive the
    status was recorded as inferred and TXA-19 itself was reported as
    dropped data - the field looked both unread and lost when it had in
    fact been mapped."""
    raw = field_str(txa, 19).strip().upper()
    verified = _VERIFIED_AVAILABILITY_STATUS.get(raw)
    if verified:
        return verified, raw
    return "current", None


def _alternate_status_code(txa) -> str | None:
    """The TXA-19 code the IG puts on `status` as an alternate code.

    "CA"/"OB"/"UN" are not unmapped: the segment map assigns them the
    alternate-codes extension, keeping the sender's own availability code
    reachable even though FHIR's status has no equivalent. We dropped it,
    so a cancelled or obsolete document was indistinguishable from an
    available one in the output."""
    raw = field_str(txa, 19).strip().upper()
    return raw if raw in _ALTERNATE_AVAILABILITY_STATUS else None


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
    status, status_source = _resolve_status(txa)
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
    alternate_status = _alternate_status_code(txa)
    if alternate_status:
        document_reference.status__ext = FHIRPrimitiveExtension(
            extension=[
                Extension(
                    url=ALTERNATE_CODES_EXTENSION,
                    valueCodeableConcept=CodeableConcept(coding=[Coding(code=alternate_status)]),
                )
            ]
        )

    if recorder:
        if alternate_status:
            recorder.record(
                document_reference_id,
                "status__ext.extension[0].valueCodeableConcept.coding[0].code",
                hl7_location("TXA", 19),
                alternate_status,
            )
        if status_source:
            # A verified read: TXA-19 said "AV", and that is what produced
            # "current". Recording it inferred left the field looking both
            # unread and dropped when it had in fact been mapped.
            recorder.record(
                document_reference_id,
                "status",
                hl7_location("TXA", 19),
                status,
                source_value=status_source,
            )
        else:
            recorder.record_inferred(
                document_reference_id,
                "status",
                'TXA-19 (Document Availability Status) has no verified crosswalk beyond "AV"->"current" - any other value, including absent, defaults to "current" (the IG\'s own stated default) rather than being read.',
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
        encounter_locations: list[Resource] = []
        encounter = (
            build_minimal_encounter(pv1, patient.id, recorder=recorder, extra_resources=encounter_locations)
            if pv1 is not None
            else None
        )
        encounter_id = encounter.id if encounter is not None else None

        binary = _build_binary_from_obx(obx_segments, txa, recorder=recorder)
        document_reference, extra_resources = build_document_reference(
            txa, patient.id, encounter_id, binary.id if binary is not None else None, recorder=recorder
        )

        resources_in_order = (
            ([encounter] if encounter is not None else [])
            + encounter_locations
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
