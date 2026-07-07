"""Tests for the regime-based signal filter config (connectors/etoro/signal_filters.py)."""
from connectors.etoro import signal_filters as sf


class Sig:
    def __init__(self, symbol, direction, conds, conditions_met, status, return_pct=None, timestamp=0.0):
        self.symbol = symbol
        self.direction = direction
        self.conditions_names = conds
        self.conditions_met = conditions_met
        self.status = status
        self.return_pct = return_pct
        self.timestamp = timestamp


TREND = "DIRECCION_TENDENCIA"
ALIN = "ALINEACION_TEMPORAL"
VOL = "VOLUMEN_RELATIVO"
PREC = "PRECIO_EN_ZONA"


def test_asset_class():
    assert sf.asset_class("BTC") == "crypto"
    assert sf.asset_class("BTCUSD") == "crypto"
    assert sf.asset_class("ETH") == "crypto"
    assert sf.asset_class("AAPL") == "equity"
    assert sf.asset_class("TSLA") == "equity"


def test_regime_direction():
    assert sf.regime_direction("BTC") == "buy"     # crypto → long
    assert sf.regime_direction("AAPL") == "sell"   # equity → short


def test_variant_v1_follows_regime_and_requires_trend():
    # equity short + trend → accepted
    assert sf.variant_v1(Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "win")) is True
    # equity short WITHOUT trend → rejected
    assert sf.variant_v1(Sig("AAPL", "sell", [ALIN, PREC, VOL], 3, "win")) is False
    # equity LONG (contra-regime) → rejected even with trend
    assert sf.variant_v1(Sig("AAPL", "buy", [ALIN, TREND, VOL], 3, "win")) is False
    # crypto LONG + trend → accepted
    assert sf.variant_v1(Sig("BTC", "buy", [ALIN, TREND, VOL], 3, "win")) is True
    # crypto SHORT (contra-regime) → rejected
    assert sf.variant_v1(Sig("BTC", "sell", [ALIN, TREND, VOL], 3, "win")) is False


def test_variant_v2_drops_precio_and_requires_3of4():
    base = Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "win")
    assert sf.variant_v2(base) is True
    # with PRECIO_EN_ZONA → rejected
    assert sf.variant_v2(Sig("AAPL", "sell", [ALIN, TREND, PREC], 3, "win")) is False
    # 4/4 → rejected (worst combo)
    assert sf.variant_v2(Sig("AAPL", "sell", [ALIN, TREND, VOL, PREC], 4, "win")) is False
    # contra-regime (fails v1) → rejected
    assert sf.variant_v2(Sig("AAPL", "buy", [ALIN, TREND, VOL], 3, "win")) is False


def test_accepting_variants():
    s = Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "win")
    assert set(sf.accepting_variants(s)) == {"V1_follow_trend", "V2_aggressive"}
    s2 = Sig("AAPL", "buy", [ALIN, TREND, VOL], 3, "win")  # contra-regime
    assert sf.accepting_variants(s2) == []


def test_variant_report_structure_and_counts():
    sigs = [
        Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "win", 1.0),   # V1 & V2
        Sig("AAPL", "sell", [ALIN, TREND, PREC], 3, "loss", -0.5),  # V1 only (has PRECIO)
        Sig("AAPL", "buy", [ALIN, TREND, VOL], 3, "win", 0.8),    # contra-regime → neither
        Sig("BTC", "buy", [ALIN, TREND, VOL], 3, "win", 2.0),     # V1 & V2 (crypto long)
        Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "neutral", 0.1),  # not decisive
    ]
    rep = sf.variant_report(sigs)
    assert set(rep.keys()) == {"base_all", "V1_follow_trend", "V2_aggressive"}
    for k in rep:
        assert set(rep[k].keys()) == {"decisive", "win_rate", "wilson_lb", "expectancy"}
    # V1: s1(win), s2(loss), s4(win) → decisive 3, WR 66.7
    assert rep["V1_follow_trend"]["decisive"] == 3
    assert rep["V1_follow_trend"]["win_rate"] == 66.7
    # V2: s1(win), s4(win) → decisive 2, WR 100
    assert rep["V2_aggressive"]["decisive"] == 2
    assert rep["V2_aggressive"]["win_rate"] == 100.0


def test_variant_report_since_ts_filters_forward():
    old = Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "win", 1.0, timestamp=100.0)
    new = Sig("AAPL", "sell", [ALIN, TREND, VOL], 3, "loss", -0.5, timestamp=200.0)
    rep_all = sf.variant_report([old, new])
    rep_fwd = sf.variant_report([old, new], since_ts=150.0)
    assert rep_all["V1_follow_trend"]["decisive"] == 2
    assert rep_fwd["V1_follow_trend"]["decisive"] == 1  # only 'new' (ts=200 >= 150)
