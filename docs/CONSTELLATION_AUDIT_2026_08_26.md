# Constellation Systems Audit — 2026-08-26

Audit only, as required. **Neither system is modified or migrated in this
PR.** This documents who writes and reads each one, today, verified by
reading the code (not assumed).

## System 1 — `core/learn/constellation.py` (`ConstellationRecord`, `vault/constellations/*.json`)

- **Writer**: `compact(index)` in `core/learn/constellation.py`. Groups
  `GravityRecord`s by `(domain, intent)` and creates a `ConstellationRecord`
  once a group reaches `COMPACTION_THRESHOLD = 10_000` members. Persists one
  JSON file per constellation under `vault/constellations/<id>.json`.
- **Invoked from**: only `cli/learn_cli.py` (a manual CLI command). No
  scheduler, cron job, meta_loop, or autonomous_observer call site calls
  `compact()` anywhere in the codebase.
- **Reader**: `list_constellations()` / `load_constellation()`, both defined
  in this same module. No route, dashboard, or API consumer reads them
  anywhere else in the codebase.
- **Real-world trigger risk**: `sales_trends` alone has 28,435 records as of
  this audit. If its `(domain, intent)` grouping (e.g. `sales_trends:sale`)
  is uniform enough to exceed 10,000 members, running `compact()` manually
  *would* produce a constellation today — the threshold is reachable at
  current scale, it's just never invoked automatically.
- **Classification: dormant / manual-only.** Designed for gravity-engine
  domain/intent event compaction; currently has no consumer in the live
  product surface.

## System 2 — SQLite `constellations` table (`vectrax/db.py`, `Constellation`)

- **Writer**: `upsert_constellation()` in `vectrax/db.py`, called from
  `vectrax/engine.py` (multiple call sites) and `vectrax/cognitive_gravity.py`.
- **Live path**: `vectrax/engine.py` is imported by
  `services/core/routes/chat.py` — i.e. this table is written to during real
  chat request handling, not just by a CLI or batch job.
- **Reader**: `get_all_constellations()` / `get_constellation()` in
  `vectrax/db.py`, consumed by `services/core/routes/gravitational.py` and
  `services/core/independence.py`. Also surfaced today via
  `core.universe_census._build_census()` → `counts.get("constellations", 0)`
  → `UniverseCensus.constellations` (already part of the current, live
  census output).
- **Classification: active.** This is the system actually exercised by the
  live chat pipeline and already reported in Census today.

## Naming/ontology collision

Both systems use the word "constellation" for structurally different things:

- System 1: a *compacted event-volume summary* over the gravity-engine's
  domain-ingested events (`sales_trends`, `freight_logistics`, etc.) — an
  aggregation designed for scale (thousands→millions of raw events).
- System 2: a *semantic cluster of legacy chat `stars`* (the pre-gravity-
  engine `vectrax/` conversational memory model) — an aggregation designed
  for a handful to a few hundred related conversational memories.

They do not share a schema, a scale, a writer, or a domain model. Treating
them as "the same concept, one legacy and one active" would be incorrect —
they are two different concepts that happen to share a name.

## Recommendation (no action taken this PR)

1. Do not merge or migrate either system yet.
2. Before any future unification, first decide which concept "constellation"
   should mean going forward — a domain/intent-scale gravity compaction
   (System 1's design) or a semantic star cluster from chat (System 2's
   actual current use) — since a single unified schema serving both without
   first resolving this would likely produce a confusing hybrid.
3. If unification proceeds, System 2 should be treated as the
   higher-priority migration target given it is live and already
   Census-visible; System 1 can remain manual/experimental until its
   `compact()` call site is deliberately wired into an automatic path (or
   retired if it never is).
