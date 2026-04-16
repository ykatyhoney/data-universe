"""Account-pool REST surface — all cookie-gated.

- ``POST /api/account-pool/import``               — add account from JSON blob
- ``POST /api/account-pool/lease``                — scraper asks for an account
- ``POST /api/account-pool/release``              — scraper returns it
- ``GET  /api/account-pool/state``                — dashboard overview
- ``POST /api/account-pool/admin/{id}/quarantine``
- ``POST /api/account-pool/admin/{id}/activate``
- ``POST /api/account-pool/admin/{id}/retire``
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from account_pool.schemas import (
    AccountImport,
    AccountLeaseRequest,
    AccountLeaseResponse,
    AccountPoolState,
    AccountReleaseRequest,
)
from account_pool.service import (
    AccountAlreadyImported,
    AccountLeaseNotFound,
    AccountUnavailable,
    get_service,
)
from dashboard.api.auth import AuthDep

router = APIRouter(prefix="/api/account-pool", tags=["account-pool"])


@router.post("/import", status_code=201)
async def import_account(_: AuthDep, body: AccountImport) -> dict[str, str]:
    try:
        account_id = await get_service().import_account(body)
    except AccountAlreadyImported as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="account already imported") from e
    return {"account_id": account_id}


@router.post("/lease", response_model=AccountLeaseResponse)
async def lease(_: AuthDep, body: AccountLeaseRequest) -> AccountLeaseResponse:
    try:
        return await get_service().lease(body)
    except AccountUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.post("/release", status_code=204)
async def release(_: AuthDep, body: AccountReleaseRequest) -> None:
    try:
        await get_service().release(body)
    except AccountLeaseNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown lease") from e


@router.get("/state", response_model=AccountPoolState)
async def state(_: AuthDep) -> AccountPoolState:
    return await get_service().snapshot()


@router.post("/admin/{account_id}/quarantine", status_code=204)
async def admin_quarantine(_: AuthDep, account_id: str) -> None:
    await get_service().set_state(account_id, "quarantined")


@router.post("/admin/{account_id}/activate", status_code=204)
async def admin_activate(_: AuthDep, account_id: str) -> None:
    await get_service().set_state(account_id, "active")


@router.post("/admin/{account_id}/retire", status_code=204)
async def admin_retire(_: AuthDep, account_id: str) -> None:
    await get_service().set_state(account_id, "retired")
