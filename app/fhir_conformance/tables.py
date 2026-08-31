"""Cardinality and required-binding tables, read off the published R4
StructureDefinitions.

**Generated, not written.** Every entry comes from
`https://hl7.org/fhir/R4/<resource>.profile.json` - its snapshot's own
`min` for the cardinality half, and its `binding` with
`strength: "required"` plus the ValueSet expansion from
`https://hl7.org/fhir/R4/valuesets.json` for the code half.
`tests/test_fhir_conformance.py::test_the_tables_still_match_the_published_spec`
re-derives them from those files, marked `network`.

Scoped to the resource types this app actually builds, plus `Bundle`.

**Direct children only** for the cardinality half. A required element
nested inside a backbone element (`Claim.item.sequence`) applies only
when its parent is present, and `fhir.resources` already enforces those
eagerly at construction - it raises rather than building the resource, so
a violation cannot reach a Bundle to be checked.
"""

# resourceType -> the elements its snapshot marks min >= 1.
REQUIRED_ELEMENTS: dict[str, tuple[str, ...]] = {
    "AllergyIntolerance": ('patient',),
    "Appointment": ('participant', 'status',),
    "Binary": ('contentType',),
    "Bundle": ('type',),
    "CarePlan": ('intent', 'status', 'subject',),
    "Claim": ('created', 'insurance', 'patient', 'priority', 'provider', 'status', 'type', 'use',),
    "ClaimResponse": ('created', 'insurer', 'outcome', 'patient', 'status', 'type', 'use',),
    "Composition": ('author', 'date', 'status', 'title', 'type',),
    "Condition": ('subject',),
    "Coverage": ('beneficiary', 'payor', 'status',),
    "CoverageEligibilityRequest": ('created', 'insurer', 'patient', 'purpose', 'status',),
    "CoverageEligibilityResponse": ('created', 'insurer', 'outcome', 'patient', 'purpose', 'request', 'status',),
    "DiagnosticReport": ('code', 'status',),
    "DocumentReference": ('content', 'status',),
    "Encounter": ('class', 'status',),
    "FamilyMemberHistory": ('patient', 'relationship', 'status',),
    "Immunization": ('occurrence[x]', 'patient', 'status', 'vaccineCode',),
    "MedicationRequest": ('intent', 'medication[x]', 'status', 'subject',),
    "Observation": ('code', 'status',),
    "PaymentReconciliation": ('created', 'paymentAmount', 'paymentDate', 'status',),
    "Procedure": ('status', 'subject',),
    "Task": ('intent', 'status',),
}


# "ResourceType.element" -> (value set url, the codes it admits). Only
# bindings whose value set is a plain include of whole code systems: the
# two that are not are listed below rather than silently skipped.
REQUIRED_BINDINGS: dict[str, tuple[str, frozenset[str]]] = {
    "AllergyIntolerance.category": (
        "http://hl7.org/fhir/ValueSet/allergy-intolerance-category",
        frozenset({'biologic', 'environment', 'food', 'medication'}),
    ),
    "AllergyIntolerance.clinicalStatus": (
        "http://hl7.org/fhir/ValueSet/allergyintolerance-clinical",
        frozenset({'active', 'inactive', 'resolved'}),
    ),
    "AllergyIntolerance.criticality": (
        "http://hl7.org/fhir/ValueSet/allergy-intolerance-criticality",
        frozenset({'high', 'low', 'unable-to-assess'}),
    ),
    "AllergyIntolerance.reaction.severity": (
        "http://hl7.org/fhir/ValueSet/reaction-event-severity",
        frozenset({'mild', 'moderate', 'severe'}),
    ),
    "AllergyIntolerance.type": (
        "http://hl7.org/fhir/ValueSet/allergy-intolerance-type",
        frozenset({'allergy', 'intolerance'}),
    ),
    "AllergyIntolerance.verificationStatus": (
        "http://hl7.org/fhir/ValueSet/allergyintolerance-verification",
        frozenset({'confirmed', 'entered-in-error', 'refuted', 'unconfirmed'}),
    ),
    "Appointment.participant.required": (
        "http://hl7.org/fhir/ValueSet/participantrequired",
        frozenset({'information-only', 'optional', 'required'}),
    ),
    "Appointment.participant.status": (
        "http://hl7.org/fhir/ValueSet/participationstatus",
        frozenset({'accepted', 'declined', 'needs-action', 'tentative'}),
    ),
    "Appointment.status": (
        "http://hl7.org/fhir/ValueSet/appointmentstatus",
        frozenset({'arrived', 'booked', 'cancelled', 'checked-in', 'entered-in-error', 'fulfilled', 'noshow', 'pending', 'proposed', 'waitlist'}),
    ),
    "Bundle.entry.request.method": (
        "http://hl7.org/fhir/ValueSet/http-verb",
        frozenset({'DELETE', 'GET', 'HEAD', 'PATCH', 'POST', 'PUT'}),
    ),
    "Bundle.entry.search.mode": (
        "http://hl7.org/fhir/ValueSet/search-entry-mode",
        frozenset({'include', 'match', 'outcome'}),
    ),
    "Bundle.type": (
        "http://hl7.org/fhir/ValueSet/bundle-type",
        frozenset({'batch', 'batch-response', 'collection', 'document', 'history', 'message', 'searchset', 'transaction', 'transaction-response'}),
    ),
    "CarePlan.activity.detail.kind": (
        "http://hl7.org/fhir/ValueSet/care-plan-activity-kind",
        frozenset({'Appointment', 'CommunicationRequest', 'DeviceRequest', 'MedicationRequest', 'NutritionOrder', 'ServiceRequest', 'Task', 'VisionPrescription'}),
    ),
    "CarePlan.activity.detail.status": (
        "http://hl7.org/fhir/ValueSet/care-plan-activity-status",
        frozenset({'cancelled', 'completed', 'entered-in-error', 'in-progress', 'not-started', 'on-hold', 'scheduled', 'stopped', 'unknown'}),
    ),
    "CarePlan.intent": (
        "http://hl7.org/fhir/ValueSet/care-plan-intent",
        frozenset({'option', 'order', 'plan', 'proposal'}),
    ),
    "CarePlan.status": (
        "http://hl7.org/fhir/ValueSet/request-status",
        frozenset({'active', 'completed', 'draft', 'entered-in-error', 'on-hold', 'revoked', 'unknown'}),
    ),
    "Claim.status": (
        "http://hl7.org/fhir/ValueSet/fm-status",
        frozenset({'active', 'cancelled', 'draft', 'entered-in-error'}),
    ),
    "Claim.use": (
        "http://hl7.org/fhir/ValueSet/claim-use",
        frozenset({'claim', 'preauthorization', 'predetermination'}),
    ),
    "ClaimResponse.outcome": (
        "http://hl7.org/fhir/ValueSet/remittance-outcome",
        frozenset({'complete', 'error', 'partial', 'queued'}),
    ),
    "ClaimResponse.processNote.type": (
        "http://hl7.org/fhir/ValueSet/note-type",
        frozenset({'display', 'print', 'printoper'}),
    ),
    "ClaimResponse.status": (
        "http://hl7.org/fhir/ValueSet/fm-status",
        frozenset({'active', 'cancelled', 'draft', 'entered-in-error'}),
    ),
    "ClaimResponse.use": (
        "http://hl7.org/fhir/ValueSet/claim-use",
        frozenset({'claim', 'preauthorization', 'predetermination'}),
    ),
    "Composition.attester.mode": (
        "http://hl7.org/fhir/ValueSet/composition-attestation-mode",
        frozenset({'legal', 'official', 'personal', 'professional'}),
    ),
    "Composition.relatesTo.code": (
        "http://hl7.org/fhir/ValueSet/document-relationship-type",
        frozenset({'appends', 'replaces', 'signs', 'transforms'}),
    ),
    "Composition.section.mode": (
        "http://hl7.org/fhir/ValueSet/list-mode",
        frozenset({'changes', 'snapshot', 'working'}),
    ),
    "Composition.status": (
        "http://hl7.org/fhir/ValueSet/composition-status",
        frozenset({'amended', 'entered-in-error', 'final', 'preliminary'}),
    ),
    "Condition.clinicalStatus": (
        "http://hl7.org/fhir/ValueSet/condition-clinical",
        frozenset({'active', 'inactive', 'recurrence', 'relapse', 'remission', 'resolved'}),
    ),
    "Condition.verificationStatus": (
        "http://hl7.org/fhir/ValueSet/condition-ver-status",
        frozenset({'confirmed', 'differential', 'entered-in-error', 'provisional', 'refuted', 'unconfirmed'}),
    ),
    "Coverage.status": (
        "http://hl7.org/fhir/ValueSet/fm-status",
        frozenset({'active', 'cancelled', 'draft', 'entered-in-error'}),
    ),
    "CoverageEligibilityRequest.purpose": (
        "http://hl7.org/fhir/ValueSet/eligibilityrequest-purpose",
        frozenset({'auth-requirements', 'benefits', 'discovery', 'validation'}),
    ),
    "CoverageEligibilityRequest.status": (
        "http://hl7.org/fhir/ValueSet/fm-status",
        frozenset({'active', 'cancelled', 'draft', 'entered-in-error'}),
    ),
    "CoverageEligibilityResponse.outcome": (
        "http://hl7.org/fhir/ValueSet/remittance-outcome",
        frozenset({'complete', 'error', 'partial', 'queued'}),
    ),
    "CoverageEligibilityResponse.purpose": (
        "http://hl7.org/fhir/ValueSet/eligibilityresponse-purpose",
        frozenset({'auth-requirements', 'benefits', 'discovery', 'validation'}),
    ),
    "CoverageEligibilityResponse.status": (
        "http://hl7.org/fhir/ValueSet/fm-status",
        frozenset({'active', 'cancelled', 'draft', 'entered-in-error'}),
    ),
    "Device.deviceName.type": (
        "http://hl7.org/fhir/ValueSet/device-nametype",
        frozenset({'manufacturer-name', 'model-name', 'other', 'patient-reported-name', 'udi-label-name', 'user-friendly-name'}),
    ),
    "Device.status": (
        "http://hl7.org/fhir/ValueSet/device-status",
        frozenset({'active', 'entered-in-error', 'inactive', 'unknown'}),
    ),
    "Device.udiCarrier.entryType": (
        "http://hl7.org/fhir/ValueSet/udi-entry-type",
        frozenset({'barcode', 'card', 'manual', 'rfid', 'self-reported', 'unknown'}),
    ),
    "DiagnosticReport.status": (
        "http://hl7.org/fhir/ValueSet/diagnostic-report-status",
        frozenset({'amended', 'appended', 'cancelled', 'corrected', 'entered-in-error', 'final', 'partial', 'preliminary', 'registered', 'unknown'}),
    ),
    "DocumentReference.docStatus": (
        "http://hl7.org/fhir/ValueSet/composition-status",
        frozenset({'amended', 'entered-in-error', 'final', 'preliminary'}),
    ),
    "DocumentReference.relatesTo.code": (
        "http://hl7.org/fhir/ValueSet/document-relationship-type",
        frozenset({'appends', 'replaces', 'signs', 'transforms'}),
    ),
    "DocumentReference.status": (
        "http://hl7.org/fhir/ValueSet/document-reference-status",
        frozenset({'current', 'entered-in-error', 'superseded'}),
    ),
    "Encounter.location.status": (
        "http://hl7.org/fhir/ValueSet/encounter-location-status",
        frozenset({'active', 'completed', 'planned', 'reserved'}),
    ),
    "Encounter.status": (
        "http://hl7.org/fhir/ValueSet/encounter-status",
        frozenset({'arrived', 'cancelled', 'entered-in-error', 'finished', 'in-progress', 'onleave', 'planned', 'triaged', 'unknown'}),
    ),
    "Encounter.statusHistory.status": (
        "http://hl7.org/fhir/ValueSet/encounter-status",
        frozenset({'arrived', 'cancelled', 'entered-in-error', 'finished', 'in-progress', 'onleave', 'planned', 'triaged', 'unknown'}),
    ),
    "FamilyMemberHistory.status": (
        "http://hl7.org/fhir/ValueSet/history-status",
        frozenset({'completed', 'entered-in-error', 'health-unknown', 'partial'}),
    ),
    "Immunization.status": (
        "http://hl7.org/fhir/ValueSet/immunization-status",
        frozenset({'completed', 'entered-in-error', 'not-done'}),
    ),
    "Location.hoursOfOperation.daysOfWeek": (
        "http://hl7.org/fhir/ValueSet/days-of-week",
        frozenset({'fri', 'mon', 'sat', 'sun', 'thu', 'tue', 'wed'}),
    ),
    "Location.mode": (
        "http://hl7.org/fhir/ValueSet/location-mode",
        frozenset({'instance', 'kind'}),
    ),
    "Location.status": (
        "http://hl7.org/fhir/ValueSet/location-status",
        frozenset({'active', 'inactive', 'suspended'}),
    ),
    "MedicationRequest.intent": (
        "http://hl7.org/fhir/ValueSet/medicationrequest-intent",
        frozenset({'filler-order', 'instance-order', 'option', 'order', 'original-order', 'plan', 'proposal', 'reflex-order'}),
    ),
    "MedicationRequest.priority": (
        "http://hl7.org/fhir/ValueSet/request-priority",
        frozenset({'asap', 'routine', 'stat', 'urgent'}),
    ),
    "MedicationRequest.status": (
        "http://hl7.org/fhir/ValueSet/medicationrequest-status",
        frozenset({'active', 'cancelled', 'completed', 'draft', 'entered-in-error', 'on-hold', 'stopped', 'unknown'}),
    ),
    "Observation.status": (
        "http://hl7.org/fhir/ValueSet/observation-status",
        frozenset({'amended', 'cancelled', 'corrected', 'entered-in-error', 'final', 'preliminary', 'registered', 'unknown'}),
    ),
    "Patient.contact.gender": (
        "http://hl7.org/fhir/ValueSet/administrative-gender",
        frozenset({'female', 'male', 'other', 'unknown'}),
    ),
    "Patient.gender": (
        "http://hl7.org/fhir/ValueSet/administrative-gender",
        frozenset({'female', 'male', 'other', 'unknown'}),
    ),
    "Patient.link.type": (
        "http://hl7.org/fhir/ValueSet/link-type",
        frozenset({'refer', 'replaced-by', 'replaces', 'seealso'}),
    ),
    "PaymentReconciliation.outcome": (
        "http://hl7.org/fhir/ValueSet/remittance-outcome",
        frozenset({'complete', 'error', 'partial', 'queued'}),
    ),
    "PaymentReconciliation.processNote.type": (
        "http://hl7.org/fhir/ValueSet/note-type",
        frozenset({'display', 'print', 'printoper'}),
    ),
    "PaymentReconciliation.status": (
        "http://hl7.org/fhir/ValueSet/fm-status",
        frozenset({'active', 'cancelled', 'draft', 'entered-in-error'}),
    ),
    "Practitioner.gender": (
        "http://hl7.org/fhir/ValueSet/administrative-gender",
        frozenset({'female', 'male', 'other', 'unknown'}),
    ),
    "PractitionerRole.availableTime.daysOfWeek": (
        "http://hl7.org/fhir/ValueSet/days-of-week",
        frozenset({'fri', 'mon', 'sat', 'sun', 'thu', 'tue', 'wed'}),
    ),
    "Procedure.status": (
        "http://hl7.org/fhir/ValueSet/event-status",
        frozenset({'completed', 'entered-in-error', 'in-progress', 'not-done', 'on-hold', 'preparation', 'stopped', 'unknown'}),
    ),
    "Specimen.status": (
        "http://hl7.org/fhir/ValueSet/specimen-status",
        frozenset({'available', 'entered-in-error', 'unavailable', 'unsatisfactory'}),
    ),
    "Task.intent": (
        "http://hl7.org/fhir/ValueSet/task-intent",
        frozenset({'filler-order', 'instance-order', 'option', 'order', 'original-order', 'plan', 'proposal', 'reflex-order', 'unknown'}),
    ),
    "Task.priority": (
        "http://hl7.org/fhir/ValueSet/request-priority",
        frozenset({'asap', 'routine', 'stat', 'urgent'}),
    ),
    "Task.status": (
        "http://hl7.org/fhir/ValueSet/task-status",
        frozenset({'accepted', 'cancelled', 'completed', 'draft', 'entered-in-error', 'failed', 'in-progress', 'on-hold', 'ready', 'received', 'rejected', 'requested'}),
    ),
}


# Required bindings this cannot check without a terminology server: their
# value sets are filter-based or enumerate an external registry. Named so
# "not checked" reads as a decision rather than an omission.
UNCHECKED_BINDINGS: dict[str, str] = {
    "Binary.contentType": "mimetypes|4.0.1",
    "Composition.confidentiality": "v3-ConfidentialityClassification|2014-03-26",
}
