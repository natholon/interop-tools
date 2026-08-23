"""X12 835 (Health Care Claim Payment/Advice - Electronic Remittance
Advice) -> FHIR PaymentReconciliation.

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
      CAS   claim-level adjustments - walked but not mapped
      SVC   2110 service-line detail - deferred

`N1` is genuinely simpler than `NM1` (no name split - just `N102` name and
`N103`/`N104` id qualifier/value), so this has its own
`_build_organization_from_n1`. It does reuse `NM1_ID_QUALIFIER_SYSTEM`,
since X12 element 66 is the same code list NM108 uses - only the position
differs.

`PaymentReconciliation.detail[]` operates at the claim level, not
service-line ("correlates a payment amount to the adjudicated claim
amounts", per the R4 spec) - so deferring `SVC` and itemized `CAS` matches
the resource's own granularity rather than being an arbitrary cut. Per
`CLP` group: `.type` = CLP02 on a disclosed local system (no free
authoritative CLP02 table exists the way x12.org publishes STC's),
`.identifier` = CLP01, `.amount` = CLP04. `.request`/`.response` stay
unset - a standalone 835 has no `Claim`/`ClaimResponse` in its Bundle to
reference, and unlike 271's `.request` there is no identifier fallback,
since `.identifier` is already spoken for by CLP01."""

import uuid

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.money import Money
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.paymentreconciliation import PaymentReconciliation, PaymentReconciliationDetail
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from app.edi.base import EdiTransactionBuilder
from app.edi.common import resolve_id_qualifier_system
from app.edi.parser import Delimiters, Segment, TransactionSet, element, find_segment, parse_decimal
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


def _find_n1(segments: list[Segment], entity_code: str) -> Segment | None:
    for seg in segments:
        if seg[0] == "N1" and element(seg, 1) == entity_code:
            return seg
    return None


def _build_n1_identifier(n1: Segment, resource_id: str | None = None, recorder=None) -> Identifier | None:
    qualifier = element(n1, 3).strip().upper()
    value = element(n1, 4)
    if not value:
        return None
    if recorder and resource_id:
        recorder.record(resource_id, "identifier[0].value", edi_location("N1", 4), value)
    return Identifier(system=resolve_id_qualifier_system(qualifier, N1_ID_FALLBACK_SYSTEM), value=value)


def _build_organization_from_n1(n1: Segment, recorder=None) -> Organization:
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

        payer = _build_organization_from_n1(payer_n1, recorder=recorder)
        payee = _build_organization_from_n1(payee_n1, recorder=recorder)

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

        clp_segments = [seg for seg in segments if seg[0] == "CLP"]
        details = [
            _build_detail(clp, i, resource_id=payment_reconciliation_id, recorder=recorder)
            for i, clp in enumerate(clp_segments)
        ]
        if details:
            payment_reconciliation.detail = details

        resources: list[Resource] = [payer, payee, payment_reconciliation]
        return _assemble_835_bundle(trace_number, *resources, recorder=recorder)
