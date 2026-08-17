"""FHIR Bundle -> HL7v2 MDM - the seventh reverse-direction slice, and the
third proof (after SIU's `Appointment`, ORU's `DiagnosticReport`+
`Observation`) that this architecture handles a genuinely different FHIR
shape - `DocumentReference` + a separately-referenced `Binary` carrying the
document's own text body, neither of which any earlier reverse slice
needed to reconstruct. Scoped to T02 alone, the same "one thing per slice"
discipline every earlier reverse slice already established - T04/T06/T08/
T10/T11 are, per `app/mappings/mdm.py`'s own module docstring, handled by
`BaseMdmMapper` with byte-for-byte identical forward logic (the official
v2-to-FHIR IG ships exactly one MDM ConceptMap, trigger-agnostic), so a
real T04/T06/T08/T10/T11 reverse breadth pass is the identical one-line
`trigger_event`-subclass shape `hl7_oru.py`'s own R30/R31/R32/R40 breadth
pass already proved trivial, disclosed as a natural next step rather than
spec­ulatively built ahead of need.

Reverses `app/mappings/mdm.py::build_document_reference`/
`_build_binary_from_obx` field-for-field: TXA-2/-3/-6/-9/-10/-12/-16/-18
(type/content-presentation/origination-date/originator/authenticator/
master-identifier/file-name/confidentiality, CWE + XCN-practitioner
reversal both reused from `hl7_common.py`), and one `OBX` per line of the
referenced `Binary`'s own decoded text body (the reverse of the forward
mapper's own `"\\n".join` over every `TX`/`FT`-typed `OBX-5`) - not a
guess at how many `OBX` segments originally existed, since the forward
mapper itself collapses that count down to one joined string with no
count preserved anywhere in FHIR. `PV1` is rebuilt via the shared
`app.transform.hl7_common.build_minimal_pv1` (MDM's own optional
Encounter is the identical minimal, lifecycle-free shape ORU's is - see
that function's own docstring for why this is its third real consumer,
not MDM-specific logic).

**Two real, disclosed round-trip fidelity gaps specific to this slice**:
TXA-19 (Document Availability Status) can't be recovered at all - the
forward `_resolve_status` unconditionally returns `"current"` regardless
of what TXA-19 actually said (per that function's own module docstring,
only `"AV"` is a verified mapping and every other/absent value defaults
to the identical FHIR status too), so there is no signal on the FHIR side
to reverse from; this builder always regenerates the one verified value,
`"AV"`, the same "can't recover a many-to-one forward mapping's original
input, pick the disclosed representative" precedent `hl7_oru.py`'s own
OBX-11 `"D"`/`"W"` gap and `edi_271.py`'s own `.disposition` gap already
established. TXA-3's own reverse has the identical shape: `"TEXT"` and
`"FORMATTED"` both map forward to the same `text/plain` MIME type, so a
`text/plain` attachment always reverses to the disclosed representative
`"TEXT"`, never `"FORMATTED"`."""

from fhir.resources.R4B.bundle import Bundle

from app.generators.base import segment
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_ts
from app.transform.hl7_common import build_minimal_pv1, build_msh, build_pid, reverse_cwe

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


def _build_xcn_from_practitioner(practitioner) -> str:
    identifier = practitioner.identifier[0].value if practitioner.identifier else ""
    name = practitioner.name[0] if practitioner.name else None
    family = (name.family or "") if name else ""
    given = name.given[0] if name and name.given else ""
    return f"{identifier}^{family}^{given}"


def _resolve_practitioner(reference, practitioners_by_id: dict):
    if reference is None or not reference.reference:
        return None
    practitioner_id = reference.reference.removeprefix("urn:uuid:")
    return practitioners_by_id.get(practitioner_id)


def _build_txa(document_reference, practitioners_by_id: dict) -> str:
    fields: dict[int, str] = {3: _reverse_content_presentation(document_reference), 19: "AV"}

    doc_type = reverse_cwe(document_reference.type)
    if doc_type:
        fields[2] = doc_type

    if document_reference.date:
        fields[6] = format_hl7_ts(document_reference.date)

    originator = _resolve_practitioner(document_reference.author[0] if document_reference.author else None, practitioners_by_id)
    if originator is not None:
        fields[9] = _build_xcn_from_practitioner(originator)

    authenticator = _resolve_practitioner(document_reference.authenticator, practitioners_by_id)
    if authenticator is not None:
        fields[10] = _build_xcn_from_practitioner(authenticator)

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
    # decode again here (see CLAUDE.md's fhir.resources gotcha list).
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
        pv1 = build_minimal_pv1(encounter)
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
