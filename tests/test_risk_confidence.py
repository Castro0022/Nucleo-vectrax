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
