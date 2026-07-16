"""
Tests — OutcomeAdapter universal + invariancia del núcleo cognitivo.

Demuestra que el MISMO núcleo (`score_outcomes` / `evaluate`) puntúa dominios
sin relación entre sí (market, freight_logistics y un dominio genérico devops)
a través de adaptadores intercambiables. Solo cambia el adaptador; el núcleo
—y su métrica común— no cambia. Esa invariancia es la evidencia de que "la
arquitectura aprende".

Run:  python -m pytest tests/test_outcome_adapter.py -v
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.learn.outcome_adapter import (
    DomainScore,
    Outcome,
    OutcomeStatus,
    Prediction,
    ThresholdOutcomeAdapter,
    clear_adapters,
    cross_domain_scoreboard,
    evaluate,
    get_adapter,
    register_adapter,
    registered_domains,
    score_outcomes,
)
from connectors.etoro.trading_outcome_adapter import TradingOutcomeAdapter
from connectors.freight.freight_outcome_adapter import FreightOutcomeAdapter


def _pred(domain, subject, predicted="", **ctx):
    return Prediction(
        domain=domain, subject=subject, predicted=predicted,
        prediction_id=f"{domain}:{subject}", context=ctx,
    )


# ── 1. Núcleo invariante ──────────────────────────────────────────────

def test_score_outcomes_core():
    outs = [
        Outcome("1", "d", "s", OutcomeStatus.WIN, 2.0),
        Outcome("2", "d", "s", OutcomeStatus.WIN, 1.0),
        Outcome("3", "d", "s", OutcomeStatus.LOSS, -1.0),
        Outcome("4", "d", "s", OutcomeStatus.NEUTRAL, 0.0),
        Outcome("5", "d", "s", OutcomeStatus.PENDING, 0.0),
    ]
    sc = score_outcomes("d", outs, baseline=0.5)
    assert sc.n_total == 5
    assert sc.n_decisive == 3          # 2 win + 1 loss
    assert (sc.wins, sc.losses, sc.neutrals, sc.pending) == (2, 1, 1, 1)
    assert sc.win_rate == round(2 / 3 * 100, 1)          # 66.7
    assert sc.accuracy == round(2 / 3, 4)
    assert sc.expectancy == round((2.0 + 1.0 - 1.0) / 3, 4)   # media sobre decisivos
    assert sc.lift_vs_baseline == round(2 / 3 - 0.5, 4)  # +0.1667


# ── 2. Trading adapter (envuelve el classify_outcome REAL) ────────────

def test_trading_adapter_win_loss_neutral():
    a = TradingOutcomeAdapter()
    assert a.domain == "market"
    win = a.resolve(_pred("market", "BTC", "buy", direction="buy", entry_price=100.0),
                    {"current_price": 100.4})   # +0.4% ≥ 0.3 → win
    loss = a.resolve(_pred("market", "BTC", "buy", direction="buy", entry_price=100.0),
                     {"current_price": 99.7})    # -0.3% ≤ -0.2 → loss
    neu = a.resolve(_pred("market", "BTC", "buy", direction="buy", entry_price=100.0),
                    {"current_price": 100.1})    # +0.1% → neutral
    pend = a.resolve(_pred("market", "BTC", "buy", direction="buy", entry_price=100.0),
                     {})                          # sin precio → pending
    assert win.status is OutcomeStatus.WIN and win.score > 0
    assert loss.status is OutcomeStatus.LOSS
    assert neu.status is OutcomeStatus.NEUTRAL
    assert pend.status is OutcomeStatus.PENDING


def test_trading_adapter_invalidation_is_loss():
    a = TradingOutcomeAdapter()
    # buy con invalidación 98: precio cae a 97 → SL tocado → loss
    out = a.resolve(
        _pred("market", "BTC", "buy", direction="buy", entry_price=100.0,
              invalidation_price=98.0),
        {"current_price": 97.0},
    )
    assert out.status is OutcomeStatus.LOSS
    assert out.evidence.get("sl_touched") is True


# ── 3. Freight adapter (verdad objetiva real: on_time / delay) ────────

def test_freight_adapter_outcomes():
    a = FreightOutcomeAdapter()
    assert a.domain == "freight_logistics"
    on_time = a.resolve(_pred("freight_logistics", "SE|FastHaul", "on_time"),
                        {"event_type": "delivery_complete", "on_time": True, "transit_days": 3})
    late = a.resolve(_pred("freight_logistics", "SE|FastHaul", "on_time"),
                     {"event_type": "delivery_complete", "on_time": False})
    delay = a.resolve(_pred("freight_logistics", "NE|Elite", "on_time"),
                      {"event_type": "delay_reported", "delay_hours": 12, "cause": "weather"})
    pend = a.resolve(_pred("freight_logistics", "X", "on_time"),
                     {"event_type": "quote_request"})
    assert on_time.status is OutcomeStatus.WIN and on_time.score == 1.0
    assert late.status is OutcomeStatus.LOSS and late.score == -1.0
    assert delay.status is OutcomeStatus.LOSS and delay.score == -12.0
    assert pend.status is OutcomeStatus.PENDING


# ── 4. Adaptador genérico por umbral (para CUALQUIER dominio) ─────────

def test_threshold_adapter_generic():
    # devops: recovery_time < SLA(60) → win (menor es mejor)
    devops = ThresholdOutcomeAdapter("devops", "recovery_s", target=60.0, higher_is_better=False)
    assert devops.resolve(_pred("devops", "api"), {"recovery_s": 40.0}).status is OutcomeStatus.WIN
    assert devops.resolve(_pred("devops", "api"), {"recovery_s": 90.0}).status is OutcomeStatus.LOSS
    assert devops.resolve(_pred("devops", "api"), {}).status is OutcomeStatus.PENDING
    # ventas: conversión > baseline(0.2) → win (mayor es mejor)
    sales = ThresholdOutcomeAdapter("sales", "conversion", target=0.2, higher_is_better=True)
    assert sales.resolve(_pred("sales", "promoA"), {"conversion": 0.35}).status is OutcomeStatus.WIN
    assert sales.resolve(_pred("sales", "promoB"), {"conversion": 0.05}).status is OutcomeStatus.LOSS


# ── 5. Registro de adaptadores ────────────────────────────────────────

def test_registry():
    clear_adapters()
    register_adapter(TradingOutcomeAdapter())
    register_adapter(FreightOutcomeAdapter())
    assert set(registered_domains()) == {"market", "freight_logistics"}
    assert get_adapter("market").domain == "market"
    assert get_adapter("freight_logistics").domain == "freight_logistics"
    assert get_adapter("inexistente") is None
    clear_adapters()


# ── 6. INVARIANCIA — el MISMO núcleo puntúa dominios sin relación ─────

def test_core_invariance_across_domains():
    market_pairs = [
        (_pred("market", "BTC", "buy", direction="buy", entry_price=100.0), {"current_price": 101.0}),
        (_pred("market", "ETH", "buy", direction="buy", entry_price=50.0), {"current_price": 50.5}),
        (_pred("market", "SOL", "buy", direction="buy", entry_price=20.0), {"current_price": 19.8}),
    ]
    freight_pairs = [
        (_pred("freight_logistics", "A", "on_time"), {"event_type": "delivery_complete", "on_time": True}),
        (_pred("freight_logistics", "B", "on_time"), {"event_type": "delivery_complete", "on_time": True}),
        (_pred("freight_logistics", "C", "on_time"), {"event_type": "delay_reported", "delay_hours": 5}),
    ]
    devops = ThresholdOutcomeAdapter("devops", "recovery_s", 60.0, higher_is_better=False)
    devops_pairs = [
        (_pred("devops", "svc1"), {"recovery_s": 30.0}),
        (_pred("devops", "svc2"), {"recovery_s": 45.0}),
        (_pred("devops", "svc3"), {"recovery_s": 120.0}),
    ]

    # MISMA función `evaluate` para los tres dominios — solo cambia el adaptador.
    s_market = evaluate(TradingOutcomeAdapter(), market_pairs)
    s_freight = evaluate(FreightOutcomeAdapter(), freight_pairs)
    s_devops = evaluate(devops, devops_pairs)

    for s, dom in ((s_market, "market"), (s_freight, "freight_logistics"), (s_devops, "devops")):
        assert isinstance(s, DomainScore)
        assert s.domain == dom
        assert s.n_decisive == 3
        assert (s.wins, s.losses) == (2, 1)
        # La MISMA métrica común sale idéntica en los tres dominios dispares.
        assert s.win_rate == round(2 / 3 * 100, 1)
        assert s.accuracy == round(2 / 3, 4)

    board = cross_domain_scoreboard([s_market, s_freight, s_devops])
    assert "market" in board and "freight_logistics" in board and "devops" in board
