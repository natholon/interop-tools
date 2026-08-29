"""X12 835 (Health Care Claim Payment/Advice - Electronic Remittance
Advice) -> FHIR PaymentReconciliation, plus a ClaimResponse per claim.

**835 has no HL segments at all** - a genuine structural break from every
other EDI family here. The 005010X221 TR3's sequence is
`ST/BPR/TRN/[REF/DTM]/N1(payer)/N1(payee)/[LX]/CLP.../SE`: an N1-led
header pair followed by a flat, repeating `CLP`-leader/`CAS`-member
structure. `group_by_leader` is the only grouping primitive needed.

**835 also has no `BHT`**, which every other family uses for
`Bundle.identifier`/`.timestamp` via the shared `assemble_bundle`. So
`Bundle.identifier` comes from `TRN02` (the trace number, whose stated
purpose is reassociating payments with remittances) and `Bundle.timestamp`
is left unset: nothing here carries a full date+time, and `BPR16` is
date-only. Feeding a date-only value to `Bundle.timestamp` (a FHIR
`instant`, which has no date-only form) would reproduce a bug class this
app has already hit twice.

Segment shape (verified against a real 835 example and X12 segment
references, not assumed):

    BPR     BPR02 payment amount, BPR04 method, BPR16 effective date
    TRN     TRN02 -> Bundle.identifier
    N1*PR   1000A payer  -> Organization
    N1*PE   1000B payee  -> Organization (who receives payment)
    CLP     2100, one per claim: CLP01 id, CLP02 status, CLP03 charge,
            CLP04 paid, CLP05 patient responsibility, CLP06 filing
            indicator, CLP07 payer control number
      CAS   adjustments -> one detail[] each, see below
      SVC   2110 service-line amounts - not mapped, see below

`N1` is genuinely simpler than `NM1` (no name split - just `N102` name and
`N103`/`N104` id qualifier/value), so this has its own
`_build_organization_from_n1`. It does reuse `NM1_ID_QUALIFIER_SYSTEM`,
since X12 element 66 is the same code list NM108 uses - only the position
differs.

`PaymentReconciliation.detail[]` operates at the claim level, not
service-line ("correlates a payment amount to the adjudicated claim
amounts", per the R4 spec). Per `CLP` group: `.type` = CLP02 on a
disclosed local system (no free authoritative CLP02 table exists the way
x12.org publishes STC's), `.identifier` = CLP01, `.amount` = CLP04.

**A claim carries three amounts and `detail` has room for one**, so the
other two ride on a `ClaimResponse` per claim - where FHIR models
adjudication totals, and what CARIN BB and Da Vinci PDex both do. R4
`PaymentReconciliationDetail` has no charge field at all (nor does R5's
`allocation`), so CLP03 was the one place in this app real money was
dropped. `detail.response` points at the ClaimResponse, which is what
that field is for.

    CLP03 -> total[submitted]   CLP06 -> subType (example binding)
    CLP04 -> total[benefit]     CLP07 -> identifier
    CLP05 -> total[patient responsibility, disclosed local code]
    CLP01 -> request, by identifier - the submitted Claim is not here

`ClaimResponse.patient` is 1..1, so this needs the 2100 person loop:
`NM1*QC` names the patient, and `NM1*IL` alone is how an 835 says the
patient is the subscriber. A claim with neither builds no ClaimResponse
and keeps only its `detail` - reported by
`edi.835-claim-missing-patient` rather than left silent, since the charge
and patient responsibility go with it.

`.status`, `.type` and `.outcome` are required and 835 carries a source
field for none of them, so each is a disclosed default recorded as
inferred; `.use` is genuinely fixed, an 835 remitting a submitted claim
rather than a preauthorization.

**BPR01/03/04 (transaction handling, credit-debit flag, payment method)
have no R4 target and are deliberately unmapped.** R5 added `.method` and
`.kind` for exactly these; R4B has neither, and this app does not ship an
R5-to-R4 backport extension it cannot verify resolves - the same call
made for C-CDA's own `ED`-typed values. They report as drops citing the
absent X12-to-FHIR crosswalk, like every other unmapped EDI element.

**CAS adjustments each become their own `detail[]`.** One CAS carries a
group code plus up to six (reason, amount, quantity) triplets, and each
triplet is a distinct adjustment with its own amount, so each gets its own
entry. Every part of this is citable rather than invented:

- `adjustment` is FHIR's own code - the R4 `payment-type` CodeSystem is
  exactly payment|adjustment|advance.
- `detail.type` binds to it at **example** strength, so carrying the X12
  codes in the same CodeableConcept is conformant. This module already
  puts CLP02 there the same way.
- CAS01 and CAS02 come from lists X12 publishes free, the same footing as
  the Claim Status Category Codes `claim_status.py` cites:
  x12.org/codes/claim-adjustment-group-codes and
  x12.org/codes/claim-adjustment-reason-codes. FHIR names no canonical
  system for either, so both keep a disclosed local URI.

**Service-line attribution is lost, and SVC's own amounts are not mapped.**
PaymentReconciliation has no service-line concept, so a CAS under an SVC
and one at claim level produce indistinguishable details - the group,
reason and amount all survive, only the line does not. SVC's own charge and
paid amounts are deliberately left unmapped: they would double-count
against the CLP04 already carried on the claim's own detail."""

import uuid

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.claimresponse import ClaimResponse, ClaimResponseTotal
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.money import Money
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.paymentreconciliation import PaymentReconciliation, PaymentReconciliationDetail
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.base import EdiTransactionBuilder
from app.edi.common import (
    CLAIM_FILING_INDICATOR_SYSTEM,
    apply_address_and_telecom,
    nm1_members,
    build_patient_from_nm1_dmg,
    resolve_id_qualifier_system,
)
from app.edi.parser import Delimiters, Segment, TransactionSet, element, find_segment, group_by_leader, parse_decimal
from app.fhir_models.builders import parse_hl7_date
from app.hl7.errors import MappingError, MissingSegmentError
from app.provenance.location import edi_location

TRANSACTION_SET_ID = "835"

_N1_PAYER = "PR"
_N1_PAYEE = "PE"

# Public (not module-private) - app/transform/remittance_835.py became a
# real reverse-direction consumer of all three.
TRN_IDENTIFIER_SYSTEM = "urn:interop-tools:x12-835-trace-number"
N1_ID_FALLBACK_SYSTEM = "urn:interop-tools:x12-n1-id"

# CLP02 (Claim Status Code) has no single free, authoritative published
# code table the way STC's Claim Status Category Codes do - a disclosed
# local system for PaymentReconciliationDetail.type, same category as
# claim_status.py's own STC-derived Coding systems.
CLP_STATUS_SYSTEM = "urn:interop-tools:x12-clp-status-code"

# The claim-level money. R4 PaymentReconciliationDetail carries only
# `.amount` - there is nowhere on it for a charge or a patient
# responsibility (R5's `allocation` has no such field either), so the
# amounts ride on a ClaimResponse per claim, which is where FHIR models
# adjudication totals and what CARIN BB and Da Vinci PDex both do.
#
# `total.category` binds at *example* strength, so the two base R4
# adjudication codes are used where they fit exactly and a disclosed local
# code covers the one that has none:
#   CLP03 -> submitted ("The total submitted amount for the claim", verbatim)
#   CLP04 -> benefit   ("Amount payable under the coverage")
#   CLP05 -> patient responsibility, which base R4 has no aggregate code
#           for - it offers copay and deductible, both narrower than CLP05.
ADJUDICATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/adjudication"
SUBMITTED_ADJUDICATION = "submitted"
BENEFIT_ADJUDICATION = "benefit"
PATIENT_RESPONSIBILITY_SYSTEM = "urn:interop-tools:x12-clp-patient-responsibility"
PATIENT_RESPONSIBILITY_CODE = "patient-responsibility"

# CLP07 (Payer Claim Control Number) identifies the payer's own
# adjudication of the claim, which is what a ClaimResponse is.
PAYER_CLAIM_CONTROL_SYSTEM = "urn:interop-tools:x12-payer-claim-control-number"
# CLP06 (Claim Filing Indicator Code, X12 element 1032) classifies the
# coverage the claim was filed under - MC Medicaid, MB Medicare Part B, CI
# Commercial, and so on. ClaimResponse.subType ("more granular claim
# type") binds at *example* strength, so the raw code is conformant there;
# X12 publishes no free authoritative table, hence a disclosed local
# system, the same footing as CLP_STATUS_SYSTEM.
CLP01_CLAIM_IDENTIFIER_SYSTEM = "urn:interop-tools:x12-claim-submitter-identifier"

# ClaimResponse requires all four, and an 835 carries a source field for
# none of them. `use` is genuinely fixed: an 835 remits a submitted claim,
# never a preauthorization or predetermination. The others are disclosed
# defaults, recorded as inferred.
_DEFAULT_CLAIM_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claim-type"
_DEFAULT_CLAIM_TYPE = "professional"
_CLAIM_RESPONSE_USE = "claim"
_CLAIM_RESPONSE_OUTCOME = "complete"

# 2100 person loops. QC is the patient; IL is the insured, used only when
# no QC is present - which is how an 835 spells "the patient is the
# subscriber".
# Public - app/edi/remittance_validation.py walks the identical claim
# groups, so the two can never disagree about which segments belong to a
# claim or which NM1 names its patient.
CLP_MEMBER_SEGMENTS = ["CAS", "SVC", "NM1", "DMG", "N3", "N4", "PER", "REF", "DTM", "AMT", "QTY"]

_NM1_PATIENT = "QC"
_NM1_INSURED = "IL"

# CAS adjustments. `adjustment` is FHIR's own code for exactly this - the
# R4 payment-type CodeSystem is payment|adjustment|advance - and
# PaymentReconciliation.detail.type binds to it at *example* strength, so
# carrying the X12 codes alongside it in the same CodeableConcept is
# conformant rather than a stretch. This module already puts CLP02 on
# detail.type the same way.
#
# Both X12 lists are published free by X12 itself, the same footing as the
# Claim Status Category Codes claim_status.py cites:
#   CAS01 https://x12.org/codes/claim-adjustment-group-codes  (CO/CR/OA/PI/PR)
#   CAS02 https://x12.org/codes/claim-adjustment-reason-codes (CARC)
# FHIR names no canonical system for either, so both keep a disclosed
# local URI, exactly as CLP_STATUS_SYSTEM does.
PAYMENT_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/payment-type"
ADJUSTMENT_PAYMENT_TYPE = "adjustment"
CAS_GROUP_SYSTEM = "urn:interop-tools:x12-claim-adjustment-group-code"
CAS_REASON_SYSTEM = "urn:interop-tools:x12-claim-adjustment-reason-code"

# CAS01 is the group code; the rest of the segment is up to six repeating
# (reason, amount, quantity) triplets starting at element 2.
_CAS_FIRST_TRIPLET_ELEMENT = 2
_CAS_MAX_TRIPLETS = 6


def _find_n1(segments: list[Segment], entity_code: str) -> Segment | None:
    for seg in segments:
        if seg[0] == "N1" and element(seg, 1) == entity_code:
            return seg
    return None


def _n1_members(segments: list[Segment], n1: Segment) -> list[Segment]:
    """The N3/N4/PER/REF describing this N1 - everything up to the next
    one. The N1 mirror of app.edi.common.nm1_members; 835 has its own
    because its party loops are N1-led, not NM1-led."""
    for index, segment in enumerate(segments):
        if segment is n1:
            members = []
            for following in segments[index + 1:]:
                if following[0] in ("N1", "CLP", "LX"):
                    break
                members.append(following)
            return members
    return []


def _build_n1_identifier(n1: Segment, resource_id: str | None = None, recorder=None) -> Identifier | None:
    qualifier = element(n1, 3).strip().upper()
    value = element(n1, 4)
    if not value:
        return None
    if recorder and resource_id:
        recorder.record(resource_id, "identifier[0].value", edi_location("N1", 4), value)
    return Identifier(system=resolve_id_qualifier_system(qualifier, N1_ID_FALLBACK_SYSTEM), value=value)


def _build_organization_from_n1(n1: Segment, recorder=None, members: list[Segment] | None = None) -> Organization:
    organization_id = str(uuid.uuid4())
    organization = Organization(id=organization_id)
    name = element(n1, 2)
    if name:
        organization.name = name
        if recorder:
            recorder.record(organization_id, "name", edi_location("N1", 2), name)
    identifier = _build_n1_identifier(n1, resource_id=organization_id, recorder=recorder)
    if identifier:
        organization.identifier = [identifier]
    apply_address_and_telecom(organization, members or [], recorder)
    return organization


def _parse_money(raw: str) -> Money | None:
    value = parse_decimal(raw)
    if value is None:
        return None
    return Money(value=value, currency="USD")


def _build_detail(clp: Segment, index: int, resource_id: str | None = None, recorder=None) -> PaymentReconciliationDetail:
    status_code = element(clp, 2)
    detail_path = f"detail[{index}]"
    if status_code:
        detail_type = CodeableConcept(coding=[Coding(system=CLP_STATUS_SYSTEM, code=status_code)])
        if recorder and resource_id:
            recorder.record(resource_id, f"{detail_path}.type.coding[0].code", edi_location("CLP", 2), status_code)
    else:
        detail_type = CodeableConcept(text="Claim Payment")
        if recorder and resource_id:
            recorder.record_inferred(
                resource_id,
                f"{detail_path}.type.text",
                "This claim's own CLP02 (Claim Status Code) is absent - PaymentReconciliationDetail.type defaults to a generic placeholder text.",
                "Claim Payment",
            )
    detail = PaymentReconciliationDetail(type=detail_type)

    claim_id = element(clp, 1)
    if claim_id:
        detail.identifier = Identifier(value=claim_id)
        if recorder and resource_id:
            recorder.record(resource_id, f"{detail_path}.identifier.value", edi_location("CLP", 1), claim_id)

    claim_paid_raw = element(clp, 4)
    paid_amount = _parse_money(claim_paid_raw)
    if paid_amount is not None:
        detail.amount = paid_amount
        if recorder and resource_id:
            recorder.record(resource_id, f"{detail_path}.amount.value", edi_location("CLP", 4), claim_paid_raw)

    return detail


def find_2100_patient_nm1(members) -> Segment | None:
    """The 2100 loop's patient NM1, preferring QC over IL.

    Both are person loops; QC names the patient directly, and IL names the
    insured, which an 835 uses alone when the patient is the subscriber.
    """
    by_code = {}
    for segment in members:
        if segment[0] != "NM1":
            continue
        by_code.setdefault(element(segment, 1).strip().upper(), segment)
    return by_code.get(_NM1_PATIENT) or by_code.get(_NM1_INSURED)


def _build_total(category: Coding, amount: Money) -> ClaimResponseTotal:
    return ClaimResponseTotal(category=CodeableConcept(coding=[category]), amount=amount)


def _build_claim_response(
    clp: Segment,
    patient_id: str,
    payer_id: str,
    payee_id: str,
    created,
    recorder=None,
) -> ClaimResponse:
    """One CLP -> one ClaimResponse, carrying the claim-level money.

    R4 PaymentReconciliationDetail has room for one amount and this claim
    has three, so the charge (CLP03), the payment (CLP04) and the patient
    responsibility (CLP05) go where FHIR models adjudication totals. The
    PaymentReconciliation's own detail then references this by urn:uuid
    rather than leaving `.response` unset.
    """
    claim_response_id = str(uuid.uuid4())
    totals = []
    for field_num, coding in (
        (3, Coding(system=ADJUDICATION_SYSTEM, code=SUBMITTED_ADJUDICATION)),
        (4, Coding(system=ADJUDICATION_SYSTEM, code=BENEFIT_ADJUDICATION)),
        (5, Coding(system=PATIENT_RESPONSIBILITY_SYSTEM, code=PATIENT_RESPONSIBILITY_CODE)),
    ):
        raw = element(clp, field_num)
        amount = _parse_money(raw)
        if amount is None:
            continue
        totals.append(_build_total(coding, amount))
        if recorder:
            index = len(totals) - 1
            recorder.record(
                claim_response_id,
                f"total[{index}].amount.value",
                edi_location("CLP", field_num),
                raw,
            )

    claim_response = ClaimResponse(
        id=claim_response_id,
        status="active",
        type=CodeableConcept(coding=[Coding(system=_DEFAULT_CLAIM_TYPE_SYSTEM, code=_DEFAULT_CLAIM_TYPE)]),
        use=_CLAIM_RESPONSE_USE,
        patient=Reference(reference=f"urn:uuid:{patient_id}"),
        created=created,
        insurer=Reference(reference=f"urn:uuid:{payer_id}"),
        outcome=_CLAIM_RESPONSE_OUTCOME,
    )
    claim_response.requestor = Reference(reference=f"urn:uuid:{payee_id}")
    if totals:
        claim_response.total = totals

    control_number = element(clp, 7)
    if control_number:
        claim_response.identifier = [
            Identifier(system=PAYER_CLAIM_CONTROL_SYSTEM, value=control_number)
        ]
        if recorder:
            recorder.record(
                claim_response_id, "identifier[0].value", edi_location("CLP", 7), control_number
            )

    filing_indicator = element(clp, 6)
    if filing_indicator:
        claim_response.subType = CodeableConcept(
            coding=[Coding(system=CLAIM_FILING_INDICATOR_SYSTEM, code=filing_indicator)]
        )
        if recorder:
            recorder.record(
                claim_response_id,
                "subType.coding[0].code",
                edi_location("CLP", 6),
                filing_indicator,
            )

    # The submitted Claim is not in this Bundle - an 835 stands alone - so
    # it is referenced by identifier, the same shape 271 uses for its own
    # unresolvable .request.
    claim_id = element(clp, 1)
    if claim_id:
        claim_response.request = Reference(
            identifier=Identifier(system=CLP01_CLAIM_IDENTIFIER_SYSTEM, value=claim_id)
        )
        if recorder:
            recorder.record(
                claim_response_id,
                "request.identifier.value",
                edi_location("CLP", 1),
                claim_id,
            )

    if recorder:
        for path, reason, value in (
            ("status", "Every ClaimResponse this app builds from an 835 has status=\"active\" - no X12 field carries one.", "active"),
            ("use", "An 835 remits a submitted claim, never a preauthorization or predetermination, so use is fixed.", _CLAIM_RESPONSE_USE),
            (
                "type.coding[0].code",
                "ClaimResponse.type is required and 835 carries no claim-type field, so it defaults to the "
                "dominant real-world value.",
                _DEFAULT_CLAIM_TYPE,
            ),
            (
                "outcome",
                "An 835 reports a completed adjudication. CLP02 has no free authoritative code table to "
                "crosswalk from, and is carried verbatim on PaymentReconciliationDetail.type.",
                _CLAIM_RESPONSE_OUTCOME,
            ),
        ):
            recorder.record_inferred(claim_response_id, path, reason, value)

    return claim_response


def _build_adjustment_details(
    cas_segments, first_index: int, resource_id: str | None = None, recorder=None
) -> list[PaymentReconciliationDetail]:
    """One detail per CAS adjustment triplet.

    A CAS segment carries one group code (CAS01) and up to six
    (reason, amount, quantity) triplets after it, so one segment can
    describe several distinct adjustments; each becomes its own detail,
    since each has its own amount.

    **Service-line attribution is lost, and that is disclosed.**
    PaymentReconciliation has no service-line concept at all - .detail[]
    is defined at the claim/payable level ("correlates a payment amount to
    the adjudicated claim amounts") - so a CAS nested under an SVC and one
    at claim level produce indistinguishable details. The adjustment's
    group, reason and amount all survive; only which line it came from
    does not.
    """
    details: list[PaymentReconciliationDetail] = []
    for cas in cas_segments:
        group_code = element(cas, 1)
        for triplet in range(_CAS_MAX_TRIPLETS):
            reason_element = _CAS_FIRST_TRIPLET_ELEMENT + triplet * 3
            reason_code = element(cas, reason_element)
            amount_raw = element(cas, reason_element + 1)
            if not reason_code and not amount_raw:
                # Triplets are positional and left-packed: the first empty
                # one ends the segment's real content.
                break

            codings = [Coding(system=PAYMENT_TYPE_SYSTEM, code=ADJUSTMENT_PAYMENT_TYPE)]
            if group_code:
                codings.append(Coding(system=CAS_GROUP_SYSTEM, code=group_code))
            if reason_code:
                codings.append(Coding(system=CAS_REASON_SYSTEM, code=reason_code))

            detail = PaymentReconciliationDetail(type=CodeableConcept(coding=codings))
            index = first_index + len(details)
            detail_path = f"detail[{index}]"
            if recorder and resource_id:
                recorder.record_inferred(
                    resource_id,
                    f"{detail_path}.type.coding[0].code",
                    'FHIR\'s own payment-type code for an adjustment - not read from an X12 field, '
                    "which carries the group and reason codes alongside it.",
                    ADJUSTMENT_PAYMENT_TYPE,
                )
                if group_code:
                    recorder.record(
                        resource_id, f"{detail_path}.type.coding[1].code", edi_location("CAS", 1), group_code
                    )
                if reason_code:
                    recorder.record(
                        resource_id,
                        f"{detail_path}.type.coding[{len(codings) - 1}].code",
                        edi_location("CAS", reason_element),
                        reason_code,
                    )

            amount = _parse_money(amount_raw)
            if amount is not None:
                detail.amount = amount
                if recorder and resource_id:
                    recorder.record(
                        resource_id,
                        f"{detail_path}.amount.value",
                        edi_location("CAS", reason_element + 1),
                        amount_raw,
                    )
            details.append(detail)
    return details


def _assemble_835_bundle(trace_number: str, *resources: Resource, recorder=None) -> Bundle:
    """The 835-specific equivalent of app.edi.common.assemble_bundle - not
    reused, since 835 has no BHT segment to derive Bundle.identifier/
    .timestamp from (see the module docstring)."""
    bundle = Bundle(id=str(uuid.uuid4()), type="collection")
    bundle.identifier = Identifier(system=TRN_IDENTIFIER_SYSTEM, value=trace_number)
    if recorder:
        recorder.record(bundle.id, "identifier.value", edi_location("TRN", 2), trace_number)
    bundle.entry = [BundleEntry(fullUrl=f"urn:uuid:{resource.id}", resource=resource) for resource in resources]
    return bundle


class Edi835Builder(EdiTransactionBuilder):
    transaction_set_id = TRANSACTION_SET_ID

    def build_bundle(self, transaction_set: TransactionSet, delimiters: Delimiters, recorder=None) -> Bundle:
        segments = transaction_set.segments

        bpr = find_segment(segments, "BPR")
        if bpr is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its BPR segment")

        # TRN is required by the 005010X221 TR3 (it identifies the
        # transaction itself, "used to uniquely identify this transaction
        # set and to aid in reassociating payments and remittances") -
        # required here the same way every sibling family requires its own
        # header-identifying segment (BHT), not left optional.
        trn = find_segment(segments, "TRN")
        if trn is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its TRN segment")
        trace_number = element(trn, 2)
        if not trace_number:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set's TRN segment has no resolvable trace number (TRN02)")

        payer_n1 = _find_n1(segments, _N1_PAYER)
        if payer_n1 is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its 1000A N1*PR (payer) segment")
        payee_n1 = _find_n1(segments, _N1_PAYEE)
        if payee_n1 is None:
            raise MissingSegmentError(f"{self.transaction_set_id} transaction set is missing its 1000B N1*PE (payee) segment")

        payer = _build_organization_from_n1(payer_n1, recorder=recorder, members=_n1_members(segments, payer_n1))
        payee = _build_organization_from_n1(payee_n1, recorder=recorder, members=_n1_members(segments, payee_n1))

        # BPR is present but its own required values not resolving is a
        # business-rule failure, not an absent-segment one - MappingError,
        # not MissingSegmentError, matching every sibling builder's own
        # "segment present, required field doesn't resolve" precedent
        # (e.g. eligibility_270.py's BHT04-unresolvable check).
        bpr02_raw = element(bpr, 2)
        payment_amount = _parse_money(bpr02_raw)
        if payment_amount is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's BPR segment has no resolvable payment amount (BPR02)")

        # BPR16 is date-only (see module docstring for why this is safe for
        # PaymentReconciliation.created/.paymentDate but would NOT be safe
        # for Bundle.timestamp).
        bpr16_raw = element(bpr, 16)
        payment_date = parse_hl7_date(bpr16_raw)
        if payment_date is None:
            raise MappingError(f"{self.transaction_set_id} transaction set's BPR segment has no resolvable payment date (BPR16)")

        payment_reconciliation_id = str(uuid.uuid4())
        payment_reconciliation = PaymentReconciliation(
            id=payment_reconciliation_id,
            status="active",
            created=payment_date,
            paymentDate=payment_date,
            paymentAmount=payment_amount,
        )
        # .paymentIssuer ("The party who generated the payment", confirmed
        # Organization-only via model_fields) is the payer - a direct,
        # unambiguous fit. .requestor ("The practitioner who is
        # responsible for the services rendered to the patient", confirmed
        # via model_fields) is a less exact fit for the payee - a disclosed
        # judgment call, not a guess: the payee (N1*PE) in an 835 is the
        # billing/rendering provider entity being paid for those same
        # services, the closest available field for "the entity associated
        # with the services this payment covers" among PaymentReconciliation's
        # own fields.
        payment_reconciliation.paymentIssuer = Reference(reference=f"urn:uuid:{payer.id}")
        payment_reconciliation.requestor = Reference(reference=f"urn:uuid:{payee.id}")
        # .outcome is left unset - deliberately, not an oversight: 835 has
        # no source field indicating whether this remittance itself was
        # "successfully processed" as a transaction (unlike 271/278's
        # outcome, which comes from a real source field - AAA/HCR
        # respectively), so there is nothing to map it from.
        if recorder:
            recorder.record_inferred(
                payment_reconciliation_id,
                "status",
                "Every PaymentReconciliation this app builds from an 835 transaction set has status=\"active\" - not read from any X12 field.",
                "active",
            )
            recorder.record(payment_reconciliation_id, "created", edi_location("BPR", 16), payment_date, source_value=bpr16_raw)
            recorder.record(
                payment_reconciliation_id, "paymentDate", edi_location("BPR", 16), payment_date, source_value=bpr16_raw
            )
            recorder.record(
                payment_reconciliation_id,
                "paymentAmount.value",
                edi_location("BPR", 2),
                bpr02_raw,
            )

        # Claim payments first, then the adjustments that explain them.
        # group_by_leader is needed again here (rather than the flat CLP
        # scan this used to do) because a CAS belongs to whichever claim
        # precedes it - including one nested under that claim's SVC lines.
        clp_groups = group_by_leader(segments, "CLP", CLP_MEMBER_SEGMENTS)
        details = [
            _build_detail(clp, i, resource_id=payment_reconciliation_id, recorder=recorder)
            for i, (clp, _members) in enumerate(clp_groups)
        ]

        # One ClaimResponse per claim, carrying the money R4
        # PaymentReconciliationDetail has nowhere to put (see
        # _build_claim_response). A claim with no resolvable 2100 person
        # loop gets none: ClaimResponse.patient is 1..1, so there would be
        # nothing to point it at.
        claim_responses: list[Resource] = []
        patients: list[Resource] = []
        for detail, (clp, members) in zip(details, clp_groups):
            patient_nm1 = find_2100_patient_nm1(members)
            if patient_nm1 is None:
                continue
            dmg = next((seg for seg in members if seg[0] == "DMG"), None)
            patient = build_patient_from_nm1_dmg(
                patient_nm1, dmg, recorder=recorder, members=nm1_members(members, patient_nm1)
            )
            claim_response = _build_claim_response(
                clp, patient.id, payer.id, payee.id, payment_date, recorder=recorder
            )
            patients.append(patient)
            claim_responses.append(claim_response)
            detail.response = Reference(reference=f"urn:uuid:{claim_response.id}")
        for _clp, members in clp_groups:
            details.extend(
                _build_adjustment_details(
                    [seg for seg in members if seg[0] == "CAS"],
                    len(details),
                    resource_id=payment_reconciliation_id,
                    recorder=recorder,
                )
            )
        if details:
            payment_reconciliation.detail = details

        resources: list[Resource] = [payer, payee, *patients, *claim_responses, payment_reconciliation]
        return _assemble_835_bundle(trace_number, *resources, recorder=recorder)
