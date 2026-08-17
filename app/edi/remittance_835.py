"""X12 835 (Health Care Claim Payment/Advice - Electronic Remittance
Advice) -> FHIR PaymentReconciliation.

**A genuine structural break from every other EDI family in this app: 835
has no HL segments at all.** Verified directly (not assumed) - the
005010X221 TR3's own segment sequence is `ST/BPR/TRN/[REF/DTM]/N1(payer)/
N1(payee)/[LX]/CLP.../SE`, an N1-led header pair (1000A payer, 1000B
payee) followed by a flat, repeating `CLP`-leader/`CAS`-member structure
for claim payments - no `HL` segment anywhere. `group_by_leader` (not
`group_by_hl_hierarchy`) is therefore the only grouping primitive this
module needs.

**835 also has no `BHT` segment** - a second structural break from every
other EDI family, all of which use `BHT` for `Bundle.identifier`/
`.timestamp` via the shared `app.edi.common.assemble_bundle` helper. This
module can't reuse that helper: `Bundle.identifier` instead comes from
`TRN02` (the trace number - "used to uniquely identify this transaction
set and to aid in reassociating payments and remittances", a natural fit
for this exact purpose), and `Bundle.timestamp` is left unset entirely -
no field in this transaction set carries a full date+time value the way
`BHT04`+`BHT05` do elsewhere, and `BPR16` (Payment Effective Date) is
date-only. Feeding a date-only value into `Bundle.timestamp` (a FHIR
`instant`, which has no date-only form) would reproduce the exact bug
class already disclosed for CDA's and this app's own `assemble_bundle`
functions - disclosed and skipped here rather than guessed at.

Loop shape (verified against a real, internally-consistent raw 835
example plus direct verification of `BPR`/`N1`/`CLP`/`CAS` field
positions against X12 segment references, not assumed):
  BPR (header)          BPR02 payment amount, BPR04 payment method,
                         BPR16 payment effective date
  TRN (trace number)    TRN02 -> Bundle.identifier
  N1*PR (1000A, Payer)   N102 name, N103/N104 id qualifier/value -> Organization
  N1*PE (1000B, Payee)   same shape -> Organization (the provider/billing
                         entity receiving payment)
  CLP (2100, one per claim, repeating)  CLP01 claim id, CLP02 status code,
                         CLP03 charge, CLP04 paid amount, CLP05 patient
                         responsibility, CLP06 filing indicator, CLP07
                         payer control number
    CAS (claim-level adjustments, repeating) - captured by the leader/
    member walk but not individually mapped, see below
    SVC (2110, service-line detail, repeating) - deferred entirely, see below

`N1`'s own shape is genuinely simpler than `NM1`'s (no first/last name
split, just `N102` free-form name + `N103`/`N104` id qualifier/value) -
this module has its own `_build_organization_from_n1`, not a reuse of
`common.py`'s `NM1`-scoped `build_organization_from_nm1`. It does reuse
`common.py`'s public `NM1_ID_QUALIFIER_SYSTEM` table directly for the
`N103`/`N104` id-qualifier lookup, since X12's own Identification Code
Qualifier list (element 66) is the identical code list NM108 already
uses - only the segment *position* differs, not the code list.

Target FHIR resource - `PaymentReconciliation`, base-spec, verified by
direct fetch of `hl7.org/fhir/R4/paymentreconciliation.html`: its own
field description confirms `.detail[]` operates at the claim/payable
level ("correlates a payment amount to the adjudicated claim amounts"),
not service-line level - so this module's own disclosed scope limit
(`2110`/`SVC` service-line detail and itemized `CAS` adjustment-reason
codes are not modeled, only `CLP04`'s aggregate paid amount per claim)
matches the resource's own documented granularity, not an arbitrary cut.
One `PaymentReconciliationDetail` per `CLP` group: `.type` carries `CLP02`
(claim status code) as a disclosed local-system coding (no single free,
authoritative `CLP02` code table was found the way STC's Claim Status
Category Codes are published on x12.org), `.identifier` carries `CLP01`
(the claim submitter's own identifier), `.amount` carries `CLP04`.
`.request`/`.response` (References to `Claim`/`ClaimResponse`) are left
unset - a standalone 835 has no real `Claim`/`ClaimResponse` resource in
its own Bundle to point at, the same gap 271 had for its own `.request`
field, but without an identifier-based fallback this time since
`PaymentReconciliationDetail.identifier` is already spoken for by
`CLP01`."""

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
