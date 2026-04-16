"""Wire/event types for the proxy pool.

``ProxyEndpoint`` is what backends return. ``LeaseRequest`` / ``LeaseResponse``
is what callers (workers, M5+) get from ``POST /api/proxy-pool/lease``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.clock import now_utc


class ProxyBackend(StrEnum):
    STATIC_LIST = "static_list"
    BRIGHT_DATA = "bright_data"
    IPROYAL = "iproyal"
    OXYLABS = "oxylabs"


class ProxyState(StrEnum):
    """Same values as :class:`shared.schemas.ProxyState` — duplicated locally
    so proxy_pool can evolve independently without circular imports."""

    HEALTHY = "healthy"
    COOLING = "cooling"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"


class LeaseOutcome(StrEnum):
    """Caller reports one of these on release. Drives fail-streak logic."""

    OK = "ok"
    RATE_LIMITED = "rate_limited"  # 429 — proxy likely fine, account needs to cool
    BLOCKED = "blocked"  # 403/captcha — proxy probably burned
    TIMEOUT = "timeout"
    ERROR = "error"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProxyEndpoint(_Frozen):
    """One entry returned by a :class:`ProxyBackend`.

    ``url`` is the fully-formed ``http(s)://[user[:pass]@]host:port`` URL
    ready to pass to a client. For residential providers the username often
    encodes region/session; see ``supports_sticky`` below.
    """

    id: str  # stable across restarts — usually hash(url) or provider-supplied
    url: str
    backend: ProxyBackend = ProxyBackend.STATIC_LIST
    supports_sticky: bool = False  # True if session token can be injected into user
    country: str | None = None  # reserved for later; not used in M3


class LeaseRequest(_Frozen):
    account_id: UUID | None = None
    source: str  # "x" / "reddit" / "youtube" — used by metrics labels
    sticky_minutes: int = Field(default=0, ge=0, le=60)
    # Session id the caller wants pinned. If not supplied and sticky_minutes>0
    # we generate one; otherwise the lease is non-sticky (fresh exit IP).
    session_id: str | None = None


class LeaseResponse(_Frozen):
    lease_id: str  # opaque token the caller passes back to /release
    proxy_id: str
    url: str  # final URL with session token injected if applicable
    session_id: str | None
    expires_at: datetime  # lease TTL; caller MUST release by then


class ReleaseRequest(_Frozen):
    lease_id: str
    outcome: LeaseOutcome
    latency_ms: int | None = None


class ProxySnapshot(_Frozen):
    """Dashboard-facing view of one proxy. Served by ``GET /api/proxy-pool/state``."""

    id: str
    url_masked: str  # credentials scrubbed before sending to frontend
    backend: str
    state: str
    session_id: str | None
    last_probe_at: datetime | None
    fail_streak: int
    quarantined_until: datetime | None
    created_at: datetime


class PoolState(_Frozen):
    """Aggregate + per-proxy view for the dashboard."""

    proxies: list[ProxySnapshot]
    counts_by_state: dict[str, int]
    ts: datetime = Field(default_factory=now_utc)
