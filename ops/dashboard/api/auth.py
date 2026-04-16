"""Single-user cookie auth for the dashboard.

Model: one shared password (env ``OPS_DASHBOARD_PASSWORD``). POST it to
``/api/auth/login``; a signed HMAC cookie ``ops_session`` is set. Every
request to ``/api/*`` (except ``/api/health`` and ``/api/auth/*``) requires
the cookie; ``/ws/live`` reads it the same way.

Not a replacement for real auth — just enough to stop a curious LAN visitor.
Keep the dashboard behind localhost / VPN regardless.
"""

from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Response, status

from shared.config import get_settings

COOKIE_NAME = "ops_session"
_SIGNATURE_SEP = "."


def _sign(message: str, secret: str) -> str:
    return hmac.new(secret.encode(), message.encode(), sha256).hexdigest()


def _mint_token(ttl_seconds: int, secret: str) -> str:
    expires_at = int(time.time()) + ttl_seconds
    payload = str(expires_at)
    return f"{payload}{_SIGNATURE_SEP}{_sign(payload, secret)}"


def _verify_token(token: str, secret: str) -> bool:
    if _SIGNATURE_SEP not in token:
        return False
    payload, given_sig = token.split(_SIGNATURE_SEP, 1)
    expected_sig = _sign(payload, secret)
    if not hmac.compare_digest(given_sig, expected_sig):
        return False
    try:
        expires_at = int(payload)
    except ValueError:
        return False
    return expires_at > int(time.time())


def set_session_cookie(response: Response) -> None:
    s = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=_mint_token(s.dashboard_session_ttl_seconds, s.dashboard_session_secret),
        max_age=s.dashboard_session_ttl_seconds,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax")


def is_valid_cookie(token: str | None) -> bool:
    if not token:
        return False
    return _verify_token(token, get_settings().dashboard_session_secret)


def require_session(
    ops_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> None:
    if not is_valid_cookie(ops_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")


AuthDep = Annotated[None, Depends(require_session)]
