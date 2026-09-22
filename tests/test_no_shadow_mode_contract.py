"""
tests/test_no_shadow_mode_contract.py — El modo sombra no puede volver.

POR QUÉ
-------
Mario no autorizó un modo sombra. El puente causal quedó IMPLEMENTADO, ACTIVO
y EFECTIVO desde el despliegue: no existe criterio paralelo, ni aprendizaje
simulado, ni variable para posponer el efecto, ni una segunda activación.

Un diseño así se reintroduce solo: basta con que alguien añada un
`if shadow:` "temporal" para volver al punto de partida sin que nadie lo note.
Este escáner recorre el CÓDIGO VIVO y falla si reaparece el vocabulario de
sombra. Es el mismo patrón que `tests/test_permission_literals_contract.py`:
un contrato verificado por AST/texto sobre el árbol real, no una convención
escrita en un comentario.

ALCANCE
-------
Se escanea el código vivo. Se excluyen `tests/` (este archivo nombra los
tokens prohibidos a propósito, como sonda) y la documentación histórica de
esta misma corrección, que sí puede describir lo que se eliminó.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: Directorios que no son código vivo del producto.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache",
    "build", "dist", ".mypy_cache", ".ruff_cache",
    # `tests` se excluye porque este mismo archivo contiene los literales
    # prohibidos como sonda; escanearlo se detectaría a sí mismo.
    "tests",
    # Documentación: puede (y debe poder) narrar la corrección histórica.
    "docs",
}

#: Identificadores prohibidos en TODO el código vivo. Son inequívocos: ninguno
#: existe hoy en el árbol, así que cualquier aparición futura solo puede venir
#: de reintroducir el diseño en sombra del puente causal.
_FORBIDDEN = {
    "ELIGIBLE_SHADOW": re.compile(r"\bELIGIBLE_SHADOW\b"),
    "shadow_criterion": re.compile(r"\bshadow_criterion\b"),
    "learning_shadow": re.compile(r"\blearning_shadow\b"),
    "MODE_SHADOW": re.compile(r"\bMODE_SHADOW\b"),
}

#: La frase genérica "shadow mode" NO se puede prohibir en todo el árbol: este
#: repositorio ya tiene un subsistema Shadow Mode PREEXISTENTE y ajeno al
#: puente causal — `core/shadow_mode.py`, el filtro constitucional de Fase 1
#: (`core/operator/constitutional_guard.py`, `core/idea_store.py`) y
#: `core/sandbox_runner.py`. Mario no pidió eliminar eso, y hacerlo quedaría muy
#: fuera del alcance de este PR. Lo que sí queda prohibido es que la frase
#: aparezca en la SUPERFICIE CAUSAL, que es donde significaría exactamente lo
#: que se eliminó.
_SHADOW_PHRASE = re.compile(r"\bshadow[ _-]?mode\b", re.IGNORECASE)

#: Los archivos que este PR hace responsables del puente causal.
_CAUSAL_SURFACE = (
    "core/learn/causal_learning.py",
    "core/learn/criterion.py",
    "core/learn/convergence_registry.py",
    "core/nucleus/internal_evidence.py",
    "core/nucleus/evidence_intent.py",
    "connectors/etoro/entry_validator.py",
)

#: Variables de entorno que no pueden existir: ninguna puede posponer el efecto.
_FORBIDDEN_ENV = re.compile(
    r"VECTRAX_(?:CAUSAL|LEARNING)_(?:SHADOW|MODE)\b", re.IGNORECASE
)


def _iter_live_files():
    """Archivos de código vivo.

    `if not path.is_file(): continue` no es decorativo: en este repositorio
    `core/alerta.py` es un DIRECTORIO, y sin el guard el escáner revienta con
    `IsADirectoryError` (mismo motivo que en test_permission_literals_contract).
    """
    for path in _ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(_ROOT).parts):
            continue
        if not path.is_file():
            continue
        yield path


@pytest.fixture(scope="module")
def live_files():
    files = list(_iter_live_files())
    assert len(files) > 100, (
        f"el escáner solo encontró {len(files)} archivos: si dejara de recorrer "
        "el árbol, este contrato pasaría vacío y no protegería nada"
    )
    return files


class TestScannerItself:
    """Si el escáner deja de funcionar, el resto pasaría en falso."""

    def test_it_reaches_the_causal_module(self, live_files):
        names = {p.relative_to(_ROOT).as_posix() for p in live_files}
        assert "core/learn/causal_learning.py" in names
        assert "core/learn/criterion.py" in names
        assert "core/learn/convergence_registry.py" in names

    def test_it_would_catch_a_reintroduction(self):
        """Sonda: los patrones detectan de verdad lo que dicen detectar."""
        assert _FORBIDDEN["ELIGIBLE_SHADOW"].search("state = ELIGIBLE_SHADOW")
        assert _FORBIDDEN["shadow_criterion"].search('d["shadow_criterion"] = x')
        assert _FORBIDDEN["learning_shadow"].search("learning_shadow = True")
        assert _FORBIDDEN["MODE_SHADOW"].search("mode = MODE_SHADOW")
        assert _SHADOW_PHRASE.search("# runs in shadow mode for now")
        assert _SHADOW_PHRASE.search("SHADOW_MODE = True")
        assert _FORBIDDEN_ENV.search('os.environ["VECTRAX_CAUSAL_SHADOW"]')

    def test_the_probes_do_not_match_innocent_text(self):
        assert not _SHADOW_PHRASE.search("overshadowed by the model")
        assert not _FORBIDDEN["shadow_criterion"].search("effective_criterion")

    def test_the_preexisting_shadow_subsystem_is_still_there(self):
        """Guard del alcance: no se ha borrado un subsistema ajeno al puente.

        `core/shadow_mode.py` existía antes de este PR y no tiene nada que ver
        con el aprendizaje causal. Si algún día desaparece, que sea por una
        decisión propia, no como efecto colateral de este contrato.
        """
        assert (_ROOT / "core" / "shadow_mode.py").is_file()


class TestNoShadowVocabularyInLiveCode:

    @pytest.mark.parametrize("token", sorted(_FORBIDDEN))
    def test_token_is_absent(self, token, live_files):
        pattern = _FORBIDDEN[token]
        offenders = []
        for path in live_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}"
                    )
        assert not offenders, (
            f"reapareció el vocabulario de sombra ({token}) en código vivo:\n  "
            + "\n  ".join(offenders)
        )

    @pytest.mark.parametrize("relpath", _CAUSAL_SURFACE)
    def test_the_causal_surface_never_says_shadow_mode(self, relpath):
        path = _ROOT / relpath
        assert path.is_file(), f"la superficie causal perdió {relpath}"
        offenders = [
            f"{relpath}:{lineno}: {line.strip()}"
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            if _SHADOW_PHRASE.search(line)
        ]
        assert not offenders, (
            "reapareció el modo sombra en la superficie causal:\n  "
            + "\n  ".join(offenders)
        )

    def test_no_environment_variable_can_defer_the_effect(self, live_files):
        offenders = []
        for path in live_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if _FORBIDDEN_ENV.search(line):
                    offenders.append(
                        f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}"
                    )
        assert not offenders, (
            "existe una variable de entorno capaz de posponer el aprendizaje:\n  "
            + "\n  ".join(offenders)
        )


class TestTheCausalModuleHasNoModes:
    """El contrato, además, comprobado sobre la API real."""

    def test_no_mode_attribute_survives(self):
        from core.learn import causal_learning as cl
        for attr in ("MODE_SHADOW", "MODE_ENFORCE", "MODES", "DEFAULT_MODE",
                     "STATE_ELIGIBLE_SHADOW", "STATE_AWAITING_EVIDENCE"):
            assert not hasattr(cl, attr), f"quedó un vestigio de sombra: {attr}"

    def test_the_store_schema_has_no_mode_column(self, tmp_path):
        from core.learn import causal_learning as cl
        conn = cl.connect(str(tmp_path / "c.db"))
        try:
            for table in ("learning_policies", "promotion_decisions",
                          "learning_traces", "learning_applications"):
                cols = {
                    r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
                }
                assert "mode" not in cols, f"{table} conserva una columna 'mode'"
                assert "execution_mode" not in cols, (
                    f"{table} conserva una columna 'execution_mode'"
                )
        finally:
            conn.close()

    def test_the_criterion_exposes_no_shadow(self):
        from core.learn import criterion
        assert not hasattr(criterion, "shadow_criterion")
        assert hasattr(criterion, "effective_criterion")


class TestNoMassiveBackfill:
    """Las 121.206 convergencias históricas no se recorren."""

    def test_the_bridge_only_evaluates_what_it_is_given(self, tmp_path):
        from core.learn import causal_learning as cl
        db = str(tmp_path / "c.db")
        # Almacén con política pero SIN entradas: no puede inventarse trabajo.
        assert cl.evaluate_live_convergences([], db_path=db)["evaluated"] == 0
        assert cl.list_learnings(db_path=db) == []

    def test_there_is_no_backfill_entry_point(self):
        from core.learn import causal_learning as cl
        for attr in dir(cl):
            assert "backfill" not in attr.lower(), (
                f"existe un punto de entrada de backfill: {attr}"
            )

    def test_the_bridge_never_reads_convergence_history(self):
        """Se comprueba sobre los IMPORTS reales, no sobre el texto.

        El módulo NOMBRA `convergence_history.db` en su documentación para decir
        que no la toca; lo que importa es que no la importe ni la abra.
        """
        import ast
        path = _ROOT / "core" / "learn" / "causal_learning.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                for alias in node.names:
                    imported.add(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        offenders = [m for m in imported if "convergence_history" in m]
        assert not offenders, (
            f"el puente causal importa la base histórica de 25,4 GB: {offenders}"
        )
        # Y ninguna cadena literal apunta a ese archivo.
        literals = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        assert not [s for s in literals if s.endswith("convergence_history.db")]

    def test_the_per_cycle_budget_is_enforced(self, tmp_path):
        """`meta_loop` no puede bloquearse evaluando un escaneo global."""
        from core.learn import causal_learning as cl
        db = str(tmp_path / "c.db")
        entries = [
            {
                "convergence_id": f"CONV-{i}",
                "domains": ["market", "market"],
                "source_pattern_ids": ["A", "B"],
                "combined_cc": 0.9, "combined_hits": 9,
            }
            for i in range(200)
        ]
        result = cl.evaluate_live_convergences(
            entries, limit=10, stats_fetcher=lambda fp: None, db_path=db,
        )
        assert result["evaluated"] == 10
        assert result["truncated"] is True

    def test_dissolutions_are_never_starved_by_the_budget(self, tmp_path):
        """Dejar de afirmar algo falso es más urgente que afirmar algo nuevo."""
        from core.learn import causal_learning as cl
        db = str(tmp_path / "c.db")
        live = [
            {
                "convergence_id": f"VIVA-{i}", "domains": ["market"],
                "source_pattern_ids": ["A", "B"], "status": "active",
                "combined_cc": 0.9, "combined_hits": 9,
            }
            for i in range(60)
        ]
        dissolved = {
            "convergence_id": "DISUELTA", "domains": ["market"],
            "source_pattern_ids": ["A", "B"], "status": "dissolved",
            "combined_cc": 0.9, "combined_hits": 9,
        }
        result = cl.evaluate_live_convergences(
            live + [dissolved], limit=5,
            stats_fetcher=lambda fp: None, db_path=db,
        )
        assert result["truncated"] is True
        assert result["weakened"] == 1, (
            "la disolución quedó fuera del presupuesto del ciclo"
        )
