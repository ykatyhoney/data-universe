from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.auth import AuthDep
from dashboard.api.dto import TaskDTO
from datastore.repositories import TaskRepo
from shared.infra import get_session

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks", response_model=list[TaskDTO])
async def list_tasks(
    _: AuthDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[TaskDTO]:
    rows = await TaskRepo.list_recent(session, limit=limit)
    return [TaskDTO.model_validate(r) for r in rows]
