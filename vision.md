# Vision: Becoming a Top Miner on Subnet 13 (Data Universe)

> Goal: sustainable, low-cost, high-credibility mining on SN13 with a long-running scraping pipeline that avoids paid scraper APIs (Apify) as much as possible, and a single-pane dashboard to see everything.

This document is deliberately opinionated. It starts from how the subnet actually scores miners (what we observed in the code), then derives the minimum pipeline that maximizes emissions per dollar.

---

## 1. How SN13 actually scores you (the parts that matter)

From [rewards/miner_scorer.py](rewards/miner_scorer.py), [rewards/data_value_calculator.py](rewards/data_value_calculator.py), [dynamic_desirability/desirability_retrieval.py](dynamic_desirability/desirability_retrieval.py):

### 1.1 Three parallel reward streams
Final per-miner emission weight is a sum:

```
final_score = P2P_component  +  min(S3_component, 2 * OD_component)  +  OD_component
```

- **P2P** = classic index scoring, scaled by `P2P_REWARD_SCALE = 0.05` and by `credibility ^ 2.5`. With the 0.05 scale this is now the *smallest* piece for most miners.
- **S3** = competition for effective bytes uploaded to S3; scaled by `s3_credibility ^ 2.5`. **Hard-capped at 2× OD component** — so if you do no on-demand, S3 earns you nothing.
- **OD** (On-Demand) = per-job rewards keyed off response latency × volume returned; scaled linearly by `ondemand_credibility` (no exponent). This is the fastest lever and it unlocks the S3 cap.

**Implication #1:** You cannot skip On-Demand. Without OD, S3 is zero, and you are left with only the 0.05-scaled P2P stream. Top miners run a low-latency OD responder.

### 1.2 Raw value of a data entity
[rewards/data_value_calculator.py](rewards/data_value_calculator.py):

```
raw_score = data_source_weight  ×  job_weight  ×  time_scalar  ×  effective_scorable_bytes
```

- `data_source_weight`: Reddit **0.65**, X **0.35**, YouTube **0.00** (removed 2026-01-08 in commit [`63b31ea`](https://github.com/macrocosm-os/data-universe/commit/63b31ea); the `YOUTUBE` enum became `UNKNOWN_3` with zero weight. `docs/scoring.md` still quotes the old 10% figure — code is authoritative)
- `job_weight`: 1.0 – 5.0 if the label matches an active Gravity/DD job, else **0.0001** (the `DEFAULT_SCALE_FACTOR` in [dynamic_desirability/constants.py](dynamic_desirability/constants.py)). So off-list data is worth 10,000× less than on-list data.
- `time_scalar = max(0, 1.0 − age_hours / 1440)` → 1.0 today, 0.5 at 30d, 0 at 60d (linear).
- `effective_scorable_bytes` — duplication penalty applies here: bytes get divided across the miners holding the same DataEntity.

**Implication #2:** Scraping labels that are NOT on the Dynamic Desirability list is a waste of storage. Stick to DD/Gravity labels + add aggressively-fresh long-tail labels that have low duplication.

### 1.3 Credibility — the compounding force
[rewards/miner_scorer.py](rewards/miner_scorer.py) lines ~32–57:

```
new_cred = α * validation_success_rate  +  (1 − α) * old_cred
```

- P2P: start 0.0, α=0.15, multiplier `cred^2.5` → takes ~30 clean validations to reach 0.9. A single dishonest bucket tanks you for weeks.
- S3: start 0.375, α=0.30, multiplier `cred^2.5`.
- OD: start 0.5, α=0.02 upward, **−5% flat per bad submission**. Bad OD data is catastrophic.

**Implication #3:** Never return data you have not validated against the live source with the *same* scraper the validator will use. If the validator calls Apify and your row disagrees, you lose. Data correctness > volume.

### 1.4 What "on-list labels" means right now
[dynamic_desirability/default.json](dynamic_desirability/default.json) (default baseline — real weights come from validator-aggregated Gravity jobs at runtime):

- **Reddit (weight 1.0)**: r/Bitcoin, r/BitcoinCash, r/Bittensor_, r/Btc, r/Cryptocurrency, r/Cryptomarkets, r/politics, r/worldnews
- **Reddit (lower)**: r/worldpolitics (0.8), r/WallstreetBets (0.7)
- **X (1.0)**: #bitcoin, #bitcoiner, #bitcoinnews, #btc, #crypto, #defi, #decentralizedfinance, #tao, #Israel, #Ukraine, #Trump, #Harris, #macrocosmos, #MacrocosmosAI

The **live** list is fetched via `DataUniverseApiClient.validator_get_latest_dd_list()` ([dynamic_desirability/desirability_retrieval.py:443](dynamic_desirability/desirability_retrieval.py#L443)). We must poll this and adapt.

---

## 2. The cost problem: X/Twitter

Every shipped X scraper in this repo ([apidojo_scraper.py](scraping/x/apidojo_scraper.py), [microworlds_scraper.py](scraping/x/microworlds_scraper.py), [quacker_url_scraper.py](scraping/x/quacker_url_scraper.py)) goes through **Apify actors**. X is 35% of the reward pie plus the entirety of most Gravity jobs → we *must* do X, but Apify-at-scale is economic suicide for a solo miner running 24/7.

**Worse:** validators use Apify's ApiDojo actor to verify our data ([apidojo_scraper.py:33-214](scraping/x/apidojo_scraper.py)). So whatever we store has to match *ApiDojo's* output shape and field values. That is the constraint we design around.

### 2.1 Options, honestly evaluated

| Path | Unit cost | Maintenance | Validation risk | Verdict |
|---|---|---|---|---|
| **A. Apify apidojo directly** | $$$$ (~$0.25 per 1k tweets) | zero | none | Use only as a **validation oracle**, not for bulk ingest |
| **B. Self-hosted twscrape / snscrape + account pool** | ~$0 + proxy/account cost | high (X keeps breaking this) | medium — shape must be mapped to XContent exactly | **Primary bulk ingester** |
| **C. Playwright + residential proxies + cookie accounts** | proxy $ | very high | low if we parse rendered DOM carefully | Fallback for accounts banned by B |
| **D. Nitter mirrors** | $0 | unreliable (most mirrors dead 2024+) | high | Don't bet on it; optional enrichment only |
| **E. Public free tier X API v2** | $0 → 500 posts/mo | low | none | Useful only for OD spot-correctness checks |

**Strategy:** bulk ingest via **(B)** into our store, and spot-verify every ~1% of rows with Apify **(A)** to make sure our parsing agrees with what validators will see. Keep a small **(A)** budget for on-demand when a job needs richer metadata (follower counts, verified badge) that (B) cannot produce cleanly.

### 2.2 Reddit is cheap or free (and now 65% of the pie)

- **Reddit** — two free paths: `Reddit.json` (public .json endpoints, no auth) and `Reddit.custom` (PRAW with a personal dev app, ~60 req/min/account). Rotate across **N** PRAW apps keyed to **N** throwaway accounts to get N × 60/min effective throughput.
- **YouTube is out** — removed validator-side on 2026-01-08 ([`63b31ea`](https://github.com/macrocosm-os/data-universe/commit/63b31ea)). The `YOUTUBE` enum is now `UNKNOWN_3` with weight 0, the validator has no YouTube scraper factory, and S3 validation has no YouTube schema. Any YouTube data we upload contributes zero to every reward stream. When YouTube went to 0%, the 10% was redistributed into Reddit (55 → 65) — so Reddit is now a **bigger** lever than before.

---

## 3. Target architecture

### 3.1 One-page diagram

```
                                  ┌────────────────────────────────────────────┐
                                  │         CONTROL / INTELLIGENCE             │
                                  │                                            │
                                  │  • Gravity-DD watcher (pulls live job list)│
                                  │  • Uniqueness oracle (SN13 dashboard API)  │
                                  │  • Label strategist  ─── emits label plan ─┼──► scraping_config.json (rewritten hourly)
                                  │  • Budget guard (Apify $ / proxies / RPS)  │
                                  └──────────────▲──────────────┬──────────────┘
                                                 │ metrics      │ plan
                                                 │              ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SCRAPER FLEET                                          │
│                                                                                           │
│   ┌──────────── REDDIT pool ───────────┐  ┌──────────── X/TWITTER pool ─────────────┐     │
│   │ N × PRAW workers (account pool)    │  │ twscrape workers (account + proxy pool) │     │
│   │ + public .json fallback            │  │ Playwright fallback workers             │     │
│   │ behind rotating residential proxy  │  │ Apify client (on-demand, budget-gated)  │     │
│   └─────────────┬──────────────────────┘  └────────────────────┬────────────────────┘     │
│                 │                                               │                          │
│   ┌──────────── YOUTUBE pool ──────────┐                        │                          │
│   │ yt-dlp metadata + transcript-api   │                        │                          │
│   │ via proxy rotation                 │                        │                          │
│   └─────────────┬──────────────────────┘                        │                          │
│                 │                                               │                          │
│                 ▼                                               ▼                          │
│     ┌──────────────────────────────┐              ┌────────────────────────────┐           │
│     │  Normalizer (→ RedditContent │              │ Normalizer (→ XContent     │           │
│     │  model)                      │              │ that matches ApiDojo shape)│           │
│     └──────────────┬───────────────┘              └──────────────┬─────────────┘           │
│                    │                                             │                         │
│                    ▼                                             ▼                         │
│            ┌──────────────────────────────────────────────────────────┐                    │
│            │   Self-validation shim: randomly re-fetches 0.5–2% of     │                    │
│            │   rows through the VALIDATOR'S scraper (Apify apidojo,    │                    │
│            │   PRAW) and drops rows whose fields diverge.              │                    │
│            └────────────────────────────┬─────────────────────────────┘                    │
└─────────────────────────────────────────┼──────────────────────────────────────────────────┘
                                          │
                                          ▼
                              ┌───────────────────────────────┐
                              │   Dedup + quality gate        │
                              │  (content hash, URI unique,   │
                              │   schema match, NSFW/GDPR)    │
                              └────────────────┬──────────────┘
                                               │
                                               ▼
            ┌─────────────────────────────────────────────────────────────────┐
            │          LOCAL STORE  ─ SqliteMinerStorage (up to 250 GB)       │
            │          partitioned rollups → Parquet  (for S3)                │
            └─────────────────┬─────────────────────────────────────┬─────────┘
                              │                                     │
                              │ miner protocol                      │ 2-hr S3 uploader
                              ▼                                     ▼
                  ┌──────────────────────┐                ┌────────────────────┐
                  │   Neurons (miner)    │                │ S3 (presigned URL, │
                  │  • GetMinerIndex     │                │  hotkey-signed)    │
                  │  • GetDataEntityBucket│                └────────────────────┘
                  │  • OnDemandRequest   │
                  └──────────┬───────────┘
                             │ OD job
                             ▼
                  ┌──────────────────────┐
                  │ OD Fast Lane:         │
                  │  - query local DB    │
                  │  - fill gap via live │
                  │    scrape (same      │
                  │    fleet, priority)  │
                  │  - target <10 s p95  │
                  └──────────────────────┘

                ┌─────────────────────────── OBSERVABILITY ──────────────────────────┐
                │  Prometheus (scraper RPS, success %, proxy health, account bans,   │
                │    dedup rate, OD latency, self-validation pass %)                 │
                │  + validator-side metrics poller (on-chain weights, credibility,   │
                │    our share by DD job, dashboard uniqueness snapshots)            │
                │  → Grafana: one board "Miner Control Room" with SLOs per source   │
                │  → Loki logs; Alertmanager → Telegram/Discord                      │
                └────────────────────────────────────────────────────────────────────┘
```

### 3.2 Why each box exists

1. **Gravity-DD watcher** → polls `DataUniverseApiClient.validator_get_latest_dd_list()` every 5 min. Off-list data is 1/10,000th value, so the whole pipeline steers from this.
2. **Uniqueness oracle** → scrapes the public SN13 dashboard for current per-bucket duplication counts. We down-weight labels that are already saturated (duplication penalty eats into `effective_scorable_bytes`).
3. **Label strategist** → once per hour rewrites `scraping/config/scraping_config.json` (or a sibling file passed via `--neuron.scraping_config_file`) with the new plan: `job_weight × (1 / current_duplication) × time_budget`. Favour DD-listed labels with low duplication and high freshness.
4. **Account + proxy pools** → the real reason this is cheap. Plan a budget of ~20 residential proxy IPs and ~10 Reddit apps, ~10 X accounts to start. Rotate round-robin with per-account cooldowns; quarantine accounts on 429/401.
5. **Normalizer** → the **critical correctness layer**. Our XContent rows must match what `apidojo_scraper.validate()` expects: same URI shape, tweet text, like/retweet/reply/quote counts (within tolerance), `is_retweet/is_reply/is_quote` flags, user `verified`, follower count, media URLs. Every divergence is a validation failure and tanks credibility.
6. **Self-validation shim** → cheap insurance. Pull a random 1% sample hourly, re-scrape via the validator's scraper (Apify apidojo / PRAW), diff fields, drop diverging rows *before* validators see them. At 1% sampling the Apify bill is trivial and credibility stays pegged near 1.0.
7. **Dedup + quality gate** → SHA over canonical URI + normalized content; enforce GDPR / NSFW / prohibited-content filters from [docs/miner_policy.md](docs/miner_policy.md).
8. **OD Fast Lane** → the biggest reward lever. `handle_on_demand` in [neurons/miner.py](neurons/miner.py) must: (a) immediately serve from local DB what it has, (b) dispatch a *priority* scrape for missing slices via the same fleet, (c) return within ~10 s p95 to max out the linear speed multiplier in [rewards/miner_scorer.py:445](rewards/miner_scorer.py#L445) (0.1→1.0 linearly across 0–120 s).
9. **S3 uploader** (existing) → already wired via the 2-hourly scheduler in [neurons/miner.py:254](neurons/miner.py#L254); just don't break filename pattern `data_YYYYMMDD_HHMMSS_count_16hex.parquet` — [docs/s3_validation.md](docs/s3_validation.md) treats filename count mismatch as hard-fail after 2025-12-02.

### 3.3 Concurrency & rate budget (starter sizing)

| Source | Workers | Accounts/keys | Proxies | Target RPS | Monthly cost |
|---|---|---|---|---|---|
| Reddit PRAW | 12 | 12 (free dev apps) | 12 residential | ~10 | ~$50 proxies |
| Reddit .json | 6 | 0 | 6 shared IPs | ~5 | $0 |
| X Playwright (primary per M5+) | 8 | 8 (aged) | 20 residential | ~6 | ~$80 proxies + one-time account purchase |
| X Apify (validation + OD gap) | n/a | 1 token | n/a | <0.05 | ~$30/mo capped |
| **Total** | **~26** | | **~38 IPs** | | **~$160/mo** |

At ~180k Reddit + ~80k X rows/day this is enough raw volume; the *quality* work is done by the strategist and the self-validation shim, not by more RPS. (Previous plan included a YouTube tier; see §2.2 — validator killed YouTube scoring 2026-01-08, so the capacity that used to go there gets redirected to Reddit PRAW + X Playwright.)

---

## 4. The dashboard ("Miner Control Room")

Single Grafana board, five rows, one goal: know within 60 s whether we are losing rank and why.

**Row 1 — Emissions & rank.** On-chain weight, incentive, rank; credibility for P2P / S3 / OD; recent trueskill vs top 10. Source: validator metagraph poller (poll subtensor every 60 s from a small `metrics_exporter.py` service).

**Row 2 — Pipeline throughput.** Rows scraped / stored / uploaded per source per hour; dedup rate; normalizer error rate; self-validation pass %. Source: Prometheus counters in the scraper fleet.

**Row 3 — Fleet health.** Proxy success %, account quarantine count, 429/401/403 per source, queue backlog, worker uptime. Without this you discover you are banned hours too late.

**Row 4 — Scoring.** Per-DD-job coverage: how many rows we have on each active Gravity job in the last 24 h vs the network median (pulled from the SN13 dashboard API). Freshness histogram of our index. Duplication factor per top-10 bucket.

**Row 5 — On-Demand.** Jobs received, response p50 / p95, success rate, OD credibility trend, rewards earned per job. This is the row that moves money fastest.

Alert rules (Alertmanager → Discord):
- OD p95 latency > 15 s for 10 min
- Self-validation pass rate < 95 % over last 30 min
- Any scraper source success rate < 80 %
- Credibility delta < 0 over last 6 h
- Storage > 230 GB

---

## 5. Anti-block playbook (the "long run" part)

1. **Proxy hygiene.** Residential > datacenter for Reddit and X. One hot IP burns a whole account. Sticky sessions per account, 15-min rotation otherwise.
2. **Account hygiene.** X accounts need a realistic browsing history before scraping; age them a week in Playwright before putting them to work. Disposable Reddit dev apps can be created fresh at will — do it.
3. **Soft-fingerprint.** twscrape's TLS/JA3 is a known signal; prefer `curl_cffi` + chrome JA3 impersonation where possible. Playwright via `playwright-stealth`.
4. **Exponential back-off with jitter per-account**, and a global 429 breaker per source that pauses new jobs for 2 minutes.
5. **Respect `robots.txt`/ToS** only to the extent required by [docs/miner_policy.md](docs/miner_policy.md) (GDPR, no CSAM, no copyrighted bulk). This is both legal duty and reputational.
6. **Observe the ban signal, not the HTTP code.** A 200 with an empty JSON is also a soft ban. Track empty-result-rate as a first-class metric.
7. **Never amend retroactively.** A blocked row that later surfaces *different* content is a credibility bomb. Let self-validation evict it.

---

## 6. Rollout plan (eight focused weeks)

| Week | Deliverable | Exit criterion |
|---|---|---|
| 1 | Fork repo; stand up base miner with default config on testnet | responds to validator on testnet |
| 1 | Grafana/Prometheus exporter skeleton | Row 1 + Row 2 populated |
| 2 | Reddit PRAW pool + proxy rotation + normalizer | ≥50 k Reddit rows/day, self-val pass ≥ 98 % |
| 3 | X twscrape pool + ApiDojo-shape normalizer | ≥30 k X rows/day, self-val pass ≥ 95 % |
| 3 | Self-validation shim (1 % Apify resample) | credibility stable on mainnet after 72 h |
| 4 | Gravity-DD watcher + Label strategist (auto-rewrites config) | on-list coverage ≥ 80 % |
| 5 | Uniqueness oracle (dashboard scrape) + dedup-aware label plan | duplication factor in our top-10 buckets below network median |
| 6 | **OD Fast Lane**: priority queue, local-first, gap-fill scraping | OD p95 < 10 s, OD credibility > 0.8 |
| 7 | Playwright hardening: fingerprint-per-account, stealth layer, HAR triage | 0 % accounts burned in a 24 h run |
| 8 | Dashboard Rows 3–5 + alerts; runbook; cost + margin report | Discord alerts firing on injected faults, unit cost / 1 k rows tracked |

---

## 7. What we deliberately are NOT doing

- **Not** scraping off-list labels in volume. The 0.0001 multiplier makes it unprofitable even if storage is "free".
- **Not** mass-paying for Apify. One token, small budget, only for validation sampling + OD last-mile enrichment.
- **Not** sharing accounts/proxies across sources. A ban on Reddit shouldn't taint X.
- **Not** storing > 30-day-old data unless a Gravity job has an explicit date range reaching back further. After 30 d `time_scalar = 0.5` and keeps falling; it burns disk for almost no reward.
- **Not** editing the on-chain protocol or scoring — we adapt, we don't fight the rules.

---

## 8. Key files to touch (starting points)

- [scraping/config/scraping_config.json](scraping/config/scraping_config.json) — rewritten hourly by the strategist; `YouTube.custom.transcript` entry is dead weight (remove on next touch).
- [scraping/coordinator.py](scraping/coordinator.py) — may need a higher worker count and a priority queue hook for OD.
- [scraping/x/](scraping/x/) — we land our X scraper as a Playwright plugin (ops/worker/plugins/x.py, M6) returning `XContent` matching `apidojo_scraper`'s shape; no edits here unless we need a reference.
- [scraping/reddit/](scraping/reddit/) — M7 account-pool wrapper around `reddit_custom_scraper.py` + a public-JSON fallback plugin.
- [neurons/miner.py](neurons/miner.py) — `handle_on_demand` becomes the OD Fast Lane (M12); add Prometheus metrics.
- The `ops/` directory is already scaffolded through M5. M6 adds `ops/worker/plugins/x.py`; M7 adds `ops/worker/plugins/reddit.py`.

---

## 9. One-paragraph TL;DR for the team

The subnet pays us in two remaining streams by source — **Reddit (65%) and X (35%)** — and in three reward streams (P2P / S3 / OD), where OD unlocks the S3 cap. **YouTube is out** as of 2026-01-08, so we build for two sources only. Our pipeline is designed around returning correct on-demand data in under ten seconds, every time. Bulk ingestion is free-tier wherever possible (PRAW + Reddit JSON for the 65% slice, Playwright + cookie accounts for X), fronted by a residential-proxy + account-pool rotation and a 1-2% Apify self-validation shim that guarantees our rows match what validators will see (upstream hardened S3 validation on 2026-04-14; `STARTING_S3_CREDIBILITY` dropped 0.375 → 0.1, so strictness is non-negotiable). A Gravity-aware label strategist rewrites the scraping config hourly so we only spend effort on data the network actually wants, avoiding the 0.0001× penalty on off-list labels. Everything is observed through a single web "Miner Control Room" dashboard (no Prometheus/Grafana stack — it's all in FastAPI + Vite) with alerts on credibility drift, OD latency, and fleet health. Target: top-10 rank within 8 weeks on ~$160/month of proxy+Apify spend.
