"""Wire types for the account pool."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from proxy_pool.schemas import LeaseResponse as ProxyLeaseResponse
from shared.clock import now_utc


class AccountState(StrEnum):
    """Subset of :class:`shared.schemas.AccountState` we actually drive from
    code. ``warming`` is a manual human step (vet the account before
    first real use); we don't transition into it automatically.
    """

    NEW = "new"  # just imported; no successful lease yet
    ACTIVE = "active"  # eligible for lease
    COOLING = "cooling"  # auth failure recent; auto-returns to active after TTL
    QUARANTINED = "quarantined"  # repeated failures; admin review required
    RETIRED = "retired"  # terminal; never leased again


class LeaseOutcome(StrEnum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"  # 429 — account fine, just back off
    AUTH_FAILED = "auth_failed"  # 401/403 — cookies dead → cool
    BLOCKED = "blocked"  # captcha/challenge → cool
    TIMEOUT = "timeout"
    ERROR = "error"


class CookieEntry(BaseModel):
    """One cookie, matching the shape Playwright consumes."""

    model_config = ConfigDict(extra="allow")  # accept provider-specific fields

    name: str
    value: str
    domain: str | None = None
    path: str | None = None
    expires: float | None = None
    httpOnly: bool | None = None
    secure: bool | None = None
    sameSite: str | None = None


class AccountImport(BaseModel):
    """Payload for ``POST /api/account-pool/import``.

    One of ``cookies`` or ``credentials`` must be provided:
    - **``cookies``** — for browser-session-authed sources (X via Playwright).
    - **``credentials``** — free-form dict for API-authed sources. Reddit
      PRAW: ``{"client_id", "client_secret", "refresh_token"}`` (or
      ``{"username", "password"}`` for script apps).

    The sealer stores whichever is present as a single JSON blob inside
    ``cookies_sealed``. On lease the blob is unsealed and the plugin picks
    the key it needs.
    """

    model_config = ConfigDict(extra="forbid")

    source: str  # "x" / "reddit"
    user_agent: str
    cookies: list[CookieEntry] | None = None
    credentials: dict[str, Any] | None = None
    pinned_proxy_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _require_auth_material(self) -> AccountImport:
        if not self.cookies and not self.credentials:
            raise ValueError("AccountImport requires at least one of 'cookies' or 'credentials'")
        return self


class AccountLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    action: str | None = None  # optional hint for metric labels (search / profile / …)


class AccountLeaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    account_id: str
    source: str
    # Exactly one of cookies / credentials is populated — depends on which
    # the account was imported with. Plugin picks whichever it needs.
    cookies: list[dict[str, Any]] | None = None
    credentials: dict[str, Any] | None = None
    user_agent: str
    proxy_lease: ProxyLeaseResponse | None = None
    expires_at: datetime


class AccountReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    outcome: LeaseOutcome
    # If the account had a pinned proxy lease, we release it in the same
    # call so callers don't have to coordinate two endpoints.
    proxy_lease_id: str | None = None


class AccountSnapshot(BaseModel):
    """Dashboard-facing view. ``cookies`` field is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    state: str
    pinned_proxy_id: str | None
    user_agent_preview: str | None  # first 40 chars
    imported_at: datetime
    last_ok_at: datetime | None
    last_fail_at: datetime | None
    last_fail_reason: str | None
    cooling_until: datetime | None
    fail_streak: int
    budget_minute_used: int
    budget_minute_max: int
    budget_hour_used: int
    budget_hour_max: int
    notes: str | None = None


class AccountPoolState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: list[AccountSnapshot]
    counts_by_source_state: list[tuple[str, str, int]]
    ts: datetime = Field(default_factory=now_utc)
