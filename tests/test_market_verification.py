"""
Tests — connectors/etoro/verification_cycle (ciclo verificado de market).

Cubre: verify_signals (win/loss decisivos → ledger; pending/entry inválido → no),
subject = símbolo, y run_market_verification con dedup por signal_id (no
doble-cuenta en el ledger acumulado).

Run:  python -m pytest tests/test_market_verification.py -v
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from connectors.etoro import verification_cycle as VC
from core.learn import verification_ledger as vledger


def _sig(sid, symbol, direction, entry, current, status, invalidation=None):
    """MarketSignal fake resuelta (solo los campos que usa el ciclo)."""
    return SimpleNamespace(
        signal_id=sid, symbol=symbol, direction=direction,
        entry_price=entry, price=entry, invalidation_price=invalidation,
        outcome_price=current, status=status,
    )


@pytest.fixture(autouse=True)
def _tmp_vault(tmp_path, monkeypatch):
    # Ledger + estado de dedup en un vault temporal (hermético).
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    vledger.clear_domain("market")
    yield
    vledger.clear_domain("market")


def test_verify_signals_records_decisive():
    sigs = [
        _sig("SIG-1", "BTC", "buy", 100.0, 102.0, "win"),    # +2% → WIN
        _sig("SIG-2", "AAPL", "buy", 100.0, 98.0, "loss"),   # -2% → LOSS
        _sig("SIG-3", "ETH", "sell", 100.0, 98.0, "win"),    # sell, precio baja → WIN
    ]
    score = VC.verify_signals(sigs, record=True)
    assert score.n_decisive == 3
    assert score.wins == 2 and score.losses == 1

    acc = vledger.domain_score("market")
    assert acc.n_decisive == 3

    # subject = símbolo (evidencia validada por símbolo).
    subs = VC.verified_subjects(min_decisive=1)
    assert set(subs.keys()) == {"BTC", "AAPL", "ETH"}


def test_verify_signals_skips_pending_and_invalid():
    sigs = [
        _sig("SIG-4", "BTC", "buy", 100.0, None, "pending"),   # sin precio → PENDING
        _sig("SIG-5", "BTC", "buy", 0.0, 100.0, "neutral"),    # entry inválido → PENDING
    ]
    score = VC.verify_signals(sigs, record=True)
    assert score.n_decisive == 0
    assert vledger.domain_score("market").n_total == 0


def test_run_market_verification_dedups_by_signal_id():
    resolved = [
        _sig("SIG-A", "BTC", "buy", 100.0, 102.0, "win"),
        _sig("SIG-B", "AAPL", "buy", 100.0, 97.0, "loss"),
    ]
    with patch("connectors.etoro.signal_recorder.load_signals", return_value=resolved):
        s1 = VC.run_market_verification()
        assert s1.n_decisive == 2
        s2 = VC.run_market_verification()   # ya verificadas → nada nuevo
        assert s2.n_total == 0

    acc = vledger.domain_score("market")
    assert acc.n_decisive == 2              # NO se duplica en el acumulado


def test_run_market_verification_empty_when_no_resolved():
    # Solo pendientes → no hay nada verificable.
    pend = [_sig("SIG-P", "BTC", "buy", 100.0, None, "pending")]
    with patch("connectors.etoro.signal_recorder.load_signals", return_value=pend):
        s = VC.run_market_verification()
    assert s.n_total == 0
    assert vledger.domain_score("market").n_total == 0
