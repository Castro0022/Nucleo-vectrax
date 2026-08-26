# Universe Panel Performance Investigation (2026-08-26)

Triggered by: the Universe panel became slow again after the sales_trends full production backfill (`docs/SALES_TRENDS_LIVE_INGEST_2026_08_26.md`, 30,793 total stars, 29,576 gravitational). This document records the exact bottleneck, why the "cybersecurity-era" optimization does not actually apply, the fix, and before/after measurements. **No star, fingerprint, connection, or gravity_index.json data was touched at any point** — this is a delivery-layer (API/caching) change only.

## Was there a pre-existing "cybersecurity-scale" optimization to reuse?

Checked directly: `scripts/cyber_backfill_demo.py` and its instrumentation (commit `822db18`, Aug 7) reported "236,494 CVE → 29,899 estrellas" — a scale comparable to today's sales_trends backfill. But this production `gravity_index.json` has **zero** `cybersecurity` stars:

```
sales_trends: 28435   freight_logistics: 711   florida_real_estate: 189
unknown: 118   core: 69   services: 21   market: 14   ...  (no cybersecurity)
```

That backfill was validated in an isolated demo/instrumentation run (per `docs/CYBERSECURITY_DOMAIN_2026_08_02.md`: "el worker permanece apagado hasta validar el backfill real") — never actually loaded into this production file. The only related historical change found (commit `73d7351`, June 15) *removed* a `[:50]` cap on the gravity star list for correctness (a panel showing 442 of 967 real stars), at a scale (967) where sending everything cost nothing. **There is no dormant large-scale optimization to reuse** — this is a new problem at a new scale, requiring a new fix.

## Measured root cause

### Step 1 — where is the payload?

`GET /v1/universe`: 2.28s, 6.03 MB response. Breakdown by top-level key:

| Key | Bytes | Share |
|---|---:|---:|
| `gravity.stars` | 5,701,088 | **94.6%** |
| `convergences` | 817,740 | 13.6%* |
| everything else | ~28,000 | <0.5% |

(*`convergences` and `gravity.stars` overlap in byte accounting since they're nested differently in the response; the point stands regardless: `gravity.stars` alone is the overwhelming majority of the payload.)

### Step 2 — backend generation vs. serialization vs. network

Localhost request, so network transfer of 6MB is sub-millisecond and was ruled out immediately. Instrumented every sub-step directly:

| Step | Time (before) |
|---|---:|
| `gi.all_records()` — disk read + JSON parse (33.8MB file) | 0.222s |
| `gi.domain_stats()` — **re-reads the file from disk again** | 0.331s |
| `gi.cross_domain_convergences()` — **re-reads the file a 3rd time** + O(\|market\| × \|rest\|) nested loop | 0.710s |
| `observe_universe()` total | 1.653s |
| `to_api_dict()` (adds `get_census()`, which **independently repeats the same 3 reloads**) | 1.751s |
| `json.dumps()` of the full response | 0.040s |

**Conclusion: 100% backend. Not serialization (40ms), not network (negligible on localhost), not `universe.html` rendering (a separate, additional cost layered on top once the payload arrives, not measured here since the backend cost alone already explains the multi-second delay).** The root cause: `GravityIndex.all_records()`, `domain_stats()`, and `cross_domain_convergences()` each independently call `self._load()`, so **one** `/v1/universe` request reads and JSON-parses the entire (now 33.8MB) index **three separate times** — and `core/universe_census.py::_build_census()` repeated the exact same pattern a fourth time internally. This was cheap and invisible at ~1,100 stars; at 29,576 it is not.

## Fix (delivery-layer only, zero data changes)

1. **`core/learn/gravity_engine.py`**: `domain_stats()` and `cross_domain_convergences()` gained an optional `records` parameter. When provided, no disk read happens — the caller's already-loaded dict is reused. Default behavior (no `records` passed) is byte-for-byte unchanged.
2. **`core/self_observation/universe_observer.py`** and **`core/universe_census.py`**: both now load the index **once** via `load_raw()` and pass it to `domain_stats()`/`cross_domain_convergences()`.
3. **`core/self_observation/universe_observer.py`**: added a 3-second TTL cache around the gravity-derived computation (star list + domain stats + convergences), mirroring the existing `core/universe_census.py` 10s TTL cache pattern. The `/v1/universe/ws` WebSocket recomputes this every 2s regardless of whether the data changed — a cache hit serves the last computed (still current) result instead of repeating the sort + O(n×m) convergence scan. A cache MISS always recomputes from the real, current, on-disk file; nothing is ever served stale beyond 3 seconds, and the cache is never a substitute for real data.

## Before/after (real production, real live server)

| Metric | Before | After (cold) | After (cache hit) |
|---|---:|---:|---:|
| `GET /v1/universe` | 2.28s | 0.914s | **0.103–0.105s** |
| `observe_universe()` (in-process) | 1.653s | 0.913s | n/a (cached) |
| `cross_domain_convergences()` | 0.710s | 0.420s (load removed, scan remains) | n/a (cached) |

**Verified identical output, only faster:**
- `gravity_index.json` SHA-256 checksum: identical before and after (`7f915ca9...`) — zero bytes changed.
- `total_stars`: 30,793 → 30,793 (unchanged).
- `gravity.stars` count: 29,576 → 29,576 (unchanged); exact star ID set verified equal.
- `gravity.convergences_total`: 11,011 → 11,011 (unchanged).

## What was deliberately NOT done

- No star, fingerprint, connection, or domain was removed, reduced, or modified.
- No cap added on how many stars are computed or stored — every gravitational star is still counted and included.
- `universe.html` canvas rendering was not touched in this change. The backend fix alone accounts for the measured multi-second delay; if perceived slowness persists specifically in browser frame rate (not initial load), that would be a separate, later investigation into canvas draw-call cost at ~30k rendered objects — out of scope here since the dominant, measured cost was conclusively backend.
- The 3s cache TTL is a delivery-layer parameter, easily tuned; it does not change what data exists, only how often the expensive computation reruns.
