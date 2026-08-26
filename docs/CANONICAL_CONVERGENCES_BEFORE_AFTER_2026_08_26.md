# Canonical Convergence Entity — Before/After Report

Required deliverable before merge, per the approved plan. All numbers below
were pulled directly from the real `vault/convergence_history.db` and
`~/.vectrax/gravity_index.json` (not estimated).

## Legacy ledger (untouched)

| | Before | After |
|---|---|---|
| `convergence_events` row count | 5,598 | 5,598 (unchanged) |
| Content hash of the table | `9402167d...` | `9402167d...` (identical) |

Verified with a full-table checksum before and after the backfill — byte-for-byte identical.

## Convergence totals

| Metric | Before (buggy) | After (canonical) |
|---|---|---|
| "Total" convergences reported | `len(cross_domain_convergences(domain_a="market"))` — a live, market-anchored, non-deduplicated event count, different every call | **160** canonical entities |
| "Active" convergences | Hardcoded artifact: always exactly the top-20 slice fed to the old ledger (`convs[:20]`) | **111** |
| Dissolved | not tracked as a real total | **49** |
| Total confirmations | not tracked | **2,809** (exactly matches the total historical `birth` event count — cross-checked) |
| Domains covered by the global scan | 1 (`market` hardcoded as the only anchor, 100% of historical events were `market`-anchored `temporal_proximity`) | **8** domains observed in canonical pairs: `market`, `freight_logistics`, `unknown`, `unknown_legacy` (historical/orphaned), `ai_provider`, `cognition`, `tests`, `strategic_asset` |
| Ambiguous historical events (not silently merged) | N/A (bug was invisible) | **270** (4.8% of events), all traced to the confirmed `key.split(":")[-1]` corruption on multi-colon `freight_logistics` fingerprints — reported via `convergence_event_map.ambiguous=1`, excluded from every canonical group |

Domain pairs in the 160 canonical convergences:

```
freight_logistics <-> market:        60
market <-> unknown:                  33
unknown <-> unknown_legacy:          33
unknown_legacy <-> unknown_legacy:   14
market <-> unknown_legacy:           11
ai_provider <-> market:               4
cognition <-> market:                 1
cognition <-> unknown_legacy:         1
market <-> tests:                     1
tests <-> unknown_legacy:             1
market <-> strategic_asset:           1
```

## Consumers now reading the canonical total

- `core.universe_census.UniverseCensus.convergences` (redefined; same field name, existing call sites unchanged) = **160**
- `core.universe_census.UniverseCensus.convergences_active` (new) = **111**
- `core.universe_census.UniverseCensus.convergence_confirmations_total` (new) = **2,809**
- `core.self_observation.universe_observer.UniverseSnapshot.to_api_dict()["gravity"]["convergences_total"]` (already indirected through Census) = **160** — verified live, identical to Census.
- `dashboard.py` / `vectrax/self_context.py` (already read `census.convergences`) inherit the fix automatically, no call-site changes needed.

Verified directly:
```
census.convergences = 160
census.convergences_active = 111
census.convergence_confirmations_total = 2809
api gravity.convergences_total = 160   # identical to census
```

## Tests

- `tests/test_convergence_registry.py`: 18/18 passed — covers A↔B symmetry,
  confirmation vs. row-count on repeats, dissolve/reappear identity
  preservation, the required alternate-representation test (bare `AAPL`
  confirms the same convergence as live `market:AAPL`), ambiguous-fragment
  rejection, multi-domain global scan with no market anchor, and
  Census/registry total consistency.
- Legacy ledger integrity test: byte-for-byte `convergence_events` compare
  before/after a backfill run — passed.
- Full regression suite (`gravity or universe or census or domain_ingest or
  domain_knowledge or freight or self_observation or quality or convergence
  or constellation`): **584 passed, 1 skipped**, run both before and after
  applying the real backfill — no regressions either time.

## Constellation audit

See `docs/CONSTELLATION_AUDIT_2026_08_26.md`. Two systems confirmed, neither
migrated: `vault/constellations/*.json` (dormant, CLI-manual only, no
consumer) vs. the SQLite `constellations` table (active, written from the
live chat path via `vectrax/engine.py`, already Census-visible).

## Non-goals confirmed unchanged

No changes to `services/ui/static/universe.html` or any dashboard route.
Both constellation systems remain exactly as they were — audited, not
migrated.
