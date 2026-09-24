"""
Tests — Punto A: reconocimiento/activación de las estrellas market_knowledge:*.

Cubre `connectors.market.ta_knowledge.output_keys_for` (mapeo función→claves,
para no duplicar la convención de nombres compuestos) y
`connectors.etoro.learning_engine._feed_knowledge_activation` (el Punto A):
activación SIN preselección de relevancia ni umbral — toda función con un
valor real (!= None) en la observación participa; ninguna se filtra por
"parecer" importante. Gravity/convergencias/criterio no se tocan: solo se
verifica que la activación llama exactamente al mecanismo ya existente
(`record_event`), nada más.

Run: python -m pytest tests/test_knowledge_activation.py -v
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

from core.learn.gravity_engine import GravityIndex
from connectors.market.ta_knowledge import output_keys_for
from connectors.etoro import learning_engine as LE
from connectors.etoro.knowledge_gravity_seed import knowledge_fingerprint, seed_knowledge_stars


@pytest.fixture
def isolated_index(tmp_path):
    idx = GravityIndex(path=str(tmp_path / "gravity_index.json"))
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=idx):
        yield idx


def _fake_signal(sid, symbol):
    return SimpleNamespace(signal_id=sid, symbol=symbol)


def _ledger_entry(features_by_tf):
    """Construye la forma real de knowledge_ledger.get_features(): cada
    timeframe con la forma real de snapshot_timeframe()."""
    return {
        "signal_id": "SIG-X",
        "computed_at": 1_700_000_000.0,
        "features": {
            tf: {"available": True, "insufficient_depth": False, "features": feats}
            for tf, feats in features_by_tf.items()
        },
    }


# ── output_keys_for ──────────────────────────────────────────────────

class TestOutputKeysFor:
    def test_single_output_function(self):
        assert output_keys_for("RSI") == ["RSI"]

    def test_multi_output_function(self):
        keys = output_keys_for("MACD")
        assert set(keys) == {"MACD_macd", "MACD_macdsignal", "MACD_macdhist"}

    def test_unknown_function_falls_back_to_name(self):
        assert output_keys_for("NO_EXISTE_ESTO") == ["NO_EXISTE_ESTO"]


# ── _feed_knowledge_activation ──────────────────────────────────────

class TestFeedKnowledgeActivation:
    def test_only_non_none_functions_activate(self, isolated_index):
        seed_knowledge_stars()  # las 196 en masa cero, como en producción
        sig = _fake_signal("SIG-1", "BTC")
        entry = _ledger_entry({
            "OneHour": {"RSI": 55.2, "CDLHAMMER": None, "ADX": 30.1},
        })

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            activated = LE._feed_knowledge_activation()

        records = isolated_index.load_raw()
        assert records[knowledge_fingerprint("RSI")].hits == 1
        assert records[knowledge_fingerprint("ADX")].hits == 1
        # None -> nunca activada: sigue en masa cero, tal como se sembró.
        assert records[knowledge_fingerprint("CDLHAMMER")].hits == 0
        assert activated == 2

    def test_no_relevance_filter_or_threshold_applied(self, isolated_index):
        """Regla fijada: TODA función con valor real participa, sin
        importar la magnitud/plausibilidad del valor -- no hay ningún if
        de calidad, solo != None."""
        seed_knowledge_stars()
        sig = _fake_signal("SIG-1", "BTC")
        # Valores deliberadamente "raros" (RSI fuera de 0-100, ADX negativo)
        # -- deben activar igual: esta capa no juzga plausibilidad.
        entry = _ledger_entry({"OneHour": {"RSI": 999.0, "ADX": -5.0}})

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            LE._feed_knowledge_activation()

        records = isolated_index.load_raw()
        assert records[knowledge_fingerprint("RSI")].hits == 1
        assert records[knowledge_fingerprint("ADX")].hits == 1

    def test_multi_output_function_activates_once_if_any_output_real(self, isolated_index):
        seed_knowledge_stars()
        sig = _fake_signal("SIG-1", "BTC")
        # MACD con 2 de 3 salidas None -- sigue siendo UNA observación real.
        entry = _ledger_entry({
            "OneHour": {"MACD_macd": 1.23, "MACD_macdsignal": None, "MACD_macdhist": None},
        })

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            activated = LE._feed_knowledge_activation()

        records = isolated_index.load_raw()
        assert records[knowledge_fingerprint("MACD")].hits == 1
        assert activated == 1  # UNA activación, no tres

    def test_multi_output_function_all_none_does_not_activate(self, isolated_index):
        seed_knowledge_stars()
        sig = _fake_signal("SIG-1", "BTC")
        entry = _ledger_entry({
            "OneHour": {"MACD_macd": None, "MACD_macdsignal": None, "MACD_macdhist": None},
        })

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            LE._feed_knowledge_activation()

        records = isolated_index.load_raw()
        assert records[knowledge_fingerprint("MACD")].hits == 0

    def test_signal_without_knowledge_is_skipped_not_an_error(self, isolated_index):
        seed_knowledge_stars()
        sig = _fake_signal("SIG-SIN-CONOCIMIENTO", "AAPL")

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=None):
            activated = LE._feed_knowledge_activation()

        assert activated == 0
        # El índice sigue teniendo las 196 en masa cero -- nada se rompió.
        records = isolated_index.load_raw()
        assert all(r.hits == 0 for r in records.values())

    def test_repeated_observation_accumulates_mass(self, isolated_index):
        """Dos señales distintas reconociendo RSI -> hits=2, no 1 -- la masa
        se acumula por repetición, tal como describe el concepto."""
        seed_knowledge_stars()
        sigs = [_fake_signal("SIG-1", "BTC"), _fake_signal("SIG-2", "ETH")]
        entry = _ledger_entry({"OneHour": {"RSI": 40.0}})

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=sigs), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            LE._feed_knowledge_activation()

        records = isolated_index.load_raw()
        assert records[knowledge_fingerprint("RSI")].hits == 2

    def test_does_not_touch_pattern_memory_or_verification_ledger(self, isolated_index):
        seed_knowledge_stars()
        sig = _fake_signal("SIG-1", "BTC")
        entry = _ledger_entry({"OneHour": {"RSI": 40.0}})

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry), \
             patch("connectors.etoro.pattern_memory.update_patterns_from_signals") as mock_pm, \
             patch("core.learn.verification_ledger.record_outcome") as mock_ledger, \
             patch("core.learn.convergence_registry.get_canonical_convergences") as mock_conv:
            LE._feed_knowledge_activation()

        mock_pm.assert_not_called()
        mock_ledger.assert_not_called()
        mock_conv.assert_not_called()

    def test_never_activates_a_fingerprint_outside_the_196_seeded(self, isolated_index):
        """No debe inventar identidades nuevas -- solo las ya sembradas."""
        seed_before = seed_knowledge_stars()
        seeded_fps = set(seed_before["created"])
        sig = _fake_signal("SIG-1", "BTC")
        entry = _ledger_entry({"OneHour": {"RSI": 40.0, "MACD_macd": 1.0}})

        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            LE._feed_knowledge_activation()

        records = isolated_index.load_raw()
        assert set(records.keys()) == seeded_fps  # ni una identidad nueva
