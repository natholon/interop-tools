"""What governs each value this app *transforms* on the way through, so a
"Transformed" row in the crosswalk can cite it rather than just asserting
the conversion happened.

A direct entry is transformed when its `source_value` differs from its
`value` - a date reformatted, a code translated, a scheme stripped. Each
of those is governed by something: a published ConceptMap, a code table,
a FHIR datatype's own format rules, or a disclosed local decision this
app made. This maps `(source_format, fhir_path)` to that source.

Keys are the FHIR path with array indices normalised away
(`name[0].use` -> `name[].use`) and the `Bundle.entry[N].resource.`
prefix stripped, so one row covers every occurrence.

**Scoped to transformations that actually occur**, the same discipline as
`hl7_field_names.py` and its siblings: the pairs here were enumerated by
running every fixture and collecting the entries where source and target
genuinely differ. A pair with no row gets no citation rather than a
guessed one - the crosswalk still shows the transformation, it just does
not claim a source for it.
"""

from app.provenance.citations import Citation

# --- shared citations --------------------------------------------------

_V2_TO_FHIR = "https://github.com/HL7/v2-to-fhir/tree/master/mappings"
_CCDA_ON_FHIR = "https://build.fhir.org/ig/HL7/ccda-on-fhir/"

HL7V2_DATETIME = Citation(
    title="HL7 v2 TS/DTM reformatted to a FHIR dateTime",
    url="https://hl7.org/fhir/R4/datatypes.html#dateTime",
    authoritative=True,
    note=(
        "v2 writes YYYYMMDD[HHMMSS][+/-ZZZZ] as one string; FHIR requires ISO 8601. The offset is "
        "preserved where the source carried one rather than being assumed UTC."
    ),
)

HL7V2_DATE = Citation(
    title="HL7 v2 date reformatted to a FHIR date",
    url="https://hl7.org/fhir/R4/datatypes.html#date",
    authoritative=True,
    note="An 8-digit v2 date rewritten as YYYY-MM-DD.",
)

CDA_DATETIME = Citation(
    title="CDA TS reformatted to a FHIR dateTime",
    url="https://hl7.org/fhir/R4/datatypes.html#dateTime",
    authoritative=True,
    note="CDA's @value carries the same digit shape as an HL7 v2 TS, rewritten as ISO 8601.",
)

CDA_DATE = Citation(
    title="CDA TS reformatted to a FHIR date",
    url="https://hl7.org/fhir/R4/datatypes.html#date",
    authoritative=True,
    note="An 8-digit CDA date rewritten as YYYY-MM-DD.",
)

X12_DATETIME = Citation(
    title="X12 date and time elements combined into a FHIR dateTime",
    url="https://hl7.org/fhir/R4/datatypes.html#dateTime",
    authoritative=True,
    note="X12 carries date (CCYYMMDD) and time (HHMM) as separate elements; FHIR needs one value.",
)

X12_DATE = Citation(
    title="X12 D8 date reformatted to a FHIR date",
    url="https://hl7.org/fhir/R4/datatypes.html#date",
    authoritative=True,
    note="An 8-digit X12 date rewritten as YYYY-MM-DD.",
)

_HL7V2_GENDER = Citation(
    title="v2-to-FHIR: PID-8 to Patient.gender",
    url=_V2_TO_FHIR,
    authoritative=True,
    note="HL7 table 0001 (Administrative Sex) translated to FHIR's administrative-gender value set.",
)

_HL7V2_ENCOUNTER_CLASS = Citation(
    title="v2-to-FHIR: PV1-2 to Encounter.class",
    url=_V2_TO_FHIR,
    authoritative=True,
    note="HL7 table 0004 (Patient Class) translated to v3-ActEncounterCode.",
)

_HL7V2_DOC_STATUS = Citation(
    title="v2-to-FHIR: TXA-19 to DocumentReference.status",
    url=_V2_TO_FHIR,
    authoritative=True,
    note=(
        'The segment map assigns status "current" when TXA-19 is "AV"; "CA"/"OB"/"UN" instead ride '
        "on status as an alternate-codes extension."
    ),
)

_CDA_GENDER = Citation(
    title="C-CDA on FHIR: CF_AdministrativeGender",
    url=_CCDA_ON_FHIR,
    authoritative=True,
    note="The IG's own ConceptMap from CDA's F/M/UN vocabulary to FHIR's administrative-gender.",
)

_CDA_ENCOUNTER_CLASS = Citation(
    title="C-CDA on FHIR: encompassingEncounter/code to Encounter.class",
    url=_CCDA_ON_FHIR,
    authoritative=True,
    note="V3 ActCode values map to .class; an unrecognised code falls back to a disclosed default.",
)

_X12_GENDER = Citation(
    title="X12 code list 1068 (Gender Code) to FHIR administrative-gender",
    url="https://hl7.org/fhir/R4/valueset-administrative-gender.html",
    authoritative=True,
    note="X12's M/F/U translated to FHIR's male/female/unknown - a different vocabulary from HL7 v2's.",
)

_X12_CLAIM_STATUS = Citation(
    title="X12 Claim Status Category Codes to Task.status",
    url="https://x12.org/codes/claim-status-category-codes",
    authoritative=True,
    note=(
        "Keyed by the category code's leading letter, since X12 draws finer distinctions within a "
        "prefix than FHIR's task-status has room for."
    ),
)

# --- disclosed local decisions, with no published crosswalk ------------

def _local(title: str, note: str) -> Citation:
    return Citation(title=title, url=None, authoritative=False, note=note)


_HL7V2_CONTENT_TYPE = _local(
    "Local table: TXA-3 to a MIME type",
    "The v2-to-FHIR IG publishes no crosswalk from HL7 table 0191 to a MIME type, so this app's own "
    "table covers the common codes and falls back to text/plain.",
)

_CDA_NAME_USE = _local(
    "Local table: HL7 v3 EntityNameUse to HumanName.use",
    "The IG maps <name> as a whole datatype and publishes no datatype table, so this is read off the "
    "v3 code system directly and is deliberately partial - codes with no unambiguous FHIR "
    "counterpart stay unmapped rather than being guessed at.",
)

_CDA_ADDRESS_USE = _local(
    "Local table: HL7 v3 PostalAddressUse to Address.use",
    "As with name use: mapped off the v3 code system directly, and partial by design.",
)

_CDA_TELECOM_USE = _local(
    "Local table: HL7 v3 TelecommunicationAddressUse to ContactPoint.use",
    "HP/WP/MC translated to home/work/mobile.",
)

_CDA_TELECOM_VALUE = _local(
    "URI scheme stripped from telecom/@value",
    "CDA writes the scheme inline (tel:, mailto:); FHIR carries it separately in ContactPoint.system.",
)

_CDA_OID_IDENTIFIER = _local(
    "Bare OID expressed as a urn:oid: URI",
    "An <id> with a @root and no @extension is a complete identifier whose value is the root itself; "
    "FHIR's convention for that is the urn:oid: form.",
)

_X12_ELIGIBILITY_INFORCE = _local(
    "Local reading of EB01 (Eligibility or Benefit Information)",
    'EB01="1" (Active Coverage) is read as coverage being in force. No published crosswalk exists.',
)

_X12_ELIGIBILITY_EXCLUDED = _local(
    "Local table: EB01 to CoverageEligibilityResponse .excluded",
    'Active maps to not-excluded, Inactive and Non-Covered to excluded; any other code leaves '
    ".excluded unset rather than guessing.",
)

_X12_PAYER_RESPONSIBILITY = Citation(
    title="X12 element 1138 (Payer Responsibility Sequence Number Code)",
    url="https://x12.org/codes",
    authoritative=False,
    note=(
        "P/S/T and the A-H continuation codes state where this payer sits in the payment sequence, which "
        "Coverage.order expresses as a plain positiveInt with no value-set binding. No official X12-to-FHIR "
        "crosswalk publishes this pairing; \"U\" (Unknown) states no order and is left unmapped."
    ),
)

_X12_RELATIONSHIP = Citation(
    title="X12 element 1069 (Individual Relationship Code)",
    url="https://terminology.hl7.org/CodeSystem-subscriber-relationship.html",
    authoritative=False,
    note=(
        "SBR02, or PAT01 for a dependent's own loop, mapped onto FHIR's subscriber-relationship codes. The "
        "binding is extensible and the pairing is this project's own: only codes with an unambiguous "
        "counterpart are mapped, so \"21\" (Unknown) leaves the field unset rather than becoming \"other\"."
    ),
)

_X12_NETWORK = _local(
    "Local text for EB12 (In Plan Network Indicator)",
    "No FHIR CodeSystem exists for this indicator and .network is a CodeableConcept, so Y/N/U are "
    "carried as disclosed text.",
)

_X12_HCR_OUTCOME = _local(
    "Local table: HCR01 (Action Code) to ClaimResponse.outcome",
    "No single authoritative free HCR01 table was found, unlike the Claim Status Category Codes, so "
    "this crosswalk is verified against a published RFI answer and companion-guide text.",
)

_DECIMAL_NORMALISED = _local(
    "Numeric value parsed into a FHIR decimal",
    "The source digits are parsed rather than copied, so a trailing zero or absent decimal point is "
    "normalised by the type rather than by this app.",
)

_BOOLEAN = _local(
    "Source code read as a FHIR boolean",
    "The crosswalk shows the resulting boolean; the code it was read from is the source value.",
)


US_REALM_HEADER_CONFIDENTIALITY = Citation(
    title="US Realm Header prohibits Composition.confidentiality (0..0)",
    url="https://hl7.org/fhir/us/ccda/StructureDefinition-US-Realm-Header.html",
    authoritative=True,
    note=(
        "The base R4 Composition mapping routes ClinicalDocument/confidentialityCode to "
        ".confidentiality, so the value is carried rather than dropped - but the US Realm Header "
        "profile constrains that element to 0..0. A document validated against that profile will "
        "flag it."
    ),
)

# (source_format, fhir_path) -> Citation, for a mapping that is correct but
# carries a caveat worth stating even though the value was copied
# unchanged. Kept separate from TRANSFORM_CITATIONS because nothing here
# transformed anything - the citation explains the decision, not a value
# change.
MAPPING_CAVEATS: dict[tuple[str, str], Citation] = {
    ("CDA", "confidentiality"): US_REALM_HEADER_CONFIDENTIALITY,
}


# (source_format, fhir_path with indices normalised) -> Citation
TRANSFORM_CITATIONS: dict[tuple[str, str], Citation] = {
    # --- HL7v2 ---------------------------------------------------------
    ("HL7v2", "Bundle.timestamp"): HL7V2_DATETIME,
    ("HL7v2", "created"): HL7V2_DATETIME,
    ("HL7v2", "date"): HL7V2_DATETIME,
    ("HL7v2", "effectiveDateTime"): HL7V2_DATETIME,
    ("HL7v2", "issued"): HL7V2_DATETIME,
    ("HL7v2", "period.start"): HL7V2_DATETIME,
    ("HL7v2", "period.end"): HL7V2_DATETIME,
    ("HL7v2", "birthDate"): HL7V2_DATE,
    ("HL7v2", "gender"): _HL7V2_GENDER,
    ("HL7v2", "class.code"): _HL7V2_ENCOUNTER_CLASS,
    ("HL7v2", "status"): _HL7V2_DOC_STATUS,
    ("HL7v2", "content[].attachment.contentType"): _HL7V2_CONTENT_TYPE,
    ("HL7v2", "valueQuantity.value"): _DECIMAL_NORMALISED,
    # --- C-CDA ---------------------------------------------------------
    ("CDA", "Bundle.timestamp"): CDA_DATETIME,
    # Composition.date is dateTime, so unlike Bundle.timestamp it keeps
    # a date-only effectiveTime - same reformatting, same citation.
    ("CDA", "date"): CDA_DATETIME,
    ("CDA", "birthDate"): CDA_DATE,
    ("CDA", "gender"): _CDA_GENDER,
    ("CDA", "class.code"): _CDA_ENCOUNTER_CLASS,
    ("CDA", "name[].use"): _CDA_NAME_USE,
    ("CDA", "address[].use"): _CDA_ADDRESS_USE,
    ("CDA", "telecom[].use"): _CDA_TELECOM_USE,
    ("CDA", "telecom[].value"): _CDA_TELECOM_VALUE,
    ("CDA", "identifier[].value"): _CDA_OID_IDENTIFIER,
    # --- X12 EDI -------------------------------------------------------
    ("EDI", "Bundle.timestamp"): X12_DATETIME,
    ("EDI", "created"): X12_DATETIME,
    ("EDI", "birthDate"): X12_DATE,
    ("EDI", "servicedDate"): X12_DATE,
    ("EDI", "item[].servicedDate"): X12_DATE,
    ("EDI", "paymentDate"): X12_DATE,
    ("EDI", "gender"): _X12_GENDER,
    ("EDI", "status"): _X12_CLAIM_STATUS,
    ("EDI", "outcome"): _X12_HCR_OUTCOME,
    ("EDI", "insurance[].inforce"): _X12_ELIGIBILITY_INFORCE,
    ("EDI", "insurance[].item[].excluded"): _X12_ELIGIBILITY_EXCLUDED,
    ("EDI", "insurance[].item[].network.text"): _X12_NETWORK,
    ("EDI", "order"): _X12_PAYER_RESPONSIBILITY,
    ("EDI", "relationship.coding[].code"): _X12_RELATIONSHIP,
}

_INDEX_RE = None


def normalise_path(fhir_path: str) -> str:
    """`Bundle.entry[2].resource.name[0].use` -> `name[].use`."""
    global _INDEX_RE
    if _INDEX_RE is None:
        import re

        _INDEX_RE = re.compile(r"\[\d+\]")
    tail = fhir_path.split(".resource.", 1)[-1]
    return _INDEX_RE.sub("[]", tail)


def caveat_for(source_format: str, fhir_path: str) -> Citation | None:
    """A caveat governing this mapping, if one is registered - see
    MAPPING_CAVEATS. Matched the same normalised way citation_for matches."""
    return MAPPING_CAVEATS.get((source_format, normalise_path(fhir_path)))


def citation_for(source_format: str | None, fhir_path: str | None) -> Citation | None:
    """The source governing this transformation, or None when nothing has
    been checked for it - the crosswalk then shows the transformation
    without claiming a source."""
    if not source_format or not fhir_path:
        return None
    return TRANSFORM_CITATIONS.get((source_format, normalise_path(fhir_path)))
