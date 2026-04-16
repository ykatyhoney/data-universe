"""Proxy-pool REST surface — all cookie-gated.

- ``POST /api/proxy-pool/lease``         — scrapers ask for a proxy
- ``POST /api/proxy-pool/release``       — scrapers return it
- ``GET  /api/proxy-pool/state``         — dashboard overview
- ``POST /api/proxy-pool/admin/sync``    — force re-load from backends
- ``POST /api/proxy-pool/admin/{id}/disable``
- ``POST /api/proxy-pool/admin/{id}/enable``
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from dashboard.api.auth import AuthDep
from proxy_pool.schemas import LeaseRequest, LeaseResponse, PoolState, ReleaseRequest
from proxy_pool.service import LeaseNotFound, ProxyUnavailable, get_service

router = APIRouter(prefix="/api/proxy-pool", tags=["proxy-pool"])


@router.post("/lease", response_model=LeaseResponse)
async def lease(_: AuthDep, body: LeaseRequest) -> LeaseResponse:
    try:
        return await get_service().lease(body)
    except ProxyUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.post("/release", status_code=204)
async def release(_: AuthDep, body: ReleaseRequest) -> None:
    try:
        await get_service().release(body)
    except LeaseNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown lease") from e


@router.get("/state", response_model=PoolState)
async def state(_: AuthDep) -> PoolState:
    return await get_service().snapshot()


@router.post("/admin/sync", status_code=204)
async def admin_sync(_: AuthDep) -> None:
    await get_service().sync_from_backends()


@router.post("/admin/{proxy_id}/disable", status_code=204)
async def admin_disable(_: AuthDep, proxy_id: str) -> None:
    await get_service().set_disabled(proxy_id, disabled=True)


@router.post("/admin/{proxy_id}/enable", status_code=204)
async def admin_enable(_: AuthDep, proxy_id: str) -> None:
    await get_service().set_disabled(proxy_id, disabled=False)
