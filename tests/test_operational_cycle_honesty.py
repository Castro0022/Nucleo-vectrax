"""
tests/test_operational_cycle_honesty.py — PARTE 6 (2026-09-20)
==============================================================================
Contrato honesto de dashboard/telemetría. Verifica:

  1. `OperationalCycle.verify_passed` nunca es `True` por defecto ni cuando
     `verify_ran=False` -- ni en el dataclass, ni en `CycleObserver
     .set_verify()`, ni en la fila persistida en `op_cycles.db`.
  2. Los 3 campos nuevos (memory_consulted, evidence_found, abstained) se
     persisten y se leen correctamente, independientes entre sí y de
     `grounded`/`verified`/`success`.
  3. `pipeline_worker._compute_evidence_found()` / `_compute_abstained()`
     calculan lo esperado para cada `final_action` de `NucleusResponse`,
     usando `evidence_authorized`/`memory_evidence` reales -- nunca
     asumiendo verdadero por defecto.

Aislamiento: `tests/conftest.py::_hermetic_base` ya redirige
`core.operational_cycle._DB_PATH` a un vault temporal por test (ver bloque
"Redirect the operational-cycle ledger").
"""
from __future__ import annotations

import sqlite3

import pytest

from core.operational_cycle import (
    CycleObserver,
    OperationalCycle,
    _conn,
    get_cycle_stats,
)


# ---------------------------------------------------------------------------
# 1. Defaults honestos
# ---------------------------------------------------------------------------

class TestHonestDefaults:
    def test_verify_passed_defaults_false(self):
        """Un OperationalCycle recién creado, sin llamar set_verify(), nunca
        debe declarar verify_passed=True -- nada se verificó todavía."""
        cycle = OperationalCycle()
        assert cycle.verify_ran is False
        assert cycle.verify_passed is False

    def test_new_fields_default_false(self):
        cycle = OperationalCycle()
        assert cycle.memory_consulted is False
        assert cycle.evidence_found is False
        assert cycle.abstained is False


# ---------------------------------------------------------------------------
# 2. CycleObserver.set_verify() nunca deja passed=True con ran=False
# ---------------------------------------------------------------------------

class TestSetVerifyHonesty:
    def test_ran_false_forces_passed_false_even_if_caller_says_true(self):
        obs = CycleObserver(channel="telegram", cycle_id="cyc_honest_1")
        obs.set_verify(ran=False, passed=True, rewritten=False)
        assert obs._cycle.verify_ran is False
        assert obs._cycle.verify_passed is False

    def test_ran_true_passed_true_preserved(self):
        obs = CycleObserver(channel="telegram", cycle_id="cyc_honest_2")
        obs.set_verify(ran=True, passed=True, rewritten=False)
        assert obs._cycle.verify_ran is True
        assert obs._cycle.verify_passed is True

    def test_ran_true_passed_false_preserved(self):
        obs = CycleObserver(channel="telegram", cycle_id="cyc_honest_3")
        obs.set_verify(ran=True, passed=False, rewritten=True)
        assert obs._cycle.verify_ran is True
        assert obs._cycle.verify_passed is False


# ---------------------------------------------------------------------------
# 3. Round-trip completo: commit() + lectura directa de op_cycles.db
# ---------------------------------------------------------------------------

class TestPersistedRowHonesty:
    def _read_row(self, cycle_id: str) -> sqlite3.Row:
        conn = _conn()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM op_cycles WHERE id = ?", (cycle_id,)
        ).fetchone()
        conn.close()
        assert row is not None, f"cycle {cycle_id} not persisted"
        return row

    def test_never_ran_verify_persists_passed_zero(self):
        obs = CycleObserver(channel="telegram", cycle_id="cyc_persist_1")
        obs.set_perceive(intent="test", lang="es", words=3)
        obs.set_decide(route="local", strategy="RESOLVE_LOCAL", confidence=0.5)
        obs.set_act(latency_ms=10.0, empty=False, fallback=False)
        # Nunca se llama set_verify() -- comportamiento por defecto.
        obs.set_respond(length=20, source="local")
        obs.commit()

        row = self._read_row("cyc_persist_1")
        assert row["verify_ran"] == 0
        assert row["verify_passed"] == 0

    def test_ran_false_passed_true_caller_still_persists_zero(self):
        """Defensa en profundidad: incluso si un caller (código legacy o
        futuro) construye el ciclo con passed=True pese a ran=False, la
        fila persistida nunca refleja `verify_passed=1`."""
        obs = CycleObserver(channel="telegram", cycle_id="cyc_persist_2")
        obs.set_respond(length=5, source="x")
        # Bypass del setter para simular un caller que no pasó por
        # set_verify() correctamente -- ejercita el enforcement final en
        # `_commit_cycle()`.
        obs._cycle.verify_ran = False
        obs._cycle.verify_passed = True
        obs.commit()

        row = self._read_row("cyc_persist_2")
        assert row["verify_ran"] == 0
        assert row["verify_passed"] == 0, (
            "verify_passed nunca debe persistir como 1 cuando verify_ran=0"
        )

    def test_memory_evidence_abstained_persist_independently(self):
        obs = CycleObserver(channel="telegram", cycle_id="cyc_persist_3")
        obs.set_respond(length=0, source="clarification")
        obs.set_memory(consulted=True, evidence_found=False)
        obs.set_verification(
            delivered=True, grounded=False, verified=False, abstained=True,
        )
        obs.commit()

        row = self._read_row("cyc_persist_3")
        assert row["memory_consulted"] == 1
        assert row["evidence_found"] == 0
        assert row["grounded"] == 0
        assert row["verified"] == 0
        assert row["abstained"] == 1
        # success sigue siendo su propio concepto -- abstenerse con
        # act_empty=False (nunca seteado aquí, default False) y respond_len=0
        # produce success=False (respond_len>0 requerido), lo cual es
        # correcto para este caso: no se envió texto real.
        assert row["success"] == 0

    def test_abstained_with_success_is_a_valid_combination(self):
        """Abstenerse con honestidad (CLARIFICATION con texto real de
        disculpa) SÍ puede ser success=1 -- no son mutuamente excluyentes."""
        obs = CycleObserver(channel="telegram", cycle_id="cyc_persist_4")
        obs.set_act(latency_ms=5.0, empty=False, fallback=False)
        obs.set_respond(length=42, source="clarification")
        obs.set_verification(
            delivered=True, grounded=False, verified=False, abstained=True,
        )
        obs.commit()

        row = self._read_row("cyc_persist_4")
        assert row["success"] == 1
        assert row["abstained"] == 1
        assert row["grounded"] == 0


# ---------------------------------------------------------------------------
# 4. get_cycle_stats() no rompe con las columnas nuevas
# ---------------------------------------------------------------------------

class TestCycleStatsCompat:
    def test_get_cycle_stats_runs_without_error(self):
        obs = CycleObserver(channel="telegram", cycle_id="cyc_stats_1")
        obs.set_act(latency_ms=5.0, empty=False, fallback=False)
        obs.set_respond(length=10, source="local")
        obs.commit()

        stats = get_cycle_stats(days=7)
        assert stats["total"] >= 1


# ---------------------------------------------------------------------------
# 5. pipeline_worker helpers: evidence_found / abstained
# ---------------------------------------------------------------------------

class _FakeNucleusResponse:
    def __init__(
        self,
        final_action="CLARIFICATION",
        evidence=None,
        memory_evidence=None,
        evidence_authorized=False,
        memory_consulted=True,
    ):
        self.final_action = final_action
        self.evidence = evidence or {}
        self.memory_evidence = memory_evidence or {}
        self.evidence_authorized = evidence_authorized
        self.memory_consulted = memory_consulted


class TestPipelineWorkerHonestHelpers:
    def _import(self):
        from core.transport import pipeline_worker as pw
        return pw

    def test_clarification_is_always_abstained(self):
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="CLARIFICATION", evidence_authorized=True,
        )
        assert pw._compute_abstained(resp) is True

    def test_local_with_evidence_authorized_is_not_abstained(self):
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="LOCAL",
            evidence={"context_stars": 2, "top_score": 0.8},
            evidence_authorized=True,
        )
        assert pw._compute_abstained(resp) is False

    def test_local_without_evidence_authorized_is_abstained(self):
        """Ruta LOCAL/PERSONAL_MEMORY que no logró autorizar evidencia real
        (p.ej. resolve_local() no encontró nada) cuenta como abstención,
        aunque el `final_action` no sea literalmente CLARIFICATION."""
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="PERSONAL_MEMORY",
            evidence={"context_stars": 0},
            evidence_authorized=False,
        )
        assert pw._compute_abstained(resp) is True

    def test_market_failure_is_abstained(self):
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="MARKET", evidence={"gap": "no data"},
            evidence_authorized=False,
        )
        assert pw._compute_abstained(resp) is True

    def test_evidence_found_true_even_when_grounded_would_be_false(self):
        """evidence_found es un concepto MÁS AMPLIO que grounded: alguna
        evidencia parcial/irrelevante puede existir (context_stars>0 en
        memory_evidence) aunque la respuesta final haya sido abstención."""
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="CLARIFICATION",
            memory_evidence={"context_stars": 3, "relevance": 0.05},
            evidence_authorized=True,
        )
        assert pw._compute_evidence_found(resp) is True
        # Y sin embargo sigue siendo una abstención honesta.
        assert pw._compute_abstained(resp) is True

    def test_evidence_found_false_when_nothing_located(self):
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="LOCAL",
            evidence={"context_stars": 0},
            memory_evidence={"context_stars": 0},
            evidence_authorized=False,
        )
        assert pw._compute_evidence_found(resp) is False

    def test_online_evidence_found_never_counts_as_abstained_when_authorized(self):
        pw = self._import()
        resp = _FakeNucleusResponse(
            final_action="ONLINE",
            evidence={"sources": ["a", "b"]},
            evidence_authorized=True,
        )
        assert pw._compute_evidence_found(resp) is True
        assert pw._compute_abstained(resp) is False
