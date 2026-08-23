"""Shared render context for the single page this app serves.

`app/routes/convert.py` and `app/routes/data_specification.py` both render
`index.html`, each populating only the keys its own outcome changes. The
template iterates `supported_types`/`transform_target_options`, and Jinja2
raises on iterating an undefined - so every key has to be present on every
render regardless of which router served it. Same "extract on second real
consumer" move as `app/routes/errors.py` and `app/routes/dropdowns.py`.
"""

from app.routes.dropdowns import grouped_supported_targets, grouped_supported_types


def default_page_context() -> dict:
    return {
        "hl7_text": "",
        "supported_types": grouped_supported_types(),
        # Message -> FHIR (no-JS fallbacks; the JS path renders these panes itself)
        "validation_result": None,
        "validation_error": None,
        "crosswalk_result": None,
        "crosswalk_bundle": None,
        "crosswalk_error": None,
        "dedup_summary": None,
        # FHIR -> Message
        "transform_bundle_json": "",
        "transform_target": "",
        "transform_target_options": grouped_supported_targets(),
        "transform_result": None,
        "transform_error": None,
    }
