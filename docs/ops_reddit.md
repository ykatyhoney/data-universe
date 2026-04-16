# Reddit scraping — operational guide (M7)

This doc covers the Reddit pipeline specific to subnet 13: how to import
accounts, what the two scraping paths do, how validator parity is enforced,
and how to operate it.

## Source weights (as of 2026-01-08)

| Source | Validator weight | Notes |
|---|---|---|
| Reddit | **0.65** | Primary target — biggest reward pie |
| X / Twitter | 0.35 | Still meaningful; handled in M6 |
| YouTube | 0.00 | Cancelled — validator dropped YT support |

Reddit is the largest single contributor to mining reward. Keeping the
Reddit pipeline green is the highest-leverage ops priority.

## Two scraping paths

Both paths land in a single Reddit scraper plugin
([ops/worker/plugins/reddit/praw_scraper.py](../ops/worker/plugins/reddit/praw_scraper.py))
registered for `Source.REDDIT`. The plugin picks a path per task:

1. **PRAW (primary)** — `asyncpraw.Reddit` with OAuth credentials from the
   account pool. Rate-limited per OAuth client (60 req/min), not per IP,
   so no proxy is strictly needed. Returns fully-hydrated submission /
   comment objects; NSFW, media, score, and `num_comments` come directly
   from Reddit's API.

2. **JSON fallback** — public `.json`-suffixed endpoints behind a proxy
   pool lease. Rate-limited per IP. The JSON shape differs slightly from
   PRAW objects; [`json_scraper._wrap_post`](../ops/worker/plugins/reddit/json_scraper.py)
   adapts it so the shared [`parse.py`](../ops/worker/plugins/reddit/parse.py)
   helpers produce identical output either way.

Path selection:

```
ctx.credentials → PRAW
else ctx.proxy   → JSON
else             → empty (task OK/empty, not ERROR)
```

## Supported task modes

| Mode | `label` format | PRAW | JSON |
|---|---|:-:|:-:|
| `SEARCH` | `r/<sub>` | ✅ | ✅ |
| `PERMALINK` | full reddit URL | ✅ | ✅ (post + top-level comments) |
| `PROFILE` | `<username>` (or `u/<username>`) | ✅ | — |

`PROFILE` on the JSON path would need per-user listing endpoints; skipped
because the validator's sampling focuses on `SEARCH` / `PERMALINK`
coverage.

## Validator parity

The normalizer produces a byte-exact
[`RedditContent`](../scraping/reddit/model.py) blob, including the
`createdAt`/`scrapedAt` minute obfuscation, the exact `https://www.reddit.com/...`
URL format, and the `communityName` casing the validator expects. A
[parity test](../ops/tests/test_reddit_normalizer_parity.py) imports the
real SN13 model and pins the invariant:

```python
from scraping.reddit.utils import validate_reddit_content
# our bytes → parse_raw → validate → is_valid=True
```

It is gated on `bittensor` + `torch` imports so CI stays lean. Run locally
with SN13 requirements installed.

NSFW-with-media combinations are filtered at normalize time — they would
fail `validate_nsfw_content` on the validator side regardless, so we drop
them here rather than staging data that can never promote.

## Importing Reddit accounts

Two shapes are accepted (both behind one `AccountImport` schema — see
[account_pool/schemas.py](../ops/account_pool/schemas.py)):

- **OAuth installed app (preferred)**
  ```json
  {
    "source": "reddit",
    "user_agent": "your-ua/1.0",
    "credentials": {
      "client_id": "abc",
      "client_secret": "def",
      "refresh_token": "ghi"
    },
    "notes": "PRAW OAuth"
  }
  ```

- **Script app (legacy)**
  ```json
  {
    "source": "reddit",
    "user_agent": "your-ua/1.0",
    "credentials": {
      "client_id": "abc",
      "client_secret": "def",
      "username": "redditor",
      "password": "hunter2"
    },
    "notes": "PRAW script"
  }
  ```

Import via:

```bash
python -m account_pool.import_cli reddit_account.json
```

The `notes` field with `"PRAW"` in it is what the dashboard's RedditPanel
counts for its `with_praw_credentials` indicator. That counter drives the
"primary path down — falling back to JSON" warning banner when it hits 0.

## Dashboard

The [RedditPanel](../ops/dashboard/web/src/components/panels/RedditPanel.tsx)
reads `GET /api/reddit/overview` and shows:

- Per-subreddit rollup: total items, promoted, quarantined, last seen.
  Sorted by volume.
- Account health badges: active / cooling / quarantined / PRAW-capable.
- Warning banner when PRAW-capable accounts drop to 0 (JSON fallback only).

## Outcome mapping (account + proxy release)

The worker runtime's [`_account_outcome_from_exception`](../ops/worker/runtime.py)
maps Reddit errors onto the account-pool state machine by keyword:

| Exception surface | Account outcome | Proxy outcome |
|---|---|---|
| `401` / `403` / `auth_failed` | `AUTH_FAILED` → cooling → quarantine after 3 | ok (IP is fine) |
| `429` / `rate_limited` | `RATE_LIMITED` (no state change) | ok |
| captcha / block | `BLOCKED` → cooling | blocked |
| timeout | `TIMEOUT` | timeout |

Both paths raise exceptions whose class name / message carry the relevant
keyword (`RedditJSONError("rate_limited (429)")` etc.), so the mapping
works for PRAW and JSON uniformly.

## Budget

Rate budgets live in Redis and default to `50/min` + `500/hr` per
account. With 10 PRAW accounts that's 500 req/min across the fleet — well
below Reddit's own caps and enough to keep the top 30 subreddits fresh
with sub-minute staleness.

## Debugging

- **HAR + screenshot**: failing tasks drop HAR files under
  [ops/worker/recording.py](../ops/worker/recording.py). The dashboard's
  Workers panel links to them from the current task row.
- **Parity failures**: run `pytest ops/tests/test_reddit_normalizer_parity.py`
  with the SN13 deps installed. The byte-exact test pinpoints which
  field diverged from `RedditContent.to_data_entity`.
- **429 bursts**: check `account_rate_budget_remaining` gauge per account
  — the dashboard panel's budget bars reflect the same value in realtime.
