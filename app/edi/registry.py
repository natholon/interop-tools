"""Transaction-set dispatch table - the app/cda/registry.py /
app/mappings/registry.py equivalent for X12. Keyed by ST01 (the transaction
set identifier code, e.g. "270"/"271") - X12's own discriminator already is
the CDA-templateId-equivalent axis here, so (unlike HL7v2's
(message_type, trigger_event) pair) no second dimension is needed - **with
one real exception, 837**, see get_transaction_builder's own docstring."""

from app.edi.base import EdiTransactionBuilder
from app.edi.claim_837i import Edi837iBuilder
from app.edi.claim_837p import Edi837pBuilder
from app.edi.claim_status import Edi276Builder, Edi277Builder
from app.edi.common import is_837i_transaction
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
    """Raises MappingError if st01 isn't registered at all. **"837" is
    intercepted before the registry dict is ever consulted** - 837P/837I
    (and, prospectively, 837D) all share the literal ST01="837", so ST01
    alone can't select a builder for this one family the way it can for
    every other EDI transaction set here. `st03` (Implementation
    Convention Reference - confirmed as the officially authoritative field
    for this exact purpose, "ST03 will always take precedence over GS08"
    per multiple companion guides, and, unlike GS08, local to the ST
    segment itself so no change to Interchange/FunctionalGroup's own
    parsed shape was needed) is checked via the shared
    `common.py::is_837i_transaction` - shared, not declared locally here,
    since `app/edi/validation.py` needs the identical check for its own
    837P-vs-837I rule dispatch and both sides must never disagree.
    `st03` is situational (a real, disclosed possibility - not every
    real-world sender populates it) - when absent or unrecognized, this
    defaults to `Edi837pBuilder` (Professional), the dominant real-world
    837 variant and this app's only-ever-registered 837 builder before
    837I shipped, the same "default to the most common real value when the
    identifying field doesn't resolve" precedent as 270's
    DEFAULT_PURPOSE/278's DEFAULT_CLAIM_TYPE - not a content-sniffing
    fallback (e.g. SV1 vs SV2 presence), which was considered but judged
    more complexity than this situational-field-absent edge case warrants,
    matching this codebase's own established risk tolerance for comparable
    "identifying field missing" cases elsewhere."""
    normalized_st01 = st01.strip().upper()
    if normalized_st01 == "837":
        if is_837i_transaction(st03):
            return Edi837iBuilder()
        return Edi837pBuilder()

    # .strip().upper() to mirror app/mappings/registry.py::get_mapper's own
    # key normalization exactly - ST01 values are numeric in practice so
    # this is currently inert, but an unnormalized lookup here would be the
    # one dispatch point in this codebase that doesn't match that pattern.
    builder = _TRANSACTION_BUILDERS.get(normalized_st01)
    if builder is None:
        raise MappingError(f"No builder registered for X12 transaction set {st01!r}")
    return builder
