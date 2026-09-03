"""HTTP middleware: the CSRF double-submit guard and the security headers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.errors import ErrorCode, error_response
from app.security import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_SAFE_METHODS,
    csrf_token_matches,
    generate_csrf_token,
    set_csrf_cookie,
)

CallNext = Callable[[Request], Awaitable[Response]]


class CsrfMiddleware(BaseHTTPMiddleware):
    """Every writing request must mirror the CSRF cookie in the CSRF header.

    A cross-site request can make the browser send the cookie, but it cannot read it and
    therefore cannot set the matching header. Sign-in and registration are exempt: they
    happen before a token exists.
    """

    def __init__(self, app: object, settings: Settings, exempt_paths: Iterable[str]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings
        self.exempt_paths = frozenset(exempt_paths)

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        if self._needs_token(request):
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME)
            if not csrf_token_matches(cookie_token, header_token):
                return error_response(
                    403,
                    ErrorCode.CSRF_FAILED,
                    f"Missing or invalid {CSRF_HEADER_NAME} header.",
                )

        response = await call_next(request)
        self._ensure_token(request, response)
        return response

    def _needs_token(self, request: Request) -> bool:
        if request.method in CSRF_SAFE_METHODS:
            return False
        return request.url.path not in self.exempt_paths

    def _ensure_token(self, request: Request, response: Response) -> None:
        """Hand out a token to clients that do not have one yet."""
        if request.cookies.get(CSRF_COOKIE_NAME):
            return
        already_set = any(
            cookie.startswith(f"{CSRF_COOKIE_NAME}=")
            for cookie in response.headers.getlist("set-cookie")
        )
        if not already_set:
            set_csrf_cookie(response, generate_csrf_token(), self.settings)


#: The policy the whole application runs under.
#:
#: `default-src 'self'` is the reason the frontend is written as render functions: a
#: template compiler would need `unsafe-eval`, and an inline `<style>` or `<script>`
#: would need `unsafe-inline`. Both are refused here, so neither can creep in unnoticed.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        # Nothing may load code or styles from anywhere else — no CDN, ever.
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        # Nothing to embed, nothing to embed us in.
        "object-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    ]
)

#: Sent with every response, whatever it is.
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    # No guessing the type of a response; a text file stays a text file.
    "X-Content-Type-Options": "nosniff",
    # A referrer never leaves this instance — the path alone can name a household.
    "Referrer-Policy": "same-origin",
    # Clickjacking: this app is never displayed inside somebody else's frame.
    "X-Frame-Options": "DENY",
    # Nothing here needs a camera, a microphone or a location. (No `interest-cohort`:
    # that opt-out belonged to a Google experiment that was abandoned, and current
    # browsers log it as an unknown feature.)
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Put the security headers on every answer — including errors and static files.

    Deliberately in one place instead of per route: a header that is only set sometimes
    is a header nobody can rely on.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
