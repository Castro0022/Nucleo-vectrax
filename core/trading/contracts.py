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


class RiskMode(str, Enum):
    """Estado global de la jaula de seguridad.

    NORMAL             — ningún límite global activado.
    HALT_NEW_RISK       — no se abre nada nuevo (kill switch manual en modo
                           halt, o pérdida diaria máxima alcanzada). NO
                           toca posiciones ya abiertas: siguen bajo su
                           propio stop duro y bajo `TradeDecisionEngine`.
    RECONCILE           — el sistema NO sabe con certeza qué posiciones
                           existen realmente, o no puede confiar en que una
                           orden se ejecute como se espera (positions_sync
                           roto o broker_execution_ok=False). No abre nada
                           nuevo Y no fuerza ninguna acción sobre
                           posiciones existentes — forzar un cierre sobre
                           datos que no son de fiar puede producir un
                           duplicado o una posición contraria accidental,
                           que es peor que no actuar. Primero se reconstruye
                           el estado real; solo entonces cabe evaluar
                           EMERGENCY_FLATTEN.
    EMERGENCY_FLATTEN   — liquidar todo. Requiere estado CONOCIDO (nunca se
                           entra aquí solo por incertidumbre): kill switch
                           manual en modo flatten, con el operador
                           afirmando que sabe exactamente qué hay y quiere
                           cerrarlo.
    """
    NORMAL = "NORMAL"
    HALT_NEW_RISK = "HALT_NEW_RISK"
    RECONCILE = "RECONCILE"
    EMERGENCY_FLATTEN = "EMERGENCY_FLATTEN"


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


# ---------------------------------------------------------------------------
# Snapshots — lo único que RiskGate lee. Nunca consulta servicios él mismo.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RiskLimits:
    """Los límites duros que el creador autoriza. RiskGate nunca los
    deriva ni los ajusta — solo los aplica."""

    max_loss_per_trade_usd: float
    max_daily_loss_usd: float
    max_open_positions: int
    max_total_exposure_usd: float


@dataclass(frozen=True)
class AccountRiskSnapshot:
    """Estado de cuenta/sistema al momento de evaluar riesgo.

    `kill_switch` es un estado fijado manualmente por el operador/creador
    (nunca calculado por el gate): NORMAL, HALT_NEW_RISK, RECONCILE o
    EMERGENCY_FLATTEN.

    `positions_sync_ok=False` — el conteo/estado local de posiciones no
    coincide con lo que reporta el broker (o no se pudo verificar).
    `broker_execution_ok=False` — el sistema no puede confiar en que una
    orden enviada al broker se ejecute como se espera (caído, latencia
    anómala, rechazos inexplicados). Cualquiera de las dos, en False,
    fuerza `RiskMode.RECONCILE` — NUNCA `EMERGENCY_FLATTEN` directamente:
    no se manda "cierra todo" a ciegas sobre un estado que no es de fiar.
    """

    equity_usd: float
    realized_pnl_today_usd: float  # negativo = pérdida
    open_positions_count: int
    total_exposure_usd: float
    kill_switch: RiskMode = RiskMode.NORMAL
    positions_sync_ok: bool = True
    broker_execution_ok: bool = True


@dataclass(frozen=True)
class MarketExecutionSnapshot:
    """Condiciones de mercado/broker para el símbolo de una entrada
    propuesta. `price=None` o `price_is_stale=True` o
    `broker_tradable=False` son todas condiciones fail-safe: si el gate
    no sabe con certeza qué está pasando, no abre una operación nueva."""

    symbol: str
    price: Optional[float]
    price_is_stale: bool
    broker_tradable: bool


@dataclass(frozen=True)
class PositionRiskSnapshot:
    """Estado de una posición ya abierta, para `check_open_position`."""

    position_id: str
    symbol: str
    side: str
    entry_price: float
    current_price: Optional[float]
    hard_stop_price: float
    size_usd: float


# ---------------------------------------------------------------------------
# Idempotencia de órdenes — contrato mínimo entre PositionManager y
# AutoExecutor. Regla inviolable: el mismo `intent_id` NUNCA puede crear
# una segunda orden real (ver core/trading/order_idempotency.py).
# ---------------------------------------------------------------------------

class OrderAction(str, Enum):
    """Lo que un `OrderIntent` le pide al broker — vocabulario propio,
    distinto de `TradeAction`: un ENTER del motor de decisión se traduce
    en un OrderIntent(OPEN); un EXIT, en un OrderIntent(CLOSE)."""
    OPEN = "OPEN"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


class ExecutionStatus(str, Enum):
    """Estado conocido de una orden enviada al broker.

    PENDING  — creada localmente, todavía no confirmada por el broker.
    ACKED    — el broker la reconoció (recibida), aún sin fill.
    PARTIAL  — fill parcial.
    FILLED   — fill completo (estado terminal).
    REJECTED — el broker la rechazó (estado terminal).
    UNKNOWN  — se perdió la confirmación (timeout, desconexión): no se
               sabe si la orden llegó, se ejecutó o no existe. NUNCA se
               trata como "falló, reenviar" — exige reconciliar contra
               el broker antes de cualquier otra acción sobre el mismo
               `intent_id` (ver `RiskMode.RECONCILE`).
    """
    PENDING = "PENDING"
    ACKED = "ACKED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OrderIntent:
    """La intención de orden que `PositionManager` genera. `intent_id`
    es la idempotency key estable: el mismo intent_id identifica el
    mismo pedido a través de reintentos/reconexiones — nunca se genera
    uno nuevo para "la misma" acción mientras la anterior siga sin
    resolverse (ver `order_idempotency.can_send_new_intent`)."""

    intent_id: str
    position_id: str
    action: OrderAction
    quantity: float
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "position_id": self.position_id,
            "action": self.action.value,
            "quantity": self.quantity,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class OrderExecution:
    """Lo último que se sabe sobre un `OrderIntent`, tal como lo reporta
    (o deja de reportar) el broker. `broker_order_id` es `None` mientras
    el intent sigue en `PENDING` (todavía no hay confirmación de que el
    broker lo recibió)."""

    intent_id: str
    broker_order_id: Optional[str]
    status: ExecutionStatus
    filled_quantity: float
    avg_fill_price: Optional[float]
    last_update_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "broker_order_id": self.broker_order_id,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "last_update_at": self.last_update_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# PositionRecord — el estado que administra PositionManager. No decide
# nada: solo estado + transición + persistencia (ver
# core/trading/position_state.py para las transiciones puras).
# ---------------------------------------------------------------------------

class PositionStatus(str, Enum):
    """
    OPEN         — el intent de apertura fue enviado, todavía sin
                    confirmación de fill.
    HOLDING      — posición viva, sin ninguna orden en curso. El único
                    estado en el que `TradeDecisionEngine` tiene algo que
                    decidir.
    REDUCING     — un intent REDUCE está en curso.
    CLOSING      — un intent CLOSE está en curso.
    RECONCILING  — la última `OrderExecution` conocida quedó en UNKNOWN.
                    No se emite ningún intent nuevo mientras se está
                    aquí — hay que preguntarle al broker qué pasó con el
                    intent anterior (ver `order_idempotency`).
    CLOSED       — terminal. Ninguna transición sale de aquí.
    """
    OPEN = "OPEN"
    HOLDING = "HOLDING"
    REDUCING = "REDUCING"
    CLOSING = "CLOSING"
    RECONCILING = "RECONCILING"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class PositionRecord:
    """Estado completo de una posición. Inmutable: cada transición
    devuelve un `PositionRecord` NUEVO (`dataclasses.replace`), nunca
    muta uno existente — mismo espíritu que `EntryThesis` congelada.

    `entry_thesis` nunca cambia entre transiciones: es la referencia fija
    contra la que se audita cada `HOLD`/`EXIT` posterior.
    `current_intent` es `None` únicamente en `HOLDING` (no hay ninguna
    orden en curso). `remaining_quantity` es lo que queda de la posición
    original — un REDUCE la reduce; solo llega a 0 cuando la posición
    está efectivamente cerrada.
    """

    position_id: str
    entry_thesis: EntryThesis
    status: PositionStatus
    remaining_quantity: float
    current_intent: Optional[OrderIntent]
    last_execution: Optional[OrderExecution]
    updated_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "entry_thesis": self.entry_thesis.to_dict(),
            "status": self.status.value,
            "remaining_quantity": self.remaining_quantity,
            "current_intent": self.current_intent.to_dict() if self.current_intent else None,
            "last_execution": self.last_execution.to_dict() if self.last_execution else None,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PositionRecord":
        return cls(
            position_id=d["position_id"],
            entry_thesis=EntryThesis.from_dict(d["entry_thesis"]),
            status=PositionStatus(d["status"]),
            remaining_quantity=float(d["remaining_quantity"]),
            current_intent=(
                OrderIntent(
                    intent_id=d["current_intent"]["intent_id"],
                    position_id=d["current_intent"]["position_id"],
                    action=OrderAction(d["current_intent"]["action"]),
                    quantity=float(d["current_intent"]["quantity"]),
                    created_at=datetime.fromisoformat(d["current_intent"]["created_at"]),
                ) if d.get("current_intent") else None
            ),
            last_execution=(
                OrderExecution(
                    intent_id=d["last_execution"]["intent_id"],
                    broker_order_id=d["last_execution"]["broker_order_id"],
                    status=ExecutionStatus(d["last_execution"]["status"]),
                    filled_quantity=float(d["last_execution"]["filled_quantity"]),
                    avg_fill_price=d["last_execution"]["avg_fill_price"],
                    last_update_at=datetime.fromisoformat(d["last_execution"]["last_update_at"]),
                ) if d.get("last_execution") else None
            ),
            updated_at=datetime.fromisoformat(d["updated_at"]),
        )
