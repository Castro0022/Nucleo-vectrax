"""
Tests for the is_usable correctness fix (decisive sample) and the PAPER-shadow
observational subsystem.

Covers the required cases:
  1. is_usable uses decisive = wins + losses (not n_total)
  2. neutral/expired do NOT inflate the usable sample
  3. a shadow candidate does NOT create a real paper trade
  4. a shadow candidate does NOT advance "Progreso a LIVE"
  5. shadow resolution reuses the SAME W/L/neutral logic as real signals
  6. a shadow pattern with decisive>=15, WR>=55, E>0 is flagged READY_FOR_MANUAL_PROMOTION
  7. promotion never happens automatically
"""
import importlib

import pytest

from connectors.etoro.pattern_memory import PatternStats, USABLE_SAMPLE, _make_key
from connectors.etoro.signal_recorder import MarketSignal
from connectors.etoro.outcome_tracker import classify_outcome
from connectors.etoro import paper_shadow
from connectors.etoro import auto_executor


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def shadow_db(tmp_path, monkeypatch):
    """Point the shadow DB at an isolated temp file."""
    monkeypatch.setattr(paper_shadow, "_DB_PATH", str(tmp_path / "paper_shadow.db"))
    return tmp_path


@pytest.fixture()
def isolated_executor(tmp_path, monkeypatch):
    """Point the real auto-executor config at a fresh temp file (mode OFF, 0 paper trades)."""
    monkeypatch.setattr(auto_executor, "_CONFIG_FILE", str(tmp_path / "auto_cfg.json"))
    monkeypatch.setattr(auto_executor, "_PAPER_LOG_FILE", str(tmp_path / "paper_trades.jsonl"))
    return tmp_path


def _pattern(key, n_wins, n_losses, n_neutral=0, n_expired=0, expectancy=0.5):
    n_total = n_wins + n_losses + n_neutral + n_expired
    return PatternStats(
        pattern_key=key, symbol="AAPL", direction="sell", session="Sesion",
        conditions_key="c", n_wins=n_wins, n_losses=n_losses,
        n_neutral=n_neutral, n_expired=n_expired, n_total=n_total,
        expectancy=expectancy,
    )


def _signal(sid, symbol="AAPL", direction="sell", entry=100.0, price=100.0):
    return MarketSignal(
        signal_id=sid, timestamp=1000.0, symbol=symbol, direction=direction,
        scenario="OPERABLE", price=price, atr=1.0, rel_volume=1.0,
        session="Sesion", conditions_met=4, conditions_names=["A", "B", "C", "D"],
        entry_price=entry, invalidation_price=None,
    )


# ── 1 & 2. is_usable uses decisive; neutral/expired don't inflate ─────

def test_is_usable_uses_decisive_not_ntotal():
    # n_total=15 but only 2 decisive (13 neutral) → NOT usable under the fix
    p = _pattern("k", n_wins=2, n_losses=0, n_neutral=13, expectancy=0.5)
    assert p.n_total == 15
    assert (p.n_wins + p.n_losses) == 2
    assert p.is_usable is False  # would have been True under the old n_total gate


def test_neutral_expired_do_not_inflate_usable_sample():
    # 14 decisive + 10 neutral → n_total=24 but decisive<15 → NOT usable
    p_below = _pattern("k1", n_wins=10, n_losses=4, n_neutral=10, expectancy=0.5)
    assert p_below.n_total >= USABLE_SAMPLE
    assert p_below.is_usable is False
    # Exactly 15 decisive, WR=60%, E>0 → usable regardless of neutrals
    p_ok = _pattern("k2", n_wins=9, n_losses=6, n_neutral=99, expectancy=0.5)
    assert (p_ok.n_wins + p_ok.n_losses) == 15
    assert round(p_ok.win_rate, 1) == 60.0
    assert p_ok.is_usable is True


# ── 5. shadow resolution reuses the same classifier ──────────────────

def test_shadow_resolution_reuses_real_outcome_logic(shadow_db):
    # Deterministic real classification via the single classifier
    status, ret, _sl = classify_outcome(
        direction="buy", entry_price=100.0, current_price=101.0,
        invalidation_price=None, window_expired=False,
    )
    assert status == "win" and ret == pytest.approx(1.0)

    sig = _signal("SIG-1", direction="buy", entry=100.0, price=100.0)
    # Simulate the REAL resolution having run (same classifier output)
    sig.status, sig.return_pct = status, ret

    paper_shadow.record_shadow_candidate(
        signal_id=sig.signal_id, symbol=sig.symbol, direction=sig.direction,
        pattern_id="P", n_decisive=6, win_rate=66.0, expectancy=0.2,
        real_reject_reason="gate real: decisivas=6<15", shadow_accept_reason="gate shadow ok",
    )
    summ = paper_shadow.resolve_shadow_trades([sig])
    assert summ["resolved"] == 1 and summ["win"] == 1

    conn = paper_shadow._conn()
    row = conn.execute("SELECT status, return_pct FROM paper_shadow_trades WHERE signal_id='SIG-1'").fetchone()
    conn.close()
    # Shadow status equals the SAME classifier's output — no second definition
    assert row["status"] == status == classify_outcome("buy", 100.0, 101.0, None, False)[0]


def test_shadow_pending_signal_stays_pending(shadow_db):
    sig = _signal("SIG-P")  # status defaults to "pending"
    paper_shadow.record_shadow_candidate(
        signal_id=sig.signal_id, symbol=sig.symbol, direction=sig.direction,
        pattern_id="P", n_decisive=6, win_rate=66.0, expectancy=0.2,
        real_reject_reason="r", shadow_accept_reason="s",
    )
    summ = paper_shadow.resolve_shadow_trades([sig])
    assert summ["resolved"] == 0


# ── 3 & 4. shadow never creates a real paper trade / advances LIVE ───

def test_shadow_does_not_create_real_paper_trade_or_advance_live(shadow_db, isolated_executor, monkeypatch):
    calls = {"exec": 0, "paper": 0, "result": 0}
    monkeypatch.setattr(auto_executor, "execute_proposal",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1))
    monkeypatch.setattr(auto_executor, "record_paper_trade",
                        lambda *a, **k: calls.__setitem__("paper", calls["paper"] + 1))
    monkeypatch.setattr(auto_executor, "record_trade_result",
                        lambda *a, **k: calls.__setitem__("result", calls["result"] + 1))

    before = auto_executor.get_config().get("paper_trades_total", 0)

    # Pattern passes shadow gate (decisive=6, WR=66, E>0) but fails real gate (decisive<15)
    key = _make_key("AAPL", "sell", "Sesion", ["A", "B", "C", "D"])
    pat = _pattern(key, n_wins=4, n_losses=2, expectancy=0.2)
    sig = _signal("SIG-SH", direction="sell")

    out = paper_shadow.run_shadow_cycle([sig], [pat])
    assert out["candidates_created"] == 1

    after = auto_executor.get_config().get("paper_trades_total", 0)
    assert before == 0 and after == 0                 # LIVE progress did NOT advance
    assert calls == {"exec": 0, "paper": 0, "result": 0}  # real executor never touched
    assert auto_executor.get_mode() == auto_executor.AutoMode.OFF


# ── 6 & 7. manual-promotion flagging, never automatic ────────────────

def test_ready_for_manual_promotion_flagged_but_not_auto(shadow_db, isolated_executor):
    key = _make_key("AAPL", "sell", "Sesion", ["A", "B", "C", "D"])
    sig = _signal("SIG-PR", direction="sell")
    paper_shadow.record_shadow_candidate(
        signal_id=sig.signal_id, symbol=sig.symbol, direction=sig.direction,
        pattern_id=key, n_decisive=6, win_rate=66.0, expectancy=0.2,
        real_reject_reason="gate real: decisivas=6<15", shadow_accept_reason="gate shadow ok",
    )

    # Not ready yet (real pattern still sub-threshold)
    weak = _pattern(key, n_wins=4, n_losses=2, expectancy=0.2)
    assert paper_shadow.check_promotions([weak]) == []

    # Now the REAL pattern reaches the ORIGINAL gate (decisive=15, WR=60, E>0)
    strong = _pattern(key, n_wins=9, n_losses=6, expectancy=0.5)
    assert strong.is_usable is True
    ready = paper_shadow.check_promotions([strong])
    assert key in ready

    stats = paper_shadow.get_shadow_stats()
    assert key in stats["ready_for_promotion"]

    # Promotion is a SUGGESTION only — the real executor was not promoted/activated
    assert auto_executor.get_mode() == auto_executor.AutoMode.OFF
    assert auto_executor.get_config().get("paper_trades_total", 0) == 0


def test_config_activated_at_persists(shadow_db):
    t1 = paper_shadow.get_config_activated_at()
    t2 = paper_shadow.get_config_activated_at()
    assert t1 == t2 and t1 > 0  # set once, then stable (out-of-sample cutoff)


def test_promotion_is_idempotent_and_manual(shadow_db):
    key = _make_key("AAPL", "sell", "Sesion", ["A", "B", "C", "D"])
    paper_shadow.record_shadow_candidate(
        signal_id="SIG-ID", symbol="AAPL", direction="sell", pattern_id=key,
        n_decisive=6, win_rate=66.0, expectancy=0.2,
        real_reject_reason="r", shadow_accept_reason="s",
    )
    strong = _pattern(key, n_wins=9, n_losses=6, expectancy=0.5)
    r1 = paper_shadow.check_promotions([strong])
    r2 = paper_shadow.check_promotions([strong])
    assert r1 == r2 == [key]  # stable / no duplicate promotion, remains a manual suggestion
