"""
tests/test_causal_live_cycle.py — El ciclo vivo aprende sin backfill.

QUÉ DEMUESTRA
-------------
Que el puente causal está ENGANCHADO al flujo real: cuando el observador llama
a `convergence_registry.record_convergence_snapshot()` — el mismo camino que
recorre el sistema vivo — las convergencias de ESE ciclo se evalúan y, si ya
cumplen los umbrales, producen aprendizaje en el PRIMER ciclo.

Y que el enganche es seguro:
  * no recorre la tabla histórica, solo lo que este ciclo tocó;
  * un fallo del almacén causal NO tumba al observador ni revierte lo que el
    registro ya escribió;
  * respeta un límite de trabajo por ciclo para no bloquear `meta_loop`.

AISLAMIENTO
-----------
El registro usa una base bajo `tmp_path`; el almacén causal usa el vault
temporal de `conftest._hermetic_base`. El gravity index vivo no se consulta:
las métricas de patrón se inyectan.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402
from core.learn import convergence_registry as reg  # noqa: E402

STRONG = {"win_rate": 0.80, "expectancy": 0.60, "confidence": 1.0, "sample_size": 20.0}
THIN = {"win_rate": 1.00, "expectancy": 1.00, "confidence": 0.25, "sample_size": 5.0}

#: Fingerprints vivos: el mapa que `normalize_entity_id` usa para resolver una
#: estrella como `exact_live_match`, de modo que `canonical_id` ES el
#: fingerprint que `fetch_pattern_stats` acepta.
LIVE = {"market:AAPL": "market", "freight_logistics:LANE-7": "freight_logistics"}

CANDIDATE = {
    "type": "intent_overlap",
    "star_a": "market:AAPL",
    "star_b": "freight_logistics:LANE-7",
    "combined_cc": 0.77,
    "combined_hits": 9,
    "domains": ["market", "freight_logistics"],
}


@pytest.fixture
def registry_db(tmp_path) -> str:
    return str(tmp_path / "registry" / "convergence.db")


def _record(fingerprint: str, wins: int, losses: int):
    """Una estrella real del gravity index con su historia de outcomes.

    Se construye un `GravityRecord` de verdad en lugar de doblar
    `fetch_pattern_stats`, para que el ciclo ejercite el camino completo:
    `cycle_stats_fetcher()` -> una lectura del índice -> `derive_pattern_stats`.
    """
    from core.learn.schemas import GravityRecord
    return GravityRecord(
        fingerprint=fingerprint,
        domain=fingerprint.split(":")[0],
        outcome_history=["win"] * wins + ["loss"] * losses,
    )


def _patch_index(monkeypatch, records):
    """Sustituye SOLO la lectura del índice, no la derivación."""
    import core.learn.gravity_engine as ge

    class _Index:
        def load_raw(self):
            return dict(records)

        def get(self, fp):
            return records.get(fp)

    monkeypatch.setattr(ge, "get_gravity_index", lambda: _Index(), raising=False)


@pytest.fixture
def strong_patterns(monkeypatch):
    """Ambos patrones fuente con métricas reales suficientes.

    16 aciertos y 4 fallos -> 20 outcomes graduados, win_rate 80 %,
    expectancy 0.6, confianza 1.0. Exactamente lo que exige la política.
    """
    records = {
        "market:AAPL": _record("market:AAPL", 16, 4),
        "freight_logistics:LANE-7": _record("freight_logistics:LANE-7", 16, 4),
    }
    _patch_index(monkeypatch, records)
    return records


# ===========================================================================
# El primer ciclo puede aprender
# ===========================================================================

class TestTheFirstLiveCycleCanLearn:

    def test_a_qualified_convergence_learns_on_the_first_cycle(
        self, registry_db, strong_patterns,
    ):
        result = reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
        )
        assert result["created"] == 1
        assert result["causal"]["learned"] >= 1, result["causal"]

        learnings = cl.list_learnings(state=cl.STATE_LEARNED)
        assert learnings, "el ciclo vivo no produjo ningún aprendizaje"
        learning = learnings[0]
        assert learning["source_pattern_ids"] == [
            "freight_logistics:LANE-7", "market:AAPL",
        ] or learning["source_pattern_ids"] == [
            "market:AAPL", "freight_logistics:LANE-7",
        ]
        assert learning["learned_at"] is not None

    def test_both_participating_domains_get_a_learning(
        self, registry_db, strong_patterns,
    ):
        """Una convergencia cruzada es evidencia para AMBOS criterios."""
        reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
        )
        domains = {l["domain"] for l in cl.list_learnings(state=cl.STATE_LEARNED)}
        assert domains == {"market", "freight_logistics"}

    def test_the_learning_points_back_to_the_canonical_convergence(
        self, registry_db, strong_patterns,
    ):
        reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
        )
        canonical = reg.get_canonical_convergences(
            status="active", db_path=registry_db,
        )
        ids = {c["convergence_id"] for c in canonical}
        for learning in cl.list_learnings():
            assert learning["source_convergence_id"] in ids

    def test_an_unqualified_pattern_stays_a_candidate(self, registry_db, monkeypatch):
        # LANE-7 con solo 5 outcomes graduados: por debajo de MIN_SAMPLE=15.
        _patch_index(monkeypatch, {
            "market:AAPL": _record("market:AAPL", 16, 4),
            "freight_logistics:LANE-7": _record("freight_logistics:LANE-7", 5, 0),
        })
        result = reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
        )
        assert result["causal"]["learned"] == 0
        assert result["causal"]["candidates"] >= 1
        assert cl.list_learnings(state=cl.STATE_LEARNED) == []


# ===========================================================================
# Repetir el ciclo no acumula aprendizaje
# ===========================================================================

class TestRepeatedCyclesDoNotAccumulate:

    def test_twenty_identical_cycles_produce_the_same_learnings(
        self, registry_db, strong_patterns,
    ):
        """El escáner corre cada pocos minutos. Eso no es evidencia nueva."""
        for _ in range(20):
            result = reg.record_convergence_snapshot(
                [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
            )
        # El registro sí cuenta confirmaciones...
        canonical = reg.get_canonical_convergences(
            status="active", db_path=registry_db,
        )[0]
        assert canonical["confirmation_count"] == 20
        # ...pero el puente causal sigue teniendo UN aprendizaje por dominio.
        assert len(cl.list_learnings()) == 2
        assert result["causal"]["reused"] >= 1

    def test_confirmation_count_does_not_create_revisions(
        self, registry_db, strong_patterns,
    ):
        for _ in range(20):
            reg.record_convergence_snapshot(
                [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
            )
        for learning in cl.list_learnings():
            # Una revisión POR DOMINIO participante: 20 confirmaciones del
            # escáner no añadieron ninguna.
            assert cl.count_revisions(
                learning["source_convergence_id"], learning["domain"],
            ) == 1


# ===========================================================================
# Ciclo de vida a través del registro real
# ===========================================================================

class TestLifecycleThroughTheRegistry:

    def test_a_dissolved_convergence_weakens_the_learning(
        self, registry_db, strong_patterns,
    ):
        reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
        )
        assert cl.list_learnings(state=cl.STATE_LEARNED)

        # Ciclo siguiente: la convergencia ya no aparece -> se disuelve.
        result = reg.record_convergence_snapshot(
            [], live_fingerprints=LIVE, db_path=registry_db,
        )
        assert result["dissolved"] == 1
        assert result["causal"]["weakened"] >= 1
        assert cl.list_learnings(state=cl.STATE_LEARNED) == []
        # La historia NO se borra.
        weakened = cl.list_learnings(state=cl.STATE_WEAKENED)
        assert weakened
        assert weakened[0]["learned_at"] is not None
        assert weakened[0]["metrics_at_promotion"]


# ===========================================================================
# El almacén causal nunca tumba al observador
# ===========================================================================

class TestPartialFailureIsContained:

    def test_a_broken_causal_store_does_not_break_the_registry(
        self, registry_db, monkeypatch,
    ):
        """Fallo total del puente: el registro conserva su resultado."""
        import core.learn.causal_learning as _cl

        def _explode(*a, **kw):
            raise RuntimeError("almacén causal caído")

        monkeypatch.setattr(_cl, "evaluate_live_convergences", _explode)
        result = reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=LIVE, db_path=registry_db,
        )
        assert result["created"] == 1, "el registro perdió su escritura"
        assert result["causal"]["errors"], "el fallo debe quedar registrado"
        # Y lo que el registro escribió sigue ahí.
        assert len(reg.get_canonical_convergences(
            status="active", db_path=registry_db,
        )) == 1

    def test_one_failing_convergence_does_not_stop_the_others(self, tmp_path):
        """Fallo parcial: se salta la rota y sigue con el resto."""
        db = str(tmp_path / "c.db")
        calls = {"n": 0}

        def _flaky(fingerprint):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("proveedor intermitente")
            return STRONG

        entries = [
            {
                "convergence_id": f"CONV-{i}", "domains": ["market"],
                "source_pattern_ids": ["A", "B"],
                "combined_cc": 0.9, "combined_hits": 9,
            }
            for i in range(3)
        ]
        result = cl.evaluate_live_convergences(
            entries, stats_fetcher=_flaky, db_path=db,
        )
        assert len(result["errors"]) == 1
        assert result["evaluated"] == 2, "las sanas debieron evaluarse igual"

    def test_a_failure_leaves_no_half_written_learning(self, tmp_path):
        db = str(tmp_path / "c.db")
        result = cl.evaluate_live_convergences(
            [{
                "convergence_id": "CONV-ROTA", "domains": ["market"],
                "source_pattern_ids": ["A", "B"],
                "combined_cc": 0.9, "combined_hits": 9,
            }],
            stats_fetcher=lambda fp: (_ for _ in ()).throw(RuntimeError("boom")),
            db_path=db,
        )
        assert result["errors"]
        assert cl.list_learnings(db_path=db) == []
        assert cl.get_decisions(convergence_id="CONV-ROTA", db_path=db) == []


# ===========================================================================
# Sin backfill
# ===========================================================================

class TestNoBackfill:

    def test_only_this_cycle_is_evaluated(self, registry_db, strong_patterns):
        """Convergencias históricas presentes en la base no se reevalúan."""
        # Ciclo 1: dos convergencias.
        other = dict(CANDIDATE, star_b="cybersecurity:CVE-1",
                     domains=["market", "cybersecurity"])
        live = dict(LIVE, **{"cybersecurity:CVE-1": "cybersecurity"})
        reg.record_convergence_snapshot(
            [CANDIDATE, other], live_fingerprints=live, db_path=registry_db,
        )
        evaluated_first = len(cl.list_learnings())
        assert evaluated_first >= 2

        # Ciclo 2: solo UNA sigue viva. La otra se disuelve (1 evaluación), y
        # no se reprocesa ninguna convergencia histórica adicional.
        result = reg.record_convergence_snapshot(
            [CANDIDATE], live_fingerprints=live, db_path=registry_db,
        )
        assert result["causal"]["evaluated"] <= 4, (
            "se evaluó más de lo que este ciclo tocó: huele a backfill"
        )
