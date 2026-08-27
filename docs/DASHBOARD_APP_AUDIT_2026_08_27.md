# VECTRAX App/Dashboard Audit & Reorganization — 2026-08-27

Branch: `feat/dashboard-app-reorg` (based on `main`).
Plan: "Auditoría y Reorganización VECTRAX App/Dashboard".

## Scope

Full audit of the existing SPA (`services/ui/templates/index.html` +
`services/ui/static/app.js`), the Universe visualizer, Observatory, and the
market/trading pages, followed by a conservative reorganization: eliminate
confirmed duplication, correct a misleading UI label, fix a broken nav link,
integrate previously-unrendered backend data, and make the operational
interface responsive — **without** touching the cognitive core, gravity
semantics, or deleting any historical data.

## Inventory findings (before)

- **Domain/population labeling gap**: `/dashboard/observatory` returns
  `total_stars=2037` (gravitational + knowledge + users — three separate
  legacy populations) alongside a `domains` breakdown that only covers the
  gravitational population (798). The two numbers were displayed side by
  side with no indication that they cover different scopes. **Not a data
  bug** — confirmed by cross-checking against `core.universe_census`.
- **Duplication**: `dashboard_operator()` (`/dashboard/operator`) and
  `system_monitor()` (`/system/monitor`) independently called
  `collect_metrics()` + `get_current_policy()` and built near-identical
  `runtime`/`governor` dicts by hand.
- **Unnecessary heavy computation**: `dashboard_operator()`'s "Universe"
  mini-card (8 numbers) called the full `observe_universe()`, which also
  collects every user star, up to 500 `convergence_history` rows, the full
  gravity engine snapshot with eToro pattern injection, Word Gravity, and
  quality-phenomena entities.
- **Redundant disk reads**: `dashboard_observatory()` called
  `gi.domain_stats()`, `gi.tier_counts()`, `gi.top_stars()`,
  `gi.growth_trends(7)`, and `gi.growth_trends(1)` — five independent
  `gravity_index.json` reloads per request. (`domain_stats`/
  `cross_domain_convergences` had already been fixed for reuse in an earlier
  PR; the other three had not.)
- **Dead endpoint**: `/dashboard/summary` has zero frontend consumers.
- **Unrendered backend data**: `/dashboard/observatory` already computed
  `observation_bias` and richer per-domain stats (`hits`/`avg_cc`/`avg_freq`/
  `tiers`/`new_by_domain`), but nothing in the SPA rendered them — they were
  only visible in the separate `observatory.html` page.
- **Misleading label**: the "Sessions" dashboard tab called
  `/dashboard/users` and rendered Telegram user profiles, not conversation
  sessions.
- **Broken link**: the topbar's "Observatory" link pointed at
  `/v1/observatory`, a route that does not exist (`observatory.html` is only
  reachable at `/static/observatory.html`; no route mounts it at `/v1/observatory`
  or `/observatory`).
- **Missing nav entries**: `market_live.html`, `pattern_performance.html`,
  and `pipeline_train.html` were fully functional pages with real routes but
  had no entry point from the main SPA nav.
- **`universe_legacy.html`**: confirmed to be a strict subset of the current
  `universe.html` (same `/v1/gravitational/universe` endpoint, minus Gravity
  Engine domains, Word Gravity, quality phenomena, and richer visuals) — zero
  unique functionality remaining.
- **Responsiveness gaps**: `.login-card` had a fixed `width: 380px`
  (overflows below 380px); `.topbar-nav` had no overflow handling; the
  Universe canvas HUD used fixed absolute-positioned panels
  (`#hud-controls` width 200 + `#hud-system` width 232, both anchored to
  opposite edges) that overlap on any viewport narrower than roughly 470px.

## Changes (after)

### Backend
- `core/learn/gravity_engine.py`: `tier_counts()`, `top_stars()`, and
  `growth_trends()` gained the same optional `records=` parameter already
  used by `domain_stats()`/`cross_domain_convergences()`, defaulting to
  `self._load()` for full backward compatibility.
- `services/core/routes/dashboard.py` — `dashboard_observatory()`: now calls
  `gi.load_raw()` once and passes `records=raw` to all five gravity calls
  (5 reloads → 1 per request).
- `core/operator/system_monitor.py`: new shared
  `runtime_and_governor_snapshot()` helper (runtime metrics + governor
  policy in one dict), used by both `/dashboard/operator` and
  `/system/monitor` so the two views can never drift.
- `services/core/routes/dashboard.py` — `dashboard_operator()`: the
  "Universe" mini-card now reads `core.universe_census.get_census()`,
  `vectrax.core_nucleus.get_core_info()`, and
  `core.self_observation.state_collector.collect_state()` instead of the
  full `observe_universe()`.
- `services/core/routes/monitor.py`: `system_monitor()` now uses the same
  shared helper; the separate hand-written Governor block was removed.
- `/dashboard/summary` marked `DEPRECATED` in its docstring (points callers
  at Census / `/dashboard/observatory`); kept, not removed — zero confirmed
  frontend consumers, but no proof of zero *external* consumers.

### Frontend
- `services/ui/static/app.js` — `loadOverview()`: added an "Observation
  Bias" card and a per-domain detail grid (hits/avg_cc/avg_freq/tiers/new by
  24h & 7d), both sourced from data `/dashboard/observatory` already
  returned (zero new backend calls). The "Dominios" section header now
  explicitly states it covers the Gravity Engine population only, with that
  population's own total shown alongside it, to remove the 2037-vs-798
  ambiguity without inventing or hiding data.
- `services/ui/templates/index.html`: "Sessions" tab relabeled "Usuarios"
  (unchanged internal route/id, to minimize risk); "Observatory" topbar link
  now opens the Dashboard instead of the broken `/v1/observatory`; added
  "Mercado", "Trading", and "Pipeline Train" topbar links to
  `/v1/market/view`, `/v1/market/patterns/view`, and
  `/v1/dashboard/train/view`.
- `services/ui/templates/universe_legacy.html`,
  `services/ui/static/observatory.html`,
  `services/ui/routes.py`: both legacy pages now carry a `DEPRECATED /
  SUPERSEDED` comment documenting the comparison and recommending removal
  in a **future** PR once external bookmarks/links are confirmed absent.
  Neither file nor its route was deleted.
- `services/ui/static/style.css`: `.login-card` now `width: 100%; max-width:
  380px` (no more horizontal overflow below 380px); `.topbar-nav` gained
  `overflow-x: auto` with `flex-shrink: 0` nav buttons (scrolls instead of
  clipping/wrapping awkwardly with the added nav links); new
  `.section-title`/`.bias-bar` component styles for the Overview additions.
- `services/ui/static/universe.html`: added a `@media (max-width: 768px)`
  block that resizes/repositions the HUD overlay panels (top bar, back
  link, filters, legend) and collapses the secondary engines/brokers panel,
  so they no longer overlap on narrow viewports. The canvas rendering and
  physics engine below are untouched.

## Verification

- `python3 -m py_compile` on every touched Python file: clean.
- New/updated tests (`tests/test_universe_performance.py`,
  `tests/test_dashboard_operator_observatory.py`, 22 tests): all passing —
  cover `records=` equivalence for `tier_counts`/`top_stars`/
  `growth_trends`, `dashboard_observatory()`'s gravity section matching an
  independent computation from the same loaded index, `dashboard_operator()`
  no longer calling `observe_universe()`, its Universe fields being sourced
  from the lightweight replacements (including graceful degradation on a
  source failure), and both `/dashboard/operator` and `/system/monitor`
  using the shared `runtime_and_governor_snapshot()` helper.
- Full hermetic suite (`pytest tests/ -m "not live"`): 3613 passed, 2
  skipped, 3 failed. All 3 failures are pre-existing and unrelated to this
  work — reproduced identically with this branch's changes stashed out
  (`test_external_gateway_capability_grounding.py`,
  `test_providers.py::test_list_models_static`,
  `voice/test_synthesizer.py`), and none of the affected files were touched
  here.
- Static verification of the HTML/JS changes (brace/paren/backtick and
  `<div>` tag balance) in lieu of a live browser check: the machine's real
  Vectrax core service was already running in production at the time of
  this work (serving real Telegram users), so it was deliberately **not**
  restarted to avoid disrupting production traffic. Live desktop/mobile
  verification is recommended as a follow-up once this branch is deployed
  or run in an isolated environment.

## Explicitly out of scope / preserved

- No changes to gravity semantics, convergence detection, or any
  cognitive-core logic.
- No historical data deleted. `universe_legacy.html`, `observatory.html`,
  and `/dashboard/summary` are all preserved and still reachable at their
  existing routes.
- `universe_legacy.html` removal is a recommended **follow-up PR**, not
  executed here, pending confirmation of zero external bookmarks/links.
