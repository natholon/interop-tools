"""Cross-format shared helpers for the reverse (FHIR Bundle -> raw text)
direction - the app/transform/ equivalent of app/mappings/common.py's/
app/cda/common.py's/app/edi/common.py's own shared-helper modules for their
respective forward directions. Built proactively (not waiting for a second
literal consumer to force the extraction) since the HL7v2/C-CDA/EDI reverse
builders are known in advance to all need the identical "find a resource in
the Bundle by type" and "format a Python date/datetime back into the
source format's own digit shape" primitives - the same kind of foreseeable
near-term reuse that already justified promoting
app.edi.common.NM1_ID_QUALIFIER_SYSTEM proactively rather than waiting for
a second incident."""

from datetime import date, datetime

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.resource import Resource


def find_resource(bundle: Bundle, resource_type: str) -> Resource | None:
    """The first entry of the given resourceType in Bundle order, or None.
    Every reverse builder needs "the Patient"/"the Encounter" in a Bundle
    this app itself only ever produces one of per type (this app's own
    forward pipelines never emit two Patients in one Bundle), so "first
    match" is unambiguous here - not a general FHIR assumption, a property
    of this app's own output shape."""
    for entry in bundle.entry or []:
        if entry.resource.get_resource_type() == resource_type:
            return entry.resource
    return None


def find_resources(bundle: Bundle, resource_type: str) -> list[Resource]:
    return [entry.resource for entry in (bundle.entry or []) if entry.resource.get_resource_type() == resource_type]


def format_hl7_date(value: date | datetime | str | None) -> str:
    """A FHIR date/dateTime value (already deserialized by fhir.resources
    into a real datetime.date/datetime.datetime, not a string - confirmed
    by direct construction) -> an HL7-TS-shaped YYYYMMDD date-only string,
    matching PID-7/DTP-style date-only fields."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("-", "")[:8]
    return value.strftime("%Y%m%d")


def format_hl7_ts(value: date | datetime | str | None) -> str:
    """A FHIR dateTime/instant value -> an HL7-TS-shaped
    YYYYMMDDHHMMSS[+/-ZZZZ] string, preserving a real timezone offset when
    present rather than dropping it - the reverse-direction mirror of
    app/fhir_models/builders.py::parse_hl7_datetime's own deliberate
    offset-preserving behavior, for the identical "silently-wrong-by-hours
    timing is a real scheduling error" reason documented there."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("-", "").replace(":", "").replace("T", "")
    base = value.strftime("%Y%m%d%H%M%S") if isinstance(value, datetime) else value.strftime("%Y%m%d")
    if isinstance(value, datetime) and value.tzinfo is not None:
        offset = value.utcoffset()
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        return f"{base}{sign}{total_minutes // 60:02d}{total_minutes % 60:02d}"
    return base
