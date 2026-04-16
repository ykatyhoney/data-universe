"""Recording helpers — HAR/screenshot path + retention sweep."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from worker import recording


@pytest.fixture(autouse=True)
def _isolate_recording_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point recording._ROOT at a tmp dir so per-test artefacts are isolated
    from real worker logs."""
    root = tmp_path / "worker"
    monkeypatch.setattr(recording, "_ROOT", root)
    monkeypatch.setattr(recording, "_HAR_DIR", root / "har")
    monkeypatch.setattr(recording, "_SCREENSHOT_DIR", root / "screenshot")


def test_paths_compose() -> None:
    assert recording.har_path("t-1").name == "t-1.har"
    assert recording.screenshot_path("t-1").name == "t-1.png"


def test_keep_har_missing_returns_none() -> None:
    assert recording.keep_har("nope") is None


def test_keep_har_present_returns_path() -> None:
    recording.ensure_dirs()
    target = recording.har_path("keep-me")
    target.write_text("{}")
    assert recording.keep_har("keep-me") == target


def test_drop_har_idempotent() -> None:
    recording.ensure_dirs()
    target = recording.har_path("drop")
    target.write_text("{}")
    assert target.is_file()
    recording.drop_har("drop")
    assert not target.is_file()
    # Second call — no-op, no error.
    recording.drop_har("drop")


def test_prune_retention_removes_old_files() -> None:
    recording.ensure_dirs()
    old = recording.har_path("ancient")
    young = recording.har_path("fresh")
    old.write_text("{}")
    young.write_text("{}")
    # Backdate ``old`` by 99 hours.
    past = time.time() - 99 * 3600
    import os

    os.utime(old, (past, past))

    removed = recording.prune_debug_artifacts_older_than(hours=48)
    assert removed == 1
    assert not old.exists()
    assert young.exists()
