"""Converting a file that holds more than one message.

`convert_to_bundle` has a one-input-one-Bundle contract and takes the
first message only, which is the right shape for the page and for
`/api/convert`. Real files are frequently batched, so this walks the
whole input and yields one result per message.

**A failure is per-message, never per-batch.** Message 300 of 500 being
unconvertible must not lose the other 499, so each item carries either a
Bundle or the error it raised. A batch of real traffic contains messages
this app cannot map, and a caller needs to know which.

**Splitting is per format, because a "message" is:**

- **HL7v2** - segments from one `MSH` up to the next. Split on the raw
  text, since each piece is then an ordinary message the existing
  pipeline converts unchanged.
- **X12** - one `ST`...`SE` transaction set. Split on the *parsed*
  interchange rather than the text: re-wrapping a fragment in its
  original `ISA`/`GS` would leave `GE01` and `IEA01` counting the
  transaction sets of the whole file, so the pieces would be
  non-conformant X12 this app's own validator would flag.
- **C-CDA** - always exactly one. A document is not a batch; several
  documents are several files.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from fhir.resources.R4B.bundle import Bundle

from app.edi.parser import parse_interchange
from app.edi.registry import get_transaction_builder
from app.hl7.errors import MissingSegmentError
from app.hl7.parser import normalize_segment_separators
from app.pipeline import convert_to_bundle, is_x12, is_xml
from app.routes.errors import ERROR_STATUS

# Streamed, so the response size is not the constraint; this bounds the
# wall time instead. Measured against a real server at the cap: first
# line in ~75ms, the whole batch in ~21s. The 2MB request cap
# (app/request_limits.py) binds first for anything larger than a small
# message.
MAX_BATCH_MESSAGES = 5_000


@dataclass(frozen=True)
class BatchItem:
    index: int
    bundle: Bundle | None = None
    error_category: str | None = None
    error_message: str | None = None


def split_hl7_messages(raw_text: str) -> list[str]:
    """One string per `MSH`-led message."""
    normalized = normalize_segment_separators(raw_text)
    segments = normalized.split("\r")
    starts = [i for i, seg in enumerate(segments) if seg.startswith("MSH")]
    if not starts:
        return [raw_text]
    bounds = starts + [len(segments)]
    return ["\r".join(segments[a:b]) for a, b in zip(starts, bounds[1:])]


def count_messages(raw_text: str) -> int:
    """How many messages the input holds, without converting any.

    Lets the cap reject an oversized batch before any work is done.
    """
    if is_xml(raw_text):
        return 1
    if is_x12(raw_text):
        try:
            interchange = parse_interchange(raw_text)
        except Exception:
            return 1  # unparseable: let the conversion report why
        return sum(len(group.transaction_sets) for group in interchange.functional_groups) or 1
    return len(split_hl7_messages(raw_text))


def iter_conversions(raw_text: str) -> Iterator[BatchItem]:
    """One `BatchItem` per message, in file order, as each is converted."""
    if is_xml(raw_text):
        yield _convert_one(0, lambda: convert_to_bundle(raw_text))
        return
    if is_x12(raw_text):
        yield from _iter_x12(raw_text)
        return
    for index, message in enumerate(split_hl7_messages(raw_text)):
        yield _convert_one(index, lambda m=message: convert_to_bundle(m))


def _iter_x12(raw_text: str) -> Iterator[BatchItem]:
    try:
        interchange = parse_interchange(raw_text)
    except Exception as exc:
        yield _as_error(0, exc)
        return
    index = 0
    for group in interchange.functional_groups:
        for transaction_set in group.transaction_sets:
            yield _convert_one(
                index,
                lambda ts=transaction_set: get_transaction_builder(ts.st01, ts.st03).build_bundle(
                    ts, interchange.delimiters
                ),
            )
            index += 1
    if index == 0:
        yield _as_error(0, MissingSegmentError("Interchange contains no ST/SE transaction set to convert"))


def _convert_one(index: int, build) -> BatchItem:
    try:
        return BatchItem(index=index, bundle=build())
    except Exception as exc:
        return _as_error(index, exc)


def _as_error(index: int, exc: Exception) -> BatchItem:
    category, _status = ERROR_STATUS.get(type(exc), ("Conversion error", 500))
    return BatchItem(index=index, error_category=category, error_message=str(exc))


def batch_notice(raw_text: str) -> dict | None:
    """What a single-message endpoint should say about a batched input.

    `convert_to_bundle` takes the first message only, and without this a
    caller posting a three-message file got one message's Bundle and no
    sign the other two existed - the one case where "disclosed rather than
    silent" was silent. None for a single message, so an ordinary response
    is unchanged.
    """
    total = count_messages(raw_text)
    if total <= 1:
        return None
    return {
        "messages": total,
        "converted": 1,
        "note": (
            f"This input holds {total:,} messages; only the first was converted. "
            "POST /api/convert/batch converts all of them."
        ),
    }
