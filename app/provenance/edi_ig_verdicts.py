"""Per-element verdicts for X12 elements this app does not map.

Every other format's drop register cites a published mapping table: HL7v2
has the v2-to-FHIR IG, C-CDA has C-CDA on FHIR. **X12 has none** - the TR3
Implementation Guides are commercial, and no free official X12-to-FHIR
crosswalk exists for any transaction set here. So EDI's register cited that
absence for *every* dropped element, uniformly, which made its "every drop
is checked" claim structural rather than earned: the citation was true as a
general statement and told a reviewer nothing about the element in front of
them, including whether it had an obvious FHIR home nobody had looked for.

It did. Working through the list found real gaps behind the blanket
citation - every party's address and contact numbers, the coverage order
and relationship, a person's middle name and suffix, a tax ID, a service
line's unit of measure, a provider's taxonomy - all now mapped. What
remains is recorded here, one verdict per element, each stating *why*
rather than restating that no crosswalk exists.

**These verdicts are this project's own reading**, not a published mapping.
Where a verdict says "no R4 target", it means the base R4 resource this app
builds has no field for it - checked against the published StructureDefinition,
not asserted. Where R5 added one, the verdict says so, because that is the
honest reason an R4-only converter cannot carry it.

Keys are `SEGMENT-ELEMENT.COMPONENT`, `SEGMENT-ELEMENT`, or a bare
`SEGMENT`, tried most specific first - a bare key covers every element of
a segment whose elements all share one answer, which is what `HI`'s
repeating diagnosis composites need. An element matching no key keeps the
general absent-crosswalk citation, so "unlisted" still means "not
individually checked".
"""

from app.provenance.models import Citation

# --- verdict kinds ---------------------------------------------------
# The base R4 resource this app builds defines no field for it. R5 may.
NO_R4_TARGET = "no_r4_target"
# X12 publishes no free code table for it, so any FHIR coding would carry
# a code this app cannot describe or validate.
NO_PUBLISHED_CODE_LIST = "no_published_code_list"
# Structural or administrative: it says how the interchange was assembled
# or attests to a business fact, rather than carrying clinical or
# financial content.
ADMINISTRATIVE = "administrative"
# A real target exists, but only for a party shape this app does not build
# it on - stated rather than silently skipped.
PARTIAL = "partial"
# Deliberately out of scope, with the scope decision named.
OUT_OF_SCOPE = "out_of_scope"

_BASE_URL = "https://x12.org/codes"

_NOTES = {
    NO_R4_TARGET: (
        "The base R4 resource this app builds defines no field for this element - checked against the "
        "published StructureDefinition rather than assumed. Carrying it would mean inventing an extension "
        "this project cannot point at a definition for."
    ),
    NO_PUBLISHED_CODE_LIST: (
        "X12 publishes no free code table for this element, so a FHIR coding built from it would carry a "
        "code neither this app nor a downstream consumer can resolve to a meaning."
    ),
    ADMINISTRATIVE: (
        "Describes how the interchange was assembled, or attests to a business fact about the submission, "
        "rather than carrying clinical or financial content a FHIR resource models."
    ),
    PARTIAL: (
        "A real FHIR target exists, but not for the party shape this element appears on here. Stated rather "
        "than silently skipped, since the same element does map elsewhere in this app."
    ),
    OUT_OF_SCOPE: "Deliberately out of scope for this converter - see the reason for which decision applies.",
}


def _citation(kind: str, reason: str) -> Citation:
    return Citation(
        title=f"X12: {reason}",
        url=_BASE_URL,
        # Never authoritative: no published crosswalk backs any of this.
        authoritative=False,
        note=_NOTES[kind],
    )


# --- the table -------------------------------------------------------
EDI_VERDICTS: dict[str, tuple[str, str]] = {
    # BPR banking detail. R5 added .method and .kind for exactly these;
    # R4B has neither, and this app does not ship an R5-to-R4 backport
    # extension it cannot verify resolves - the same call already made for
    # C-CDA's ED-typed values.
    "BPR-1": (NO_R4_TARGET, "transaction handling code; R5 added PaymentReconciliation.kind, R4B has none"),
    "BPR-3": (NO_R4_TARGET, "credit/debit flag; FHIR Money is signed but no R4 field states the direction"),
    "BPR-4": (NO_R4_TARGET, "payment method; R5 added PaymentReconciliation.method, R4B has none"),
    "BPR-5": (NO_R4_TARGET, "payment format code, part of the same R5-only payment detail"),
    "BPR-6": (ADMINISTRATIVE, "sender bank ID qualifier - ACH routing detail, not payment content"),
    "BPR-7": (ADMINISTRATIVE, "sender bank identification number - ACH routing detail"),
    "BPR-8": (ADMINISTRATIVE, "sender account number qualifier - ACH routing detail"),
    "BPR-9": (ADMINISTRATIVE, "sender account number - ACH routing detail"),
    "BPR-10": (ADMINISTRATIVE, "originating company identifier - ACH routing detail"),
    "BPR-12": (ADMINISTRATIVE, "receiver bank ID qualifier - ACH routing detail"),
    "BPR-13": (ADMINISTRATIVE, "receiver bank identification number - ACH routing detail"),
    "BPR-14": (ADMINISTRATIVE, "receiver account number qualifier - ACH routing detail"),
    "BPR-15": (ADMINISTRATIVE, "receiver account number - ACH routing detail"),
    # CLM attestations. Base R4 Claim has no field for a signature on
    # file, an assignment of benefits, or a release-of-information code.
    "CLM-5.1": (NO_R4_TARGET, "837I facility code is a UB-04 Type of Bill, a different vocabulary from 837P/D's place of service"),
    "CLM-5.2": (NO_PUBLISHED_CODE_LIST, "facility code qualifier, meaningful only alongside a mapped CLM05-1"),
    "CLM-5.3": (NO_R4_TARGET, "claim frequency type code - original/replacement/void has no base R4 Claim field"),
    "CLM-6": (ADMINISTRATIVE, "provider signature on file - an attestation about the submission"),
    "CLM-7": (ADMINISTRATIVE, "provider accept-assignment code - a payer contract term, not claim content"),
    "CLM-8": (ADMINISTRATIVE, "benefits assignment certification indicator - an attestation"),
    "CLM-9": (ADMINISTRATIVE, "release of information code - a consent attestation about the submission"),
    # CL1 admission detail.
    "CL1-1": (NO_R4_TARGET, "admission type code; no ClaimInformationCategory code covers it, unlike CL103's discharge status"),
    "CL1-2": (NO_R4_TARGET, "admission source code; same absent category as CL101"),
    # CLP remainder.
    "CLP-8": (NO_R4_TARGET, "facility type code on a remittance - PaymentReconciliation models no facility"),
    "CLP-9": (NO_R4_TARGET, "claim frequency code on a remittance - same absent field as CLM05-3"),
    # HI's non-diagnosis usages. Keyed on the bare segment because the
    # same answer applies at every element position: HI01 through HI12 are
    # twelve slots for the same kind of composite.
    "HI": (OUT_OF_SCOPE, "occurrence, value and condition code usages, each needing its own supportingInfo category decision"),
    # PER's own two unmappable elements. PER03-08 (the qualifier/value
    # pairs) do map, to ContactPoint.
    "PER-1": (NO_R4_TARGET, "contact function code - ContactPoint has no field for who the contact is to the party"),
    "PER-2": (NO_R4_TARGET, "contact name - a bare ContactPoint list carries no name, and this app builds no Organization.contact"),
    # PRV.
    "PRV-1": (PARTIAL, "provider code; the taxonomy maps to Claim.careTeam.qualification only for a rendering or attending provider"),
    "PRV-2": (NO_R4_TARGET, "reference qualifier, consumed to decide whether PRV03 is a taxonomy at all"),
    "PRV-3": (PARTIAL, "taxonomy on a billing provider; Claim.careTeam is the rendering/attending provider and Organization has no qualification field"),
    # REF beyond EI.
    "REF-1": (NO_PUBLISHED_CODE_LIST, "reference qualifier; only EI (employer ID) has a canonical FHIR naming system"),
    "REF-2": (NO_PUBLISHED_CODE_LIST, "reference value for a qualifier with no canonical system to place it on"),
    # STC detail beyond the category/status pair.
    "STC-1.3": (NO_R4_TARGET, "entity code identifying who the status is about - Task models no such role"),
    "STC-2": (NO_R4_TARGET, "status effective date; Task.businessStatus carries no date of its own"),
    "STC-3": (ADMINISTRATIVE, "action code telling the receiver what to do next about the submission"),
    "STC-5": (NO_R4_TARGET, "total claim charge on a status response - Task models no money"),
    "STC-6": (NO_R4_TARGET, "claim payment amount on a status response - Task models no money"),
    # TRN.
    "TRN-3": (ADMINISTRATIVE, "originating company identifier, part of the trace-number envelope rather than its value"),
    # UM prior-authorisation detail.
    "UM-1": (NO_R4_TARGET, "request category code; Claim.type is bound to a fixed set UM01 does not map onto"),
    "UM-2": (NO_R4_TARGET, "certification type code - Claim models no certification lifecycle"),
    "UM-4.1": (OUT_OF_SCOPE, "service location composite, part of the 2000F service-level loop this app does not model"),
    "UM-4.2": (OUT_OF_SCOPE, "service location qualifier, same 2000F scope limit"),
    "UM-9": (NO_R4_TARGET, "delay reason code explaining a late submission - Claim models no submission timeliness"),
    # AAA rejection detail beyond the outcome.
    "AAA-2": (NO_PUBLISHED_CODE_LIST, "agency qualifier for the reject reason, with no canonical system"),
    "AAA-4": (ADMINISTRATIVE, "follow-up action code telling the sender what to do next"),
    # EB detail beyond what CoverageEligibilityResponse models.
    "EB-2": (NO_R4_TARGET, "coverage level code; CoverageEligibilityResponse models no per-level benefit breakdown"),
    "EB-4": (NO_R4_TARGET, "insurance type code, part of that same absent breakdown"),
    # DTP on a claim-status request.
    "DTP-1": (NO_R4_TARGET, "date qualifier on a 276 status request; Task carries no service period"),
    "DTP-2": (NO_R4_TARGET, "date format qualifier for the same unmapped date"),
    "DTP-3": (NO_R4_TARGET, "service date on a 276 status request; Task carries no service period"),
    # PAT beyond the relationship.
    "PAT-1": (PARTIAL, "patient relationship code; it maps to Coverage.relationship, and is reported only where no Coverage is built"),
    # Elements that normally map, and are reported only when this
    # particular message does not give the mapping what it needs.
    "CLP-3": (PARTIAL, "total claim charge; it maps to ClaimResponse.total[submitted], which needs the 2100 patient this claim does not name"),
    "CLP-5": (PARTIAL, "patient responsibility; same ClaimResponse the absent 2100 patient loop prevents building"),
    "CLP-6": (PARTIAL, "claim filing indicator; maps to ClaimResponse.subType, absent for the same reason"),
    "CLP-7": (PARTIAL, "payer claim control number; maps to ClaimResponse.identifier, absent for the same reason"),
    "PER-3": (NO_PUBLISHED_CODE_LIST, "contact number qualifier outside TE/FX/EM/UR, which are the shapes ContactPoint.system can state"),
    "PER-4": (NO_PUBLISHED_CODE_LIST, "contact number whose qualifier names no ContactPoint system"),
    "SV1-7": (PARTIAL, "diagnosis pointer that resolves to no HI position, so it points at no Claim.diagnosis entry"),
    "SBR-2": (PARTIAL, "relationship code outside the set with an unambiguous subscriber-relationship counterpart - notably \"21\" (Unknown)"),
}


def edi_verdict(location: str) -> Citation | None:
    """The verdict for a dropped element's location, or None when it has
    not been individually checked - which keeps the general
    absent-crosswalk citation, so "unlisted" still means "unchecked"."""
    # Most specific first: component, then element, then the whole
    # segment. The bracketed occurrence is not part of the key - which
    # occurrence a drop came from never changes the answer.
    base = location.split("[")[0] + location.partition("]")[2] if "[" in location else location
    element = base.split(".")[0]
    entry = EDI_VERDICTS.get(base) or EDI_VERDICTS.get(element) or EDI_VERDICTS.get(element.split("-")[0])
    if entry is None:
        return None
    kind, reason = entry
    return _citation(kind, reason)
