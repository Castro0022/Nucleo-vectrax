"""
Tests — Lector de duración (core/trend_reader.py).

Verifica que los deltas temporales se calculan desde las anclas reales
(first_seen ISO de gravity, timestamp epoch de convergencias, resolved_ts epoch
del verification ledger), que `days_since` acepta ambos formatos, y que todo es
defensivo (si una fuente falla, no rompe).

Run:  python -m pytest tests/test_trend_reader.py -v
"""
from __future__ import annotations

import datetime
import os
import sys
import types
from contextlib import ExitStack
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import trend_reader as TR


# ── Helpers ─────────────────────────────────────────────────────────────

def _iso_ago(days: float) -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)).isoformat()


def _epoch_ago(days: float) -> float:
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)).timestamp()


def _rec(**kw):
    base = dict(fingerprint="fp", domain="market", intent="",
                first_seen="", last_seen="", hits=1, freq=0.0)
    base.update(kw)
    return types.SimpleNamespace(**base)


class _GI:
    def __init__(self, by_dom=None, get_map=None, top=None, stats=None):
        self._by = by_dom or {}
        self._get = get_map or {}
        self._top = top or {}
        self._stats = stats or {}

    def by_domain(self, d):
        return self._by.get(d, [])

    def get(self, fp):
        return self._get.get(fp)

    def top_stars(self, n=10, domain=None):
        return self._top.get(domain, [])[:n]

    def domain_stats(self):
        return self._stats


# ── 1. days_since acepta epoch, ISO y None ──────────────────────────────

def test_days_since_formats():
    assert TR.days_since(None) is None
    assert TR.days_since("") is None
    assert TR.days_since("garbage") is None
    assert abs(TR.days_since(_epoch_ago(5)) - 5) < 0.2
    assert abs(TR.days_since(_iso_ago(3)) - 3) < 0.2
    assert TR.days_since(_epoch_ago(-2)) == 0.0     # futuro → 0, nunca negativo


# ── 2. Duración por patrón ──────────────────────────────────────────────

def test_get_pattern_duration():
    rec = _rec(fingerprint="fp1", domain="market", intent="BTC",
               first_seen=_iso_ago(10), last_seen=_iso_ago(1), hits=50, freq=5.0)
    gi = _GI(get_map={"fp1": rec})
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        d = TR.get_pattern_duration("fp1")
        assert d["domain"] == "market" and d["intent"] == "BTC"
        assert abs(d["days_observed"] - 10) < 0.2
        assert abs(d["days_since_last"] - 1) < 0.2
        assert TR.get_pattern_duration("missing") is None


def test_top_pattern_durations():
    recs = [
        _rec(fingerprint="a", domain="freight_logistics", intent="rate_change",
             first_seen=_iso_ago(9), last_seen=_iso_ago(0.5)),
        _rec(fingerprint="b", domain="freight_logistics", intent="quote_request",
             first_seen=_iso_ago(4), last_seen=_iso_ago(0.2)),
    ]
    gi = _GI(top={"freight_logistics": recs})
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        out = TR.top_pattern_durations("freight_logistics", n=2)
        assert [o["intent"] for o in out] == ["rate_change", "quote_request"]
        assert abs(out[0]["days_observed"] - 9) < 0.3


# ── 3. Edad de convergencias + filtro por dominio ───────────────────────

def test_active_convergence_ages_and_filter():
    rows = [
        {"key": "a:b", "intent": "x", "domains": '["market"]',
         "timestamp": _epoch_ago(5), "combined_cc": 0.5, "combined_hits": 10},
        {"key": "c:d", "intent": "y", "domains": '["freight_logistics","market"]',
         "timestamp": _epoch_ago(2), "combined_cc": 0.4, "combined_hits": 8},
    ]
    with patch("core.learn.convergence_history.get_active", return_value=rows):
        allc = TR.active_convergence_ages()
        assert len(allc) == 2
        assert allc[0]["key"] == "a:b"                 # más antigua primero
        assert abs(allc[0]["age_days"] - 5) < 0.3
        assert len(TR.active_convergence_ages(domain="market")) == 2
        fr = TR.active_convergence_ages(domain="freight_logistics")
        assert len(fr) == 1 and fr[0]["key"] == "c:d"


# ── 4. Span de outcomes verificados ─────────────────────────────────────

def test_domain_outcome_span():
    outs = [
        types.SimpleNamespace(resolved_ts=_epoch_ago(8)),
        types.SimpleNamespace(resolved_ts=_epoch_ago(1)),
        types.SimpleNamespace(resolved_ts=None),        # ignorado
    ]
    with patch("core.learn.verification_ledger.load_outcomes", return_value=outs):
        sp = TR.domain_outcome_span("market")
        assert sp["n_outcomes"] == 2
        assert abs(sp["first_days_ago"] - 8) < 0.3
        assert abs(sp["last_days_ago"] - 1) < 0.3
        assert abs(sp["span_days"] - 7) < 0.3
    with patch("core.learn.verification_ledger.load_outcomes", return_value=[]):
        assert TR.domain_outcome_span("market") is None


# ── 5. Composición por dominio ──────────────────────────────────────────

def test_get_domain_duration_composes():
    recs = [
        _rec(first_seen=_iso_ago(12), last_seen=_iso_ago(2)),
        _rec(first_seen=_iso_ago(3), last_seen=_iso_ago(0.5)),
    ]
    outs = [types.SimpleNamespace(resolved_ts=_epoch_ago(6))]
    rows = [{"key": "a:b", "intent": "x", "domains": '["market"]',
             "timestamp": _epoch_ago(4)}]
    with ExitStack() as st:
        st.enter_context(patch("core.learn.gravity_engine.get_gravity_index",
                               return_value=_GI(by_dom={"market": recs})))
        st.enter_context(patch("core.learn.verification_ledger.load_outcomes",
                               return_value=outs))
        st.enter_context(patch("core.learn.convergence_history.get_active",
                               return_value=rows))
        d = TR.get_domain_duration("market")

    assert d["n_patterns"] == 2
    assert abs(d["observing_since_days"] - 12) < 0.3        # earliest first_seen
    assert abs(d["days_since_last_activity"] - 0.5) < 0.3   # last_seen más reciente
    assert d["outcomes"]["n_outcomes"] == 1
    assert d["active_convergences"] == 1
    assert abs(d["oldest_convergence_days"] - 4) < 0.3


# ── 6. Digest grounded ──────────────────────────────────────────────────

def test_build_duration_digest_grounded():
    recs = [_rec(first_seen=_iso_ago(10), last_seen=_iso_ago(1))]
    top = [_rec(fingerprint="fp", domain="market", intent="BTC",
                first_seen=_iso_ago(9), last_seen=_iso_ago(0.5))]
    outs = [types.SimpleNamespace(resolved_ts=_epoch_ago(6))]
    rows = [{"key": "a:b", "intent": "x", "domains": '["market"]',
             "timestamp": _epoch_ago(4)}]
    with ExitStack() as st:
        st.enter_context(patch("core.learn.gravity_engine.get_gravity_index",
                               return_value=_GI(by_dom={"market": recs},
                                                top={"market": top})))
        st.enter_context(patch("core.learn.verification_ledger.load_outcomes",
                               return_value=outs))
        st.enter_context(patch("core.learn.convergence_history.get_active",
                               return_value=rows))
        txt = TR.build_duration_digest(domain="market", lang="es")

    assert "DESDE CU" in txt          # header
    assert "market" in txt
    assert "10 días" in txt           # observando desde hace 10 días
    assert "BTC" in txt               # patrón más activo con antigüedad
    assert "verificados desde hace 6" in txt
    assert "convergencia" in txt


def test_build_duration_digest_empty_when_no_data():
    with ExitStack() as st:
        st.enter_context(patch("core.learn.gravity_engine.get_gravity_index",
                               return_value=_GI()))
        st.enter_context(patch("core.learn.verification_ledger.load_outcomes",
                               return_value=[]))
        st.enter_context(patch("core.learn.convergence_history.get_active",
                               return_value=[]))
        assert TR.build_duration_digest(domain="market") == ""


# ── 7. Robustez defensiva ───────────────────────────────────────────────

def test_defensive_on_source_failure():
    with patch("core.learn.gravity_engine.get_gravity_index",
               side_effect=RuntimeError("boom")):
        assert TR.get_pattern_duration("x") is None
        assert TR.top_pattern_durations("market") == []
    with patch("core.learn.convergence_history.get_active",
               side_effect=RuntimeError("boom")):
        assert TR.active_convergence_ages() == []
    with patch("core.learn.verification_ledger.load_outcomes",
               side_effect=RuntimeError("boom")):
        assert TR.domain_outcome_span("market") is None
    with ExitStack() as st:
        for t in ("core.learn.gravity_engine.get_gravity_index",
                  "core.learn.verification_ledger.load_outcomes",
                  "core.learn.convergence_history.get_active"):
            st.enter_context(patch(t, side_effect=RuntimeError("boom")))
        d = TR.get_domain_duration("market")     # no debe lanzar
        assert d["n_patterns"] == 0 and d["active_convergences"] == 0


# ── 8. Antigüedad ligera por dominio (solo gravity) ─────────────────────

def test_domain_observing_since_days():
    gi = _GI(by_dom={"market": [_rec(first_seen=_iso_ago(9)),
                                _rec(first_seen=_iso_ago(3))]})
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        assert abs(TR.domain_observing_since_days("market") - 9) < 0.3   # el más antiguo
        assert TR.domain_observing_since_days("empty") is None
    with patch("core.learn.gravity_engine.get_gravity_index",
               side_effect=RuntimeError("boom")):
        assert TR.domain_observing_since_days("market") is None
