"""
tests/test_idea_constitutional_visibility.py — Un bloqueo no es una omisión.

EL PROBLEMA
-----------
Los tres llamadores internos de `IdeaStore.add()` —`ingest_from_router_learning`,
`ingest_from_convergence_learner`, `ingest_from_router_analysis`— viven dentro
de un `try/except Exception`. Al hacer efectivo el control constitucional,
un bloqueo caía en ese manejador genérico junto a un timeout de red o un JSON
corrupto, y la ingesta devolvía 0 sin distinguir nada.

Desde fuera, tres cosas muy distintas se veían iguales:

  * "el control decidió que esta idea no debe crearse, y estas reglas lo
    explican"
  * "el control no pudo pronunciarse"
  * "no había ideas que importar"

LO QUE SE EXIGE
---------------
1. Bloqueo constitucional -> la idea NO se crea Y el bloqueo es VISIBLE:
   resultado estructurado, contador y registro auditable con correlation_id,
   reglas y motivo.
2. Error técnico -> tratamiento de siempre, sin contarse como bloqueo.
3. Idea permitida -> se crea con normalidad.

No se cambia ninguna política: `create_idea` sigue siendo AUTO.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.idea_store import (  # noqa: E402
    ConstitutionalBlock,
    ConstitutionalGuardUnavailable,
    IdeaPriority,
    IdeaSource,
    IdeaStore,
)
from core.operator import constitutional_mode  # noqa: E402
from core.operator.constitutional_guard import GateDecision  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(
        constitutional_mode, "_MODE_PATH",
        str(tmp_path / "mode.json"), raising=False,
    )
    constitutional_mode._cache["mode"] = None
    yield
    constitutional_mode._cache["mode"] = None


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Store aislado, y un `_DATA_DIR` propio para las ingestas.

    `ingest_from_router_learning()` lee `<_DATA_DIR>/router_proposals.jsonl`.
    Sin redirigirlo, la prueba leería las propuestas REALES del repositorio.
    """
    import core.idea_store as _is
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(_is, "_DATA_DIR", str(data), raising=False)
    s = IdeaStore(path=str(tmp_path / "ideas.jsonl"))
    s._test_data_dir = str(data)
    return s


def _write_proposals(store, n):
    """Deja `n` propuestas pendientes donde la ingesta las busca."""
    import json
    path = Path(store._test_data_dir) / "router_proposals.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            # `_semantic_dedup_key` identifica el PATRÓN, no la ocurrencia:
            # si solo variara el texto, las tres colapsarían en una sola idea
            # y la prueba mediría la deduplicación en vez del bloqueo.
            f.write(json.dumps({
                "cycle_id": i, "proposal_type": "conflict_pattern",
                "priority": "high", "status": "pending", "created_at": 1000 + i,
                "description": f"propuesta {i}", "suggested_action": f"accion {i}",
                "affected_component": f"componente_{i}", "evidence": {},
            }) + "\n")


def _blocked_gate(reason="Ley 7 lo impide", rules=("L7=block",)):
    """Una `GateDecision` que deniega, con reglas y correlación reales."""
    import time
    return GateDecision(
        allowed=False, reason=reason, correlation_id="CID-BLOQUEO",
        action="create_idea", application_point="core.idea_store.IdeaStore.create",
        actor="router_learning", mode=constitutional_mode.ACTIVE,
        timestamp=time.time(), rules=rules,
    )


def _add(store, title="idea de prueba"):
    return store.add(
        title=title, description="d", source=IdeaSource.ROUTER_LEARNING,
        priority=IdeaPriority.MEDIUM, impact_score=0.5,
        affected_component="core",
    )


# ===========================================================================
# 3. Idea permitida -> se crea con normalidad
# ===========================================================================

class TestAllowedIdeaIsCreated:

    def test_an_allowed_idea_is_created(self, store):
        idea = _add(store)
        assert idea is not None
        assert idea.status.value == "pending"

    def test_no_block_is_recorded(self, store):
        _add(store)
        assert store.constitutional_block_count() == 0
        assert store.constitutional_blocks() == []

    def test_create_idea_is_still_auto(self):
        """No se cambia la política confirmada por Mario."""
        from core.operator.decision_authority import AUTO_ACTIONS, check_authority
        assert "create_idea" in AUTO_ACTIONS
        assert check_authority(
            "create_idea", governor_mode="observe", risk_level="LOW",
        ).auto_approved is True


# ===========================================================================
# 1. Bloqueo constitucional -> idea NO creada + bloqueo VISIBLE
# ===========================================================================

class TestConstitutionalBlockIsVisible:

    def test_the_idea_is_not_created(self, store):
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            return_value=_blocked_gate(),
        ):
            with pytest.raises(ConstitutionalBlock):
                _add(store)
        assert store.all() == [], "la idea se creó pese al bloqueo"

    def test_the_exception_carries_the_audit_fields(self, store):
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            return_value=_blocked_gate(),
        ):
            with pytest.raises(ConstitutionalBlock) as excinfo:
                _add(store)
        exc = excinfo.value
        assert exc.correlation_id == "CID-BLOQUEO"
        assert exc.rules == ("L7=block",)
        assert "Ley 7 lo impide" in exc.reason
        assert exc.to_dict()["rules"] == ["L7=block"]

    def test_an_ingest_records_the_block_instead_of_swallowing_it(self, store):
        """El caso real: dentro del `except Exception` de la ingesta."""
        _write_proposals(store, 1)
        with patch.object(
            IdeaStore, "add",
            side_effect=ConstitutionalBlock("Ley 7 lo impide", _blocked_gate()),
        ):
            added = store.ingest_from_router_learning()

        assert added == 0
        assert store.constitutional_block_count() >= 1, (
            "el bloqueo se absorbió en el except genérico: invisible"
        )
        block = store.constitutional_blocks()[0]
        assert block["correlation_id"] == "CID-BLOQUEO"
        assert block["rules"] == ["L7=block"]
        assert "Ley 7 lo impide" in block["reason"]
        assert block["source"] == "router_learning"

    def test_the_block_is_logged_auditably(self, store, caplog):
        with caplog.at_level(logging.WARNING, logger="vectrax.idea_store"):
            store._record_constitutional_block(
                ConstitutionalBlock("Ley 7 lo impide", _blocked_gate()),
                source="router_learning", title="idea X",
            )
        msg = caplog.text
        assert "CONSTITUTIONAL" in msg
        assert "CID-BLOQUEO" in msg
        assert "L7=block" in msg
        assert "Ley 7 lo impide" in msg

    def test_the_counter_is_bounded(self, store):
        for _ in range(store.MAX_CONSTITUTIONAL_BLOCKS + 25):
            store._record_constitutional_block(
                ConstitutionalBlock("r", _blocked_gate()),
                source="router_learning", title="t",
            )
        assert store.constitutional_block_count() == store.MAX_CONSTITUTIONAL_BLOCKS

    def test_blocks_are_returned_newest_first(self, store):
        for i in range(3):
            store._record_constitutional_block(
                ConstitutionalBlock(f"motivo {i}", _blocked_gate()),
                source="router_learning", title=f"idea {i}",
            )
        titles = [b["title"] for b in store.constitutional_blocks()]
        assert titles == ["idea 2", "idea 1", "idea 0"]


# ===========================================================================
# 2. Error técnico -> tratamiento de siempre, NO contado como bloqueo
# ===========================================================================

class TestTechnicalErrorKeepsItsOldTreatment:

    def test_a_crashed_guard_is_not_a_verdict(self, store):
        """Ambos impiden la acción, pero significan cosas opuestas."""
        assert issubclass(ConstitutionalGuardUnavailable, ConstitutionalBlock)
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ConstitutionalGuardUnavailable) as excinfo:
                _add(store)
        assert "no pudo evaluar" in str(excinfo.value)
        assert store.all() == [], "falla cerrado: la idea no se crea"

    def test_a_crashed_guard_is_not_counted_as_a_block(self, store):
        _write_proposals(store, 1)
        with patch.object(
            IdeaStore, "add",
            side_effect=ConstitutionalGuardUnavailable("control caído"),
        ):
            added = store.ingest_from_router_learning()
        assert added == 0
        assert store.constitutional_block_count() == 0, (
            "una avería técnica se contó como bloqueo constitucional"
        )

    def test_an_ordinary_exception_keeps_the_old_treatment(self, store):
        _write_proposals(store, 1)
        with patch.object(IdeaStore, "add", side_effect=ValueError("json roto")):
            added = store.ingest_from_router_learning()
        assert added == 0
        assert store.constitutional_block_count() == 0

    def test_the_three_outcomes_are_distinguishable(self, store):
        """El punto entero: tres desenlaces, tres lecturas distintas."""
        # (a) permitido
        assert _add(store, "permitida") is not None
        assert store.constitutional_block_count() == 0
        # (b) bloqueo -> contador sube
        store._record_constitutional_block(
            ConstitutionalBlock("Ley 7", _blocked_gate()),
            source="router_learning", title="bloqueada",
        )
        assert store.constitutional_block_count() == 1
        # (c) avería -> contador NO sube
        with patch(
            "core.operator.constitutional_guard.enforce_check",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ConstitutionalGuardUnavailable):
                _add(store, "averiada")
        assert store.constitutional_block_count() == 1
        assert len(store.all()) == 1

# ===========================================================================
# Resultado estructurado: el contador LLEGA al consumidor, sin confundirse
# ===========================================================================

class TestTheStructuredResultReachesTheConsumer:

    def _refresh_with(self, store, *, side_effect, n=1):
        _write_proposals(store, n)
        with patch.object(IdeaStore, "add", side_effect=side_effect):
            return store.refresh()

    def test_the_shape_separates_counts_from_blocks(self, store):
        result = store.refresh()
        assert set(result) == {"added", "added_total", "constitutional_blocked"}
        assert isinstance(result["added"], dict)
        assert isinstance(result["added_total"], int)
        assert isinstance(result["constitutional_blocked"], int)

    def test_summing_the_result_is_impossible_not_wrong(self, store):
        """El error que la forma anterior permitía: contar bloqueos como altas.

        Con las cuentas ANIDADAS, `sum()` revienta en el acto en vez de
        devolver un número equivocado en silencio.
        """
        result = store.refresh()
        with pytest.raises(TypeError):
            sum(result.values())

    def test_a_blocked_idea_is_never_counted_as_added(self, store):
        result = self._refresh_with(
            store, side_effect=ConstitutionalBlock("Ley 7", _blocked_gate()), n=1,
        )
        assert result["added_total"] == 0
        assert result["added"]["router_proposals"] == 0
        assert result["constitutional_blocked"] >= 1

    def test_the_route_surfaces_the_block_count(self):
        """El consumidor HTTP real, no un doble."""
        import ast
        route = _ROOT / "services" / "core" / "routes" / "ideas.py"
        tree = ast.parse(route.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "refresh_ideas"
        )
        keys = {
            n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert "constitutional_blocked" in keys, (
            "la ruta no expone el contador: el bloqueo no llega al consumidor"
        )

    @pytest.mark.parametrize("relpath", [
        "services/core/routes/ideas.py",
        "core/meta_loop.py",
        "vectrax/telegram_gateway.py",
    ])
    def test_every_consumer_reads_the_block_count(self, relpath):
        """Los TRES consumidores de `refresh()`, no solo la ruta.

        Un contador que nadie lee es lo mismo que no tenerlo: es el defecto
        que ya apareció con `operational_consumer`, declarado y sin
        consumidores reales.
        """
        text = (_ROOT / relpath).read_text(encoding="utf-8")
        assert "constitutional_blocked" in text, (
            f"{relpath} no lee el contador de bloqueos"
        )
        assert "added_total" in text, (
            f"{relpath} no usa el total ya calculado y podría volver a sumar"
        )

    def test_no_consumer_sums_the_refresh_result(self):
        """Ningún consumidor puede volver a sumar el resultado entero."""
        import re
        for rel in ("services/core/routes/ideas.py", "core/meta_loop.py",
                    "vectrax/telegram_gateway.py"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert not re.search(r"sum\(\s*(added|result)\.values\(\)\s*\)", text), (
                f"{rel} vuelve a sumar el resultado de refresh()"
            )


# ===========================================================================
# Auditoría PERSISTENTE: no un log, un asiento que sobrevive al proceso
# ===========================================================================

class TestThePersistentAudit:

    @staticmethod
    def _details(row):
        """`metadata` se guarda como texto JSON en SQLite."""
        import json
        meta = row.get("metadata")
        if isinstance(meta, str):
            meta = json.loads(meta)
        return (meta or {}).get("details", {})

    def _ledger_rows(self, title=None):
        from core import audit_ledger
        rows = [
            r for r in audit_ledger.query(limit=200)
            if str(r.get("action", "")).startswith("constitutional_block:")
        ]
        if title is not None:
            rows = [r for r in rows if self._details(r).get("title") == title]
        return rows

    def test_the_block_lands_in_the_audit_ledger(self, store):
        store._record_constitutional_block(
            ConstitutionalBlock("Ley 7 lo impide", _blocked_gate()),
            source="router_learning", title="idea auditada",
        )
        rows = self._ledger_rows(title="idea auditada")
        assert rows, "el bloqueo no dejó asiento en `audit_ledger`"
        details = self._details(rows[0])
        for field in ("correlation_id", "title", "rules", "reason"):
            assert field in details, f"el asiento no guarda {field!r}"
        assert details["correlation_id"] == "CID-BLOQUEO"
        assert details["title"] == "idea auditada"
        assert details["rules"] == ["L7=block"]
        assert "Ley 7 lo impide" in details["reason"]

    def test_the_entry_survives_the_process(self, store, tmp_path):
        """La prueba de fondo: otro proceso lo lee de `<vault>/audit_ledger.db`.

        Un WARNING en el log no es auditoría: se rota y se pierde. Esto sí
        sobrevive porque está en SQLite, y se demuestra leyéndolo desde un
        intérprete NUEVO que no comparte memoria con este.
        """
        import json
        import os
        import subprocess
        import sys

        from core import audit_ledger

        store._record_constitutional_block(
            ConstitutionalBlock("Ley 7 lo impide", _blocked_gate()),
            source="router_learning", title="idea que sobrevive",
        )
        db = audit_ledger.LEDGER_PATH
        assert os.path.isfile(db), f"no existe el almacén persistente {db}"

        code = (
            "import json, sqlite3, sys\n"
            "c = sqlite3.connect(sys.argv[1])\n"
            "rows = c.execute("
            "  \"SELECT action, metadata FROM audit_ledger \"\n"
            "  \"WHERE action LIKE 'constitutional_block:%'\").fetchall()\n"
            "print(json.dumps([{'action': a, 'metadata': json.loads(m)} "
            "for a, m in rows]))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code, db],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, out.stderr
        rows = json.loads(out.stdout)
        assert rows, "otro proceso no encontró ningún asiento"
        mine = [
            r for r in rows
            if r["metadata"].get("details", {}).get("title") == "idea que sobrevive"
        ]
        assert mine, "otro proceso no encontró ESTE asiento: no era persistente"
        details = mine[0]["metadata"]["details"]
        assert details["correlation_id"] == "CID-BLOQUEO"
        assert details["title"] == "idea que sobrevive"
        assert details["rules"] == ["L7=block"]
        assert "Ley 7 lo impide" in details["reason"]

    def test_the_store_is_named_and_is_sqlite(self):
        from core import audit_ledger
        assert audit_ledger.LEDGER_PATH.endswith("audit_ledger.db")
        assert "audit_ledger" in audit_ledger._CREATE_TABLE

    def test_a_ledger_failure_does_not_break_the_ingest(self, store, caplog):
        """No poder asentar es grave y se dice; no convierte el bloqueo en error."""
        with patch("core.operator.ledger_bridge.record_event",
                   side_effect=RuntimeError("ledger caído")):
            with caplog.at_level(logging.ERROR, logger="vectrax.idea_store"):
                store._record_constitutional_block(
                    ConstitutionalBlock("Ley 7", _blocked_gate()),
                    source="router_learning", title="t",
                )
        assert "no se pudo asentar" in caplog.text
        assert store.constitutional_block_count() == 1


# ===========================================================================
# Los seis desenlaces que exigió la revisión, juntos
# ===========================================================================

class TestTheSixRequiredOutcomes:

    # Las propuestas se escriben donde la ingesta las busca de verdad
    # (`<_DATA_DIR>/router_proposals.jsonl`), no se doblan con un mock.

    def test_block_does_not_persist_the_idea(self, store):
        with patch("core.operator.constitutional_guard.enforce_check",
                   return_value=_blocked_gate()):
            with pytest.raises(ConstitutionalBlock):
                _add(store)
        assert store.all() == []
        path = Path(store._path)
        if path.exists():
            assert not [l for l in path.read_text(encoding="utf-8").splitlines()
                        if l.strip()], "la idea quedó escrita en el JSONL"

    def test_the_rest_of_the_batch_continues(self, store):
        """Una idea bloqueada no puede silenciar a las demás."""
        _write_proposals(store, 3)
        calls = {"n": 0}
        real_add = IdeaStore.add

        def _selective(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConstitutionalBlock("Ley 7", _blocked_gate())
            return real_add(self, *a, **kw)

        with patch.object(IdeaStore, "add", _selective):
            added = store.ingest_from_router_learning()

        assert calls["n"] == 3, "el lote se detuvo en la idea bloqueada"
        assert added == 2, "las ideas no bloqueadas debieron crearse"
        assert store.constitutional_block_count() == 1

    def test_guard_crash_does_not_persist_and_is_distinct(self, store):
        with patch("core.operator.constitutional_guard.enforce_check",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(ConstitutionalGuardUnavailable):
                _add(store)
        assert store.all() == [], "falla cerrado: no persiste"
        assert store.constitutional_block_count() == 0, (
            "una avería se contó como veredicto"
        )

    def test_technical_error_keeps_its_treatment(self, store):
        _write_proposals(store, 1)
        with patch.object(IdeaStore, "add", side_effect=ValueError("json roto")):
            added = store.ingest_from_router_learning()
        assert added == 0
        assert store.constitutional_block_count() == 0

    def test_authorised_persists_exactly_once(self, store):
        idea = _add(store, "autorizada")
        assert idea is not None
        assert len(store.all()) == 1
        lines = [
            l for l in Path(store._path).read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(lines) == 1, f"la idea se escribió {len(lines)} veces"

    def test_no_block_is_ever_counted_as_added(self, store):
        _write_proposals(store, 4)
        with patch.object(
            IdeaStore, "add",
            side_effect=ConstitutionalBlock("Ley 7", _blocked_gate()),
        ):
            result = store.refresh()
        assert result["added_total"] == 0
        assert result["constitutional_blocked"] == 4
