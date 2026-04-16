from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Now = Callable[[], datetime]


def _default_now() -> datetime:
    return datetime.now(UTC)


_now_impl: Now = _default_now


def now_utc() -> datetime:
    """Current UTC time. Tests may monkeypatch via ``set_clock`` to freeze it."""
    return _now_impl()


def set_clock(fn: Now) -> None:
    """Test helper: replace the clock source."""
    global _now_impl
    _now_impl = fn


def reset_clock() -> None:
    """Restore the real clock."""
    global _now_impl
    _now_impl = _default_now
