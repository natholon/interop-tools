"""Computes the mapping decisions a conversion actually made, so a
reviewer can accept or reject each one for the message at hand.

**Decisions are computed, never hand-declared.** A register a reviewer is
meant to trust has to be complete, and a hand-maintained list is complete
only until someone forgets to add to it. Both kinds are derived from data
the pillar already produces:

- **Inferred mappings** - every `ProvenanceEntry` with
  `derivation="inferred"` is, by definition, a value this app produced
  without a source field to point at. Those already carry a `reason`.
- **Dropped source data** - components that were present in the source
  message but never recorded as mapped. Derived by diffing the components
  the raw message actually populates against the components the recorder
  saw, using the datatype tables in `hl7_field_names.py`.

**The join problem, and why it needs an explicit allowance.** Some mappers
read several components and collapse them into one value (PV1-3's point of
care + room become a single display string), recording the fact at *field*
level with no component. A pure diff cannot tell that apart from a genuine
drop, and would report every component of such a field as lost. `JOINED_
FIELDS` names those fields and the components they genuinely consume, so
the diff stays accurate. That table is the one hand-maintained piece here -
kept deliberately tiny, and a field listed there still reports any
component *outside* its consumed set as dropped.
"""

from pydantic import BaseModel

from app.hl7.parser import normalize_segment_separators, truncate_to_first_message
from app.provenance.citations import Citation, DEFAULT_BY_FORMAT, DROP_NOT_YET_CHECKED
from app.provenance.hl7_field_names import SEGMENT_FIELD_NAMES, component_names_for_field
from app.provenance.hl7_locator import parse_hl7_location
from app.provenance.models import CrosswalkReport

DecisionKind = str  # "inferred" | "dropped"

# Fields whose mapper reads several components and collapses them into one
# recorded value. Maps (segment, field) -> the components genuinely
# consumed. Any populated component NOT listed is still reported dropped.
#
# Hand-maintained by necessity (nothing in the recorded data distinguishes
# "joined" from "dropped"), so it is kept as small as possible and each
# entry names the function responsible.
JOINED_FIELDS: dict[tuple[str, int], set[int]] = {
    # app.mappings.common.location_display - joins point of care + room.
    ("PV1", 3): {1, 2},
    ("PV1", 6): {1, 2},
    ("AIL", 3): {1, 2},
    # app.mappings.common.person_display - family + given, id as fallback.
    ("PV1", 7): {1, 2, 3},
}
# AIG-3 is deliberately absent: it is CWE-shaped, not PL, and
# app.mappings.siu._build_aig_resource records the component it actually
# used (2, falling back to 1), so the plain diff is already accurate.


class MappingDecision(BaseModel):
    """One reviewable decision. `id` is stable for a given
    (kind, location, target) so a reviewer's accept/reject survives a
    re-run of the same message - it deliberately does NOT include the
    field's *value*, or changing the message would silently discard the
    review."""

    id: str
    kind: DecisionKind
    summary: str
    detail: str | None = None
    source_location: str | None = None
    field_label: str | None = None
    fhir_path: str | None = None
    lost_value: str | None = None
    citation: Citation


def _decision_id(kind: str, *parts: str | None) -> str:
    joined = "|".join(p for p in parts if p)
    return f"{kind}:{joined}"


def _inferred_decisions(report: CrosswalkReport) -> list[MappingDecision]:
    """Every inferred entry is a mapping this app made without a source
    field - exactly the class of decision a reviewer needs to sign off."""
    citation = DEFAULT_BY_FORMAT.get(report.source_format or "", None)
    decisions = []
    for entry in report.entries:
        if entry.derivation != "inferred":
            continue
        decisions.append(
            MappingDecision(
                id=_decision_id("inferred", entry.fhir_path),
                kind="inferred",
                summary=f"{entry.fhir_path} was inferred, not read from the source.",
                detail=entry.reason,
                fhir_path=entry.fhir_path,
                lost_value=entry.value,
                citation=citation or DEFAULT_BY_FORMAT["HL7v2"],
            )
        )
    return decisions


def _mapped_components(report: CrosswalkReport) -> dict[tuple[str, int, int], set[int]]:
    """(segment, field, repetition) -> the components the recorder saw.
    A field-level record (no component) counts as component 1, which is
    what `field_str`'s own default reads."""
    seen: dict[tuple[str, int, int], set[int]] = {}
    for entry in report.entries:
        if not entry.source_location:
            continue
        parsed = parse_hl7_location(entry.source_location)
        if parsed is None:
            continue
        key = (parsed.segment_id, parsed.field, parsed.repetition or 0)
        seen.setdefault(key, set()).add(parsed.component or 1)
    return seen


def _dropped_decisions(
    report: CrosswalkReport, populated: dict[tuple[str, int, int], dict[int, str]]
) -> list[MappingDecision]:
    """A component present in the source but never recorded as mapped."""
    mapped = _mapped_components(report)
    decisions = []
    for key, components in sorted(populated.items()):
        segment_id, field, repetition = key
        consumed = set(mapped.get(key, set())) | JOINED_FIELDS.get((segment_id, field), set())

        # A field nothing touched at all is one decision ("this field is
        # not mapped"), not one per component - a reviewer reading five
        # rows for an entirely-unmapped PV1-8 learns nothing the single
        # row doesn't already say, and the noise buries the fields where
        # only *part* was dropped.
        if not consumed:
            whole = SEGMENT_FIELD_NAMES.get(segment_id, {}).get(field)
            raw = "^".join(components.get(i, "") for i in range(1, max(components) + 1))
            decisions.append(
                MappingDecision(
                    id=_decision_id("dropped", f"{segment_id}-{field}"),
                    kind="dropped",
                    summary=f"{segment_id}-{field} is present in the source but not mapped to any FHIR field.",
                    detail=f"{whole} carried {raw!r}." if whole else f"The field carried {raw!r}.",
                    source_location=f"{segment_id}-{field}",
                    field_label=whole,
                    lost_value=raw,
                    citation=DROP_NOT_YET_CHECKED,
                )
            )
            continue

        for component, value in sorted(components.items()):
            if component in consumed:
                continue
            location = f"{segment_id}-{field}.{component}"
            names = component_names_for_field(segment_id, field)
            label = names.get(component) if names else None
            decisions.append(
                MappingDecision(
                    id=_decision_id("dropped", location),
                    kind="dropped",
                    summary=f"{location} was present in the source but is not mapped to any FHIR field.",
                    detail=(
                        f"{label} carried {value!r}." if label else f"Component {component} carried {value!r}."
                    ),
                    source_location=location,
                    field_label=label,
                    lost_value=value,
                    citation=DROP_NOT_YET_CHECKED,
                )
            )
    return decisions


def compute_decisions(
    report: CrosswalkReport, populated_components: dict[tuple[str, int, int], dict[int, str]] | None = None
) -> list[MappingDecision]:
    """Inferred mappings first, then dropped source data - the order a
    reviewer reads them in (what the app added, then what it discarded)."""
    decisions = _inferred_decisions(report)
    if populated_components:
        decisions.extend(_dropped_decisions(report, populated_components))
    return decisions


def scan_populated_components(raw_text: str) -> dict[tuple[str, int, int], dict[int, str]]:
    """(segment, field, repetition) -> {component: value} for every
    non-empty component in the message.

    Re-splits the raw text rather than walking the parsed `hl7.Message`,
    for the same reason `app/provenance/hl7_locator.py` does: the library
    collapses a field with no `^` to a bare string, so component identity
    is only reliable when read off the original text. Reuses the parser's
    own normalize/truncate helpers so this sees exactly the text the real
    parse saw (one message, `\r`-separated).

    MSH is skipped entirely: its field numbering is offset by one (MSH-1
    is the field separator itself, not a `|`-split token), and none of its
    fields are composites this app decomposes - including it would emit
    noise, not decisions.
    """
    normalized = truncate_to_first_message(normalize_segment_separators(raw_text))
    populated: dict[tuple[str, int, int], dict[int, str]] = {}
    for line in normalized.split("\r"):
        if not line.strip():
            continue
        fields = line.split("|")
        segment_id = fields[0]
        if segment_id == "MSH":
            continue
        for field_index, field_text in enumerate(fields[1:], start=1):
            if not field_text:
                continue
            for repetition, repetition_text in enumerate(field_text.split("~")):
                if not repetition_text:
                    continue
                components = repetition_text.split("^")
                if len(components) == 1:
                    # No component separator at all - nothing to drop.
                    continue
                values = {i: v for i, v in enumerate(components, start=1) if v}
                if values:
                    populated[(segment_id, field_index, repetition)] = values
    return populated


DATA_ABSENT_REASON_URL = "http://hl7.org/fhir/StructureDefinition/data-absent-reason"
# The code used when a reviewer rejects an inferred value. None of the 15
# DataAbsentReason codes means "a reviewer rejected our inference" - that
# concept isn't in the value set. "unknown" ("the value is expected to
# exist but is not known") is the accurate fit: after rejection the value
# certainly exists in reality, this app just has no approved basis to
# assert it. Confirmed with the product owner rather than assumed.
REJECTED_ABSENT_REASON = "unknown"

# How to express a rejected value conformantly, per (resourceType, field).
# "code" = the field's own value set has a null-flavour code, so emit it
# and the resource stays a fully valid model. "absent" = the value set has
# no such code AND the binding is Required, so the only conformant option
# is to drop the value and carry FHIR's data-absent-reason extension on
# the primitive instead.
#
# Every row was checked against that field's published R4 value set - the
# fhir.resources library does NOT validate value sets (it accepts
# intent="null"), so it cannot be used to derive this.
REJECTION_STRATEGY: dict[tuple[str, str], str] = {
    ("Encounter", "status"): "code",           # value set includes "unknown"
    ("Observation", "status"): "code",         # value set includes "unknown"
    ("DiagnosticReport", "status"): "code",    # value set includes "unknown"
    ("Appointment", "status"): "absent",       # no null code; Required binding
    ("DocumentReference", "status"): "absent",  # current|superseded|entered-in-error only
}
NULL_FLAVOUR_CODE = "unknown"


class RejectionOutcome(BaseModel):
    """What actually happened to one rejected decision. `applied=False`
    means the rejection could not be expressed conformantly and the
    original value was left in place - reported rather than silently
    dropped, since a reviewer who rejected something needs to know it
    did not take effect."""

    decision_id: str
    fhir_path: str
    applied: bool
    strategy: str | None = None
    note: str | None = None


def _split_entry_path(fhir_path: str) -> tuple[int, str] | None:
    """"Bundle.entry[3].resource.status" -> (3, "status"). Returns None
    for a Bundle-level fact, which has no entry to rewrite."""
    if not fhir_path.startswith("Bundle.entry["):
        return None
    head, _, tail = fhir_path.partition("].resource.")
    if not tail:
        return None
    try:
        return int(head[len("Bundle.entry[") :]), tail
    except ValueError:
        return None


def apply_rejections(
    bundle_dict: dict, decisions: list[MappingDecision], rejected_ids: set[str]
) -> tuple[dict, list[RejectionOutcome]]:
    """Apply a reviewer's rejections to an already-serialized Bundle.

    Operates on the serialized dict, not the model, because a rejected
    required value has to be expressed as value-absent-plus-extension -
    legal FHIR, but `fhir.resources` enforces required fields even on
    assignment and cannot represent it. Working at the JSON level keeps
    the output conformant rather than bending it to the library's stricter
    model.

    Only nested field paths one level under `.resource` are handled (all
    of HL7v2's inferred surface is `status`); anything deeper is reported
    as not applied rather than silently ignored.
    """
    entries = bundle_dict.get("entry") or []
    outcomes: list[RejectionOutcome] = []
    by_id = {d.id: d for d in decisions}

    for decision_id in sorted(rejected_ids):
        decision = by_id.get(decision_id)
        if decision is None:
            continue
        # NB: a dropped decision has no fhir_path (it never produced a
        # FHIR field), so this must not guard on fhir_path before the
        # kind check below - doing so silently swallowed every rejected
        # drop instead of reporting it.
        if decision.kind != "inferred":
            # Rejecting a *drop* means "this should have been mapped" -
            # a gap to fix in the mapper, not something conversion can
            # act on. Recorded so the reviewer sees it was registered.
            outcomes.append(
                RejectionOutcome(
                    decision_id=decision_id,
                    fhir_path=decision.fhir_path or decision.source_location or "",
                    applied=False,
                    note="Rejecting dropped data flags an unmapped field; conversion cannot supply a mapping.",
                )
            )
            continue

        split = _split_entry_path(decision.fhir_path)
        if split is None:
            outcomes.append(
                RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=False,
                                 note="Bundle-level values are not rejectable.")
            )
            continue
        index, field = split
        if index >= len(entries) or "." in field:
            outcomes.append(
                RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=False,
                                 note="Only top-level fields of a Bundle entry can be rejected.")
            )
            continue

        resource = entries[index].get("resource", {})
        strategy = REJECTION_STRATEGY.get((resource.get("resourceType", ""), field))
        if strategy == "code":
            resource[field] = NULL_FLAVOUR_CODE
        elif strategy == "absent":
            resource.pop(field, None)
            resource[f"_{field}"] = {
                "extension": [{"url": DATA_ABSENT_REASON_URL, "valueCode": REJECTED_ABSENT_REASON}]
            }
        else:
            # Not in the table means nobody has checked this field's own
            # value set. Removing it could silently produce a
            # non-conformant resource, so the value stays and the
            # reviewer is told the rejection did not take effect.
            outcomes.append(
                RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=False,
                                 note="No verified conformant representation for this field; value left unchanged.")
            )
            continue
        outcomes.append(
            RejectionOutcome(decision_id=decision_id, fhir_path=decision.fhir_path, applied=True, strategy=strategy)
        )
    return bundle_dict, outcomes
