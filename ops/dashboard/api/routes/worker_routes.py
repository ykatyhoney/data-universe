"""Worker-debug REST: HAR + screenshot download per task id.

Mostly read-only; auth-gated like the rest. HAR/screenshot files live
under :data:`worker.recording._ROOT`; this endpoint serves them with the
right media type so the dashboard can link to them directly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from dashboard.api.auth import AuthDep
from worker import recording

router = APIRouter(prefix="/api/worker", tags=["worker"])


@router.get("/debug/{task_id}/har")
async def download_har(_: AuthDep, task_id: str) -> FileResponse:
    path = recording.har_path(task_id)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no HAR for task")
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"{task_id}.har",
    )


@router.get("/debug/{task_id}/screenshot")
async def download_screenshot(_: AuthDep, task_id: str) -> FileResponse:
    path = recording.screenshot_path(task_id)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no screenshot for task")
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"{task_id}.png",
    )


@router.get("/debug/{task_id}/exists")
async def debug_exists(_: AuthDep, task_id: str) -> dict[str, bool]:
    """Cheap probe the dashboard uses to decide whether to show debug links."""
    return {
        "har": recording.har_path(task_id).is_file(),
        "screenshot": recording.screenshot_path(task_id).is_file(),
    }
