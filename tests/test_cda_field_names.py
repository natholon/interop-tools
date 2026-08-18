"""Unit tests for app/provenance/cda_field_names.py - the C-CDA sibling of
tests/test_hl7_field_names.py/test_edi_field_names.py - the xpath_location()
-shaped source_location -> human-readable "source field name" label behind
the Data Specification page's crosswalk table and hover tooltip column."""

from app.provenance.cda_field_names import resolve_cda_field_label


def test_bare_trailing_tag_labels():
    assert resolve_cda_field_label("recordTarget/patientRole/patient/name[0]/family") == "Family Name"
    assert resolve_cda_field_label("recordTarget/patientRole/patient/name[0]/given[0]") == "Given Name"
    assert resolve_cda_field_label("recordTarget/patientRole/addr[0]/city") == "City"
    assert resolve_cda_field_label("recordTarget/patientRole/id[0]") == "ID"
    assert resolve_cda_field_label("text") == "Text"
    assert resolve_cda_field_label("title") == "Title"


def test_code_attribute_label_depends_on_parent_tag():
    # "@code" alone is ambiguous - dozens of unrelated coded fields end
    # this way, so the label depends on which tag carries it.
    assert resolve_cda_field_label("act/entryRelationship[SUBJ]/observation/value/@code") == "Coded Value"
    assert resolve_cda_field_label("code/@code") == "Code"
    assert (
        resolve_cda_field_label("recordTarget/patientRole/patient/administrativeGenderCode/@code")
        == "Administrative Gender"
    )
    assert resolve_cda_field_label("substanceAdministration/routeCode/@code") == "Route of Administration"
    assert resolve_cda_field_label("act/statusCode/@code") == "Status Code"
    assert resolve_cda_field_label("procedure/targetSiteCode/@code") == "Target Site"


def test_value_attribute_label_depends_on_parent_tag():
    assert (
        resolve_cda_field_label("componentOf/encompassingEncounter/effectiveTime/low/@value") == "Start Date/Time"
    )
    assert (
        resolve_cda_field_label("componentOf/encompassingEncounter/effectiveTime/high/@value") == "End Date/Time"
    )
    assert resolve_cda_field_label("recordTarget/patientRole/patient/birthTime/@value") == "Date of Birth"
    assert resolve_cda_field_label("recordTarget/patientRole/telecom[0]/@value") == "Contact Point"
    assert resolve_cda_field_label("substanceAdministration/doseQuantity/@value") == "Dose Quantity"


def test_universal_attribute_labels_ignore_parent_tag():
    # @displayName/@negationInd/@moodCode mean the same thing everywhere
    # they occur in this app's own real usage - no parent lookup needed.
    assert resolve_cda_field_label("code/@displayName") == "Display Name"
    assert resolve_cda_field_label("act/entryRelationship[SUBJ]/observation/value/@displayName") == "Display Name"
    assert resolve_cda_field_label("substanceAdministration/@negationInd") == "Negation Indicator"
    assert resolve_cda_field_label("procedure/@negationInd") == "Negation Indicator"
    assert resolve_cda_field_label("substanceAdministration/@moodCode") == "Mood Code"


def test_entry_relationship_label_variant():
    assert resolve_cda_field_label("component[0]/observation/entryRelationship[CAUS]") == "Cause of Death Relationship"


def test_unrecognized_final_segment_returns_none():
    assert resolve_cda_field_label("act/entryRelationship[SUBJ]/observation/someUnmappedTag") is None
    assert resolve_cda_field_label("someUnmappedTag/@code") is None


def test_empty_or_none_source_location_returns_none():
    assert resolve_cda_field_label(None) is None
    assert resolve_cda_field_label("") is None


def test_disclosed_marker_string_returns_none_not_a_crash():
    # narrative_sections.py's own "text (×N blocks)" marker isn't a real
    # xpath at all - must degrade safely, never guess or crash.
    assert resolve_cda_field_label("text (×3 blocks)") is None
