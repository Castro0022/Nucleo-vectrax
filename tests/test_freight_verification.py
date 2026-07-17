"""
Tests — cierre del ciclo VERIFICADO de freight.

Verifica que el cableado (verify_events + verification_ledger genérico) produce
Outcomes REALES contra la verdad objetiva del dominio (on_time / delay), los
persiste, y que el DomainScore acumulado refleja desempeño real (no el proxy de
coherencia). Usa un vault temporal (VECTRAX_VAULT_DIR) — no toca datos reales.

Run:  python -m pytest tests/test_freight_verification.py -v
"""
from __future__ import annotations

import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.learn.outcome_adapter import Outcome, OutcomeStatus


def _ev(event_type, **data):
    """Imita un FreightEvent (event_type + data) sin depender del simulador."""
    return types.SimpleNamespace(event_type=event_type, data=data, source="test", ts=0.0)


# ── 1. Ledger genérico (persistencia + agregación) ────────────────────

def test_verification_ledger_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    from core.learn import verification_ledger as vl
    vl.clear_domain("freight_logistics")
    assert vl.record_outcome(Outcome("p1", "freight_logistics", "SE|X", OutcomeStatus.WIN, 1.0))
    assert vl.record_outcome(Outcome("p2", "freight_logistics", "SE|X", OutcomeStatus.LOSS, -1.0))
    # PENDING no se persiste (nada verificado que guardar)
    assert not vl.record_outcome(Outcome("p3", "freight_logistics", "SE|X", OutcomeStatus.PENDING, 0.0))
    outs = vl.load_outcomes("freight_logistics")
    assert len(outs) == 2
    sc = vl.domain_score("freight_logistics")
    assert sc.n_decisive == 2 and sc.wins == 1 and sc.losses == 1
    assert sc.win_rate == 50.0


# ── 2. verify_events — verdad objetiva real (on_time / delay) ─────────

def test_verify_events_real_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    from connectors.freight import verification_cycle as vc
    from core.learn import verification_ledger as vl
    vl.clear_domain("freight_logistics")

    events = [
        _ev("delivery_complete", region="Southeast", carrier="FastHaul", on_time=True, transit_days=3),
        _ev("delivery_complete", region="Southeast", carrier="FastHaul", on_time=True),
        _ev("delivery_complete", region="Southeast", carrier="FastHaul", on_time=False),
        _ev("delay_reported", region="NE", carrier="Elite", delay_hours=12, cause="weather"),
        _ev("quote_request", region="Southeast", carrier="FastHaul"),   # ignorado: sin verdad
        _ev("capacity_update", region="NE"),                            # ignorado
    ]
    score = vc.verify_events(events, record=True)
    # 4 eventos con verdad (3 delivery + 1 delay) → 2 win, 2 loss
    assert score.n_total == 4
    assert score.n_decisive == 4
    assert (score.wins, score.losses) == (2, 2)
    assert score.win_rate == 50.0

    # acumulado persistido en el ledger
    acc = vc.verified_score()
    assert acc.n_decisive == 4

    # criterio validado por subject (lane|carrier)
    subs = vc.verified_subjects(min_decisive=1)
    assert "Southeast|FastHaul" in subs
    se = subs["Southeast|FastHaul"]
    assert (se.wins, se.losses) == (2, 1)      # 2 a tiempo, 1 tarde
    assert "NE|Elite" in subs and subs["NE|Elite"].losses == 1


def test_verify_events_accepts_dict_events(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    from connectors.freight import verification_cycle as vc
    from core.learn import verification_ledger as vl
    vl.clear_domain("freight_logistics")
    events = [
        {"event_type": "delivery_complete", "data": {"region": "W", "carrier": "C", "on_time": True}},
        {"event_type": "delivery_complete", "data": {"region": "W", "carrier": "C", "on_time": False}},
    ]
    sc = vc.verify_events(events, record=False)
    assert sc.n_decisive == 2 and sc.wins == 1 and sc.losses == 1
    # record=False → no persiste
    assert vc.verified_score().n_decisive == 0


# ── 3. No fabrica: eventos sin verdad → nada verificado ───────────────

def test_verify_events_without_outcome_truth_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    from connectors.freight import verification_cycle as vc
    from core.learn import verification_ledger as vl
    vl.clear_domain("freight_logistics")
    events = [
        _ev("quote_request", region="Southeast", carrier="FastHaul"),
        _ev("load_booking", region="NE", carrier="Elite"),
        _ev("capacity_update", region="W"),
    ]
    sc = vc.verify_events(events, record=True)
    assert sc.n_total == 0 and sc.n_decisive == 0
    assert vc.verified_score().n_decisive == 0
