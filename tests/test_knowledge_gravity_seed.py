"""
Tests — connectors/etoro/knowledge_gravity_seed (siembra de conocimiento, masa cero).

Cubre exactamente lo crítico: la siembra crea 196 identidades (una por
función, sin multiplicar por salida/símbolo/timeframe), con hits=0 y sin
historial, y NUNCA pierde ni reactiva una estrella ya existente de otro
origen — la propiedad que hace o rompe `update_records()` al reemplazar el
índice completo.

Run: python -m pytest tests/test_knowledge_gravity_seed.py -v
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.learn.gravity_engine import GravityIndex, GravityRecord, Tier
from connectors.etoro import knowledge_gravity_seed as SEED
from connectors.market.ta_knowledge import known_function_names


@pytest.fixture
def isolated_index(tmp_path):
    """GravityIndex real, pero apuntando a un archivo temporal — nunca al
    índice de producción."""
    idx = GravityIndex(path=str(tmp_path / "gravity_index.json"))
    with patch("core.learn.gravity_engine.get_gravity_index", return_value=idx):
        yield idx


class TestSeedKnowledgeStars:
    def test_creates_exactly_one_star_per_function(self, isolated_index):
        summary = SEED.seed_knowledge_stars()
        names = known_function_names()
        assert summary["total_before"] == 0
        assert len(summary["created"]) == len(names)
        assert summary["total_after"] == len(names)

        records = isolated_index.load_raw()
        assert len(records) == len(names)
        for name in names:
            fp = SEED.knowledge_fingerprint(name)
            assert fp in records

    def test_seeded_stars_have_zero_mass_and_no_history(self, isolated_index):
        SEED.seed_knowledge_stars()
        records = isolated_index.load_raw()
        rec = records[SEED.knowledge_fingerprint("RSI")]
        assert rec.hits == 0
        assert rec.cc_score == 0.0
        assert rec.outcome_history == []
        assert rec.activation_history == []
        assert rec.domain == "market"
        assert rec.intent == "ta_indicator"
        assert rec.first_seen  # tiene timestamp de siembra
        # last_seen nace IGUAL a first_seen (nunca "") -- ver nota del
        # módulo: "" hace que _parse_iso lo trate como "ahora" reevaluado
        # en cada comparación, y entonces la primera activación real nunca
        # logra avanzarlo. Sembrado como first_seen, sí puede avanzar.
        assert rec.last_seen == rec.first_seen

    def test_multi_output_function_is_still_one_star(self, isolated_index):
        """MACD produce 3 salidas numéricas (macd/macdsignal/macdhist) —
        debe seguir siendo UNA sola estrella, no tres. (MACDEXT/MACDFIX son
        funciones DISTINTAS de TA-Lib, conceptos propios — no cuentan aquí.)"""
        SEED.seed_knowledge_stars()
        records = isolated_index.load_raw()
        assert "market_knowledge:MACD" in records
        for suffix in ("_macd", "_macdsignal", "_macdhist"):
            assert f"market_knowledge:MACD{suffix}" not in records

    def test_does_not_touch_or_lose_preexisting_unrelated_stars(self, isolated_index):
        """Propiedad crítica: update_records() reemplaza el archivo entero.
        Una estrella de OTRO dominio, sembrada antes por otra parte del
        sistema, debe sobrevivir intacta — mismo hits/cc_score/historial."""
        pre_existing = GravityRecord(
            fingerprint="freight:LANE-123",
            tier=Tier.WARM.value,
            hits=42,
            first_seen="2026-01-01T00:00:00+00:00",
            last_seen="2026-06-01T00:00:00+00:00",
            cc_score=0.77,
            domain="freight_logistics",
            intent="rate_quote",
            outcome_history=["win", "win", "loss"],
        )
        isolated_index.update_records({"freight:LANE-123": pre_existing})

        SEED.seed_knowledge_stars()

        records = isolated_index.load_raw()
        survivor = records["freight:LANE-123"]
        assert survivor.hits == 42
        assert survivor.cc_score == 0.77
        assert survivor.outcome_history == ["win", "win", "loss"]
        assert survivor.domain == "freight_logistics"
        # Y las 196 nuevas convivieron con ella en la misma escritura.
        assert len(records) == 1 + len(known_function_names())

    def test_idempotent_second_run_does_not_duplicate_or_reset(self, isolated_index):
        first = SEED.seed_knowledge_stars()
        assert len(first["created"]) == len(known_function_names())

        # Simula que una estrella sembrada YA se activó una vez (fuera de
        # esta siembra) -- la segunda corrida no debe resetear eso.
        records = isolated_index.load_raw()
        records["market_knowledge:RSI"].hits = 5
        isolated_index.update_records(records)

        second = SEED.seed_knowledge_stars()
        assert second["created"] == []
        assert len(second["already_present"]) == len(known_function_names())
        assert second["total_before"] == second["total_after"] == len(known_function_names())

        # La activación simulada sigue intacta -- la siembra no la tocó.
        records_after = isolated_index.load_raw()
        assert records_after["market_knowledge:RSI"].hits == 5

    def test_seed_then_two_real_activations_advance_last_seen_keep_first_seen(self, isolated_index):
        """Demuestra el ciclo completo pedido:
        siembra -> hits=0 -> 1ra activación real -> hits=1, last_seen
        actualizado -> 2da activación -> hits=2, last_seen avanza -- y
        first_seen permanece siendo el momento de ADQUISICIÓN del
        conocimiento (la siembra), no el de ninguna activación."""
        from types import SimpleNamespace
        from connectors.etoro import learning_engine as LE

        seed_summary = SEED.seed_knowledge_stars()
        assert "market_knowledge:RSI" in seed_summary["created"]

        seeded = isolated_index.load_raw()["market_knowledge:RSI"]
        assert seeded.hits == 0
        seed_first_seen = seeded.first_seen
        seed_last_seen = seeded.last_seen
        assert seed_last_seen == seed_first_seen  # nace sin activación real

        sig = SimpleNamespace(signal_id="SIG-1", symbol="BTC")
        entry = {
            "signal_id": "SIG-1", "computed_at": 0.0,
            "features": {"OneHour": {"available": True, "insufficient_depth": False,
                                      "features": {"RSI": 55.2}}},
        }

        # ── 1ra activación real ──────────────────────────────────────
        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            LE._feed_knowledge_activation()

        after_1 = isolated_index.load_raw()["market_knowledge:RSI"]
        assert after_1.hits == 1
        assert after_1.first_seen == seed_first_seen  # NUNCA cambia
        assert after_1.last_seen > seed_last_seen      # avanzó de verdad
        assert after_1.last_seen != ""                 # nunca atascado

        # ── 2da activación real ──────────────────────────────────────
        with patch("connectors.etoro.signal_recorder.load_signals", return_value=[sig]), \
             patch("connectors.etoro.knowledge_ledger.get_features", return_value=entry):
            LE._feed_knowledge_activation()

        after_2 = isolated_index.load_raw()["market_knowledge:RSI"]
        assert after_2.hits == 2
        assert after_2.first_seen == seed_first_seen   # sigue sin cambiar
        assert after_2.last_seen >= after_1.last_seen  # avanza de nuevo

    def test_no_multiplication_by_symbol_or_timeframe(self, isolated_index):
        """El fingerprint nunca lleva símbolo ni timeframe -- es el mismo
        concepto para cualquier mercado."""
        SEED.seed_knowledge_stars()
        records = isolated_index.load_raw()
        for fp in records:
            assert "BTC" not in fp and "AAPL" not in fp
            assert "1h" not in fp.lower() and "1d" not in fp.lower()
