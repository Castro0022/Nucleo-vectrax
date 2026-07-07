"""
Vectrax eToro — PAPER-shadow (observational, NON-executing)
============================================================
Records what trades VECTRAX *would* have taken under a MORE FLEXIBLE
experimental gate, WITHOUT executing them, WITHOUT counting them as real
paper_trades, and WITHOUT advancing "Progreso a LIVE".

Hard boundaries (enforced by construction — see tests):
  • Never calls the real Auto-Executor (`auto_executor.execute_proposal`).
  • Never increments `auto_executor` `paper_trades_total` (progress to LIVE).
  • Never opens a real or simulated eToro position.
  • Reuses the SINGLE outcome classifier `outcome_tracker.classify_outcome`
    (via the underlying real signal's already-resolved status). No second
    win/loss/neutral definition lives here.

Promotion is only *suggested* for human review — never automatic. A shadow
pattern becomes READY_FOR_MANUAL_PROMOTION when its REAL pattern satisfies the
original gate (`PatternStats.is_usable`: decisive>=15 ∧ WR>=55% ∧ E>0).

Storage: <vault>/paper_shadow.db (SQLite). Tables auto-created (idempotent):
  • paper_shadow_trades       — one row per hypothetical trade (per signal)
  • paper_shadow_promotions   — one row per pattern flagged for promotion

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.etoro.paper_shadow")

# ── Storage ───────────────────────────────────────────────────────────
_VAULT = os.environ.get("VECTRAX_VAULT_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault",
)
_DB_PATH = os.path.join(_VAULT, "paper_shadow.db")

# ── Experimental shadow gate (INTENTIONALLY more flexible than the real gate) ──
# The real gate (PatternStats.is_usable) requires decisive>=15, WR>=55, E>0.
# The shadow gate collects earlier candidates so we can build evidence without
# risking the real execution path. These are NOT trading thresholds.
SHADOW_MIN_DECISIVE   = 5      # decisive (win+loss) outcomes, not n_total
SHADOW_MIN_WIN_RATE   = 50.0
SHADOW_MIN_EXPECTANCY = 0.0

# Hypothetical position sizing for pnl_sim only (never real money).
SHADOW_NOTIONAL_USD = 100.0

_RESOLVED = ("win", "loss", "neutral", "expired")
_DECISIVE = ("win", "loss")


# ── DB ────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_shadow_trades (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at           REAL NOT NULL,
    timestamp            TEXT NOT NULL,
    symbol               TEXT NOT NULL,
    direction            TEXT NOT NULL,
    pattern_id           TEXT NOT NULL,
    signal_id            TEXT NOT NULL UNIQUE,
    n_decisive           INTEGER NOT NULL,
    win_rate             REAL NOT NULL,
    expectancy           REAL NOT NULL,
    real_reject_reason   TEXT NOT NULL,
    shadow_accept_reason TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
    return_pct           REAL,
    pnl_sim              REAL,
    resolved_at          REAL
);
CREATE TABLE IF NOT EXISTS paper_shadow_promotions (
    pattern_id  TEXT PRIMARY KEY,
    marked_at   REAL NOT NULL,
    evidence    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS paper_shadow_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


# ── Gate evaluation (read-only, pure) ─────────────────────────────────

def _decisive(n_wins: int, n_losses: int) -> int:
    return int(n_wins) + int(n_losses)


def real_reject_reason(n_wins: int, n_losses: int, win_rate: float, expectancy: float) -> str:
    """Why the REAL gate (PatternStats.is_usable) rejects this pattern.

    Returns "" if the real gate would ACCEPT (⇒ NOT a shadow case). Uses the
    same criteria as PatternStats.is_usable (decisive>=15 ∧ WR>=55 ∧ E>0).
    """
    from connectors.etoro.pattern_memory import USABLE_SAMPLE, USABLE_WIN_RATE

    dec = _decisive(n_wins, n_losses)
    fails: List[str] = []
    if dec < USABLE_SAMPLE:
        fails.append(f"decisivas={dec}<{USABLE_SAMPLE}")
    if win_rate < USABLE_WIN_RATE:
        fails.append(f"WR={win_rate:.0f}%<{USABLE_WIN_RATE:.0f}%")
    if expectancy <= 0:
        fails.append(f"E={expectancy:+.3f}<=0")
    return "gate real: " + ", ".join(fails) if fails else ""


def evaluate_shadow_gate(
    n_wins: int, n_losses: int, win_rate: float, expectancy: float
) -> Tuple[bool, str]:
    """Experimental gate. Returns (accept, reason)."""
    dec = _decisive(n_wins, n_losses)
    ok = (
        dec >= SHADOW_MIN_DECISIVE
        and win_rate >= SHADOW_MIN_WIN_RATE
        and expectancy > SHADOW_MIN_EXPECTANCY
    )
    reason = (
        f"gate shadow: decisivas={dec}>={SHADOW_MIN_DECISIVE}, "
        f"WR={win_rate:.0f}%>={SHADOW_MIN_WIN_RATE:.0f}%, "
        f"E={expectancy:+.3f}>{SHADOW_MIN_EXPECTANCY:.1f}"
    )
    return ok, reason


# ── Candidate recording ───────────────────────────────────────────────

def record_shadow_candidate(
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    pattern_id: str,
    n_decisive: int,
    win_rate: float,
    expectancy: float,
    real_reject_reason: str,
    shadow_accept_reason: str,
    timestamp: Optional[float] = None,
) -> bool:
    """Insert a shadow candidate (one per signal_id). Returns True if inserted.

    NEVER executes anything. NEVER touches the real executor.
    """
    ts = timestamp if timestamp is not None else time.time()
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO paper_shadow_trades "
            "(created_at, timestamp, symbol, direction, pattern_id, signal_id, "
            " n_decisive, win_rate, expectancy, real_reject_reason, "
            " shadow_accept_reason, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')",
            (
                time.time(), iso, symbol.upper(), direction, pattern_id, signal_id,
                int(n_decisive), round(float(win_rate), 2), round(float(expectancy), 4),
                real_reject_reason, shadow_accept_reason,
            ),
        )
        conn.commit()
        inserted = cur.rowcount > 0
    finally:
        conn.close()
    if inserted:
        logger.info(
            "SHADOW_CANDIDATE_CREATED | %s %s | pattern=%s signal=%s | "
            "N_dec=%d WR=%.0f%% E=%+.3f | %s | %s",
            direction.upper(), symbol.upper(), pattern_id, signal_id,
            n_decisive, win_rate, expectancy, real_reject_reason, shadow_accept_reason,
        )
    return inserted


# ── Resolution (reuses the real signal outcome — single classifier) ───

def resolve_shadow_trades(signals: Optional[List[Any]] = None) -> Dict[str, int]:
    """Resolve pending shadow trades by INHERITING the outcome of the underlying
    real signal, which was classified by `outcome_tracker.classify_outcome`.

    This is the ONLY place shadow status is set, and it never re-derives
    win/loss/neutral independently — it reuses the real signal's resolved
    status (single source of truth).
    """
    # Import the canonical classifier so the dependency is explicit/auditable,
    # even though we inherit the already-resolved status from the signal.
    from connectors.etoro.outcome_tracker import classify_outcome  # noqa: F401

    if signals is None:
        from connectors.etoro.signal_recorder import load_signals
        signals = load_signals()
    by_id = {s.signal_id: s for s in signals}

    summary = {"resolved": 0, "win": 0, "loss": 0, "neutral": 0, "expired": 0}
    conn = _conn()
    try:
        pend = conn.execute(
            "SELECT id, signal_id FROM paper_shadow_trades WHERE status='pending'"
        ).fetchall()
        for row in pend:
            sig = by_id.get(row["signal_id"])
            if not sig:
                continue
            st = getattr(sig, "status", "pending")
            if st not in _RESOLVED:
                continue  # underlying real signal not resolved yet
            ret = float(getattr(sig, "return_pct", 0) or 0)
            pnl = round(ret / 100.0 * SHADOW_NOTIONAL_USD, 4)
            conn.execute(
                "UPDATE paper_shadow_trades SET status=?, return_pct=?, "
                "pnl_sim=?, resolved_at=? WHERE id=?",
                (st, round(ret, 4), pnl, time.time(), row["id"]),
            )
            summary["resolved"] += 1
            summary[st] = summary.get(st, 0) + 1
            logger.info(
                "SHADOW_TRADE_RESOLVED | signal=%s | status=%s ret=%+.3f%% pnl_sim=%.4f",
                row["signal_id"], st, ret, pnl,
            )
            logger.info(
                "SHADOW_FEEDBACK_RECORDED | signal=%s | status=%s (reusa classify_outcome)",
                row["signal_id"], st,
            )
        conn.commit()
    finally:
        conn.close()
    return summary


# ── Promotion (SUGGESTION ONLY — never automatic) ─────────────────────

def check_promotions(patterns: Optional[List[Any]] = None) -> List[str]:
    """Flag shadow patterns whose REAL PatternStats now pass the original gate
    (PatternStats.is_usable). Returns list of pattern_ids READY_FOR_MANUAL_PROMOTION.

    NEVER promotes automatically — only records a suggestion + logs it once.
    """
    if patterns is None:
        from connectors.etoro.pattern_memory import get_patterns
        patterns = get_patterns()
    usable_by_key = {p.pattern_key: p for p in patterns if p.is_usable}

    conn = _conn()
    try:
        shadow_keys = {
            r["pattern_id"]
            for r in conn.execute(
                "SELECT DISTINCT pattern_id FROM paper_shadow_trades"
            ).fetchall()
        }
        already = {
            r["pattern_id"]
            for r in conn.execute(
                "SELECT pattern_id FROM paper_shadow_promotions"
            ).fetchall()
        }
        ready: List[str] = []
        for key in shadow_keys:
            pat = usable_by_key.get(key)
            if not pat:
                continue
            ready.append(key)
            if key not in already:
                dec = pat.n_wins + pat.n_losses
                evidence = f"decisivas={dec} WR={pat.win_rate:.0f}% E={pat.expectancy:+.3f}"
                conn.execute(
                    "INSERT OR IGNORE INTO paper_shadow_promotions "
                    "(pattern_id, marked_at, evidence) VALUES (?,?,?)",
                    (key, time.time(), evidence),
                )
                logger.info(
                    "SHADOW_READY_FOR_PROMOTION | pattern=%s | %s | "
                    "REQUIERE aprobacion humana (no automatico)",
                    key, evidence,
                )
        conn.commit()
        return sorted(ready)
    finally:
        conn.close()


# ── Full cycle (called from learning_engine — defensive, never raises) ─

def run_shadow_cycle(
    signals: Optional[List[Any]] = None,
    patterns: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Observe → record shadow candidates (real-reject ∧ shadow-accept) →
    resolve → check promotions. Read-only w.r.t. real execution.
    """
    created = 0
    try:
        from connectors.etoro.pattern_memory import get_patterns, _make_key
        if signals is None:
            from connectors.etoro.signal_recorder import load_signals
            signals = load_signals()
        if patterns is None:
            patterns = get_patterns()
        by_key = {p.pattern_key: p for p in patterns}

        for sig in signals:
            try:
                key = _make_key(sig.symbol, sig.direction, sig.session, sig.conditions_names)
            except Exception:
                continue
            pat = by_key.get(key)
            if not pat:
                continue
            rr = real_reject_reason(pat.n_wins, pat.n_losses, pat.win_rate, pat.expectancy)
            if not rr:
                continue  # real gate accepts → not a shadow case
            accept, sr = evaluate_shadow_gate(
                pat.n_wins, pat.n_losses, pat.win_rate, pat.expectancy
            )
            if not accept:
                continue
            if record_shadow_candidate(
                signal_id=sig.signal_id, symbol=sig.symbol, direction=sig.direction,
                pattern_id=key, n_decisive=_decisive(pat.n_wins, pat.n_losses),
                win_rate=pat.win_rate, expectancy=pat.expectancy,
                real_reject_reason=rr, shadow_accept_reason=sr,
                timestamp=getattr(sig, "timestamp", None),
            ):
                created += 1
    except Exception as exc:
        logger.debug("run_shadow_cycle scan error: %s", exc)

    resolved = resolve_shadow_trades(signals)
    ready = check_promotions(patterns)

    # Observability: score the regime variants ALL vs FORWARD (read-only).
    try:
        from connectors.etoro.signal_filters import variant_report
        from connectors.etoro.signal_recorder import load_signals as _ls
        _sigs = signals if signals is not None else _ls()
        _cut = get_config_activated_at()
        _all = variant_report(_sigs)
        _fwd = variant_report(_sigs, since_ts=_cut)
        for _name in ("V1_follow_trend", "V2_aggressive"):
            _a = _all.get(_name, {})
            _f = _fwd.get(_name, {})
            logger.info(
                "SHADOW_VARIANT | %s | all: n=%d E=%+.4f | forward: n=%d E=%+.4f",
                _name, _a.get("decisive", 0), _a.get("expectancy", 0.0),
                _f.get("decisive", 0), _f.get("expectancy", 0.0),
            )
    except Exception as exc:
        logger.debug("shadow variant report error: %s", exc)

    return {"candidates_created": created, "resolved": resolved.get("resolved", 0),
            "ready_for_promotion": len(ready)}


# ── Out-of-sample cutoff (regime config activation timestamp) ────────

def get_config_activated_at() -> float:
    """Timestamp when regime-variant measurement went live (set once).

    Signals created at/after this mark the OUT-OF-SAMPLE (forward) window used
    to validate the regime config before any promotion to real execution.
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT value FROM paper_shadow_meta WHERE key='config_activated_at'"
        ).fetchone()
        if row:
            return float(row["value"])
        ts = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO paper_shadow_meta (key, value) VALUES ('config_activated_at', ?)",
            (str(ts),),
        )
        conn.commit()
        return ts
    finally:
        conn.close()


# ── Stats for dashboard ──────────────────────────────────────

def get_shadow_stats() -> Dict[str, Any]:
    """Aggregate shadow stats for the dashboard. Separate from the real executor."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM paper_shadow_trades").fetchall()
        ready = {
            r["pattern_id"]
            for r in conn.execute("SELECT pattern_id FROM paper_shadow_promotions").fetchall()
        }
    finally:
        conn.close()

    total = len(rows)
    pending = sum(1 for r in rows if r["status"] == "pending")
    wins = sum(1 for r in rows if r["status"] == "win")
    losses = sum(1 for r in rows if r["status"] == "loss")
    neutral = sum(1 for r in rows if r["status"] == "neutral")
    expired = sum(1 for r in rows if r["status"] == "expired")
    decisive = wins + losses
    shadow_wr = round(wins / decisive * 100, 1) if decisive else 0.0
    dec_rows = [r for r in rows if r["status"] in _DECISIVE and r["return_pct"] is not None]
    shadow_exp = round(sum(r["return_pct"] for r in dec_rows) / len(dec_rows), 4) if dec_rows else 0.0

    reasons: Dict[str, int] = {}
    for r in rows:
        reasons[r["real_reject_reason"]] = reasons.get(r["real_reject_reason"], 0) + 1
    top_reasons = sorted(reasons.items(), key=lambda x: -x[1])[:5]

    # Regime-variant scoring (read-only, separate from real gate).
    # by_variant = all signals (incl. in-sample); by_variant_forward = only
    # signals created after config activation (true out-of-sample evidence).
    _cut = 0.0
    try:
        from connectors.etoro.signal_filters import variant_report
        from connectors.etoro.signal_recorder import load_signals
        _sigs = load_signals()
        _cut = get_config_activated_at()
        by_variant = variant_report(_sigs)
        by_variant_forward = variant_report(_sigs, since_ts=_cut)
    except Exception:
        by_variant = {}
        by_variant_forward = {}

    return {
        "enabled": True,
        "candidates_total": total,
        "pending": pending,
        "resolved": wins + losses + neutral + expired,
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "expired": expired,
        "shadow_win_rate": shadow_wr,
        "shadow_expectancy": shadow_exp,   # realized avg return % on decisive shadow trades
        "top_reject_reasons": [{"reason": k, "count": v} for k, v in top_reasons],
        "ready_for_promotion": sorted(ready),
        "by_variant": by_variant,           # V1/V2 over ALL signals (incl. in-sample)
        "by_variant_forward": by_variant_forward,  # V1/V2 over out-of-sample (post-activation)
        "config_activated_at": _cut,
        "note": "observational only — no real paper trades, does not advance LIVE progress",
    }
