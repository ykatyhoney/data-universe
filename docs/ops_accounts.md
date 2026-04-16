# Account pool (M4)

Cookie-authenticated accounts (X primary, Reddit secondary, YouTube
future). Cookies are encrypted at rest with Fernet; they never land in
logs, never leave the process, never reach the dashboard UI.

## Quick start

```bash
# 1. Generate a Fernet key once, save it in your .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → copy the output into OPS_ACCOUNT_POOL_KEY

# 2. Start the stack
make start

# 3. Prepare a JSON payload (see schema below), then import
python -m account_pool.import_cli /path/to/account.json

# 4. Verify in the dashboard — Accounts panel lights up
```

Cookies are sealed with `OPS_ACCOUNT_POOL_KEY` at import; the JSON file is
no longer needed. **Delete it after import** — the pool is the only copy
that matters.

## Environment variables

| Env var | Required | Purpose |
|---|---|---|
| `OPS_ACCOUNT_POOL_KEY` | **yes (or pool disabled)** | Fernet key (URL-safe base64, 44 chars) |

If `OPS_ACCOUNT_POOL_KEY` is missing, the routes return 503; the rest of
the app still runs. This is intentional: a dev who ships a node without
the key gets a loud warning, not silent plaintext.

## Import JSON schema

```jsonc
{
  "source": "x",                              // "x" | "reddit" | "youtube"
  "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X ...) AppleWebKit/...",
  "cookies": [
    {
      "name": "auth_token",
      "value": "<secret>",
      "domain": ".x.com",
      "path": "/",
      "expires": 1785600000.0,                // optional, seconds since epoch
      "httpOnly": true,
      "secure": true,
      "sameSite": "Lax"                       // optional
    }
  ],
  "pinned_proxy_id": null,                    // or a proxy id from /api/proxy-pool/state
  "notes": "aged 7 days; logged in via mobile"
}
```

Cookie fields are forwarded verbatim to Playwright. Most providers' browser
export extensions (e.g. Chrome's "Cookie-Editor") already produce this shape.

## Exporting cookies safely

1. Log into the target site in a clean browser profile.
2. **Browse naturally** for at least 15 minutes — search, scroll, like a
   couple of posts. A fresh login + immediate export is a classic bot
   signature.
3. Export via a trusted extension to JSON.
4. Strip anything you don't recognise — the service only needs the
   auth-carrying cookies (`auth_token`, `ct0` for X; `reddit_session` /
   `token_v2` for Reddit). Extra cookies are harmless but noisy.
5. `python -m account_pool.import_cli account.json`
6. `shred -u account.json` (or equivalent) — the seal is the only copy.

## Pinning an account to a proxy

Long-lived X accounts survive longer if the IP stays stable. To pin:

```bash
# Look up the desired proxy id
curl -s http://localhost:8000/api/proxy-pool/state | jq '.proxies[].id'
# → 1a2b3c4d...

# Import with pinned_proxy_id set
python -m account_pool.import_cli account.json   # where the JSON has pinned_proxy_id
```

On every lease the account-pool service atomically requests a matching
proxy lease from the proxy pool with the account-id as the sticky key.
One round-trip, one logical identity.

## State machine

```
      import
        │
        ▼
     ┌─────┐
     │ NEW │  (first OK lease → active)
     └──┬──┘
        ▼
   ┌────────┐   auth_failed / blocked   ┌──────────┐
   │ ACTIVE │ ──────────────────────►   │ COOLING  │  (30 min TTL → auto-active)
   └──┬─────┘                           └──┬───────┘
      ▲                                     │ 3rd fail
      │ admin or rate_limited (no-op)       ▼
      │                                ┌─────────────┐
      └────────── admin activate ──────│ QUARANTINED │  (admin review only)
                                       └──┬──────────┘
                                          │ admin retire
                                          ▼
                                      ┌───────┐
                                      │RETIRED│  (terminal)
                                      └───────┘
```

- **NEW** — just imported; eligible for first lease so the state machine
  has a chance to get to ACTIVE on success.
- **ACTIVE** — eligible for leases.
- **COOLING** — removed from the lease pool for 30 min after an auth /
  block failure. Dashboard shows the countdown.
- **QUARANTINED** — 3 consecutive non-OK failures. Admin has to
  re-activate or retire.
- **RETIRED** — permanent. Cookies remain encrypted on disk (audit trail)
  but the row will never be leased again.

`RATE_LIMITED` is deliberately **not** a state transition — it's a signal
to back off, not evidence the account is burned.

## REST surface

All cookie-gated:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/account-pool/import` | register a new account (body = AccountImport) |
| `POST` | `/api/account-pool/lease` | request account + paired proxy lease |
| `POST` | `/api/account-pool/release` | report outcome + release paired proxy |
| `GET`  | `/api/account-pool/state` | dashboard view — NEVER includes cookies |
| `POST` | `/api/account-pool/admin/{id}/quarantine` | take out of rotation |
| `POST` | `/api/account-pool/admin/{id}/activate` | re-enable |
| `POST` | `/api/account-pool/admin/{id}/retire` | terminal: never leased again |

## Rate budgets

Defaults are conservative — both can be tightened later via code if
specific providers get stricter:

| Budget | Default | Purpose |
|---|---|---|
| per minute | 50 req | short-burst ceiling |
| per hour | 500 req | long-term sustained rate |

On lease, both counters increment atomically in Redis with TTL. If either
is exceeded, the lease returns 503 and the caller must pick a different
account or retry later. Worker code should back off on 503.

## Security notes

- Cookies are **Fernet-encrypted** at rest. Loss of `OPS_ACCOUNT_POOL_KEY`
  = loss of every account's cookies; store it in a password manager /
  keychain, not in the repo.
- A CI tripwire (`test_account_no_log_leak.py`) asserts that the cookie
  name, value, and user-agent never appear in log output. If you add a
  new log line that references a cookie, the test will fail.
- The dashboard's `/api/account-pool/state` endpoint NEVER includes
  cookies — only a 40-char user-agent preview.
- Raw JSON files: always delete after import. The pool is the canonical
  copy.

## Monitoring

The Accounts panel in the dashboard shows:

- per-source / per-state counts as badges
- each account's pinned proxy (first 8 hex chars of id), user-agent preview
- live minute + hour budget bars (green → yellow → red)
- last successful lease + last failure (relative time + reason)
- admin buttons: quarantine / activate / retire

Canonical Prometheus metrics:

- `account_state{account_id, source, state}` — 1/0 gauge per state
- `account_rate_budget_remaining{account_id, source}` — live per-minute
  remaining budget

Both are populated on every lease / release / admin change.
