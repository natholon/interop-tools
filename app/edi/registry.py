"""Transaction-set dispatch table - the app/cda/registry.py /
app/mappings/registry.py equivalent for X12. Keyed by ST01 (the transaction
set identifier code, e.g. "270"/"271") - X12's own discriminator already is
the CDA-templateId-equivalent axis here, so (unlike HL7v2's
(message_type, trigger_event) pair) no second dimension is needed."""

from app.edi.base import EdiTransactionBuilder
from app.edi.eligibility_270 import Edi270Builder
from app.edi.eligibility_271 import Edi271Builder
from app.hl7.errors import MappingError

_TRANSACTION_BUILDERS: dict[str, EdiTransactionBuilder] = {
    Edi270Builder.transaction_set_id: Edi270Builder(),
    Edi271Builder.transaction_set_id: Edi271Builder(),
}


def get_transaction_builder(st01: str) -> EdiTransactionBuilder:
    # .strip().upper() to mirror app/mappings/registry.py::get_mapper's own
    # key normalization exactly - ST01 values are numeric in practice so
    # this is currently inert, but an unnormalized lookup here would be the
    # one dispatch point in this codebase that doesn't match that pattern.
    builder = _TRANSACTION_BUILDERS.get(st01.strip().upper())
    if builder is None:
        raise MappingError(f"No builder registered for X12 transaction set {st01!r}")
    return builder
