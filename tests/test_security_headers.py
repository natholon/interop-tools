"""The response headers, and the one directive that had to be relaxed."""

from fastapi.testclient import TestClient

from app.main import app
from app.security_headers import CSP

client = TestClient(app)


def test_every_page_carries_the_hardening_headers():
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["content-security-policy"] == CSP


def test_api_responses_carry_them_too():
    response = client.post("/api/validate", json={"hl7_text": "MSH|^~\\&|A|B|C|D|20260101120000||ADT^A01|1|P|2.5\r"})
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"


def test_script_src_allows_no_inline_or_eval():
    # This is the directive that actually stops XSS, and it stays strict.
    assert "script-src 'self'" in CSP
    assert "script-src 'self' 'unsafe-inline'" not in CSP
    assert "unsafe-eval" not in CSP


def test_style_src_allows_inline_and_only_style_src_does():
    # A tooltip is positioned by writing style.left/style.top, which no
    # class can replace. Verified in a browser: a strict style-src blocked
    # 60 style writes and left the tooltip in the corner.
    assert "style-src 'self' 'unsafe-inline'" in CSP
    assert CSP.count("unsafe-inline") == 1


def test_the_page_is_not_embeddable_or_a_form_target():
    assert "frame-ancestors 'none'" in CSP
    assert "form-action 'self'" in CSP
    assert "base-uri 'none'" in CSP
    assert "object-src 'none'" in CSP


def test_swagger_is_exempt_from_the_csp_but_not_the_rest():
    # Its assets come from a CDN, so the policy would leave a blank page.
    response = client.get("/docs")
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
