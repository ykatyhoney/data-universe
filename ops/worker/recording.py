"""HAR + screenshot capture for failed scrapes.

Layout (relative to the service CWD — pm2 sets it to ``ops/``):

    logs/worker/har/{task_id}.har
    logs/worker/screenshot/{task_id}.png

Dashboard serves both via ``/api/worker/debug/{task_id}/{kind}``. Retention:
files older than ``OPS_WORKER_DEBUG_RETENTION_HOURS`` (default 48) are
pruned by the retention sweeper.

S3/MinIO upload is explicitly out of M5 scope — single-host SSD-backed
disk is fine for the foreseeable volume (O(thousands) files/day at worst).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

log = get_logger(__name__)

_ROOT = Path("logs") / "worker"
_HAR_DIR = _ROOT / "har"
_SCREENSHOT_DIR = _ROOT / "screenshot"


def ensure_dirs() -> None:
    _HAR_DIR.mkdir(parents=True, exist_ok=True)
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def har_path(task_id: str) -> Path:
    return _HAR_DIR / f"{task_id}.har"


def screenshot_path(task_id: str) -> Path:
    return _SCREENSHOT_DIR / f"{task_id}.png"


async def capture_screenshot(page: Page, task_id: str) -> Path | None:
    """Best-effort — returns the path on success, None on failure."""
    ensure_dirs()
    target = screenshot_path(task_id)
    try:
        await page.screenshot(path=str(target), full_page=False)
        return target
    except Exception as e:
        log.warning("worker.screenshot_failed", task_id=task_id, error=str(e))
        return None


def keep_har(task_id: str) -> Path | None:
    """Confirm the HAR exists (Playwright writes on context close). Called
    only on the failure path; the runtime calls :func:`drop_har` on success.
    """
    target = har_path(task_id)
    return target if target.is_file() else None


def drop_har(task_id: str) -> None:
    """Delete HAR for a successfully-completed task. HARs are big; keeping
    them on success wastes disk. Idempotent."""
    target = har_path(task_id)
    if target.is_file():
        try:
            target.unlink()
        except OSError as e:
            log.warning("worker.drop_har_failed", task_id=task_id, error=str(e))


def prune_debug_artifacts_older_than(hours: int) -> int:
    """Retention: delete HAR + screenshot files older than ``hours``. Called
    by the dashboard retention sweeper (M2's retention module can import
    this)."""
    import time

    cutoff = time.time() - max(1, hours) * 3600
    removed = 0
    for d in (_HAR_DIR, _SCREENSHOT_DIR):
        if not d.exists():
            continue
        for f in d.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def wipe_debug_root() -> None:
    """Nuclear — test-only."""
    if _ROOT.exists():
        shutil.rmtree(_ROOT)
