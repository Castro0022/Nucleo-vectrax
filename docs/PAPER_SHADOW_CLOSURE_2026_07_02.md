# PAPER-shadow + `is_usable` Fix — Task Closure Report

- **Task:** Diagnose why the trading gate rejected 100% of patterns (0/72 usable), fix it without lowering thresholds blindly, and add an observational PAPER-shadow channel.
- **Delivered:** 2026-07-02 · **Archived:** 2026-07-05 · **Status:** ✅ Closed (merged, deployed, verified, documented)
- **Branch/merge:** `feat/paper-shadow-usable-fix` → PR #26 → `main` (`c7ad731`)
- **Related:** dashboard audit + canvas SSOT (PR #24/#25, `b2ff112`), canvas label `materializadas` (`f094a8e`), README docs (`746af54`)

## 1. Problem
Dashboard audit found **0 of 72 patterns "usable"**, so the auto-executor never paper-traded and "Progreso a LIVE" stayed frozen at 0/30 despite 224 resolved signals.

## 2. Root-cause diagnosis (evidence-based)
- Only **2/72** patterns reached `n_total ≥ 15`; **0** reached ≥20/30. Global WR **41.1%**, weighted expectancy **−0.037** (net-losing base rate).
- **Sample-definition bug:** `PatternStats.is_usable` gated on `n_total`, which includes `neutral`/`expired` outcomes that do **not** inform win-rate/expectancy — inflating the sample (e.g. a pattern showing "N=10" often had only 2–6 *decisive* outcomes).
- **Conclusion:** not a case for lowering N/WR/E blindly — patterns are genuinely not reliable yet (insufficient *decisive* data). The threshold simulation confirmed relaxing to N10/WR55 unlocked only 7/224 decisive signals (statistical noise).

## 3. Implemented changes
### 3.1 Correctness fix — `connectors/etoro/pattern_memory.py`
`is_usable` now measures the **decisive** sample (`n_wins + n_losses`) instead of `n_total`. Thresholds **unchanged** (decisive ≥15, WR ≥55%, E >0). This aligns the sample with the metric it guards; the real gate stays closed until real evidence exists.

### 3.2 Single outcome classifier — `connectors/etoro/outcome_tracker.py`
`_classify_outcome` promoted to public **`classify_outcome`** (single source of truth for W/L/neutral/expired) with a backward-compat alias. No second definition anywhere.

### 3.3 PAPER-shadow subsystem — `connectors/etoro/paper_shadow.py` (new)
Observational channel that records the trades VECTRAX *would* have taken under a flexible experimental gate, **without executing, without real paper trades, without advancing LIVE progress**.

- **Shadow gate** (experimental): decisive ≥5, WR ≥50%, E >0.
- **Candidate** = a signal whose pattern **fails the real gate but passes the shadow gate**.
- **Resolution** inherits the underlying real signal's outcome (produced by `classify_outcome`) — no duplicated logic.
- **Promotion:** `READY_FOR_MANUAL_PROMOTION` only when the REAL `PatternStats.is_usable` passes — **never automatic**, suggestion for human review.
- **Storage:** `<vault>/paper_shadow.db` → tables `paper_shadow_trades` (+ `paper_shadow_promotions`), auto-created (idempotent).
- **Logs:** `SHADOW_CANDIDATE_CREATED`, `SHADOW_TRADE_RESOLVED`, `SHADOW_FEEDBACK_RECORDED`, `SHADOW_READY_FOR_PROMOTION`.

### 3.4 Integration
- `connectors/etoro/learning_engine.py` — **Step 9** calls `run_shadow_cycle()` (defensive; no executor changes).
- `services/core/routes/pattern_performance.py` — `/v1/market/patterns` exposes a separate `shadow` block.
- `services/ui/static/app.js` — PAPER-shadow card in the SPA Patterns tab, separated from the real executor (0/30).

## 4. Hard boundaries (enforced by tests)
- Never calls the real Auto-Executor; never increments `paper_trades_total`; never advances LIVE progress.
- Reuses `classify_outcome` — no second win/loss/neutral definition.
- Promotion is suggestion-only and requires the real gate.

## 5. Tests — `tests/test_paper_shadow.py` (7, all pass)
`is_usable` uses decisive · neutral/expired don't inflate · no real paper trade · no LIVE advance · resolution reuses classifier · `READY_FOR_MANUAL_PROMOTION` on real gate · promotion not automatic. Related suites (`test_pattern_performance`, `test_advanced_architecture`) still green (59 passed).

## 6. Git & deployment trail
- Commit `32210a6` (7 files) → PR #26 → merged to `main` as `c7ad731`.
- README docs `746af54`; canvas label `f094a8e`.
- Deployed to Vultr (`vectrax-core`, `api.vectrax.app`) via `deploy_vultr.sh`.

## 7. Production verification (2026-07-02)
- Container healthy; **0** ERROR/CRITICAL/Traceback since deploy.
- `/v1/market/patterns`: `shadow.enabled=True`, real executor `paper 0/30` (unchanged).
- Manual `run_shadow_cycle`: **69 candidates** created + resolved, shadow WR **55.3%**, E **+0.195**, `ready_for_promotion=[]`.
- Persistence confirmed: 69 rows survive; re-run idempotent (dedup by `signal_id`); all four `SHADOW_*` log types verified.

## 8. Status & next steps
- **Closed.** The worker populates shadow candidates each learning cycle (Step 9). When a pattern crosses the real gate it appears as `READY_FOR_MANUAL_PROMOTION` for **manual** review.
- No open items. No change to real trading behavior (executor remains OFF/gated).

## References
- Docs: `README.md` → "🧪 PAPER-shadow — Observational Trade Learning"; `CHANGELOG.md` → 2026-07-02.
- Code: `connectors/etoro/{paper_shadow,outcome_tracker,pattern_memory,learning_engine}.py`, `services/core/routes/pattern_performance.py`, `services/ui/static/app.js`, `tests/test_paper_shadow.py`.
