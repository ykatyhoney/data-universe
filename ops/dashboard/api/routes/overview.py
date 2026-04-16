from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.auth import AuthDep
from dashboard.api.dto import ChainStateDTO, OverviewDTO
from datastore.models import ChainState, DDJob
from datastore.repositories import AccountRepo, ProxyRepo, TaskRepo, WorkerRepo
from shared.infra import get_session

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview", response_model=OverviewDTO)
async def overview(
    _: AuthDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OverviewDTO:
    proxies = await ProxyRepo.counts_by_state(session)
    accounts = await AccountRepo.counts_by_source_state(session)
    workers = await WorkerRepo.counts_by_state(session)
    tasks = await TaskRepo.counts_by_state(session)

    active_jobs_res = await session.execute(select(func.count()).select_from(DDJob))
    active_dd_jobs = int(active_jobs_res.scalar_one() or 0)

    # latest chain-state row across any hotkey
    latest_res = await session.execute(select(ChainState).order_by(ChainState.ts.desc()).limit(1))
    latest_row = latest_res.scalar_one_or_none()
    latest: ChainStateDTO | None = (
        ChainStateDTO.model_validate(latest_row) if latest_row is not None else None
    )

    return OverviewDTO(
        proxies_by_state=proxies,
        accounts_by_source_state=accounts,
        workers_by_state=workers,
        tasks_by_state=tasks,
        active_dd_jobs=active_dd_jobs,
        latest_chain_state=latest,
    )
