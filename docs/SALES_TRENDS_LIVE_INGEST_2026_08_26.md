# sales_trends Live Ingestion Activation (2026-08-26)

Follow-up to the Gravity temporal pattern extension (Stage 1: PR #102, Stage 2: PR #103, Stage 3: PR #104). Those stages built the `sales_trends` domain contract, `MAX_ACTIVATION_HISTORY` calibration, and two periodicity detectors — all validated exclusively against isolated, throwaway indices. This activation (PR #105) is the first time `sales_trends` received a real, live event into the actual production Gravity index.

## Starting state

Verified directly against the running production instance before any change:
- `GET /v1/universe` (live API): 0 stars mentioning "sales" out of 41 gravitational stars.
- `~/.vectrax/gravity_index.json` (the real file): 0 of 1,121 fingerprints had `domain == "sales_trends"`.

## Finding: no domain-specific wiring was needed

`POST /v1/ingest/{domain}` (`services/core/routes/ingest_api.py`) is generic by design: it accepts any `domain` path segment, and `core.domain_ingester.ingest_event()` resolves the corresponding `config/domain_templates/{domain}.json` if one exists (falling back to a generic key=value formatter otherwise — see `event_to_text()`). Since `sales_trends.json` already existed (Stage 1), the endpoint could already accept real `sales_trends` traffic with zero code changes. The reason zero stars existed was purely that no tenant had ever sent an event — not a technical block.

## Bug found and fixed

`IngestEvent` (the request body model) declares an optional `timestamp: Optional[str]` field, but the route handler never forwarded it to `ingest_event()`:

```python path=null start=null
# before (bug): event.timestamp silently discarded
result = do_ingest(
    tenant_id=tenant.tenant_id, domain=domain,
    event_type=event.event_type, data=event.data,
)
```

This meant `event_timestamp` was always `None` over the real API — any historical or precise timestamp a tenant supplied was silently dropped, even though the underlying engine has fully supported `event_timestamp` since Stage 1 (effective-clock replay, UTC normalization, Déjà Vu promotion timing). Fixed with a one-line change:

```python path=null start=null
result = do_ingest(
    tenant_id=tenant.tenant_id, domain=domain,
    event_type=event.event_type, data=event.data,
    event_timestamp=event.timestamp,
)
```

## Tests

`tests/test_ingest_api.py` (7 tests, `fastapi.testclient.TestClient` against an isolated tenant DB + gravity index — never the real files):
- Domain-agnostic behavior: `sales_trends` and an unregistered domain both ingest successfully with no special-casing.
- Auth: invalid/missing API key rejected; a domain-locked tenant cannot post to a different domain.
- Regression guard: `timestamp` propagates to `first_seen`/`activation_history` when supplied; falls back to ingestion time when omitted.

## Live validation against real production

Restarted the local supervisor on the fix branch and validated against the actual running instance (`http://localhost:8900`), not a test:

1. Created a real tenant scoped to `domain=sales_trends` (`core.tenant.create_tenant`).
2. POSTed one clearly-labeled validation event to `POST /v1/ingest/sales_trends`:
   - `product=VALIDATION-SKU-001`, `category=validation`, `region=EU`, with an explicit historical `timestamp=2019-03-15T00:00:00+00:00`.
3. Verified directly in the real files:
   - `~/.vectrax/gravity_index.json`: 1,121 → **1,122** fingerprints. New record: `domain=sales_trends`, `first_seen=2019-03-15T00:00:00+00:00` (matches the supplied timestamp exactly, not the ingestion time — proof the fix works), `activation_history=["2019-03-15T00:00:00+00:00"]`.
   - `GET /v1/universe`: `total_stars` 2,323 → **2,324**, `star_breakdown.gravitational` 1,121 → **1,122**.

Per Gravity's Law 4 (no deletion — see `core/learn/gravity_engine.py`), this validation star now persists permanently in the real index. It is deliberately labeled (`VALIDATION-SKU-001` / `category=validation`) so it is never mistaken for real business data.

## What activating ingestion does NOT mean

This activates **ingestion only** — real `sales_trends` events now create real stars in production. It does **not** activate periodicity detection:
- `detect_periodicity()` and `detect_periodicity_detrended()` (`core/learn/temporal_pattern.py`) remain completely unwired from the ingest pipeline, exactly as they were left at the end of Stage 3.
- No scheduled job or fire-and-forget hook calls either detector automatically on new `sales_trends` activity.
- Running either detector over real accumulated production data (once enough real tenant traffic exists) remains a distinct, separate, not-yet-approved step.

## Full production backfill (same day, after live-ingestion validation)

After the single-star validation above confirmed the pipeline end-to-end, the full 2-year deduplicated dataset was loaded directly into the real production index via a new script, `scripts/backfill_sales_trends_PRODUCTION.py`.

**Critical difference from the Stage 3 dry-run script**: `scripts/calibrate_sales_trends.py::_BulkGravityIndex` (reused by the Stage 3 dry run) always starts its in-memory cache **empty** — correct only for a throwaway temp path. Pointed at the real production file, flushing an empty-seeded cache would have silently **discarded every pre-existing star in the system** (every domain, every user). `_BulkGravityIndexSeeded` fixes this by loading whatever already exists on disk into the cache once, before any new event is applied, so pre-existing data is preserved and only new/changed fingerprints are added.

**Safety sequence executed:**
1. Dry-checked the script (no `--confirm`) to confirm it correctly read the real pre-existing state (1,142 fingerprints: 1,141 non-`sales_trends` + the 1 validation star) without writing anything.
2. Stopped the live supervisor (`launchctl bootout`) to eliminate any risk of a concurrent writer racing with this script's single end-of-run flush.
3. Ran with `--confirm`: wrote a timestamped backup of the production file first (`gravity_index.json.backup_pre_sales_trends_<UTC timestamp>`, 1,357.4 KB), reused the exact Stage 3 `deduplicate_overlap()` (22,130 exact-duplicate rows removed from the overlap — same figure as the Stage 3 dry run), ingested all 1,022,290 deduplicated rows, flushed once.
4. The script self-verified: all 1,141 pre-existing non-`sales_trends` fingerprints were still present after the flush — zero data loss.
5. Restarted the supervisor (`launchctl bootstrap`) and confirmed live via `GET /v1/health` and `GET /v1/universe`.

**Real results:**
- `~/.vectrax/gravity_index.json`: 1,142 → **29,576** fingerprints (28,434 net new `sales_trends` stars).
- `sales_trends` stars in production: **28,435** (28,434 from the backfill + the earlier validation star). Top hits match the Stage 3 dry-run findings exactly: `[5367, 3783, 3491, 2824, 2822]`.
- `GET /v1/universe`: `total_stars` 2,324 → **30,793**, `star_breakdown.gravitational` 1,122 → **29,576**.

`detect_periodicity()` / `detect_periodicity_detrended()` remain unwired from the ingest pipeline — this backfill populates real stars only; running either detector over this real accumulated data is still a separate, not-yet-approved step (same boundary as Stage 3).

## Related documents

- `docs/SALES_TRENDS_CALIBRATION_2026_08_25.md` — Stage 2, 1-year `MAX_ACTIVATION_HISTORY` calibration.
- `docs/SALES_TRENDS_STAGE3_2026_08_26.md` — Stage 3, 2-year backfill dry-run + trend/periodicity separation.
