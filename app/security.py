"""Console auth: one local token, constant-time compared, cookie or header."""

from __future__ import annotations

import secrets

from fastapi import Cookie, HTTPException, Query, Request, status

from .config import settings

COOKIE_NAME = "gai_token"


def _matches(candidate: str | None) -> bool:
    if not candidate:
        return False
    return secrets.compare_digest(candidate, settings.console_token)


async def require_token(
    request: Request,
    t: str | None = Query(default=None),
    gai_token: str | None = Cookie(default=None),
) -> None:
    """Accept the token from ?t=, the cookie, or X-Console-Token.

    ?t= on first visit sets the cookie so subsequent HTMX/SSE calls authenticate.
    """
    header = request.headers.get("x-console-token")
    if not (_matches(t) or _matches(header) or _matches(gai_token)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token required")
    if t and not _matches(gai_token):
        request.state.set_cookie_token = t
