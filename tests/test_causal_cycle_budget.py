"""
tests/test_causal_cycle_budget.py — El presupuesto por ciclo no posterga nada.

EL DEFECTO REPRODUCIDO
----------------------
`evaluate_live_convergences` descontaba presupuesto ANTES de evaluar. Con 51
convergencias y un tope de 50, las 50 primeras lo consumían entero **aunque ya
estuvieran evaluadas y su evidencia no hubiera cambiado**. La número 51 no
entraba nunca: ni en ese ciclo ni en ningún otro, porque el orden era siempre
el mismo.

LAS CINCO PROPIEDADES QUE SE EXIGEN
-----------------------------------
1. Una revisión reutilizada no consume presupuesto de evaluación nueva.
2. Hay progreso durable entre ciclos.
3. Ninguna convergencia queda permanentemente postergada.
4. Ninguna disolución se pierde, ni con más de 50 evaluaciones.
5. Disoluciones y retiradas tienen prioridad.

La (2) se comprueba con un REINICIO intermedio: el orden se deriva de
`promotion_decisions`, no de un cursor en memoria, así que sobrevive.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn import causal_learning as cl  # noqa: E402

STRONG = {"win_rate": 0.80, "expectancy": 0.60, "confidence": 1.0, "sample_size": 20.0}
LIMIT = cl.MAX_EVALUATIONS_PER_CYCLE          # 50


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "causal" / "causal_learning.db")


def _qualified(fp):
    return STRONG


def _entries(n, *, status="active", prefix="CONV", domain="market"):
    return [
        {
            "convergence_id": f"{prefix}-{i:03d}",
            "domains": [domain],
            "source_pattern_ids": [f"{domain}:A{i}", f"{domain}:B{i}"],
            "combined_cc": 0.9, "combined_hits": 9,
            "status": status,
            "claim": f"convergencia {i}",
        }
        for i in range(n)
    ]


def _seen(db_path):
    """Convergencias con al menos una decisión registrada."""
    return {d["convergence_id"] for d in cl.get_decisions(limit=10_000, db_path=db_path)}


# ===========================================================================
# 51 convergencias: la 51 NO puede quedar postergada para siempre
# ===========================================================================

class TestFiftyOneConvergences:

    def test_the_fifty_first_is_reached_on_the_next_cycle(self, db):
        entries = _entries(51)

        first = cl.evaluate_live_convergences(
            entries, stats_fetcher=_qualified, db_path=db,
        )
        assert first["budget_spent"] == LIMIT
        assert first["truncated"] is True
        assert len(_seen(db)) == LIMIT

        second = cl.evaluate_live_convergences(
            entries, stats_fetcher=_qualified, db_path=db,
        )
        # Las 50 ya evaluadas son REUTILIZACIONES: no gastan presupuesto.
        assert second["reused"] == LIMIT
        assert second["budget_spent"] == 1, (
            "una revisión reutilizada consumió presupuesto de evaluación nueva"
        )
        assert len(_seen(db)) == 51, "la convergencia 51 siguió sin evaluarse"

    def test_a_steady_state_processes_every_convergence(self, db):
        """Si nada cambia, el ciclo entero cabe: todo es reutilización."""
        entries = _entries(51)
        for _ in range(3):
            cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)

        final = cl.evaluate_live_convergences(
            entries, stats_fetcher=_qualified, db_path=db,
        )
        assert final["evaluated"] == 51
        assert final["reused"] == 51
        assert final["budget_spent"] == 0
        assert final["truncated"] is False

    def test_no_convergence_is_starved_over_many_cycles(self, db):
        """120 convergencias, tope 50: todas acaban evaluadas."""
        entries = _entries(120)
        for _ in range(4):
            cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)
        assert len(_seen(db)) == 120

    def test_the_least_recently_evaluated_goes_first(self, db):
        entries = _entries(51)
        cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)
        pending_before = {e["convergence_id"] for e in entries} - _seen(db)
        assert len(pending_before) == 1

        cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)
        assert pending_before <= _seen(db), (
            "la que llevaba más tiempo sin evaluarse no fue la primera"
        )


# ===========================================================================
# Progreso durable: sobrevive a un reinicio
# ===========================================================================

class TestDurableProgress:

    def test_progress_survives_a_restart(self, db):
        """El orden sale de la base, no de un cursor en memoria.

        Se simula el reinicio recargando el módulo: cualquier estado en proceso
        se pierde. El progreso no puede depender de él.
        """
        import importlib
        entries = _entries(51)

        cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)
        seen_before = _seen(db)
        assert len(seen_before) == LIMIT

        reloaded = importlib.reload(cl)
        second = reloaded.evaluate_live_convergences(
            entries, stats_fetcher=_qualified, db_path=db,
        )
        assert second["budget_spent"] == 1
        assert len(reloaded.get_decisions(limit=10_000, db_path=db)) == 51

    def test_two_cycles_never_redo_the_same_new_evaluation(self, db):
        entries = _entries(30)
        first = cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)
        second = cl.evaluate_live_convergences(entries, stats_fetcher=_qualified, db_path=db)
        assert first["budget_spent"] == 30
        assert second["budget_spent"] == 0
        assert second["reused"] == 30


# ===========================================================================
# Disoluciones: prioridad, y ninguna se pierde
# ===========================================================================

class TestDissolutionsAreNeverLost:

    def test_more_than_fifty_dissolutions_are_all_processed(self, db):
        """60 disoluciones con tope 50: las 10 restantes NO se pierden.

        El registro emite una disolución UNA sola vez, en la transición
        activa->disuelta, y no vuelve a emitirla. Si el ciclo no la procesa y
        no la encola, el criterio seguiría apoyándose en evidencia que ya no
        existe, para siempre.
        """
        live = _entries(60)
        cl.evaluate_live_convergences(live, limit=200, stats_fetcher=_qualified, db_path=db)
        assert len(cl.list_learnings(state=cl.STATE_LEARNED, limit=200, db_path=db)) == 60

        dissolved = _entries(60, status="dissolved")
        first = cl.evaluate_live_convergences(
            dissolved, limit=LIMIT, stats_fetcher=_qualified, db_path=db,
        )
        assert first["weakened"] == LIMIT
        assert first["enqueued"] == 10, "las disoluciones sobrantes no se encolaron"

        # El ciclo siguiente las drena ANTES que nada, sin que el registro las
        # vuelva a emitir (se pasa una lista vacía a propósito).
        second = cl.evaluate_live_convergences(
            [], limit=LIMIT, stats_fetcher=_qualified, db_path=db,
        )
        assert second["drained"] == 10
        assert second["weakened"] == 10
        assert cl.list_learnings(state=cl.STATE_LEARNED, limit=200, db_path=db) == [], (
            "quedaron aprendizajes vivos de convergencias ya disueltas"
        )

    def test_dissolutions_go_before_live_convergences(self, db):
        """Con el presupuesto justo, la disolución gana."""
        live = _entries(50, prefix="VIVA")
        gone = _entries(1, prefix="MUERTA", status="dissolved")
        result = cl.evaluate_live_convergences(
            live + gone, limit=1, stats_fetcher=_qualified, db_path=db,
        )
        assert result["weakened"] == 1
        seen = _seen(db)
        assert "MUERTA-000" in seen
        assert len(seen) == 1

    def test_a_dissolution_is_not_lost_when_the_budget_is_one(self, db):
        gone = _entries(5, status="dissolved")
        cl.evaluate_live_convergences(gone, limit=1, stats_fetcher=_qualified, db_path=db)
        for _ in range(5):
            cl.evaluate_live_convergences([], limit=1, stats_fetcher=_qualified, db_path=db)
        assert len(_seen(db)) == 5

    def test_the_queue_empties_as_work_is_done(self, db):
        gone = _entries(3, status="dissolved")
        cl.evaluate_live_convergences(gone, limit=1, stats_fetcher=_qualified, db_path=db)
        conn = cl.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 2
        finally:
            conn.close()

        for _ in range(3):
            cl.evaluate_live_convergences([], limit=5, stats_fetcher=_qualified, db_path=db)
        conn = cl.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
        finally:
            conn.close()

    def test_live_convergences_are_not_enqueued(self, db):
        """Solo se encola lo que el registro no vuelve a emitir."""
        result = cl.evaluate_live_convergences(
            _entries(60), limit=LIMIT, stats_fetcher=_qualified, db_path=db,
        )
        assert result["truncated"] is True
        assert result["enqueued"] == 0
        conn = cl.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
        finally:
            conn.close()


# ===========================================================================
# El índice de gravedad se lee UNA vez por ciclo
# ===========================================================================

class TestTheIndexIsReadOncePerCycle:

    def test_cycle_fetcher_loads_the_index_a_single_time(self, db, monkeypatch):
        """`GravityIndex.get()` relee el índice ENTERO en cada llamada.

        Sin este agrupamiento, 50 convergencias de 2 patrones costaban 100
        lecturas íntegras por ciclo — el bloqueo de `meta_loop` que el
        presupuesto existe para evitar.
        """
        import core.learn.gravity_engine as ge
        loads = {"n": 0}

        class _Index:
            def load_raw(self):
                loads["n"] += 1
                return {}

        monkeypatch.setattr(ge, "get_gravity_index", lambda: _Index(), raising=False)
        cl.evaluate_live_convergences(_entries(30), db_path=db)
        assert loads["n"] == 1, f"el índice se leyó {loads['n']} veces en un ciclo"

    def test_an_unreadable_index_qualifies_nothing(self, db, monkeypatch):
        """Fallo seguro: sin métricas, nada se promueve."""
        import core.learn.gravity_engine as ge

        def _boom():
            raise RuntimeError("índice ilegible")

        monkeypatch.setattr(ge, "get_gravity_index", _boom, raising=False)
        result = cl.evaluate_live_convergences(_entries(3), db_path=db)
        assert result["learned"] == 0
        assert result["candidates"] == 3
