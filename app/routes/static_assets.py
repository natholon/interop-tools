"""Cache-busting for `/static/*` asset URLs - appends the file's own
on-disk modification time as a query string (`?v=<mtime>`) so a browser
that already cached an old copy of `app/static/style.css`/`app.js`/
`data_specification.js` is forced to re-fetch it the moment the file
actually changes, with no build step or manual version bump to remember.

**A real, reproduced bug this exists to prevent, not a hypothetical
one**: `app/main.py` mounts `/static` via a bare `StaticFiles(...)` with
no cache-control configuration, and every template referenced these
assets by a plain, unversioned path (`href="/static/style.css"`) - a
browser's own default HTTP caching heuristics can keep serving a stale
copy across ordinary page reloads with no visible sign anything is
wrong (the server's HTML/API responses are all correct; only the
already-cached JS/CSS is stale). Confirmed directly: a real UI fix
verified correct in a fresh Puppeteer browser context (no prior cache)
was reported broken by the user in their own already-open browser
session - the exact symptom a stale-cached JS file produces (a table
column layout matching the *previous* version of the code)."""

from pathlib import Path

_STATIC_DIR = Path("app/static")


def static_url(filename: str) -> str:
    """`{{ static_url('style.css') }}` -> `/static/style.css?v=<mtime>`.
    Falls back to `?v=0` (still correct, just non-cache-busting) if the
    file can't be stat'd - never raises and blocks the page from
    rendering over a missing/misnamed asset."""
    try:
        version = int((_STATIC_DIR / filename).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{filename}?v={version}"
