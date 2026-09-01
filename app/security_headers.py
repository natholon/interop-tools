"""Response headers that constrain what a browser will do with the page.

Defence in depth rather than a fix: the page already escapes every value
it renders (`renderWithMarks` builds text nodes, Jinja autoescape is on),
so there is no known injection for a CSP to stop. It stops the *next* one,
and costs nothing.

The policy is strict because the page needs nothing external - one
stylesheet, one script and one SVG favicon, all same-origin. `script-src`
allows no inline script and no `eval`, which is the directive that
actually stops XSS. `style-src` does allow inline, for one reason given
at the directive itself.

**Swagger UI is exempt.** `/docs` and `/redoc` load their assets from a
CDN, so the strict policy would leave a blank page; they get every other
header but no CSP. Whether those routes should be public at all is a
deployment decision, not one this module makes.
"""

CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        # A tooltip is positioned by writing style.left/style.top, which
        # CSP counts as an inline style and no class can replace - the
        # coordinates are computed per hover. Verified in a browser:
        # a strict style-src blocked 60 style writes and left the
        # tooltip stuck in the corner.
        #
        # This is a real relaxation, and a narrow one. What stops XSS
        # here is script-src staying strict - no inline script, no
        # eval, same-origin only. An injected *style* cannot execute;
        # its worst use needs an injection point, and every value this
        # page renders is escaped into a text node already.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "font-src 'self'",
        # Nothing here is meant to be embedded, submitted elsewhere, or
        # to load a plugin.
        "frame-ancestors 'none'",
        "form-action 'self'",
        "base-uri 'none'",
        "object-src 'none'",
    )
)

_HEADERS = {
    "x-content-type-options": "nosniff",
    # The app takes pasted clinical messages; a referrer should never
    # carry any of that to a third party.
    "referrer-policy": "no-referrer",
    # Redundant with frame-ancestors for a current browser, free for one
    # that predates CSP level 2.
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
}

_CSP_EXEMPT = ("/docs", "/redoc", "/openapi.json")


class SecurityHeaders:
    """Add the headers above to every response."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        send_csp = not any(path.startswith(p) for p in _CSP_EXEMPT)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for name, value in _HEADERS.items():
                    headers.append((name.encode(), value.encode()))
                if send_csp:
                    headers.append((b"content-security-policy", CSP.encode()))
            await send(message)

        await self.app(scope, receive, send_with_headers)
