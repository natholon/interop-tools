"""Unit tests for app/routes/static_assets.py - the cache-busting
`static_url()` Jinja2 global that appends a static file's own mtime as a
query string, so a browser can't keep serving a stale cached copy of
app/static/*.js/*.css across an edit."""

from app.routes.static_assets import static_url


def test_static_url_appends_a_version_query_string():
    url = static_url("style.css")
    assert url.startswith("/static/style.css?v=")
    version = url.split("?v=")[1]
    assert version.isdigit()
    assert int(version) > 0


def test_static_url_missing_file_falls_back_to_v0_without_raising():
    assert static_url("does-not-exist.js") == "/static/does-not-exist.js?v=0"


def test_static_url_reflects_real_file_content_changes(tmp_path, monkeypatch):
    # The whole point of this mechanism: editing the file must change the
    # version, or a browser has no reason to treat the URL as new.
    import app.routes.static_assets as static_assets_module

    monkeypatch.setattr(static_assets_module, "_STATIC_DIR", tmp_path)
    asset = tmp_path / "probe.js"
    asset.write_text("console.log('v1');")
    first = static_url("probe.js")

    # Force a distinct mtime (some filesystems have 1-second resolution).
    import os

    later = asset.stat().st_mtime + 2
    asset.write_text("console.log('v2');")
    os.utime(asset, (later, later))
    second = static_url("probe.js")

    assert first != second
