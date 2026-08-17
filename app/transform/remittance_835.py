"""FHIR Bundle -> X12 835 (Health Care Claim Payment/Advice - Electronic
Remittance Advice) - the thirteenth reverse-direction slice, and the
first EDI reverse slice for a family with **no `HL` hierarchy and no
`BHT` segment at all** (see `app/edi/remittance_835.py`'s own module
docstring for the forward side's identical disclosure) - a flat `BPR`/
`TRN`/`N1`(payer)/`N1`(payee)/`CLP`... shape, genuinely simpler to
reverse than every HL-hierarchy-based family this app has reversed so
far, needing no loop-resolution helper at all.

Reverses `app/edi/remittance_835.py::_build_organization_from_n1`/
`_build_detail`/`Edi835Builder.build_bundle` field-for-field: `N1`'s own
simpler shape (`N102` name, `N103`/`N104` id qualifier/value - no
first/last name split, so this builds `N1` directly rather than reusing
`edi_common.py`'s `NM1`-scoped `build_org_nm1`), `BPR02`/`BPR16` (payment
amount/date), `TRN02` (trace number, `Bundle.identifier`'s own source
here since there's no `BHT` to derive it from), and one `CLP` segment per
`PaymentReconciliationDetail` (`CLP01` claim id, `CLP02` status code,
`CLP04` paid amount).

**Payer/payee resolution is more direct than any earlier EDI reverse
slice**: `PaymentReconciliation.paymentIssuer`/`.requestor` are direct
references to the payer/payee `Organization` resources - no Bundle-order
fallback or exclusion logic needed at all, since both fields are always
populated by the forward mapper and there's no third organization/HL
loop to disambiguate against.

**Disclosed round-trip fidelity gaps**: `BPR01`/`BPR03`/`BPR04` (Handling
Code/Credit-Debit Flag/Payment Method) and `CLP03`/`CLP05`/`CLP06`/`CLP07`
(charge amount/patient responsibility/filing indicator/payer control
number) have no FHIR-side home at all - the forward mapper never reads
any of them into `PaymentReconciliation`/`.detail[]` - so each gets a
fixed, disclosed placeholder value on the way back out (`CLP03` mirrors
`CLP04`'s own paid amount, the closest available real number, rather than
a fabricated one), the same "no source field, disclosed placeholder"
precedent every earlier reverse slice already established. `CAS`
(claim-level adjustments) and `SVC` (service-line detail) are never
regenerated either, matching the forward mapper's own disclosed
"not modeled this phase" scope limit for both."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.common import NM1_ID_QUALIFIER_SYSTEM
from app.edi.generator import format_x12_date
from app.edi.remittance_835 import CLP_STATUS_SYSTEM, N1_ID_FALLBACK_SYSTEM
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource
from app.transform.edi_common import DEFAULT_ST_CONTROL, build_envelope_segments, build_trailer_segments, envelope_datetime

# Reverse of app.edi.common.NM1_ID_QUALIFIER_SYSTEM, inverted directly -
# the identical code list N103/NM108 both use (see app.edi.remittance_835's
# own module docstring). **Not** a reuse of app.transform.edi_common's own
# `reverse_nm1_qualifier`: that function's fallback-marker prefix is
# NM1-scoped (`urn:interop-tools:x12-nm1-id:`), genuinely different from
# N1's own (`urn:interop-tools:x12-n1-id:`) - reusing it here would fail
# to strip N1's own fallback marker (e.g. this fixture's own "XV"
# qualifier, not in the canonical table), silently falling back to the
# wrong default qualifier instead.
_SYSTEM_TO_N1_QUALIFIER = {system: qualifier for qualifier, system in NM1_ID_QUALIFIER_SYSTEM.items()}
_DEFAULT_N1_QUALIFIER = "24"  # Employer's Identification Number - a disclosed generic default
_N1_ID_FALLBACK_MARKER = f"{N1_ID_FALLBACK_SYSTEM}:"


def _reverse_n1_qualifier(identifier) -> str:
    if identifier is None or not identifier.system:
        return _DEFAULT_N1_QUALIFIER
    if identifier.system in _SYSTEM_TO_N1_QUALIFIER:
        return _SYSTEM_TO_N1_QUALIFIER[identifier.system]
    if identifier.system.startswith(_N1_ID_FALLBACK_MARKER):
        return identifier.system[len(_N1_ID_FALLBACK_MARKER) :]
    return _DEFAULT_N1_QUALIFIER


def _build_n1_segment(entity_code: str, organization) -> str:
    name = organization.name or "UNKNOWN"
    identifier = organization.identifier[0] if organization.identifier else None
    if identifier and identifier.value:
        # N103/N104 (id qualifier/value) - N1's own shape has no
        # first/last name split to worry about, unlike NM1, so this is
        # built directly rather than reusing edi_common.py's NM1-scoped
        # build_org_nm1/build_person_nm1.
        qualifier = _reverse_n1_qualifier(identifier)
        return f"N1*{entity_code}*{name}*{qualifier}*{identifier.value}~"
    return f"N1*{entity_code}*{name}~"


def _build_bpr_segment(payment_reconciliation) -> str:
    amount = payment_reconciliation.paymentAmount.value if payment_reconciliation.paymentAmount else "0.00"
    date = format_x12_date(payment_reconciliation.paymentDate) if payment_reconciliation.paymentDate else ""
    # BPR01 ("I" - Remittance Information Only)/BPR03 ("C" - Checks)/
    # BPR04 ("NON" - Non-Payment Data, the safest disclosed default since
    # this app has no source field indicating a real payment method) have
    # no FHIR-side home - fixed, disclosed placeholders, same precedent as
    # every earlier "no source field" gap in this app.
    fields = ["I", str(amount), "C", "NON", "", "", "", "", "", "", "", "", "", "", "", date]
    return "BPR*" + "*".join(fields) + "~"


def _build_clp_segment(detail) -> str:
    claim_id = detail.identifier.value if detail.identifier else ""
    status_code = ""
    if detail.type and detail.type.coding:
        for coding in detail.type.coding:
            if coding.system == CLP_STATUS_SYSTEM and coding.code:
                status_code = coding.code
    paid_amount = str(detail.amount.value) if detail.amount else "0.00"
    # CLP03 (charge amount) has no FHIR-side home - mirrors CLP04's own
    # paid amount as the closest available real number, disclosed rather
    # than fabricated. CLP05-07 (patient responsibility/filing indicator/
    # payer control number) are left empty for the same reason.
    fields = [claim_id, status_code, paid_amount, paid_amount, "", "", ""]
    return "CLP*" + "*".join(fields) + "~"


class Edi835Builder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        payment_reconciliation = find_resource(bundle, "PaymentReconciliation")
        if payment_reconciliation is None:
            raise MappingError("Bundle has no PaymentReconciliation resource - cannot build an 835 message")
        payer = None
        if payment_reconciliation.paymentIssuer:
            payer_id = payment_reconciliation.paymentIssuer.reference.removeprefix("urn:uuid:")
            payer = next(
                (
                    e.resource
                    for e in bundle.entry
                    if e.resource.get_resource_type() == "Organization" and e.resource.id == payer_id
                ),
                None,
            )
        if payer is None:
            raise MappingError("Bundle has no resolvable payer Organization - cannot build an 835 message")

        payee = None
        if payment_reconciliation.requestor:
            payee_id = payment_reconciliation.requestor.reference.removeprefix("urn:uuid:")
            payee = next(
                (
                    e.resource
                    for e in bundle.entry
                    if e.resource.get_resource_type() == "Organization" and e.resource.id == payee_id
                ),
                None,
            )
        if payee is None:
            raise MappingError("Bundle has no resolvable payee Organization - cannot build an 835 message")

        trace_number = bundle.identifier.value if bundle.identifier else "0000000000"
        now = envelope_datetime(bundle.timestamp)

        envelope_segments = build_envelope_segments(now)
        header_segments = [
            f"ST*835*{DEFAULT_ST_CONTROL}~",
            _build_bpr_segment(payment_reconciliation),
            f"TRN*1*{trace_number}*1512345678~",
            _build_n1_segment("PR", payer),
            _build_n1_segment("PE", payee),
        ]
        body_segments = [_build_clp_segment(detail) for detail in (payment_reconciliation.detail or [])]

        trailer_segments = build_trailer_segments(header_segments, body_segments)
        return "".join(envelope_segments + header_segments + body_segments + trailer_segments)
