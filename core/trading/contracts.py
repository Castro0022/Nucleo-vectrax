"""
core/trading/contracts.py — Contratos tipados entre capas de trading.

Fija el vocabulario que viaja entre `TradeDecisionEngine`, `RiskGate`,
`PositionManager` y `AutoExecutor` ANTES de escribir comportamiento en
ninguna de esas capas. Ninguna capa debe pasar `dict` arbitrarios a otra:
solo estos objetos, o colecciones tipadas de ellos.

  EntryThesis   — congelada en el momento del ENTER. Nunca se reescribe
                  mientras la posición sigue abierta (evita que el HOLD
                  invente una tesis nueva sobre la marcha — sesgo de
                  confirmación). Un EXIT/REDUCE posterior audita contra
                  ESTA tesis, no contra una reinterpretación del presente.
  TradeDecision — lo que `TradeDecisionEngine` propone en cada ciclo
                  (criterio, evidencia). No implica autorización ni
                  ejecución.
  RiskVerdict   — lo que `RiskGate` responde. No es un booleano: dice
                  explícitamente qué acción sustituye a la propuesta del
                  motor de decisión cuando un límite duro se activa.

Regla de autoridad (no se codifica aquí, pero condiciona el diseño):
`RiskGate` puede anular a `TradeDecisionEngine`. `TradeDecisionEngine`
nunca puede anular a `RiskGate`.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums de vocabulario
# ---------------------------------------------------------------------------

class TradeAction(str, Enum):
    """Lo que `TradeDecisionEngine` puede proponer en un ciclo."""
    ENTER = "ENTER"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class RiskAction(str, Enum):
    """Lo que `RiskGate` puede imponer sobre una propuesta.

    PASS           — no hay objeción; la decisión del motor sigue en pie.
    OVERRIDE_REDUCE — el riesgo obliga a reducir la posición, gane o
                       pierda el criterio del motor.
    OVERRIDE_EXIT   — el riesgo obliga a cerrar la posición.
    BLOCK_ENTRY     — el riesgo impide abrir una posición nueva.
    """
    PASS = "PASS"
    OVERRIDE_REDUCE = "OVERRIDE_REDUCE"
    OVERRIDE_EXIT = "OVERRIDE_EXIT"
    BLOCK_ENTRY = "BLOCK_ENTRY"


# ---------------------------------------------------------------------------
# EntryThesis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EntryThesis:
    """La hipótesis con la que Vectrax entró — congelada al ENTER.

    `evidence_snapshot_id` + `evidence_refs` existen para que la tesis sea
    auditable después sin reinterpretar el pasado: no basta con guardar un
    dict bonito, tiene que poder reconstruirse exactamente qué estrellas/
    señales/patrones existían en el momento del ENTER.
    """

    position_id: str
    symbol: str
    side: str
    created_at: datetime

    thesis: str
    invalidation_conditions: Tuple[str, ...]
    confidence_at_entry: float
    evidence_snapshot: Dict[str, Any]
    evidence_snapshot_id: str
    evidence_refs: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "created_at": self.created_at.isoformat(),
            "thesis": self.thesis,
            "invalidation_conditions": list(self.invalidation_conditions),
            "confidence_at_entry": round(self.confidence_at_entry, 4),
            "evidence_snapshot": self.evidence_snapshot,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EntryThesis":
        return cls(
            position_id=d["position_id"],
            symbol=d["symbol"],
            side=d["side"],
            created_at=datetime.fromisoformat(d["created_at"]),
            thesis=d["thesis"],
            invalidation_conditions=tuple(d.get("invalidation_conditions", ())),
            confidence_at_entry=float(d["confidence_at_entry"]),
            evidence_snapshot=dict(d.get("evidence_snapshot", {})),
            evidence_snapshot_id=d["evidence_snapshot_id"],
            evidence_refs=tuple(d.get("evidence_refs", ())),
        )


# ---------------------------------------------------------------------------
# TradeDecision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeDecision:
    """Lo que `TradeDecisionEngine` propone en un ciclo.

    `position_id` es `None` únicamente para `action=ENTER` (la posición
    todavía no existe). Para HOLD/REDUCE/EXIT es obligatorio: identifica
    la posición contra cuya `EntryThesis` se evaluó la decisión.
    """

    action: TradeAction
    position_id: Optional[str]
    reason: str
    confidence: float
    evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.action != TradeAction.ENTER and self.position_id is None:
            raise ValueError(
                f"TradeDecision(action={self.action}) requiere position_id "
                "— solo ENTER puede dejarlo en None"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "position_id": self.position_id,
            "reason": self.reason,
            "confidence": round(self.confidence, 4),
            "evidence_refs": list(self.evidence_refs),
        }


# ---------------------------------------------------------------------------
# RiskVerdict
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RiskVerdict:
    """Lo que `RiskGate` responde a una `TradeDecision`.

    No es `allowed: bool`. Cuando un límite duro se activa sobre un HOLD,
    el gate tiene que decir explícitamente qué acción lo sustituye —
    `forced_action` es esa sustitución. `PASS` es la única respuesta que
    deja vigente la decisión original del motor.
    """

    forced_action: RiskAction
    reason: str
    rule_id: str

    @property
    def passed(self) -> bool:
        """True si el RiskGate no tiene objeción — la decisión del motor
        sigue en pie tal cual."""
        return self.forced_action == RiskAction.PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "forced_action": self.forced_action.value,
            "reason": self.reason,
            "rule_id": self.rule_id,
        }
