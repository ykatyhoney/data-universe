# Data pipeline (M2.5)

> Authoritative diagram + invariants. Every scraper / normaliser / validator
> the milestones add downstream MUST conform to this layout. Renaming any
> stream / table / state value is a coordinated change, not a local one.

## Flow

```
            ┌────────────────┐                                ┌─────────────────┐
            │ strategist /   │  ScrapeTaskEnvelope            │   miner protocol│
            │ OD fast lane   │ ─────────────────────────────► │   handler       │
            │ (M12, M13)     │                                │                 │
            └────────────────┘                                └────────┬────────┘
                       │                                               │
                       ▼                                               ▼
              ┌──────────────────┐                          ┌──────────────────┐
              │ Redis Stream     │                          │ Redis Stream     │
              │  scrape:tasks    │                          │ ondemand:requests│
              └────────┬─────────┘                          └────────┬─────────┘
                       │ XREADGROUP                                   │ XREADGROUP
                       ▼                                              ▼
              ┌──────────────────┐                          ┌──────────────────┐
              │ Worker fleet     │                          │ OD fast lane     │
              │ (M5+)            │                          │ (M12)            │
              └────────┬─────────┘                          └──────────────────┘
                       │  ScrapeResultEnvelope
                       ▼
              ┌──────────────────┐
              │ Redis Stream     │
              │ scrape:results   │
              └────────┬─────────┘
                       │ XREADGROUP   group=normalizer
                       ▼
              ┌──────────────────────────────────────────────┐
              │ PipelineOrchestrator.ingest_loop             │
              │   1. INSERT stg_raw_items  (audit trail)     │
              │   2. normalize → NormalizedItem              │
              │   3. dedup_index.reserve()  ON CONFLICT NOOP │
              │   4. INSERT stg_normalized_items state=pending│
              │   5. publish ValidationEnvelope              │
              └────────┬─────────────────────────────────────┘
                       │  ValidationEnvelope
                       ▼
              ┌──────────────────┐
              │ Redis Stream     │
              │ validation:queue │
              └────────┬─────────┘
                       │ XREADGROUP   group=self_validator
                       ▼
              ┌──────────────────────────────────────────────┐
              │ PipelineOrchestrator.validate_loop           │
              │   1. SelfValidator.validate(envelope)        │
              │   2. INSERT stg_validation_results           │
              │   3. on fail → mark normalized=quarantined   │
              │      on pass → leave state=pending           │
              └────────┬─────────────────────────────────────┘
                       │
                       ▼
              ┌──────────────────────────────────────────────┐
              │ PipelineOrchestrator.promote_loop            │
              │   BridgePromoter.promote_once(): claim batch │
              │   → build DataEntity → store_data_entities   │
              │   → INSERT stg_promotion_log                 │
              │   → mark normalized=promoted                 │
              └────────┬─────────────────────────────────────┘
                       │  asyncio.to_thread(...)
                       ▼
              ┌──────────────────────────────────────────────┐
              │   storage/miner.sqlite  (SqliteMinerStorage) │
              │     existing SN13 code, untouched            │
              └────────┬─────────────────────────────────────┘
                       │  2-hr Parquet rollup (existing)
                       ▼
                  S3 presigned uploads
```

## Invariants

1. **No scraper writes to `SqliteMinerStorage` directly.** Everything transits
   `stg_raw_items` + `stg_normalized_items` first. The bridge promoter is the
   *sole* writer past staging.
2. **Dedup is the single gate.** `stg_dedup_index.canonical_uri` is the
   authority for "already stored?" — even the OD fast lane consults it before
   scraping. First writer wins.
3. **Audit before normalize.** `stg_raw_items` insert happens *before*
   normalization so we can replay against an updated parser later.
4. **Validation passes → row stays `pending`.** The promoter claims `pending`
   rows; this means the validation-pass path doesn't have to coordinate with
   the promoter via state. Validation failures flip directly to `quarantined`.
5. **At-least-once on every stream.** `consume()` never auto-acks — caller
   ACKs only after durable persist. Crashes mid-batch resurface the message
   on the next consumer start (replay from PEL with id=`0`).
6. **Cardinality of canonical_uri is bounded.** Tracking params + slug drift
   are stripped in `storage.dedup.canonical_uri`. Two URLs that point at the
   same upstream post collapse to the same canonical form.

## Tables (SQLite, no schema namespace)

| Table | Retention | Owner | Notes |
|---|---|---|---|
| `stg_raw_items` | 7 d | orchestrator.ingest | audit trail; `raw_json` blob |
| `stg_normalized_items` | 7 d (terminal states only) | orchestrator | state machine (`pending`/`validating`/`promoted`/`quarantined`/`dropped`) |
| `stg_dedup_index` | 35 d | orchestrator.ingest | global URI uniqueness; outlives normalized |
| `stg_validation_results` | 30 d | orchestrator.validate | one row per sample check |
| `stg_promotion_log` | 30 d | orchestrator.promote | bridge audit trail |

`pending` and `validating` are NEVER auto-pruned — they represent in-flight
work. A backlog there means the orchestrator stalled; check the dashboard.

## Streams

| Stream | Producer | Consumer group | MAXLEN | Retention |
|---|---|---|---|---|
| `scrape:tasks` | strategist (M13), OD fast lane (M12) | `workers` | 100k | tail-trim |
| `scrape:results` | workers (M5+) | `normalizer` | 50k | tail-trim |
| `validation:queue` | orchestrator.ingest | `self_validator` | 10k | tail-trim |
| `ondemand:requests` | miner protocol handler (M12) | `od_fast_lane` | 5k | tail-trim |

Approximate `MAXLEN` trims (XADD ... MAXLEN ~) so a stalled consumer can't
fill Redis. Combined with at-least-once delivery this means a long stall +
resume can lose old, never-consumed events; for our pipeline that's fine
(strategist re-issues tasks on its next tick).

## Failure modes & recovery

| Failure | Containment |
|---|---|
| Normalizer raises | Row never stages; raw audit row remains; ACK so no retry storm. |
| Canonicalization rejects URI | Same as above; `pipeline.dedup_canonicalize_failed` warning. |
| Self-validator fails (exception) | NACK (no ack); message replays on next consumer tick. |
| Validator says `passed=False` | normalized row → `quarantined`; promoter never claims it. |
| Bridge `store_data_entities` raises | normalized rows flipped back to `pending`; next promote tick retries. |
| `normalized_json` missing `content` | Row → `quarantined`; logged with row id for inspection. |
| Redis unreachable | WS hub + streams reconnect with backoff; orchestrator stalls cleanly until Redis returns. |

## Where things live in code

- `ops/datastore/models.py`             — ORM models (`Stg*`).
- `ops/datastore/migrations/versions/0002_staging_tables.py` — Alembic migration.
- `ops/datastore/dedup.py`              — canonical URI + content hash.
- `ops/datastore/streams.py`            — Redis Streams helpers (publish/consume/ack).
- `ops/datastore/repositories.py`       — staging repos.
- `ops/datastore/sqlite_adapter.py`     — bridge into existing `SqliteMinerStorage`.
- `ops/shared/pipeline.py`            — typed envelopes (`StreamName`, `ConsumerGroup`).
- `ops/normalizer/base.py`            — `Normalizer` protocol + `PassthroughNormalizer` (M9 fills in real ones).
- `ops/self_validator/base.py`        — `SelfValidator` protocol + `AlwaysPassValidator` (M10 fills in the real shim).
- `ops/pipeline/orchestrator.py`      — schedules ingest / validate / promote / metrics loops.
- `ops/dashboard/api/retention.py`    — hourly sweep across all `stg_*` tables.

## Adding a new source

1. Add a per-source canonicalisation in [datastore/dedup.py](../ops/datastore/dedup.py)
   under `_SOURCE_DISPATCH`.
2. Implement `Normalizer` for that source (in `ops/normalizer/<source>.py`)
   that produces a validator-parity content blob.
3. Register the normalizer when constructing `PipelineOrchestrator`:
   ```python
   PipelineOrchestrator(normalizers={Source.X.value: XNormalizer(), ...})
   ```
4. The rest (staging, dedup, validation queueing, promotion) is automatic.
