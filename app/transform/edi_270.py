"""FHIR Bundle -> X12 270 (Health Care Eligibility Benefit Inquiry) - the
third reverse-direction slice, and the first proof this architecture
generalizes to a delimited-text format with its own self-describing
envelope (ISA/GS/ST...SE/GE/IEA) and HL-hierarchy loop structure, not just
to pipe-delimited HL7v2 and XML C-CDA. Scoped to 270's own real Phase-1
forward scope (see app/edi/eligibility_270.py's own module docstring: payer
2000A / provider 2000B / subscriber 2000C / optional dependent 2000D, one
EQ item, one DTP serviced date) - 270/271 had no earlier, narrower slice
the way CCD's Problems-only or ADT's A01-only slices did, so there's no
"first slice within 270" to further reduce this to.

Reverses app/edi/common.py::build_organization_from_nm1/
build_practitioner_from_nm1/build_patient_from_nm1_dmg/
build_coverage/build_service_type_category field-for-field, using each
one's own exact NM1/DMG element positions and the same NM1_ID_QUALIFIER_
SYSTEM table (in reverse) that resolve_id_qualifier_system builds forward.
Shared envelope/NM1/DMG/payer-provider/subscriber-dependent logic lives in
app/transform/edi_common.py (see that module's own docstring - promoted
there once app/transform/edi_271.py became a second real consumer).

**A real, disclosed round-trip fidelity gap, not a bug**: several 270
fields the forward mapper reads have no FHIR-side home at all (ISA/GS
envelope-level sender/receiver ids, control numbers) and get a fixed,
disclosed placeholder value on the way back out, the same "no source
field, use a disclosed placeholder" precedent app/transform/hl7_adt.py
already established for MSH-3/4/5/6."""

from fhir.resources.R4B.bundle import Bundle

from app.edi.common import SERVICE_TYPE_CODE_SYSTEM
from app.edi.generator import format_x12_date, format_x12_time
from app.hl7.errors import MappingError
from app.transform.base import MessageBuilder
from app.transform.common import find_resource, find_resources, format_hl7_date
from app.transform.edi_common import (
    DEFAULT_ST_CONTROL,
    build_dmg,
    build_envelope_segments,
    build_org_nm1,
    build_person_nm1,
    build_trailer_segments,
    envelope_datetime,
    resolve_payer_and_provider,
    resolve_subscriber_and_dependent,
    sanitize_x12_text,
)


class Edi270Builder(MessageBuilder):
    def build_message(self, bundle: Bundle) -> str:
        request = find_resource(bundle, "CoverageEligibilityRequest")
        payer, provider = resolve_payer_and_provider(
            bundle,
            request.insurer if request else None,
            request.provider if request else None,
        )
        if payer is None:
            raise MappingError("Bundle has no Organization resource - cannot resolve the payer for a 270 message")
        if provider is None:
            raise MappingError("Bundle has no provider resource (Organization/Practitioner) for a 270 message")

        patients = find_resources(bundle, "Patient")
        if not patients:
            raise MappingError("Bundle has no Patient resource - cannot build a 270 message")
        subscriber, dependent = resolve_subscriber_and_dependent(bundle, patients)

        now = envelope_datetime(bundle.timestamp)
        bht_reference = sanitize_x12_text(bundle.identifier.value) if bundle.identifier and bundle.identifier.value else "REF00000001"

        envelope_segments = build_envelope_segments(now)

        st_to_hl_segments = [
            f"ST*270*{DEFAULT_ST_CONTROL}~",
            f"BHT*0022*13*{bht_reference}*{format_x12_date(now)}*{format_x12_time(now)}~",
            "HL*1**20*1~",
            build_org_nm1("PR", payer),
            "HL*2*1*21*1~",
            build_org_nm1("1P", provider)
            if provider.get_resource_type() == "Organization"
            else build_person_nm1("1P", provider, include_id=True),
            f"HL*3*2*22*{1 if dependent else 0}~",
            build_person_nm1("IL", subscriber, include_id=True),
        ]
        subscriber_dmg = build_dmg(subscriber)
        if subscriber_dmg:
            st_to_hl_segments.append(subscriber_dmg)
        if dependent is not None:
            st_to_hl_segments.append("HL*4*3*23*0~")
            st_to_hl_segments.append(build_person_nm1("QC", dependent, include_id=False))
            dependent_dmg = build_dmg(dependent)
            if dependent_dmg:
                st_to_hl_segments.append(dependent_dmg)

        body_segments = []
        if request is not None and request.item:
            for item in request.item:
                if item.category and item.category.coding:
                    coding = item.category.coding[0]
                    if coding.system == SERVICE_TYPE_CODE_SYSTEM and coding.code:
                        body_segments.append(f"EQ*{coding.code}~")
        if not body_segments:
            body_segments.append("EQ*30~")
        if request is not None and request.servicedDate:
            body_segments.append(f"DTP*291*D8*{format_hl7_date(request.servicedDate)}~")

        trailer_segments = build_trailer_segments(st_to_hl_segments, body_segments)

        return "".join(envelope_segments + st_to_hl_segments + body_segments + trailer_segments)
