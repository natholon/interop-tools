"""Top-level entry point for the Data Specification pillar - the
provenance-tracking mirror of `app/pipeline.py::convert_to_bundle`, but
deliberately NOT built by threading a `recorder` parameter through
`app/pipeline.py`/`app/cda/pipeline.py`/`app/edi/pipeline.py` themselves.
Instead this module reuses `app.pipeline`'s own `is_x12`/`is_xml` sniffing
functions directly (the real, stable, single-purpose functions - not
re-derived) and, for all three formats, does the same tiny parse-then-
dispatch `app/hl7/pipeline.py::convert_hl7_to_bundle`/
`app/cda/pipeline.py::convert_cda_to_bundle`/
`app/edi/pipeline.py::convert_edi_to_bundle` already do, just with a
`ProvenanceRecorder` threaded into the one call
(`mapper.to_bundle(...)`/`builder.build_bundle(...)`) that actually needs
it."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.parser import parse_document
from app.cda.registry import get_document_builder
from app.cda.validation import resolve_trigger_event as resolve_cda_trigger_event
from app.edi.common import resolve_837_variant
from app.edi.parser import first_transaction_set, parse_interchange
from app.edi.registry import get_transaction_builder
from app.hl7.errors import MissingSegmentError
from app.hl7.parser import field_str, parse_message, require_segment
from app.mappings.registry import get_mapper
from app.pipeline import is_x12, is_xml
from app.provenance.models import CrosswalkReport
from app.provenance.recorder import ProvenanceRecorder
from app.provenance.resolver import resolve_bundle_paths

# X12 transaction-set families (keyed by ST01, EXCEPT the 837 trio - see
# below) with real, complete field-level instrumentation - the EDI-format
# mirror of _INSTRUMENTED_MESSAGE_TYPES below. Extended as each family's
# own provenance slice actually ships. 270/271 became this pillar's own
# first proof the architecture generalizes to a delimited-text format with
# no XML/pipe-delimited structure to lean on - see CLAUDE.md's own Data
# Specification section for the edi_location() design notes.
#
# 837P/837I/837D share the literal ST01="837" (see app/edi/registry.py's
# own get_transaction_builder docstring), so a single "837" entry here
# would incorrectly mark all three variants instrumented the moment any
# one of them ships - keyed by the resolved variant string ("837P"/"837I"/
# "837D", via the same resolve_837_variant() registry.py/validation.py
# both already use) instead, the identical finer-than-ST01 granularity
# problem C-CDA's own per-section (not per-document-type) instrumentation
# already had to solve.
_INSTRUMENTED_TRANSACTION_SETS = {"270", "271", "276", "277", "278", "835", "837P", "837I", "837D"}
# Every section registered in app/cda/registry.py::SECTION_BUILDERS is now
# instrumented: all seven general-purpose sections (Problems, Medications,
# Allergies, Immunizations, Vital Signs, Results, Procedures) plus, for
# free via shared entry-level builders, Discharge Summary's own Hospital
# Discharge Diagnosis (reuses problems.py::build_condition) and Discharge
# Medications (reuses medications.py::build_medication_request) - see each
# module's own docstring.
#
# `unsupported` still deliberately stays True unconditionally for every
# CDA document, though - not graduated to a per-document-type
# _INSTRUMENTED_...-style set the way HL7v2/EDI's own binary
# "registered => fully covered" bar works. That bar doesn't translate
# cleanly here: unlike an HL7v2 message type (where every segment the
# standard defines for that type is either read or a disclosed, narrow
# per-field gap) or an EDI transaction set, a C-CDA document type carries
# its own IG-required *sections* this app's forward-conversion pillar
# itself never maps at all - Discharge Summary's own Hospital Course/Plan
# of Treatment, and History and Physical's own required narrative sections
# (Reason for Visit, HPI, Physical Exam, etc., see
# app/cda/history_and_physical.py/discharge_summary.py) - not merely
# "outside this app's read scope" the way an unread HL7v2 field is, but
# entire required sections a real consumer would expect reflected in the
# output. Marking any document type "fully supported" here would overclaim
# relative to what conversion itself actually guarantees for it - the
# identical, permanent disclosure the forward-conversion pillar already
# makes for this exact gap (see CLAUDE.md's own C-CDA subsection).
_CDA_UNSUPPORTED_REASON = (
    "Field-level provenance for C-CDA covers every section this app's "
    "forward conversion recognizes (the document header and all seven "
    "general-purpose sections), but some document types (Discharge "
    "Summary, History and Physical) carry their own additional IG-required "
    "sections this app's conversion never maps at all - so no C-CDA "
    "document type is ever reported fully supported."
)

# Message types with real, complete field-level instrumentation. Extended
# as each message type's own provenance slice actually ships.
_INSTRUMENTED_MESSAGE_TYPES = {"ADT", "SIU", "ORU", "MDM"}


def convert_with_provenance(raw_text: str) -> tuple[Bundle, CrosswalkReport]:
    """Sniff the input format (reusing app.pipeline's own sniffing, not
    re-derived) and convert it, returning both the real Bundle (identical
    to what convert_to_bundle would produce - conversion behavior itself
    is never altered by requesting provenance) and a CrosswalkReport.

    EDI input converts normally and threads a recorder through when its own
    ST01 is in `_INSTRUMENTED_TRANSACTION_SETS` (so `entries` is genuinely
    non-empty for a 270/271 transaction set) and reports `unsupported=True`
    for every other transaction-set family, checked explicitly the same
    way HL7v2's own `_INSTRUMENTED_MESSAGE_TYPES` is - not inferred from
    "the recorder produced zero facts," since every EDI family's own
    `build_bundle()` accepts a recorder and threads it into the shared
    `assemble_bundle()` call it shares with every other family (see
    app/edi/common.py), so a not-yet-instrumented family's own recorder can
    still accumulate a Bundle.identifier/.timestamp fact or two despite
    having no instrumentation for its own resource-specific fields at all.
    C-CDA input converts normally and threads a recorder through too (so
    `entries` may be genuinely non-empty for a document with a header
    and/or a Problems section), but also always reports `unsupported=True`
    - see `_CDA_UNSUPPORTED_REASON`'s own comment for why no CDA document
    type is "fully instrumented" yet. An HL7v2 message whose message_type
    isn't in `_INSTRUMENTED_MESSAGE_TYPES` converts normally too and
    reports `unsupported=True` for the identical reason.

    Raises the same exception shapes convert_to_bundle does
    (Hl7ParseError/CdaParseError/EdiParseError, MissingSegmentError,
    MappingError, or pydantic.ValidationError) for a genuinely
    unconvertible message - this function never swallows a real
    conversion failure into an "unsupported" report."""
    if is_x12(raw_text):
        interchange = parse_interchange(raw_text)
        transaction_set = first_transaction_set(interchange)
        if transaction_set is None:
            raise MissingSegmentError("Interchange contains no ST/SE transaction set to convert")
        builder = get_transaction_builder(transaction_set.st01, transaction_set.st03)
        recorder = ProvenanceRecorder(source_format="EDI")
        bundle = builder.build_bundle(transaction_set, interchange.delimiters, recorder=recorder)
        entries = resolve_bundle_paths(bundle, recorder)
        st01 = transaction_set.st01.strip().upper()
        # 837P/837I/837D share ST01="837" - resolve which variant this
        # actually is (the same resolver registry.py's own dispatch uses)
        # so _INSTRUMENTED_TRANSACTION_SETS can track them independently.
        trigger_event = resolve_837_variant(transaction_set.st03) if st01 == "837" else st01
        unsupported = trigger_event not in _INSTRUMENTED_TRANSACTION_SETS
        unsupported_reason = (
            f"Field-level provenance for X12 {trigger_event} is not implemented yet." if unsupported else None
        )
        return bundle, CrosswalkReport(
            message_type="EDI",
            trigger_event=trigger_event,
            source_format="EDI",
            entries=entries,
            unsupported=unsupported,
            unsupported_reason=unsupported_reason,
        )
    if is_xml(raw_text):
        document = parse_document(raw_text)
        builder = get_document_builder(document)
        recorder = ProvenanceRecorder(source_format="CDA")
        bundle = builder.build_bundle(document, recorder=recorder)
        entries = resolve_bundle_paths(bundle, recorder)
        return bundle, CrosswalkReport(
            message_type="CDA",
            trigger_event=resolve_cda_trigger_event(document),
            source_format="CDA",
            entries=entries,
            unsupported=True,
            unsupported_reason=_CDA_UNSUPPORTED_REASON,
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
