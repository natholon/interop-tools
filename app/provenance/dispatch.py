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
# "registered => fully covered" bar works. **The justification has
# narrowed three times now, not disappeared**: once when narrative_
# sections.py shipped (every section either document type's own IG
# requires converts to *something*, not silently skipped), again once
# app/cda/social_history.py/family_history.py/plan_of_treatment.py's own
# follow-up slice shipped real structured-entry parsing (Observation/
# FamilyMemberHistory/CarePlan) for the three sections that can carry one,
# and again once app/cda/procedures.py's own Indication/Comment Activity/
# `author`->`Procedure.recorder` follow-up shipped, a fourth time once
# app/cda/common.py's own originalText -> CodeableConcept.text resolution
# shipped (both the inline and the narrative-anchor `<reference
# value="#ID"/>` shapes), and a fifth once app/cda/discharge_medications.py
# gained its own `MedicationRequest.category="discharge"` marker.
#
# **The last standing item, `author` -> a FHIR `Provenance` resource, has
# since been reclassified as a deliberate, permanent scope decision rather
# than deferred work** - see CLAUDE.md's own C-CDA subsection for the full
# reasoning. In short: `Provenance` models "who created/revised/signed
# this record, and when," which presumes a system that STORES records over
# time; this app is a stateless converter with no record lifecycle for
# such a resource to describe (its required `recorded` timestamp has no
# honest value here - conversion time is clinically meaningless). Where
# the source `<author>` has a real home on the resource itself, the plain
# attribute already carries it (`Procedure.recorder`,
# `Annotation.authorReference`), which reaches a downstream consumer
# without fabricating an audit record for an event that never happened.
# The C-CDA on FHIR IG itself explicitly declines to give guidance here
# ("does not provide definitive CDA <-> FHIR guidance on when resource
# attributes... vs. dedicated Provenance resources should be used").
#
# With that reclassified, C-CDA now sits exactly where every fully-
# instrumented HL7v2 message type already does: a deliberate decision NOT
# to map something has never counted against provenance coverage here
# (app/mappings/mdm.py leaves TXA-13/TXA-17 unmapped for well-documented
# reasons, and MDM still reports unsupported=False). So every C-CDA
# document type now graduates to fully supported too - see
# _INSTRUMENTED_CDA_DOCUMENT_TYPES below. This constant is kept for the
# one case that genuinely remains: a document type this app doesn't
# recognize at all.
_CDA_UNSUPPORTED_REASON = (
    "Field-level provenance for C-CDA is implemented for the CCD, "
    "Discharge Summary, and History and Physical document types; this "
    "document's own type isn't one this app recognizes."
)

# C-CDA document types with real, complete field-level instrumentation -
# the CDA-format mirror of _INSTRUMENTED_MESSAGE_TYPES/
# _INSTRUMENTED_TRANSACTION_SETS below. Every section registered in
# app/cda/registry.py::SECTION_BUILDERS is instrumented, so this is
# keyed per document type (as resolved by
# app.cda.validation.resolve_trigger_event) rather than per section.
_INSTRUMENTED_CDA_DOCUMENT_TYPES = {"CCD", "DISCHARGESUMMARY", "HISTORYANDPHYSICAL"}

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
    C-CDA input converts normally and threads a recorder through too;
    every document type this app recognizes (CCD, Discharge Summary,
    History and Physical) is now fully instrumented and reports
    `unsupported=False` - see `_INSTRUMENTED_CDA_DOCUMENT_TYPES` and the
    long comment above it for why that graduation is correct rather than
    an overclaim. An HL7v2 message whose message_type isn't in
    `_INSTRUMENTED_MESSAGE_TYPES` converts normally too and reports
    `unsupported=True`.

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
        document_type = resolve_cda_trigger_event(document)
        unsupported = document_type not in _INSTRUMENTED_CDA_DOCUMENT_TYPES
        return bundle, CrosswalkReport(
            message_type="CDA",
            trigger_event=document_type,
            source_format="CDA",
            entries=entries,
            unsupported=unsupported,
            unsupported_reason=_CDA_UNSUPPORTED_REASON if unsupported else None,
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
