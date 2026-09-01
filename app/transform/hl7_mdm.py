"""FHIR Bundle -> HL7v2 MDM (T02/T04/T06/T08/T10/T11).

All six triggers share one `_BaseMdmBuilder` plus a one-line
`trigger_event` subclass each, mirroring the forward direction, where the
v2-to-FHIR IG ships exactly one trigger-agnostic MDM ConceptMap.

Reverses `app/mappings/mdm.py::build_document_reference`/
`_build_binary_from_obx` field-for-field: TXA-2/-3/-6/-9/-10/-12/-16/-18
(type, content presentation, origination date, originator, authenticator,
master identifier, file name, confidentiality), and one `OBX` per line of
the referenced `Binary`'s decoded text. The original OBX count is not
recoverable - the forward mapper joins them into one string and keeps no
count - so one segment per line is the disclosed choice. `PV1` comes from
the shared `build_minimal_pv1`, MDM's optional Encounter being the same
lifecycle-free shape ORU's is.

TXA-19 round-trips: the forward mapper carries a `"CA"`/`"OB"`/`"UN"` code
on `status.extension` as an alternate code (the IG's own rule), so it is
read back when present, falling back to `"AV"` - the only value the IG
maps to a status at all.

Disclosed round-trip fidelity gap: TXA-3's `"TEXT"` and `"FORMATTED"` both
map forward to `text/plain`, so a `text/plain` attachment always reverses
to `"TEXT"`."""

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.mappings.mdm import ALTERNATE_CODES_EXTENSION
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_ts
from app.transform.hl7_common import (
    build_minimal_pv1,
    build_msh,
    build_pid,
    build_xcn_from_practitioner,
    reverse_cwe,
)

# Reverse of app/mappings/mdm.py::_CONTENT_PRESENTATION_TO_MIME_TYPE -
# "TEXT" is the disclosed representative for text/plain (that MIME type
# also maps back from "FORMATTED", but the reverse can't recover which of
# the two a given DocumentReference originally carried).
_MIME_TYPE_TO_CONTENT_PRESENTATION = {
    "text/plain": "TEXT",
    "text/html": "HTML",
    "application/xml": "XML",
    "application/rtf": "RTF",
    "application/pdf": "PDF",
    "image/jpeg": "JPEG",
}
_DEFAULT_CONTENT_PRESENTATION = "TEXT"

_MASTER_IDENTIFIER_SYSTEM = "urn:interop-tools:document-number"
_FILE_NAME_SYSTEM = "urn:interop-tools:document-file-name"


def _reverse_content_presentation(document_reference) -> str:
    if not document_reference.content:
        return _DEFAULT_CONTENT_PRESENTATION
    content_type = document_reference.content[0].attachment.contentType
    return _MIME_TYPE_TO_CONTENT_PRESENTATION.get(content_type, _DEFAULT_CONTENT_PRESENTATION)


def _resolve_practitioner(reference, practitioners_by_id: dict):
    if reference is None or not reference.reference:
        return None
    practitioner_id = reference.reference.removeprefix("urn:uuid:")
    return practitioners_by_id.get(practitioner_id)


def _reverse_availability_status(document_reference) -> str:
    """TXA-19. The forward mapper puts a "CA"/"OB"/"UN" code on
    `status.extension` as an alternate code (the IG's own rule), so when
    one is there it is the real original value and reverses exactly.
    Otherwise "AV" - the only TXA-19 value the IG maps to a status, and
    the one `status="current"` is verified to come from."""
    extension = getattr(document_reference, "status__ext", None)
    for entry in getattr(extension, "extension", None) or []:
        if entry.url != ALTERNATE_CODES_EXTENSION:
            continue
        concept = entry.valueCodeableConcept
        if concept and concept.coding and concept.coding[0].code:
            return concept.coding[0].code
    return "AV"


def _build_txa(document_reference, practitioners_by_id: dict) -> str:
    fields: dict[int, str] = {
        3: _reverse_content_presentation(document_reference),
        19: _reverse_availability_status(document_reference),
    }

    doc_type = reverse_cwe(document_reference.type)
    if doc_type:
        fields[2] = doc_type

    if document_reference.date:
        fields[6] = format_hl7_ts(document_reference.date)

    originator = _resolve_practitioner(document_reference.author[0] if document_reference.author else None, practitioners_by_id)
    if originator is not None:
        fields[9] = build_xcn_from_practitioner(originator)

    authenticator = _resolve_practitioner(document_reference.authenticator, practitioners_by_id)
    if authenticator is not None:
        fields[10] = build_xcn_from_practitioner(authenticator)

    if document_reference.masterIdentifier and document_reference.masterIdentifier.value:
        fields[12] = document_reference.masterIdentifier.value

    if document_reference.identifier:
        file_name = next((i.value for i in document_reference.identifier if i.system == _FILE_NAME_SYSTEM), None)
        if file_name:
            fields[16] = file_name

    if document_reference.securityLabel and document_reference.securityLabel[0].coding:
        code = document_reference.securityLabel[0].coding[0].code
        if code:
            fields[18] = code

    if document_reference.description:
        fields[25] = document_reference.description

    return segment("TXA", fields, 25)


def _resolve_binary(document_reference, binaries_by_id: dict):
    if not document_reference.content:
        return None
    url = document_reference.content[0].attachment.url
    if not url:
        return None
    return binaries_by_id.get(url.removeprefix("urn:uuid:"))


def _build_obx_segments(binary) -> list[str]:
    # Binary.data decodes to raw bytes at construction time (fhir.resources'
    # own Base64Binary type) - it's already decoded, not a base64 string to
    # decode again here (see docs/build-history.md's fhir.resources gotcha list).
    if binary is None or not binary.data:
        return []
    body = binary.data.decode("utf-8")
    return [segment("OBX", {1: str(i + 1), 2: "TX", 5: line}, 5) for i, line in enumerate(body.split("\n"))]


class _BaseMdmBuilder(MessageBuilder):
    trigger_event: str

    def build_message(self, bundle: Bundle) -> str:
        patient = find_resource(bundle, "Patient")
        if patient is None:
            raise MappingError(f"Bundle has no Patient resource - cannot build an MDM^{self.trigger_event} message")
        document_reference = find_resource(bundle, "DocumentReference")
        if document_reference is None:
            raise MappingError(
                f"Bundle has no DocumentReference resource - cannot build an MDM^{self.trigger_event} message"
            )
        encounter = find_resource(bundle, "Encounter")
        practitioners_by_id = {p.id: p for p in find_resources(bundle, "Practitioner")}
        binaries_by_id = {b.id: b for b in find_resources(bundle, "Binary")}

        msh, msh_dt = build_msh(bundle, "MDM", self.trigger_event)
        evn = segment("EVN", {1: self.trigger_event, 2: msh_dt}, 2)
        pv1 = build_minimal_pv1(encounter, practitioners_by_id)
        pid = build_pid(patient)
        txa = _build_txa(document_reference, practitioners_by_id)
        obx_segments = _build_obx_segments(_resolve_binary(document_reference, binaries_by_id))

        segments = [msh, evn]
        if pv1:
            segments.append(pv1)
        segments.append(pid)
        segments.append(txa)
        segments.extend(obx_segments)

        return "\r".join(segments) + "\r"


class MdmT02Builder(_BaseMdmBuilder):
    trigger_event = "T02"


class MdmT04Builder(_BaseMdmBuilder):
    trigger_event = "T04"


class MdmT06Builder(_BaseMdmBuilder):
    trigger_event = "T06"


class MdmT08Builder(_BaseMdmBuilder):
    trigger_event = "T08"


class MdmT10Builder(_BaseMdmBuilder):
    trigger_event = "T10"


class MdmT11Builder(_BaseMdmBuilder):
    trigger_event = "T11"
