"""
tests/test_risk_confidence.py — `compute_confidence`, rescatada de la sombra.

QUÉ PASÓ
--------
`compute_confidence` vivía en `core/shadow_mode.py`, un módulo de observación
en sombra que **nada activaba**: ni CLI, ni ruta de API, ni Telegram, ni un
solo invocador en código vivo. Parecía código muerto.

Pero esta función siempre estuvo VIVA: `core/proposal_engine.py` la usa para
calcular el `confidence_score` que alimenta a `classify_proposal()` y por tanto
determina la zona de autonomía de cada propuesta. Borrar el archivo "por ser
sombra" habría roto la clasificación de propuestas en tiempo de import.

Al retirar el modo sombra, la función se movió a `core/risk_engine.py`, donde
viven sus entradas (`RiskAssessment`, `_clamp`) y donde su semántica encaja: es
estadística pura sobre las señales de riesgo, sin ninguna relación con observar
en sombra.

QUÉ PROTEGE ESTE ARCHIVO
------------------------
Que el comportamiento es IDÉNTICO al anterior. Los tres casos de
`TestConfidence` vienen literalmente del antiguo `tests/test_shadow_mode.py`:
si el valor cambiara, la zona de autonomía asignada a una propuesta cambiaría
con él.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.risk_engine import (  # noqa: E402
    OperationContext,
    RiskAssessment,
    SignalResult,
    compute_confidence,
)


def _ctx(op="ingest", topic="general", mode="act"):
    return OperationContext(op_type=op, topic=topic, governor_mode=mode)


def _assessment(values):
    return RiskAssessment(
        risk_score=0.5,
        risk_level="MEDIUM",
        signals=[
            SignalResult(name=f"s{i}", value=v, weight=1.0)
            for i, v in enumerate(values)
        ],
        timestamp=0,
        op_context=_ctx(),
    )


class TestConfidence:
    """Portados sin cambios desde el antiguo tests/test_shadow_mode.py."""

    def test_identical_signals_high_confidence(self):
        assert compute_confidence(_assessment([0.5] * 6)) == 1.0

    def test_spread_signals_low_confidence(self):
        # Máximo desacuerdo posible: mitad en 0, mitad en 1.
        assert compute_confidence(_assessment([0.0, 1.0, 0.0, 1.0, 0.0, 1.0])) == 0.0

    def test_confidence_in_range(self):
        for values in ([0.1, 0.9], [0.3, 0.4, 0.5], [0.0, 0.2, 0.8, 1.0]):
            assert 0.0 <= compute_confidence(_assessment(values)) <= 1.0

    def test_fewer_than_two_signals_is_full_confidence(self):
        """Sin dispersión medible no hay desacuerdo que penalizar."""
        assert compute_confidence(_assessment([])) == 1.0
        assert compute_confidence(_assessment([0.7])) == 1.0

    @pytest.mark.parametrize("values,expected", [
        ([0.5] * 6,                        1.0),
        ([0.0, 1.0, 0.0, 1.0, 0.0, 1.0],   0.0),
        ([0.0, 1.0],                       0.0),
        ([0.4, 0.6],                       0.8),
        ([0.25, 0.75],                     0.5),
        ([0.0, 0.5, 1.0],                  0.18350341907227397),
        ([0.2, 0.4, 0.6, 0.8],             0.5527864045000421),
        ([0.9, 0.9, 0.9, 0.1],             0.3071796769724491),
    ])
    def test_golden_values_are_bit_for_bit_unchanged(self, values, expected):
        """Valores GOLDEN calculados con la fórmula original de shadow_mode.py.

        `1 − (pstdev(values) / 0.5)`, acotado a [0,1]. Fijarlos aquí impide que
        el traslado —o cualquier refactor futuro— desplace el resultado. Un
        desplazamiento cambiaría el `confidence_score` y con él la zona de
        autonomía asignada a cada propuesta, en silencio.

        Derivados de una reimplementación independiente de la fórmula, NO de la
        función trasladada: copiarlos de la implementación actual haría la
        prueba circular. `test_the_formula_itself_is_the_original` mantiene esa
        comprobación viva para entradas arbitrarias.
        """
        assert compute_confidence(_assessment(values)) == pytest.approx(
            expected, abs=1e-12,
        )

    def test_the_formula_itself_is_the_original(self):
        """Recalcula el contrato desde cero, sin leer la implementación."""
        import statistics
        for values in ([0.1, 0.9], [0.3, 0.4, 0.5], [0.0, 0.2, 0.8, 1.0],
                       [0.05] * 4, [1.0, 0.0, 0.5, 0.5, 0.7]):
            expected = max(0.0, min(1.0, 1.0 - (statistics.pstdev(values) / 0.5)))
            assert compute_confidence(_assessment(values)) == pytest.approx(
                expected, abs=1e-12,
            )


class TestItIsStillWiredToProposals:
    """El consumidor real sigue conectado tras el traslado."""

    def test_proposal_engine_imports_it_from_risk_engine(self):
        import ast
        path = _ROOT / "core" / "proposal_engine.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sources = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and any(a.name == "compute_confidence" for a in node.names)
        }
        assert sources == {"core.risk_engine"}, (
            f"compute_confidence se importa desde {sources}, no desde risk_engine"
        )

    def test_it_still_feeds_the_autonomy_zone(self):
        """El valor sigue llegando a `classify_proposal` como confidence_score."""
        text = (_ROOT / "core" / "proposal_engine.py").read_text(encoding="utf-8")
        assert "conf_score = compute_confidence(risk_assessment)" in text
        assert "confidence_score=conf_score" in text


class TestTheShadowIsGone:
    """La capacidad oculta no vuelve."""

    def test_the_module_no_longer_exists(self):
        assert not (_ROOT / "core" / "shadow_mode.py").exists()

    def test_nothing_imports_it(self):
        import ast
        offenders = []
        skip = {".git", ".venv", "venv", "__pycache__", "node_modules",
                "build", "dist", ".pytest_cache"}
        for path in _ROOT.rglob("*.py"):
            if any(part in skip for part in path.relative_to(_ROOT).parts):
                continue
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "core.shadow_mode":
                    offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "core.shadow_mode":
                            offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
        assert not offenders, f"todavía se importa core.shadow_mode: {offenders}"

    def test_no_state_key_survives(self):
        from core.state_manager import DEFAULT_STATE
        assert "shadow_mode" not in DEFAULT_STATE

    def test_the_autonomy_zones_no_longer_list_it(self):
        from core.autonomy_policy import SEMI_SAFE_PATHS
        assert "core/shadow_mode.py" not in SEMI_SAFE_PATHS

    def test_the_packaging_manifest_no_longer_lists_it(self):
        """`vectrax.egg-info/SOURCES.txt` está RASTREADO en git.

        Un manifiesto que lista un archivo inexistente es una mentira sobre el
        contenido del paquete, no un detalle cosmético.
        """
        manifest = _ROOT / "vectrax.egg-info" / "SOURCES.txt"
        if not manifest.is_file():
            pytest.skip("el manifiesto no está presente en este árbol")
        listed = manifest.read_text(encoding="utf-8").splitlines()
        assert "core/shadow_mode.py" not in listed

    def test_no_orphan_comment_mislabels_another_state_key(self):
        """El comentario `# Shadow mode` etiquetaba `"meta": {}` tras el borrado.

        Quitar una clave y dejar su comentario convierte la documentación en
        una etiqueta equivocada sobre la clave siguiente.
        """
        text = (_ROOT / "core" / "state_manager.py").read_text(encoding="utf-8")
        assert "Shadow mode" not in text
        assert "shadow" not in text.lower()

    def test_this_pr_retires_only_the_old_module(self):
        """Alcance explícito: los mecanismos constitucionales siguen intactos.

        Retirar `core/shadow_mode.py` NO es retirar el control constitucional.
        `constitutional_mode` es un interruptor gobernado —shadow/enforce, con
        kill switch y fail-safe— y sigue exactamente donde estaba.
        """
        assert (_ROOT / "core" / "operator" / "constitutional_mode.py").is_file()
        assert (_ROOT / "core" / "operator" / "constitutional_guard.py").is_file()
        from core.operator import constitutional_mode
        assert hasattr(constitutional_mode, "get_mode")
        assert hasattr(constitutional_mode, "revert_to_shadow")
        assert constitutional_mode.SHADOW == "shadow"
        assert constitutional_mode.ENFORCE == "enforce"
