from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from dashboard.api.auth import clear_session_cookie, set_session_cookie
from shared.config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str


@router.post("/login", status_code=204)
async def login(body: LoginIn, response: Response) -> None:
    if not hmac.compare_digest(body.password, get_settings().dashboard_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad password")
    set_session_cookie(response)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    clear_session_cookie(response)
