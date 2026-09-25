"""
core/trading/order_idempotency.py — Regla de idempotencia de órdenes.

Regla inviolable: el mismo `intent_id` nunca puede crear una segunda
orden real. Este módulo fija, en funciones puras, cuándo es seguro que
`PositionManager` emita un `OrderIntent` NUEVO para una posición, y qué
hacer cuando el resultado de uno anterior se desconoce.

El caso que motiva este contrato:

    Vectrax manda CLOSE (intent_id=X)
            ↓
    timeout — nunca llega ACKED/FILLED/REJECTED, status queda UNKNOWN
            ↓
    Vectrax NO debe pensar "falló" y mandar CLOSE otra vez con un
    intent_id nuevo — si el primero sí ejecutó, la segunda orden abre
    una posición contraria accidental.
            ↓
    Correcto: no emitir ningún OrderIntent nuevo para esa posición.
    Primero preguntar al broker por el intent_id=X (reconciliar).

`UNKNOWN` es exactamente la misma idea que `RiskMode.RECONCILE`
(`core/trading/risk_rules.py`): "no sé qué tengo" no se resuelve
actuando de nuevo a ciegas, se resuelve preguntándole al mundo real.

-----------------------------------------------------------------------
can_send_new_intent(previous) — regla:

  - `previous is None`               -> True (no hay nada que duplicar)
  - `previous.status` es terminal
    (FILLED o REJECTED)              -> True (el ciclo anterior cerró;
                                         un nuevo intent_id es una acción
                                         nueva, no un reintento)
  - `previous.status` es cualquier otro
    (PENDING, ACKED, PARTIAL, UNKNOWN) -> False. Ya existe una orden en
    vuelo o de resultado desconocido para esa posición — reconciliar
    contra el broker, nunca reenviar.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from typing import Optional

from core.trading.contracts import ExecutionStatus, OrderExecution

# Estados que cierran el ciclo de vida de un OrderIntent — solo desde
# aquí es seguro emitir un intent_id nuevo para la misma posición.
TERMINAL_STATUSES = frozenset({ExecutionStatus.FILLED, ExecutionStatus.REJECTED})


def is_terminal(status: ExecutionStatus) -> bool:
    """True si el intent ya llegó a un desenlace conocido y definitivo."""
    return status in TERMINAL_STATUSES


def requires_reconciliation(status: ExecutionStatus) -> bool:
    """True únicamente para UNKNOWN: se perdió la confirmación y el
    estado real es desconocido — la única acción válida es preguntarle
    al broker, nunca reenviar ni asumir."""
    return status == ExecutionStatus.UNKNOWN


def can_send_new_intent(previous: Optional[OrderExecution]) -> bool:
    """¿Es seguro que PositionManager emita un `OrderIntent` nuevo?

    `previous` es la última `OrderExecution` conocida para la posición/
    acción en cuestión (o `None` si nunca se emitió un intent para ella).
    """
    if previous is None:
        return True
    return is_terminal(previous.status)
