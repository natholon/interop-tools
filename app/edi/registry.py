"""Transaction-set dispatch table - the app/cda/registry.py /
app/mappings/registry.py equivalent for X12. Keyed by ST01 (the transaction
set identifier code, e.g. "270"/"271") - X12's own discriminator already is
the CDA-templateId-equivalent axis here, so (unlike HL7v2's
(message_type, trigger_event) pair) no second dimension is needed - **with
one real exception, 837**, see get_transaction_builder's own docstring."""

from app.edi.base import EdiTransactionBuilder
from app.edi.claim_837d import Edi837dBuilder
from app.edi.claim_837i import Edi837iBuilder
from app.edi.claim_837p import Edi837pBuilder
from app.edi.claim_status import Edi276Builder, Edi277Builder
from app.edi.common import resolve_837_variant
from app.edi.eligibility_270 import Edi270Builder
from app.edi.eligibility_271 import Edi271Builder
from app.edi.prior_auth import Edi278Builder
from app.edi.remittance_835 import Edi835Builder
from app.hl7.errors import MappingError

_TRANSACTION_BUILDERS: dict[str, EdiTransactionBuilder] = {
    Edi270Builder.transaction_set_id: Edi270Builder(),
    Edi271Builder.transaction_set_id: Edi271Builder(),
    Edi276Builder.transaction_set_id: Edi276Builder(),
    Edi277Builder.transaction_set_id: Edi277Builder(),
    # 278 is the one exception to "one ST01 per builder" among the other
    # EDI families - request and response share the literal ST01="278"
    # (see app/edi/prior_auth.py's own module docstring), so Edi278Builder
    # is the only entry in this dict that internally branches on BHT02
    # rather than being one of a request/response pair of registry
    # entries. "837" is NOT in this dict at all - see
    # get_transaction_builder's own docstring for why it's intercepted
    # before this dict is ever consulted.
    Edi278Builder.transaction_set_id: Edi278Builder(),
    Edi835Builder.transaction_set_id: Edi835Builder(),
}

def get_transaction_builder(st01: str, st03: str = "") -> EdiTransactionBuilder:
    """Raises MappingError if st01 isn't registered.

    **"837" is intercepted before the dict is consulted**: 837P/837I/837D
    share the literal ST01="837", so ST01 alone cannot select a builder for
    this one family. The variant comes from `st03` (Implementation Convention
    Reference), resolved via the shared `common.py::resolve_837_variant` -
    shared because `app/edi/validation.py` needs the identical decision for
    its own 837 rule dispatch and the two must never disagree.

    ST03 rather than GS08 because it is the authoritative field for this
    ("ST03 will always take precedence over GS08", per multiple companion
    guides) and, being local to the ST segment, needs no change to
    Interchange/FunctionalGroup's parsed shape.

    `st03` is situational - not every sender populates it - so an absent or
    unrecognized value defaults to `Edi837pBuilder`, the dominant real-world
    variant. Content sniffing (SV1 vs SV2 vs SV3) was considered and judged
    more complexity than this edge case warrants."""
    normalized_st01 = st01.strip().upper()
    if normalized_st01 == "837":
        variant = resolve_837_variant(st03)
        if variant == "837I":
            return Edi837iBuilder()
        if variant == "837D":
            return Edi837dBuilder()
        return Edi837pBuilder()

    # .strip().upper() to mirror app/mappings/registry.py::get_mapper's own
    # key normalization exactly - ST01 values are numeric in practice so
    # this is currently inert, but an unnormalized lookup here would be the
    # one dispatch point in this codebase that doesn't match that pattern.
    builder = _TRANSACTION_BUILDERS.get(normalized_st01)
    if builder is None:
        raise MappingError(f"No builder registered for X12 transaction set {st01!r}")
    return builder
