from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.auth import AuthDep
from dashboard.api.dto import AccountDTO, ProxyDTO, WorkerDTO
from datastore.repositories import AccountRepo, ProxyRepo, WorkerRepo
from shared.infra import get_session

router = APIRouter(prefix="/api", tags=["fleet"])


@router.get("/proxies", response_model=list[ProxyDTO])
async def list_proxies(
    _: AuthDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProxyDTO]:
    rows = await ProxyRepo.list_all(session)
    return [ProxyDTO.model_validate(r) for r in rows]


@router.get("/accounts", response_model=list[AccountDTO])
async def list_accounts(
    _: AuthDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AccountDTO]:
    rows = await AccountRepo.list_all(session)
    return [AccountDTO.model_validate(r) for r in rows]


@router.get("/workers", response_model=list[WorkerDTO])
async def list_workers(
    _: AuthDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[WorkerDTO]:
    rows = await WorkerRepo.list_all(session)
    return [WorkerDTO.model_validate(r) for r in rows]
