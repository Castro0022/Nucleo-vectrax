# Regime Signal Filters — Forward (Out-of-Sample) Monitoring

Living log of the **out-of-sample validation** of the regime-based signal filters
(V1/V2), observed via PAPER-shadow. Purpose: decide — with forward evidence, not
in-sample — whether to promote to real PAPER execution (Phase B of the deployment
plan). **Nothing here trades; the real gate (`is_usable`) and auto-executor are untouched.**

## Setup
- **Config:** `connectors/etoro/signal_filters.py` — `DIRECCION_TENDENCIA` is the only edge condition; `REGIME_DIRECTION = {equity: sell, crypto: buy}`; **V1** (follow-trend), **V2** (V1 + drop `PRECIO_EN_ZONA` + only 3/4).
- **Out-of-sample cutoff:** `config_activated_at = 2026-07-07T05:14:06Z` (set-once, `paper_shadow_meta`). Only signals CREATED at/after this count as forward.
- **Alert:** `paper_shadow.check_forward_milestone()` — one-time creator notification (Telegram `_tg_notify` + `observation_ledger` + `SHADOW_FORWARD_FIRST` log) the moment the first forward signals are recorded. Wired into the periodic learning cycle (Step 9).
- **Promotion gate (Phase B):** V1 forward `decisive ≥ 30` ∧ `expectancy > 0` ∧ `Wilson LB(WR) ≥ 50%`, consistent across ≥2 sub-windows. Manual only.

## Monitoring results

### 2026-07-07 22:44Z (~17.5h after activation)
Alert verification:
- `SHADOW_FORWARD_FIRST` fired **once** at **14:10:34Z** (`post=10`, all pending at fire time), `tg_sent=True` → creator notified.
- `forward_first_alerted = 1`; a single alert line across 32 learning cycles → **idempotent, no re-fire**. ✅

Forward window (post-cutoff):
- Signals: **30** — `{win: 7, loss: 9, neutral: 14}` (neutral 47%).
- **V1_follow_trend:** decisive **7**, WR **71.4%**, Wilson LB **35.9%**, E **+0.585**.
- **V2_aggressive:** decisive **1**, WR 100%, Wilson LB 20.7%, E +0.826.
- In-sample (all, for reference): V1 n=122 E +0.153; V2 n=31 E +0.121.

Honest read: **not conclusive.** V1 forward E looks strong but `n=7` (Wilson LB 35.9% → WR could be ~36–92%). Gate progress: **7/30** decisive. Do not act on this yet.

## How to re-check
- Dashboard: `GET /v1/market/patterns` → `shadow.by_variant_forward` + `shadow.config_activated_at`.
- Logs: `docker compose logs | grep SHADOW_VARIANT` (per-cycle all vs forward) and `SHADOW_FORWARD_FIRST` (the one-time alert).

## Next checkpoint
Re-evaluate when V1 forward `decisive ≥ 30`. If it holds `E > 0` with Wilson LB(WR) ≥ 50% consistently → candidate for Phase B (connect V1 to PAPER execution behind `VX_REGIME_PAPER_GATE`, manual approval). If it decays to `E ≤ 0` → discard (confirms in-sample overfit).
