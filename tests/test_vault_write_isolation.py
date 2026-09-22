"""
tests/test_vault_write_isolation.py — El vault rastreado no se escribe desde las pruebas.

DEFECTO QUE PREVIENE
--------------------
`vault/learned_rules.jsonl` está RASTREADO en git y, a la vez, lo escribe el
sistema vivo. Ejecutar la suite lo modificaba: comprobado sobre el SHA base
`5f7a5d7`, `pytest tests/integration/test_presencia_pura.py` incrementaba el
contador `applications` de una regla real.

CAUSA
-----
`tests/conftest.py::_hermetic_base` (autouse) YA redirigía `VECTRAX_VAULT_DIR`
a un vault temporal en cada prueba. El problema es que este módulo no lo
honraba: `LearnedRulesStore.__init__` tenía `path: str = RULES_PATH` como
argumento por defecto, y Python evalúa los valores por defecto UNA vez, al
importar el módulo, desde `core.learn.VAULT_DIR` (= raíz del proyecto). La ruta
quedaba congelada y el aislamiento del conftest no tenía ningún efecto sobre
ella — la misma clase de fallo que el propio conftest ya compensaba caso a caso
para `observation_ledger` y `user_memory` con `monkeypatch.setattr`.

CORRECCIÓN
----------
La ruta se resuelve EN CADA ACCESO (`default_rules_path()`), honrando
`VECTRAX_VAULT_DIR` — el mismo convenio que ya usan `core/audit_ledger.py`,
`observability/audit_engine.py` y `core/learn/verification_ledger.py`. Sin esa
variable se conserva EXACTAMENTE la ruta de producción de hoy.

Estas pruebas NO modifican, restauran ni limpian el vault: solo leen su hash.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.learn.learned_rules import (  # noqa: E402
    RULES_PATH,
    LearnedRule,
    LearnedRulesStore,
    default_rules_path,
    get_rules_store,
    reset_rules_store,
)

_TRACKED_RULES = _ROOT / "vault" / "learned_rules.jsonl"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _clean_singleton():
    """El singleton no debe filtrarse entre pruebas ni hacia el resto de la suite."""
    reset_rules_store()
    yield
    reset_rules_store()


# ---------------------------------------------------------------------------
# Resolución de ruta
# ---------------------------------------------------------------------------

class TestPathResolution:

    def test_explicit_path_is_used_verbatim(self, tmp_path):
        target = tmp_path / "reglas.jsonl"
        store = LearnedRulesStore(path=str(target))
        assert store.path == str(target)

    def test_explicit_path_writes_only_inside_tmp_path(self, tmp_path, monkeypatch):
        # Aunque la variable de entorno apunte a otro sitio, el override gana.
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "otro"))
        target = tmp_path / "solo_aqui" / "reglas.jsonl"
        before = _sha256(_TRACKED_RULES)

        store = LearnedRulesStore(path=str(target))
        rid = store.add_rule(LearnedRule(description="aislada", pattern="p", category="c"))
        assert store.activate_rule(rid) is True
        assert store.record_application(rid) is True

        assert target.is_file(), "debe escribir en la ruta inyectada"
        assert store.get(rid)["applications"] == 1
        assert _sha256(_TRACKED_RULES) == before, "el vault rastreado no puede cambiar"
        assert not (tmp_path / "otro").exists(), "no debe crear el directorio del env"

    def test_vectrax_vault_dir_is_honoured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "vault"))
        assert default_rules_path() == str(tmp_path / "vault" / "learned_rules.jsonl")
        assert LearnedRulesStore().path == str(tmp_path / "vault" / "learned_rules.jsonl")

    def test_env_var_is_read_at_access_time_not_at_import(self, tmp_path, monkeypatch):
        """Un store YA construido debe seguir a la variable si esta cambia.

        Es la propiedad concreta que hacía imposible aislar el singleton.
        """
        store = LearnedRulesStore()          # construido ANTES del monkeypatch
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "tarde"))
        assert store.path == str(tmp_path / "tarde" / "learned_rules.jsonl")

    def test_no_override_preserves_the_production_path(self, monkeypatch):
        monkeypatch.delenv("VECTRAX_VAULT_DIR", raising=False)
        assert default_rules_path() == RULES_PATH
        assert LearnedRulesStore().path == RULES_PATH
        assert get_rules_store().path == RULES_PATH
        assert RULES_PATH.endswith("vault/learned_rules.jsonl")

    def test_empty_override_falls_back_to_production(self, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", "")
        assert default_rules_path() == RULES_PATH

    def test_construction_has_no_side_effects_on_disk(self, tmp_path, monkeypatch):
        """Construir un store no debe crear directorios: antes lo hacía en __init__."""
        target = tmp_path / "nunca_creado"
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(target))
        LearnedRulesStore()
        assert not target.exists()


# ---------------------------------------------------------------------------
# Aislamiento entre almacenes
# ---------------------------------------------------------------------------

class TestStoresAreIsolated:

    def test_two_stores_do_not_share_data(self, tmp_path):
        a = LearnedRulesStore(path=str(tmp_path / "a" / "rules.jsonl"))
        b = LearnedRulesStore(path=str(tmp_path / "b" / "rules.jsonl"))

        rid_a = a.add_rule(LearnedRule(description="solo A", pattern="a"))
        rid_b = b.add_rule(LearnedRule(description="solo B", pattern="b"))

        assert a.get(rid_a) is not None and a.get(rid_b) is None
        assert b.get(rid_b) is not None and b.get(rid_a) is None
        assert len(a.list_all()) == 1
        assert len(b.list_all()) == 1

    def test_reset_rules_store_drops_the_singleton(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "v1"))
        first = get_rules_store()
        reset_rules_store()
        second = get_rules_store()
        assert first is not second


# ---------------------------------------------------------------------------
# Guard de regresión: la suite de aprendizaje no toca el archivo rastreado
# ---------------------------------------------------------------------------

class TestTrackedVaultIsNeverWritten:

    def test_full_convergence_cycle_under_override_leaves_the_vault_intact(
        self, tmp_path, monkeypatch,
    ):
        """Reproduce la operación EXACTA que contaminaba.

        `run_convergence_cycle()` -> fase [7] -> `record_application()`
        (core/nucleus/total_convergence.py). Con el vault redirigido, el archivo
        rastreado no puede moverse.
        """
        before = _sha256(_TRACKED_RULES)
        if before is None:
            pytest.skip("vault/learned_rules.jsonl no está presente en este árbol")

        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "vault"))
        import core.state_manager as sm
        monkeypatch.setattr(sm, "STATE_PATH", str(tmp_path / "cognition_state.json"))
        monkeypatch.setattr(sm, "RUNTIME_DIR", str(tmp_path))
        reset_rules_store()

        # Una regla activa cuyo patrón case con el intent del ciclo, para que la
        # fase [7] intente registrar la aplicación de verdad.
        store = get_rules_store()
        rid = store.add_rule(LearnedRule(description="d", pattern="text", category="general"))
        store.activate_rule(rid)

        from core.convergence_hook import run_convergence_cycle
        try:
            run_convergence_cycle("mensaje de prueba", source="test", owner="tester")
        finally:
            try:
                from core.nucleus import total_convergence
                total_convergence.reset_convergence_engine()
            except Exception:
                pass

        assert _sha256(_TRACKED_RULES) == before, (
            "El ciclo de convergencia escribió en el vault RASTREADO."
        )
        assert (tmp_path / "vault" / "learned_rules.jsonl").is_file(), (
            "Debería haber escrito en el vault redirigido."
        )

    def test_the_contaminating_test_body_is_now_isolated(self, tmp_path, monkeypatch):
        """Ejecuta EN PROCESO el cuerpo del test que contaminaba, con su mismo
        aislamiento, y comprueba que el archivo rastreado no se mueve.

        Deliberadamente NO se lanza un pytest anidado en subproceso: el proyecto
        depende de `sentence-transformers` (y por tanto de torch), así que un
        proceso hijo vuelve a pagar la importación del stack de embeddings
        dentro del propio quality gate — coste alto e impredecible en CI para
        una propiedad que se puede demostrar aquí mismo.

        La verificación de extremo a extremo ("la suite COMPLETA no toca el
        vault") se hace por MEDICIÓN, no dentro de la suite:

            git worktree add /tmp/base <SHA_BASE>
            (cd /tmp/base && python -m pytest tests -q; git status --short vault/)
            (cd .       && python -m pytest tests -q; git status --short vault/)

        Sobre 5f7a5d7 la primera imprime `M vault/learned_rules.jsonl` y la
        segunda no imprime nada.
        """
        before = _sha256(_TRACKED_RULES)
        if before is None:
            pytest.skip("vault/learned_rules.jsonl no está presente en este árbol")

        # La ruta de PRODUCCIÓN es el archivo rastreado: eso es lo que el store
        # usaba siempre, ignorando el aislamiento que `conftest._hermetic_base`
        # ya aplicaba. Ahora el aislamiento se honra y el store resuelve fuera.
        assert RULES_PATH == str(_TRACKED_RULES)
        assert LearnedRulesStore().path != RULES_PATH, (
            "conftest._hermetic_base debería tener el vault redirigido a tmp"
        )

        monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path / "vault"))
        import core.state_manager as sm
        monkeypatch.setattr(sm, "STATE_PATH", str(tmp_path / "cognition_state.json"))
        monkeypatch.setattr(sm, "RUNTIME_DIR", str(tmp_path))
        reset_rules_store()

        from core.nucleus.presencia_pura import activate, deactivate
        from core.convergence_hook import run_convergence_cycle

        activate()
        try:
            record = run_convergence_cycle(
                "mensaje procesado en presencia pura", source="test", owner="test_user",
            )
            assert record is not None
            assert "memory" in record.phases_completed
            assert "gravitation" in record.phases_completed
        finally:
            deactivate()
            try:
                from core.nucleus import total_convergence
                total_convergence.reset_convergence_engine()
            except Exception:
                pass

        assert _sha256(_TRACKED_RULES) == before, (
            "El cuerpo de tests/integration/test_presencia_pura.py volvió a "
            "escribir en vault/learned_rules.jsonl."
        )
