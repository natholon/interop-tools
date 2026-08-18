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
it.

**Dedup-aware provenance, an opt-in `deduplicate` parameter**: `app/
dedup.py::deduplicate_bundle` merges duplicate Patient/Practitioner/
Organization/Location entries within an already-built Bundle - a real,
evidenced case for 837P/837I's own Billing-vs-Rendering-provider shape
(see that module's own docstring). Wiring it in here turned out to need
*zero* changes to `app/provenance/{recorder,resolver,highlighting}.py`
themselves - `ProvenanceRecorder`'s own facts are keyed by
`(resource_id, relative_path)`, and `deduplicate_bundle` never changes a
surviving resource's own `.id` (only entries for the *removed* duplicate
drop out of `bundle.entry`, via `model_copy(deep=True)` followed by an
entry-list filter), so a fact recorded during mapping against a resource
that dedup goes on to remove simply hits `resolve_bundle_paths`'s own
pre-existing "a `resource_id` not found in `bundle.entry` is skipped, not
raised" branch (see that function's own docstring - written for a
different, hypothetical reason originally, but it's exactly the
mechanism this needs) once `deduplicate_bundle` runs *before*
`resolve_bundle_paths`, not after. The surviving (canonical) resource's
own facts are completely unaffected - dedup never rewrites a kept
resource's own fields, only *other* resources' `Reference`s that pointed
at a resource now removed - so they resolve against their own,
correctly-recomputed post-dedup `Bundle.entry[N]` index exactly as
before. This also means a removed duplicate's own source segment/element
(e.g. an 837P claim's Rendering Provider `NM1` loop, once its
`Practitioner` gets merged into the Billing Provider's) simply shows no
highlight in the Data Specification page's correlated view - the
correct, honest result, since that segment's own data didn't produce a
*separate* surviving resource for a highlight to point at."""

from fhir.resources.R4B.bundle import Bundle

from app.cda.parser import parse_document
from app.cda.registry import get_document_builder
from app.cda.validation import resolve_trigger_event as resolve_cda_trigger_event
from app.dedup import DedupResult, deduplicate_bundle
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
# Allergies, Immunizations, Vital Signs, Results, Procedures), Discharge
# Summary's own Hospital Discharge Diagnosis/Discharge Medications sections
# (for free, via shared entry-level builders - problems.py::build_condition,
# medications.py::build_medication_request), and now all twelve narrative-
# section templateIds app/cda/narrative_sections.py registers (Discharge
# Summary's own Hospital Course/Plan of Treatment, History and Physical's
# own nine required narrative sections) - see each module's own docstring.
#
# `unsupported` still deliberately stays True unconditionally for every CDA
# document, though - not graduated to a per-document-type
# _INSTRUMENTED_...-style set the way HL7v2/EDI's own binary
# "registered => fully covered" bar works. **The justification narrowed
# once narrative_sections.py shipped, but didn't disappear**: every section
# either document type's own IG requires now converts to *something* (a
# real structured FHIR resource, or - for the ten narrative-only sections -
# a DocumentReference+Binary pair), so this is no longer "entire required
# sections this app's forward-conversion pillar never maps at all" the way
# it was before. What remains is narrower: three of those twelve sections
# (Plan of Treatment, Social History, Family History) can carry real
# structured entries in practice - a Plan of Care Activity Observation,
# Social History/Smoking Status Observations, a Family History Organizer -
# that this app deliberately doesn't parse yet (see app/cda/narrative_
# sections.py's own docstring for why), so a document carrying one of those
# still isn't *fully* represented even though it's no longer *unrepresented*.
# Marking any document type "fully supported" here would still overclaim
# relative to what conversion itself actually guarantees for it - a
# narrower, but still real and disclosed, permanent gap the forward-
# conversion pillar itself already discloses (see CLAUDE.md's own C-CDA
# subsection).
_CDA_UNSUPPORTED_REASON = (
    "Field-level provenance for C-CDA covers the document header, all "
    "seven general-purpose sections, and every narrative-only section "
    "either document type's own IG requires - but Plan of Treatment/Social "
    "History/Family History can also carry real structured clinical data "
    "this app's conversion doesn't parse yet (only their narrative text), "
    "so no C-CDA document type is ever reported fully supported."
)

# Message types with real, complete field-level instrumentation. Extended
# as each message type's own provenance slice actually ships.
_INSTRUMENTED_MESSAGE_TYPES = {"ADT", "SIU", "ORU", "MDM"}


def convert_with_provenance(raw_text: str, deduplicate: bool = False) -> tuple[Bundle, CrosswalkReport, DedupResult | None]:
    """Sniff the input format (reusing app.pipeline's own sniffing, not
    re-derived) and convert it, returning the real Bundle (identical to
    what convert_to_bundle would produce when `deduplicate=False` -
    conversion behavior itself is never altered by requesting provenance),
    a CrosswalkReport, and - only when `deduplicate=True` - the
    `DedupResult` describing what was merged (`None` otherwise, the same
    "opt-in, never automatic" contract `app/dedup.py` itself establishes).
    See this module's own docstring for how dedup and provenance resolution
    interact.

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
        bundle, dedup_result = _deduplicate_if_requested(bundle, deduplicate)
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
        ), dedup_result
    if is_xml(raw_text):
        document = parse_document(raw_text)
        builder = get_document_builder(document)
        recorder = ProvenanceRecorder(source_format="CDA")
        bundle = builder.build_bundle(document, recorder=recorder)
        bundle, dedup_result = _deduplicate_if_requested(bundle, deduplicate)
        entries = resolve_bundle_paths(bundle, recorder)
        return bundle, CrosswalkReport(
            message_type="CDA",
            trigger_event=resolve_cda_trigger_event(document),
            source_format="CDA",
            entries=entries,
            unsupported=True,
            unsupported_reason=_CDA_UNSUPPORTED_REASON,
        ), dedup_result

    message = parse_message(raw_text)
    msh = require_segment(message, "MSH")
    message_type = field_str(msh, 9, component=1)
    trigger_event = field_str(msh, 9, component=2)
    mapper = get_mapper(message_type, trigger_event)

    recorder = ProvenanceRecorder(source_format="HL7v2")
    bundle = mapper.to_bundle(message, recorder=recorder)
    bundle, dedup_result = _deduplicate_if_requested(bundle, deduplicate)
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
    ), dedup_result


def _deduplicate_if_requested(bundle: Bundle, deduplicate: bool) -> tuple[Bundle, DedupResult | None]:
    """`deduplicate_bundle(bundle)` when requested, run *before*
    `resolve_bundle_paths` in every branch above - see this module's own
    docstring for why that ordering, not `resolve_bundle_paths` itself, is
    what makes provenance resolution dedup-safe for free."""
    if not deduplicate:
        return bundle, None
    result = deduplicate_bundle(bundle)
    return result.bundle, result
