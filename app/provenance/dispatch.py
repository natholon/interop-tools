"""Top-level entry point for the Data Specification pillar - the
provenance-tracking mirror of `app/pipeline.py::convert_to_bundle`, but
deliberately NOT built by threading a `recorder` parameter through
`app/pipeline.py`/`app/cda/pipeline.py`/`app/edi/pipeline.py` themselves.
Doing that would add a dead, no-op parameter to two format pipelines
(`app/cda/pipeline.py`, `app/edi/pipeline.py`) whose own builder ABCs
(`CdaDocumentBuilder.build_bundle`, `EdiTransactionBuilder.build_bundle`)
don't do anything with it yet in this phase - widening the diff into files
that don't need to change. Instead this module reuses `app.pipeline`'s own
`is_x12`/`is_xml` sniffing functions directly (the real, stable, single-
purpose functions - not re-derived) and, for HL7v2, does the same tiny
parse-then-dispatch `app/hl7/pipeline.py::convert_hl7_to_bundle` already
does, just with a `ProvenanceRecorder` threaded into the one call
(`mapper.to_bundle(message, recorder=recorder)`) that actually needs it."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.pipeline import convert_cda_to_bundle
from app.edi.pipeline import convert_edi_to_bundle
from app.hl7.parser import field_str, parse_message, require_segment
from app.mappings.registry import get_mapper
from app.pipeline import is_x12, is_xml
from app.provenance.models import CrosswalkReport
from app.provenance.recorder import ProvenanceRecorder
from app.provenance.resolver import resolve_bundle_paths

_EDI_UNSUPPORTED_REASON = "Field-level provenance for X12 EDI is not implemented yet."
_CDA_UNSUPPORTED_REASON = "Field-level provenance for C-CDA is not implemented yet."

# Message types with real, complete field-level instrumentation - not "any
# type whose recorder produced at least one fact," since SIU/ORU/MDM's own
# to_bundle() already accepts a recorder and passes it into build_patient/
# assemble_bundle "for free" (see each of their own to_bundle docstrings),
# so a SIU/ORU/MDM message DOES produce a few real Patient/Bundle-level
# facts despite its own Appointment/DiagnosticReport/DocumentReference
# fields having no instrumentation at all - "entries is non-empty" alone
# would incorrectly report those three types as fully supported. Extended
# as each message type's own provenance slice actually ships.
_INSTRUMENTED_MESSAGE_TYPES = {"ADT"}


def convert_with_provenance(raw_text: str) -> tuple[Bundle, CrosswalkReport]:
    """Sniff the input format (reusing app.pipeline's own sniffing, not
    re-derived) and convert it, returning both the real Bundle (identical
    to what convert_to_bundle would produce - conversion behavior itself
    is never altered by requesting provenance) and a CrosswalkReport.

    EDI/CDA input converts normally but always reports `unsupported=True` -
    neither format has any field-level instrumentation yet (Phase 0's own
    disclosed scope). An HL7v2 message whose message_type isn't in
    `_INSTRUMENTED_MESSAGE_TYPES` (every type this phase except ADT)
    converts normally too and reports `unsupported=True` for the identical
    reason - checked explicitly against that set, not inferred from
    "the recorder produced zero facts": SIU/ORU/MDM's own to_bundle()
    already accepts a recorder and threads it into the PID/MSH fields they
    share with ADT via build_patient/assemble_bundle, so their own recorder
    actually does accumulate a few real facts despite having no
    instrumentation for their own type-specific resource at all - an
    empty-entries heuristic would have misreported them as fully supported.

    Raises the same exception shapes convert_to_bundle does
    (Hl7ParseError/CdaParseError/EdiParseError, MissingSegmentError,
    MappingError, or pydantic.ValidationError) for a genuinely
    unconvertible message - this function never swallows a real
    conversion failure into an "unsupported" report."""
    if is_x12(raw_text):
        bundle = convert_edi_to_bundle(raw_text)
        return bundle, CrosswalkReport(
            source_format="EDI", entries=[], unsupported=True, unsupported_reason=_EDI_UNSUPPORTED_REASON
        )
    if is_xml(raw_text):
        bundle = convert_cda_to_bundle(raw_text)
        return bundle, CrosswalkReport(
            source_format="CDA", entries=[], unsupported=True, unsupported_reason=_CDA_UNSUPPORTED_REASON
        )

    message = parse_message(raw_text)
    msh = require_segment(message, "MSH")
    message_type = field_str(msh, 9, component=1)
    trigger_event = field_str(msh, 9, component=2)
    mapper = get_mapper(message_type, trigger_event)

    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = mapper.to_bundle(message, recorder=recorder)
    entries = resolve_bundle_paths(bundle, recorder)
    unsupported = message_type not in _INSTRUMENTED_MESSAGE_TYPES
    unsupported_reason = (
        f"Field-level provenance for {message_type}^{trigger_event} is not implemented yet." if unsupported else None
    )
    return bundle, CrosswalkReport(
        message_type=message_type,
        trigger_event=trigger_event,
        source_format="HL7v2",
        entries=entries,
        unsupported=unsupported,
        unsupported_reason=unsupported_reason,
    )
