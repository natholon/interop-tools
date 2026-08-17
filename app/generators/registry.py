import random

from app.cda.generator import generate_ccd, generate_discharge_summary, generate_history_and_physical
from app.edi.claim_837d_generator import generate_837d
from app.edi.claim_837i_generator import generate_837i
from app.edi.claim_837p_generator import generate_837p
from app.edi.claim_status_generator import generate_276, generate_277
from app.edi.eligibility_generator import generate_270, generate_271
from app.edi.prior_auth_generator import generate_278_request, generate_278_response
from app.edi.remittance_generator import generate_835
from app.generators.adt import (
    generate_adt_a01,
    generate_adt_a02,
    generate_adt_a03,
    generate_adt_a04,
    generate_adt_a05,
    generate_adt_a08,
    generate_adt_a11,
    generate_adt_a13,
    generate_adt_a38,
)
from app.generators.mdm import (
    generate_mdm_t02,
    generate_mdm_t04,
    generate_mdm_t06,
    generate_mdm_t08,
    generate_mdm_t10,
    generate_mdm_t11,
)
from app.generators.oru import (
    generate_oru_r01,
    generate_oru_r30,
    generate_oru_r31,
    generate_oru_r32,
    generate_oru_r40,
)
from app.generators.siu import (
    generate_siu_s12,
    generate_siu_s13,
    generate_siu_s14,
    generate_siu_s15,
    generate_siu_s17,
    generate_siu_s26,
)
from app.hl7.errors import MappingError

_GENERATORS = {
    ("ADT", "A01"): (generate_adt_a01, "ADT^A01 - Admit"),
    ("ADT", "A02"): (generate_adt_a02, "ADT^A02 - Transfer"),
    ("ADT", "A03"): (generate_adt_a03, "ADT^A03 - Discharge"),
    ("ADT", "A04"): (generate_adt_a04, "ADT^A04 - Register"),
    ("ADT", "A05"): (generate_adt_a05, "ADT^A05 - Pre-admit"),
    ("ADT", "A08"): (generate_adt_a08, "ADT^A08 - Update"),
    ("ADT", "A11"): (generate_adt_a11, "ADT^A11 - Cancel Admit"),
    ("ADT", "A13"): (generate_adt_a13, "ADT^A13 - Cancel Discharge"),
    ("ADT", "A38"): (generate_adt_a38, "ADT^A38 - Cancel Pre-Admit"),
    ("SIU", "S12"): (generate_siu_s12, "SIU^S12 - New Appointment"),
    ("SIU", "S13"): (generate_siu_s13, "SIU^S13 - Reschedule"),
    ("SIU", "S14"): (generate_siu_s14, "SIU^S14 - Modify"),
    ("SIU", "S15"): (generate_siu_s15, "SIU^S15 - Cancel"),
    ("SIU", "S17"): (generate_siu_s17, "SIU^S17 - Delete"),
    ("SIU", "S26"): (generate_siu_s26, "SIU^S26 - Patient No-Show"),
    ("ORU", "R01"): (generate_oru_r01, "ORU^R01 - Observation Result"),
    ("ORU", "R30"): (generate_oru_r30, "ORU^R30 - Point-of-Care Result (New Order)"),
    ("ORU", "R31"): (generate_oru_r31, "ORU^R31 - Point-of-Care Result (Search for Order)"),
    ("ORU", "R32"): (generate_oru_r32, "ORU^R32 - Point-of-Care Result (Pre-Ordered)"),
    ("ORU", "R40"): (generate_oru_r40, "ORU^R40 - Point-of-Care Result (No New Order)"),
    ("MDM", "T02"): (generate_mdm_t02, "MDM^T02 - New Document"),
    ("MDM", "T04"): (generate_mdm_t04, "MDM^T04 - Document Status Update"),
    ("MDM", "T06"): (generate_mdm_t06, "MDM^T06 - Document Addendum"),
    ("MDM", "T08"): (generate_mdm_t08, "MDM^T08 - Document Edit"),
    ("MDM", "T10"): (generate_mdm_t10, "MDM^T10 - Document Replacement"),
    ("MDM", "T11"): (generate_mdm_t11, "MDM^T11 - Document Cancel"),
    # "CCD"/"DISCHARGESUMMARY" stand in for trigger_event even though
    # C-CDA has no real trigger-event concept - reusing this flat
    # (message_type, trigger) registry costs zero UI/route changes
    # (list_supported_types(), /api/generate, the dropdown, and app.js's
    # click handler all work unchanged) versus introducing a genuine third
    # axis for two entries. The dict KEY is uppercase to match generate()'s
    # own .upper()-normalized lookup below (same as every HL7v2 trigger
    # value) - the human-readable LABEL deliberately keeps mixed-case
    # "DischargeSummary" rather than matching the key's all-caps casing
    # (unlike "CCD", where both happen to already coincide, being an
    # acronym): the label is what actually renders in the UI dropdown, and
    # "DISCHARGESUMMARY" reads worse there than "DischargeSummary" - a
    # deliberate readability choice, not an inconsistency to "fix".
    ("CDA", "CCD"): (generate_ccd, "CDA^CCD - Continuity of Care Document"),
    ("CDA", "DISCHARGESUMMARY"): (generate_discharge_summary, "CDA^DischargeSummary - Discharge Summary"),
    ("CDA", "HISTORYANDPHYSICAL"): (
        generate_history_and_physical,
        "CDA^HistoryAndPhysical - History and Physical Note",
    ),
    ("EDI", "270"): (generate_270, "EDI^270 - Eligibility Inquiry"),
    ("EDI", "271"): (generate_271, "EDI^271 - Eligibility Response"),
    ("EDI", "276"): (generate_276, "EDI^276 - Claim Status Request"),
    ("EDI", "277"): (generate_277, "EDI^277 - Claim Status Response"),
    # 278 request and response share the literal ST01="278" (see
    # app/edi/prior_auth.py's own module docstring) - unlike every other
    # EDI pair here, there's no second ST01 value to key a second registry
    # entry off of, so "278REQUEST"/"278RESPONSE" are synthetic
    # trigger_event strings that exist only for this generator dropdown;
    # both still round-trip through the same Edi278Builder on convert,
    # since that builder itself branches on BHT02, not on how the sample
    # was generated.
    ("EDI", "278REQUEST"): (generate_278_request, "EDI^278 - Prior Authorization Request"),
    ("EDI", "278RESPONSE"): (generate_278_response, "EDI^278 - Prior Authorization Response"),
    ("EDI", "835"): (generate_835, "EDI^835 - Remittance Advice"),
    # "837P"/"837I"/"837D" are synthetic trigger_event strings that exist
    # only for this dropdown, the same reason 278's own
    # "278REQUEST"/"278RESPONSE" are - all three share the literal
    # ST01="837" (see app/edi/registry.py::get_transaction_builder's own
    # docstring), so there's no second ST01 to key a second registry entry
    # off of; a generated sample still round-trips through the same
    # ST03-based dispatch on convert, since that dispatch reads the
    # generated text's own ST03, not how the sample was generated.
    ("EDI", "837P"): (generate_837p, "EDI^837P - Professional Claim"),
    ("EDI", "837I"): (generate_837i, "EDI^837I - Institutional Claim"),
    ("EDI", "837D"): (generate_837d, "EDI^837D - Dental Claim"),
}


def list_supported_types() -> list[tuple[str, str, str]]:
    """Return (message_type, trigger_event, human_label) for every supported
    generator, in a stable order suitable for a UI dropdown."""
    return [(msg_type, trigger, label) for (msg_type, trigger), (_, label) in _GENERATORS.items()]


def generate(message_type: str, trigger_event: str, seed: int | None = None) -> str:
    key = (message_type.strip().upper(), trigger_event.strip().upper())
    entry = _GENERATORS.get(key)
    if entry is None:
        raise MappingError(f"No generator registered for message type {message_type}^{trigger_event}")
    generator_fn, _ = entry
    rng = random.Random(seed)
    return generator_fn(rng)
