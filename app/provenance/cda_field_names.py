"""Human-readable C-CDA element names for the Data Specification page's
crosswalk table and hover tooltip - the CDA sibling of `app/provenance/
hl7_field_names.py`/`edi_field_names.py`, same idea (a "source field
name" label shown alongside the raw `source_location` string, e.g.
"Family Name" for ".../name[0]/family", "Route of Administration" for
".../routeCode/@code") but resolved from `xpath_location()`'s own
`segment("/"segment)*` shape instead of a fixed segment/field grammar.

**Scoped to exactly the trailing path shapes this app's own CDA mappers
actually record provenance against** - confirmed by grepping every
`xpath_location(...)` call site (and every `f"{base}/..."` string it
feeds into) across `app/cda/*.py`, the same "map what's actually used"
discipline `hl7_field_names.py` already established. An unrecognized
final segment resolves to `None` (no label), never a guess.

**No whole-path parser exists for CDA the way `parse_hl7_location()`/
`parse_edi_location()` do for the other two formats** (see `app/
provenance/cda_locator.py`'s own docstring - a CDA location is composed
*forward*, one relative segment at a time, with no fixed field-numbering
grammar to parse backward from) - `resolve_cda_field_label()` therefore
takes the raw `source_location` string directly, not a parsed dataclass,
splitting it itself and reusing `cda_locator.py`'s own `parse_segment()`
to strip `[N]`/`[LABEL]` brackets off each piece, the one part of that
module's own parsing this label lookup needs.

**Only the *final* path segment (and, for an attribute, its immediate
parent) carries labeling information** - every real call site confirms
this: `code`/`value`/`routeCode`/`statusCode`/etc. are always further
extended with `/@code`, `/@displayName`, or `/@value` before a fact is
ever actually recorded (verified directly, not assumed, by tracing every
`_LOCATION`/`_base`/`*_location` constant in `app/cda/*.py` to its own
`recorder.record(...)` call site) - a bare `code`/`value` never reaches
this lookup in practice. Because a bare `"@code"`/`"@value"` is ambiguous
on its own (dozens of unrelated coded/timestamp fields across every
section share the identical trailing attribute name), the label is keyed
by *(parent tag, attribute)*, not the attribute alone - `"@displayName"`/
`"@negationInd"`/`"@moodCode"` are the one exception, since each means
the identical thing everywhere it appears in this app's own real usage."""

from app.provenance.cda_locator import parse_segment

# Attribute labels that mean the same thing regardless of which element
# carries them - confirmed by real usage: @displayName always names a
# coded value's own display text, @negationInd/@moodCode each occur under
# exactly one entry-level shape per this app's own mappers.
_UNIVERSAL_ATTR_NAMES: dict[str, str] = {
    "displayName": "Display Name",
    "negationInd": "Negation Indicator",
    "moodCode": "Mood Code",
    # An II's two halves. They mean the same thing wherever they appear -
    # @root names the assigning authority (an OID), @extension the id
    # within it - so they need no parent-tag disambiguation. Shortened the
    # same way CX.1 is ("ID", not "ID Number") to stay readable in a narrow
    # table column.
    "root": "Assigning Authority",
    "extension": "ID",
    "codeSystem": "Coding System",
}

# "@code" alone is ambiguous - keyed by the tag immediately preceding it.
_CODE_ATTR_NAMES: dict[str, str] = {
    "code": "Code",
    "value": "Coded Value",
    "administrativeGenderCode": "Administrative Gender",
    "routeCode": "Route of Administration",
    "statusCode": "Status Code",
    "targetSiteCode": "Target Site",
}

# "@value" alone is ambiguous the same way - keyed by parent tag. Several
# of these are IVL_TS bound tags (low/high/center) reached via
# `app/cda/common.py::effective_time_location()`'s own three-shape
# resolution, not `effectiveTime` itself directly.
_VALUE_ATTR_NAMES: dict[str, str] = {
    "effectiveTime": "Effective Time",
    "low": "Start Date/Time",
    "high": "End Date/Time",
    "center": "Date/Time",
    "birthTime": "Date of Birth",
    "time": "Recorded Date/Time",
    "doseQuantity": "Dose Quantity",
    "rateQuantity": "Rate Quantity",
    "telecom": "Contact Point",
    "value": "Value",  # e.g. Family History's own onsetAge value
}

# A bare trailing tag ("descend into this child, take its own text
# content" - see cda_locator.py's own docstring for this shape) - the
# tag name itself is what needs labeling, no parent context required.
_TEXT_TAG_NAMES: dict[str, str] = {
    "family": "Family Name",
    "given": "Given Name",
    "city": "City",
    "state": "State or Province",
    "postalCode": "Postal Code",
    "country": "Country",
    "streetAddressLine": "Street Address Line",
    "text": "Text",
    "title": "Title",
    "id": "ID",
    "lotNumberText": "Lot Number",
}

# A bare trailing tag carrying its own typeCode/classCode bracket filter
# (parse_segment's own `label`) whose meaning depends on that label, not
# just the tag - Family History's own Cause-of-Death relationship check
# is the one real case (the relationship's mere presence, not any nested
# value, is what's recorded - see app/cda/family_history.py).
_RELATIONSHIP_LABEL_NAMES: dict[str, str] = {
    "CAUS": "Cause of Death Relationship",
}


def resolve_cda_field_label(source_location: str | None) -> str | None:
    """Resolves the human-readable label for a `xpath_location()`-shaped
    `source_location` string. Returns `None` for an empty/absent location
    or a final segment this table doesn't recognize."""
    if not source_location:
        return None
    segments = source_location.split("/")
    final = segments[-1]

    if final == "text()":
        return "Text"

    if final.startswith("@"):
        attr = final[1:]
        parent_tag = parse_segment(segments[-2]).tag if len(segments) >= 2 else None
        if attr == "code" and parent_tag:
            return _CODE_ATTR_NAMES.get(parent_tag)
        if attr == "value" and parent_tag:
            return _VALUE_ATTR_NAMES.get(parent_tag)
        return _UNIVERSAL_ATTR_NAMES.get(attr)

    parsed_final = parse_segment(final)
    if parsed_final.tag == "entryRelationship" and parsed_final.label:
        return _RELATIONSHIP_LABEL_NAMES.get(parsed_final.label)
    return _TEXT_TAG_NAMES.get(parsed_final.tag)
