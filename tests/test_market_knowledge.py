"""
Tests — Etapa 1: conocimiento técnico formal sobre experiencia ya vivida.

Cubre:
  - ta_knowledge.compute_knowledge: catálogo completo (174 funciones, sin
    Math Operators/Math Transform), patrones de vela con signo (no booleano),
    None ante histórico insuficiente (nunca un valor inventado).
  - knowledge_snapshot.snapshot_at: corte de fuga (nunca usa una vela
    posterior a as_of_ts), profundidad insuficiente declarada explícitamente.
  - knowledge_backfill.backfill_signal_knowledge: solo señales YA
    RESUELTAS, idempotente (no recalcula lo ya enriquecido salvo force),
    y NO toca pattern_memory/gravity/verification_ledger.

Run: python -m pytest tests/test_market_knowledge.py -v
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

import connectors.etoro.etoro_client  # noqa: F401 - asegura el submódulo importado para patch()
from connectors.market import ta_knowledge as TAK
from connectors.etoro import knowledge_snapshot as KS
from connectors.etoro import knowledge_backfill as KB
from connectors.etoro import knowledge_ledger as KL


def _synthetic_series(n=120, seed=42):
    """Serie determinista de velas (sin numpy.random para no depender de
    semillas entre versiones): un paseo simple con vaivén conocido."""
    close = [100.0]
    for i in range(1, n):
        close.append(close[-1] + ((i % 7) - 3) * 0.3)
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    open_ = [close[i - 1] if i > 0 else close[0] for i in range(n)]
    volume = [1000.0 + (i % 5) * 50 for i in range(n)]
    return open_, high, low, close, volume


# ── ta_knowledge ─────────────────────────────────────────────────────

class TestComputeKnowledge:
    def test_catalog_excludes_only_non_computable_functions(self):
        """Regla fijada: el conocimiento no se preselecciona por utilidad
        humana. Solo se excluye lo que NO COMPUTA sobre precio real (NaN/inf
        estructural) — todo lo demás entra, tenga o no literatura de TA
        detrás."""
        names = TAK.known_function_names()
        # Patrones/tendencia/momentum/volumen SÍ deben estar.
        assert "CDLHAMMER" in names
        assert "RSI" in names
        assert "MACD" in names
        assert "OBV" in names
        # Antes excluidas por categoría completa — ahora SÍ deben estar:
        # tienen valor real computable, aunque no tengan interpretación de
        # manual (SIN, ADD) o sí la tengan (LN, MAX/MAXINDEX ~ base de Aroon).
        assert "SIN" in names
        assert "LN" in names
        assert "ADD" in names
        assert "MAX" in names
        assert "MAXINDEX" in names
        # Solo estas 5 quedan fuera: NaN/inf estructural, no relevancia.
        for excluded in ("ACOS", "ASIN", "EXP", "COSH", "SINH"):
            assert excluded not in names
        assert len(names) == 196

    def test_excluded_functions_are_structurally_non_computable_on_real_price(self):
        """Verifica la ÚNICA razón admitida para excluir algo: que no
        produzca un valor utilizable sobre precio real (no que se le juzgue
        poco relevante)."""
        import numpy as np
        from talib import abstract

        close = np.array([60000.0, 61000.0, 59500.0, 62000.0] * 10)
        for name in ("ACOS", "ASIN"):
            out = abstract.Function(name)({"close": close})
            assert np.isnan(out[-1]), f"{name} debería ser NaN sobre precio real"
        for name in ("EXP", "COSH", "SINH"):
            out = abstract.Function(name)({"close": close})
            assert np.isinf(out[-1]), f"{name} debería ser inf sobre precio real"

    def test_computes_full_catalog_on_synthetic_series(self):
        o, h, l, c, v = _synthetic_series()
        features = TAK.compute_knowledge(o, h, l, c, v)
        assert "RSI" in features
        assert features["RSI"] is not None
        assert isinstance(features["RSI"], float)
        # MACD tiene 3 salidas → 3 claves con sufijo.
        assert "MACD_macd" in features
        assert "MACD_macdsignal" in features
        assert "MACD_macdhist" in features
        # Patrón de vela: entero con signo, NO booleano.
        assert "CDLHAMMER" in features
        assert features["CDLHAMMER"] in (None, -200, -100, 0, 100, 200)
        assert not isinstance(features["CDLHAMMER"], bool)

    def test_insufficient_history_yields_none_not_fabricated(self):
        o, h, l, c, v = _synthetic_series(n=3)  # muy corto para casi todo
        features = TAK.compute_knowledge(o, h, l, c, v)
        # Con 3 barras, RSI(14) no es calculable → None, nunca un número.
        assert features.get("RSI") is None

    def test_too_short_series_returns_empty(self):
        assert TAK.compute_knowledge([1], [1], [1], [1], [1]) == {}

    def test_no_talib_returns_empty_gracefully(self):
        with patch.object(TAK, "TALIB_AVAILABLE", False):
            o, h, l, c, v = _synthetic_series()
            assert TAK.compute_knowledge(o, h, l, c, v) == {}


# ── knowledge_snapshot: corte de fuga ───────────────────────────────

def _candle(t, o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "time": t}


class TestSnapshotLeakage:
    def _mock_candles(self, n=120, poison_future=False):
        o, h, l, c, v = _synthetic_series(n)
        candles = [
            _candle(1_700_000_000 + i * 3600, o[i], h[i], l[i], c[i], v[i])
            for i in range(n)
        ]
        if poison_future:
            # Vela "del futuro" respecto a as_of_ts, con un valor extremo:
            # si se filtrara mal, cambiaría el resultado de forma detectable.
            candles.append(_candle(1_700_000_000 + n * 3600 + 3600, 1e6, 1e6, 1e6, 1e6, 1e6))
        return candles

    def test_future_candle_never_used(self):
        as_of_ts = 1_700_000_000 + 119 * 3600  # justo la última vela "real"

        with patch("connectors.etoro.etoro_client.get_candles") as mock_get:
            mock_get.return_value = {
                "success": True,
                "candles": self._mock_candles(n=120, poison_future=False),
            }
            clean = KS.snapshot_timeframe(instrument_id=1, interval="OneHour", as_of_ts=as_of_ts)

        with patch("connectors.etoro.etoro_client.get_candles") as mock_get:
            mock_get.return_value = {
                "success": True,
                "candles": self._mock_candles(n=120, poison_future=True),
            }
            poisoned = KS.snapshot_timeframe(instrument_id=1, interval="OneHour", as_of_ts=as_of_ts)

        assert clean["available"] and poisoned["available"]
        assert clean["bars_used"] == poisoned["bars_used"]
        assert clean["features"]["RSI"] == poisoned["features"]["RSI"]

    def test_as_of_ts_older_than_available_window_is_insufficient(self):
        with patch("connectors.etoro.etoro_client.get_candles") as mock_get:
            mock_get.return_value = {"success": True, "candles": self._mock_candles(n=120)}
            result = KS.snapshot_timeframe(
                instrument_id=1, interval="OneHour", as_of_ts=1_600_000_000,  # muy anterior
            )
        assert result["available"] is False
        assert result["insufficient_depth"] is True
        assert result["features"] == {}

    def test_snapshot_at_unresolved_symbol_never_invents_data(self):
        with patch("connectors.etoro.etoro_client.get_instrument_id", return_value=None):
            out = KS.snapshot_at("NOEXISTE", as_of_ts=1_700_000_000)
        assert set(out.keys()) == set(KS.DEFAULT_TIMEFRAMES)
        for tf_data in out.values():
            assert tf_data["available"] is False
            assert tf_data["features"] == {}


# ── knowledge_ledger: append-only, unido por signal_id, nunca en signal_recorder ──

class TestKnowledgeLedger:
    @pytest.fixture(autouse=True)
    def _tmp_vault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
        KL.clear()
        yield
        KL.clear()

    def test_record_and_get_by_signal_id(self):
        KL.record_features("SIG-1", {"OneHour": {"RSI": 50.0}})
        entry = KL.get_features("SIG-1")
        assert entry is not None
        assert entry["features"]["OneHour"]["RSI"] == 50.0
        assert entry["signal_id"] == "SIG-1"

    def test_unknown_signal_returns_none(self):
        assert KL.get_features("NUNCA-EXISTIO") is None
        assert KL.has_features("NUNCA-EXISTIO") is False

    def test_recompute_appends_never_overwrites_in_place(self):
        """Recalcular (force) escribe una línea NUEVA — nunca reescribe el
        archivo. La lectura devuelve la más reciente; el historial previo
        queda en el archivo, no se pierde."""
        KL.record_features("SIG-1", {"OneHour": {"RSI": 40.0}})
        path = KL._path()
        size_after_first = os.path.getsize(path)

        KL.record_features("SIG-1", {"OneHour": {"RSI": 55.0}})
        size_after_second = os.path.getsize(path)

        # El archivo CRECIÓ (append), no se reescribió del mismo tamaño.
        assert size_after_second > size_after_first
        # Dos líneas en el archivo (ambas conservadas).
        with open(path) as f:
            assert len(f.readlines()) == 2
        # La lectura sirve la MÁS RECIENTE.
        assert KL.get_features("SIG-1")["features"]["OneHour"]["RSI"] == 55.0

    def test_does_not_touch_signal_recorder_file(self):
        """El ledger de conocimiento vive en su propio archivo — nunca
        escribe en etoro_signals.jsonl."""
        with patch("connectors.etoro.signal_recorder.update_signal") as mock_update:
            KL.record_features("SIG-1", {"OneHour": {}})
        mock_update.assert_not_called()


# ── knowledge_backfill: solo experiencia ya vivida, sin segundo cerebro ──

def _fake_signal(sid, symbol, status, ts=1_700_000_000.0):
    return SimpleNamespace(signal_id=sid, symbol=symbol, timestamp=ts, status=status)


class TestBackfillScope:
    @pytest.fixture(autouse=True)
    def _tmp_vault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
        KL.clear()
        yield
        KL.clear()

    def test_only_resolved_signals_are_candidates(self):
        sigs = [
            _fake_signal("S1", "BTC", "pending"),
            _fake_signal("S2", "AAPL", "win"),
            _fake_signal("S3", "ETH", "loss"),
        ]
        with patch("connectors.etoro.signal_recorder.load_signals", return_value=sigs), \
             patch("connectors.etoro.knowledge_snapshot.snapshot_at", return_value={
                 "OneHour": {"available": True, "insufficient_depth": False, "features": {"RSI": 50.0}},
             }):
            summary = KB.backfill_signal_knowledge()

        assert summary["candidates"] == 2  # S1 (pending) queda fuera
        assert summary["enriched"] == 2
        assert KL.get_features("S2") is not None
        assert KL.get_features("S3") is not None
        assert KL.get_features("S1") is None  # nunca procesada

    def test_idempotent_skips_already_enriched(self):
        sigs = [
            _fake_signal("S1", "BTC", "win"),
            _fake_signal("S2", "AAPL", "loss"),
        ]
        KL.record_features("S1", {"OneHour": {"available": True}})  # ya enriquecida

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=sigs), \
             patch("connectors.etoro.knowledge_snapshot.snapshot_at", return_value={
                 "OneHour": {"available": True, "insufficient_depth": False, "features": {}},
             }) as mock_snapshot:
            summary = KB.backfill_signal_knowledge()

        assert summary["candidates"] == 1  # solo S2 (S1 ya tiene conocimiento)
        mock_snapshot.assert_called_once_with("AAPL", 1_700_000_000.0)

    def test_force_recomputes_everything(self):
        sigs = [_fake_signal("S1", "BTC", "win")]
        KL.record_features("S1", {"OneHour": {"available": True}})

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=sigs), \
             patch("connectors.etoro.knowledge_snapshot.snapshot_at", return_value={}):
            summary = KB.backfill_signal_knowledge(force=True)
        assert summary["candidates"] == 1
        assert summary["processed"] == 1

    def test_does_not_touch_signal_recorder_pattern_memory_gravity_or_ledger(self):
        """Etapa 1 es SOLO conocimiento adjunto por signal_id — no debe
        tocar ninguna otra pieza del sistema de aprendizaje, ni reescribir
        el JSONL mutable de señales."""
        sigs = [_fake_signal("S1", "BTC", "win")]
        with patch("connectors.etoro.signal_recorder.load_signals", return_value=sigs), \
             patch("connectors.etoro.signal_recorder.update_signal") as mock_update, \
             patch("connectors.etoro.knowledge_snapshot.snapshot_at", return_value={}), \
             patch("connectors.etoro.pattern_memory.update_patterns_from_signals") as mock_pm, \
             patch("core.learn.gravity_engine.get_gravity_index") as mock_gravity, \
             patch("core.learn.verification_ledger.record_outcome") as mock_ledger:
            KB.backfill_signal_knowledge()

        mock_update.assert_not_called()
        mock_pm.assert_not_called()
        mock_gravity.assert_not_called()
        mock_ledger.assert_not_called()

    def test_errors_are_isolated_per_signal(self):
        sigs = [
            _fake_signal("S1", "BTC", "win"),
            _fake_signal("S2", "AAPL", "loss"),
        ]

        def _snapshot_side_effect(symbol, ts, *a, **kw):
            if symbol == "BTC":
                raise RuntimeError("boom")
            return {"OneHour": {"available": True, "insufficient_depth": False, "features": {}}}

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=sigs), \
             patch("connectors.etoro.knowledge_snapshot.snapshot_at", side_effect=_snapshot_side_effect):
            summary = KB.backfill_signal_knowledge()

        assert summary["errors"] == 1
        assert summary["enriched"] == 1  # AAPL sí se procesó
        assert KL.get_features("S1") is None
        assert KL.get_features("S2") is not None
