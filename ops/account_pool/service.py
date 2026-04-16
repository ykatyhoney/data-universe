"""Account pool service — cookies, rate budgets, proxy pinning.

Responsibilities:
    1. ``import_account`` — seal cookies with :class:`CookieSealer`, persist.
    2. ``lease`` — pick an account with budget remaining (optionally pinned
       to a proxy session), return cookies + UA + proxy lease atomically.
    3. ``release`` — translate outcome → state machine; release paired proxy.
    4. ``snapshot`` — dashboard view, cookies intentionally absent.

Design notes:
- Cookies NEVER appear in logs. The service logs by ``account_id`` only;
  a regex test in ``test_account_no_log_leak.py`` enforces this.
- Rate budgets live in Redis (min + hour counters, auto-expiring) — one
  source of truth across processes and lease invocations.
- Proxy pinning is *per account*, not per scrape. The account's
  ``pinned_proxy_id`` ties it to one specific proxy, and the lease passes
  ``account_id`` as the sticky key to ProxyPoolService.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from account_pool.crypto import CookieSealer
from account_pool.schemas import (
    AccountImport,
    AccountLeaseRequest,
    AccountLeaseResponse,
    AccountPoolState,
    AccountReleaseRequest,
    AccountSnapshot,
    LeaseOutcome,
)
from datastore.models import Account
from datastore.repositories import AccountRepo
from proxy_pool.schemas import LeaseRequest as ProxyLeaseRequest
from proxy_pool.schemas import ReleaseRequest as ProxyReleaseRequest
from proxy_pool.service import ProxyPoolService
from shared.clock import now_utc
from shared.infra import get_redis, get_session_factory
from shared.logging import get_logger
from shared.metrics import account_rate_budget_remaining, account_state

log = get_logger(__name__)

# Rate budgets — defaults per M4 spec; env-tunable later.
BUDGET_PER_MINUTE = 50
BUDGET_PER_HOUR = 500
COOLING_MINUTES = 30
QUARANTINE_FAIL_THRESHOLD = 3  # failed leases within window → quarantine
DEFAULT_LEASE_TTL_SECONDS = 60 * 60
STICKY_MINUTES = 45

# Redis keys.
_BUDGET_MIN_PREFIX = "acct:budget:m:"  # + account_id  (TTL 60s)
_BUDGET_HOUR_PREFIX = "acct:budget:h:"  # + account_id  (TTL 3600s)
_LEASE_PREFIX = "acct:lease:"  # + lease_id  (TTL = DEFAULT_LEASE_TTL_SECONDS)


def _new_lease_id() -> str:
    return secrets.token_urlsafe(12)


def _budget_min_key(account_id: str) -> str:
    return f"{_BUDGET_MIN_PREFIX}{account_id}"


def _budget_hour_key(account_id: str) -> str:
    return f"{_BUDGET_HOUR_PREFIX}{account_id}"


def _lease_key(lease_id: str) -> str:
    return f"{_LEASE_PREFIX}{lease_id}"


class AccountUnavailable(Exception):
    """No active account with budget remaining for the requested source."""


class AccountLeaseNotFound(Exception):
    """Release called with an unknown / expired lease id."""


class AccountAlreadyImported(Exception):
    """import_account called for an id that already exists."""


class AccountPoolService:
    def __init__(self, sealer: CookieSealer, proxy_pool: ProxyPoolService | None) -> None:
        self._sealer = sealer
        self._proxy_pool = proxy_pool
        # Lazy-created; bound to the running loop when first acquired.
        self._write_lock: asyncio.Lock | None = None

    def _guard(self) -> asyncio.Lock:
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    # ---------- import ---------- #

    async def import_account(self, payload: AccountImport) -> str:
        account_id = str(uuid4())
        # Seal a typed v2 blob so lease can demux cookies vs credentials.
        # v1 blobs (bare cookie array) remain readable via :meth:`_unseal_auth`.
        blob: dict[str, Any] = {"v": 2}
        if payload.cookies is not None:
            blob["cookies"] = [c.model_dump(exclude_none=True) for c in payload.cookies]
        if payload.credentials is not None:
            blob["credentials"] = payload.credentials
        sealed = self._sealer.seal(blob)
        factory = get_session_factory()
        async with self._guard(), factory() as s, s.begin():
            try:
                await AccountRepo.insert(
                    s,
                    account_id=account_id,
                    source=payload.source,
                    user_agent=payload.user_agent,
                    cookies_sealed=sealed,
                    pinned_proxy_id=payload.pinned_proxy_id,
                    imported_at=now_utc(),
                    notes=payload.notes,
                )
            except Exception as e:
                raise AccountAlreadyImported(str(e)) from e
        log.info(
            "account_pool.imported",
            account_id=account_id,
            source=payload.source,
            pinned_proxy_id=payload.pinned_proxy_id,
        )
        return account_id

    # ---------- lease ---------- #

    async def lease(self, req: AccountLeaseRequest) -> AccountLeaseResponse:
        redis = get_redis()
        candidate = await self._pick_candidate(req.source)
        if candidate is None:
            raise AccountUnavailable(f"no active account for source={req.source}")

        # Check + atomically bump budget — INCR returns new value, then set
        # TTL on first use. If over limit, undo the increment and skip.
        m_key = _budget_min_key(candidate.id)
        h_key = _budget_hour_key(candidate.id)
        m_used = int(await redis.incr(m_key))
        if m_used == 1:
            await redis.expire(m_key, 60)
        h_used = int(await redis.incr(h_key))
        if h_used == 1:
            await redis.expire(h_key, 3600)

        if m_used > BUDGET_PER_MINUTE or h_used > BUDGET_PER_HOUR:
            await redis.decr(m_key)
            await redis.decr(h_key)
            raise AccountUnavailable(
                f"account {candidate.id} budget exhausted "
                f"(m={m_used}/{BUDGET_PER_MINUTE}, h={h_used}/{BUDGET_PER_HOUR})"
            )

        # Decrypt auth material (kept in-memory; never logged).
        cookies, credentials = self._unseal_auth(candidate.cookies_sealed or b"")

        # Paired proxy lease if the account is pinned to a proxy.
        proxy_lease = None
        if candidate.pinned_proxy_id and self._proxy_pool is not None:
            try:
                proxy_lease = await self._proxy_pool.lease(
                    ProxyLeaseRequest(
                        account_id=UUID(candidate.id),
                        source=req.source,
                        sticky_minutes=STICKY_MINUTES,
                        session_id=candidate.id,  # deterministic per account
                    )
                )
            except Exception as e:
                log.warning(
                    "account_pool.proxy_lease_failed",
                    account_id=candidate.id,
                    error=str(e),
                )

        now = now_utc()
        lease_id = _new_lease_id()
        expires_at = now + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS)
        await redis.setex(
            _lease_key(lease_id),
            DEFAULT_LEASE_TTL_SECONDS,
            f"{candidate.id}|{proxy_lease.lease_id if proxy_lease else ''}",
        )

        account_rate_budget_remaining.labels(account_id=candidate.id, source=candidate.source).set(
            max(0, BUDGET_PER_MINUTE - m_used)
        )

        log.info(
            "account_pool.lease",
            account_id=candidate.id,
            source=req.source,
            action=req.action,
            pinned_proxy=bool(proxy_lease),
        )
        return AccountLeaseResponse(
            lease_id=lease_id,
            account_id=candidate.id,
            source=candidate.source,
            cookies=cookies,
            credentials=credentials,
            user_agent=candidate.user_agent or "",
            proxy_lease=proxy_lease,
            expires_at=expires_at,
        )

    def _unseal_auth(self, ciphertext: bytes) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        """Unseal the auth blob, supporting both v1 (bare cookie list) and
        v2 (dict with ``cookies`` and/or ``credentials``) layouts."""
        raw = self._sealer.unseal(ciphertext)
        if isinstance(raw, list):
            # v1 — pre-M7 accounts, cookies-only.
            return raw, None
        if isinstance(raw, dict):
            cookies = raw.get("cookies")
            credentials = raw.get("credentials")
            # Safety: validate shapes.
            if cookies is not None and not isinstance(cookies, list):
                cookies = None
            if credentials is not None and not isinstance(credentials, dict):
                credentials = None
            return cookies, credentials
        return None, None

    async def _pick_candidate(self, source: str) -> Account | None:
        """Pick an eligible account for ``source``. Cooled accounts whose
        cooldown has elapsed are first auto-returned to active."""
        factory = get_session_factory()
        async with factory() as s:
            accounts = await AccountRepo.active_for_source(s, source)
            if not accounts:
                # See if any cooled account is ready to come back.
                now = now_utc()
                async with s.begin_nested():
                    all_accounts = await AccountRepo.list_all(s)
                    recovered = False
                    for a in all_accounts:
                        if (
                            a.state == "cooling"
                            and a.cooling_until
                            and a.cooling_until.replace(tzinfo=a.cooling_until.tzinfo or now.tzinfo) < now
                        ):
                            await AccountRepo.set_state(
                                s,
                                account_id=a.id,
                                state="active",
                                cooling_until=None,
                            )
                            recovered = True
                    if recovered:
                        accounts = await AccountRepo.active_for_source(s, source)

        return secrets.choice(list(accounts)) if accounts else None

    # ---------- release ---------- #

    async def release(self, req: AccountReleaseRequest) -> None:
        redis = get_redis()
        raw = await redis.get(_lease_key(req.lease_id))
        if raw is None:
            raise AccountLeaseNotFound(req.lease_id)
        account_id, paired_proxy_lease_id = [*raw.split("|", 1), ""][:2]

        # Release the paired proxy first so a failure here doesn't prevent
        # the account state update. Outcome mapping is intentional:
        #   account OK           → proxy outcome OK (proxy behaved).
        #   rate_limited         → proxy OK (account got throttled, not the IP).
        #   auth_failed          → proxy OK (cookies died, IP still fine).
        #   blocked / timeout    → proxy BLOCKED / TIMEOUT (IP involvement likely).
        proxy_lease_id = req.proxy_lease_id or paired_proxy_lease_id
        if proxy_lease_id and self._proxy_pool is not None:
            proxy_outcome_map = {
                LeaseOutcome.OK: "ok",
                LeaseOutcome.RATE_LIMITED: "ok",
                LeaseOutcome.AUTH_FAILED: "ok",
                LeaseOutcome.BLOCKED: "blocked",
                LeaseOutcome.TIMEOUT: "timeout",
                LeaseOutcome.ERROR: "error",
            }
            from proxy_pool.schemas import LeaseOutcome as ProxyOutcome

            try:
                await self._proxy_pool.release(
                    ProxyReleaseRequest(
                        lease_id=proxy_lease_id,
                        outcome=ProxyOutcome(proxy_outcome_map[req.outcome]),
                    )
                )
            except Exception as e:
                log.warning(
                    "account_pool.proxy_release_failed",
                    error=str(e),
                    proxy_lease_id=proxy_lease_id,
                )

        now = now_utc()
        factory = get_session_factory()
        async with self._guard(), factory() as s, s.begin():
            if req.outcome == LeaseOutcome.OK:
                await AccountRepo.touch_ok(s, account_id, now)
                await AccountRepo.promote_new_to_active(s, account_id)
            elif req.outcome in (LeaseOutcome.AUTH_FAILED, LeaseOutcome.BLOCKED):
                streak = await AccountRepo.bump_fail(s, account_id, now, req.outcome.value)
                if streak >= QUARANTINE_FAIL_THRESHOLD:
                    await AccountRepo.set_state(s, account_id=account_id, state="quarantined")
                else:
                    await AccountRepo.set_state(
                        s,
                        account_id=account_id,
                        state="cooling",
                        cooling_until=now + timedelta(minutes=COOLING_MINUTES),
                    )
            elif req.outcome == LeaseOutcome.RATE_LIMITED:
                # Not an account fail — just record the timestamp.
                pass
            else:
                # timeout / error — soft fail, just record.
                await AccountRepo.bump_fail(s, account_id, now, req.outcome.value)

        # Update per-account state gauge.
        factory2 = get_session_factory()
        async with factory2() as s:
            acc = await AccountRepo.get(s, account_id)
        if acc is not None:
            for st in ("new", "active", "cooling", "quarantined", "retired"):
                account_state.labels(account_id=account_id, source=acc.source, state=st).set(
                    1 if acc.state == st else 0
                )

        await redis.delete(_lease_key(req.lease_id))
        log.info("account_pool.release", account_id=account_id, outcome=req.outcome.value)

    # ---------- admin ---------- #

    async def set_state(self, account_id: str, state: str) -> None:
        factory = get_session_factory()
        async with self._guard(), factory() as s, s.begin():
            await AccountRepo.set_state(s, account_id=account_id, state=state, cooling_until=None)
        log.info("account_pool.admin_set_state", account_id=account_id, state=state)

    # ---------- snapshot ---------- #

    async def snapshot(self) -> AccountPoolState:
        factory = get_session_factory()
        async with factory() as s:
            rows = await AccountRepo.list_all(s)
            counts = await AccountRepo.counts_by_source_state(s)
        redis = get_redis()
        snapshots: list[AccountSnapshot] = []
        for a in rows:
            m_used = int(await redis.get(_budget_min_key(a.id)) or 0)
            h_used = int(await redis.get(_budget_hour_key(a.id)) or 0)
            ua = (a.user_agent or "")[:40] if a.user_agent else None
            snapshots.append(
                AccountSnapshot(
                    id=a.id,
                    source=a.source,
                    state=a.state,
                    pinned_proxy_id=a.pinned_proxy_id,
                    user_agent_preview=ua,
                    imported_at=a.imported_at,
                    last_ok_at=a.last_ok_at,
                    last_fail_at=a.last_fail_at,
                    last_fail_reason=a.last_fail_reason,
                    cooling_until=a.cooling_until,
                    fail_streak=a.fail_streak,
                    budget_minute_used=m_used,
                    budget_minute_max=BUDGET_PER_MINUTE,
                    budget_hour_used=h_used,
                    budget_hour_max=BUDGET_PER_HOUR,
                    notes=a.notes,
                )
            )
        return AccountPoolState(accounts=snapshots, counts_by_source_state=counts)


# ---------- module-level singleton ---------- #


_service: AccountPoolService | None = None


def set_service(svc: AccountPoolService | None) -> None:
    global _service
    _service = svc


def get_service() -> AccountPoolService:
    if _service is None:
        raise RuntimeError("AccountPoolService not initialised — lifespan hasn't run")
    return _service


# Silence an unused-import warning in typed helpers.
_ = (Any,)
