"""Browser helpers — fingerprint determinism, launch args, stealth config."""

from __future__ import annotations

import pytest

from worker.browser import chromium_launch_args, fingerprint_for


def test_fingerprint_is_stable_per_account() -> None:
    a = fingerprint_for("account-abc")
    b = fingerprint_for("account-abc")
    assert a == b


def test_fingerprint_differs_across_accounts() -> None:
    """Not a strict guarantee (tiny chance of collision) but the space is
    large enough that the default samples don't collide."""
    fps = {fingerprint_for(f"acc-{i}") for i in range(10)}
    assert len(fps) > 1


def test_random_fingerprint_when_no_account() -> None:
    fp = fingerprint_for(None)
    assert fp.timezone
    assert fp.locale
    assert fp.viewport_width > 0
    assert fp.viewport_height > 0


@pytest.mark.parametrize(
    "flag",
    [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ],
)
def test_chromium_launch_args_include_critical_flags(flag: str) -> None:
    assert flag in chromium_launch_args()
