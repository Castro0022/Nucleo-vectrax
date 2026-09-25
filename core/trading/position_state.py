"""
core/trading/position_state.py — Transiciones puras de PositionRecord.

`position_manager.py` no decide por qué salir. Solo administra estado +
transición + persistencia. Este módulo es el "solo administra estado +
transición" — la persistencia (leer/escribir el JSONL, consultar al
broker) vive fuera, en la integración con `connectors/etoro/*`.

Dos mundos deliberadamente separados, nunca mezclados en una sola
función:

  apply_decision(record, decision, intent, now)
      "la capa de criterio quiere REDUCE/EXIT" — crea/referencia un
      OrderIntent y mueve el estado a REDUCING/CLOSING. NUNCA marca
      CLOSED aquí: mandar la orden no es lo mismo que el broker
      confirmando que se ejecutó. Marcar CLOSED en el momento de enviar
      el CLOSE es el error clásico que produce posiciones "fantasma".

  apply_execution_update(record, execution, now)
      "el broker confirmó/actualizó algo" — la única función que puede
      producir CLOSED, y solo cuando `execution.status == FILLED` sobre
      un intent CLOSE (o un REDUCE que agota `remaining_quantity`).

Máquina de estados (ver `PositionStatus` en contracts.py):

    OPEN --[execution FILLED]--> HOLDING
    HOLDING --[decision REDUCE]--> REDUCING
    HOLDING --[decision EXIT]--> CLOSING
    REDUCING --[execution FILLED, remaining>0]--> HOLDING
    REDUCING --[execution FILLED, remaining<=0]--> CLOSED
    REDUCING --[execution REJECTED]--> HOLDING (el reduce no ocurrió)
    CLOSING --[execution FILLED]--> CLOSED
    CLOSING --[execution REJECTED]--> HOLDING (el close no ocurrió)
    (OPEN|REDUCING|CLOSING|RECONCILING) --[execution UNKNOWN]--> RECONCILING
    RECONCILING --[execution FILLED/REJECTED/...]--> se resuelve igual
        que si esa ejecución hubiera llegado directo (misma lógica) —
        RECONCILING no es más que "la última execution fue UNKNOWN".

Ninguna función de este módulo:
  - llama al broker (no hay I/O — todo pasa por parámetros tipados),
  - importa core.nucleus, core.learn ni nada de señales/patrones
    (verificado estáticamente en tests/test_position_state.py, igual
    que en risk_gate.py),
  - avanza un estado porque "pasó tiempo" — todo cambio de estado viene
    de un `TradeDecision` o una `OrderExecution` explícitos, nunca de un
    reloj.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from core.trading.contracts import (
    EntryThesis,
    ExecutionStatus,
    OrderAction,
    OrderExecution,
    OrderIntent,
    PositionRecord,
    PositionStatus,
    TradeAction,
    TradeDecision,
)
from core.trading.order_idempotency import can_send_new_intent

# Estados en los que existe una orden en curso — apply_execution_update
# solo tiene sentido en estos (más RECONCILING, que es "la anterior
# quedó en UNKNOWN" y sigue esperando resolución).
_STATUSES_WITH_IN_FLIGHT_INTENT = frozenset({
    PositionStatus.OPEN,
    PositionStatus.REDUCING,
    PositionStatus.CLOSING,
    PositionStatus.RECONCILING,
})

# Estados en los que TradeDecisionEngine puede proponer algo —
# cualquier otro estado ya tiene una orden en curso o es terminal.
_STATUSES_ACCEPTING_DECISIONS = frozenset({PositionStatus.HOLDING, PositionStatus.REDUCING})


def open_position(
    position_id: str,
    thesis: EntryThesis,
    intent: OrderIntent,
    now: datetime,
) -> PositionRecord:
    """Crea el `PositionRecord` inicial, en `OPEN`, a partir del
    `OrderIntent(OPEN)` recién emitido. Solo pasa a `HOLDING` cuando
    llegue su `apply_execution_update(..., FILLED)`."""
    if thesis.position_id != position_id:
        raise ValueError(
            f"entry_thesis.position_id={thesis.position_id!r} no coincide "
            f"con position_id={position_id!r}"
        )
    if intent.position_id != position_id:
        raise ValueError(
            f"intent.position_id={intent.position_id!r} no coincide "
            f"con position_id={position_id!r}"
        )
    if intent.action != OrderAction.OPEN:
        raise ValueError(f"open_position requiere OrderIntent(action=OPEN), recibió {intent.action}")

    return PositionRecord(
        position_id=position_id,
        entry_thesis=thesis,
        status=PositionStatus.OPEN,
        remaining_quantity=intent.quantity,
        current_intent=intent,
        last_execution=None,
        updated_at=now,
    )


def apply_decision(
    record: PositionRecord,
    decision: TradeDecision,
    intent: Optional[OrderIntent],
    now: datetime,
) -> PositionRecord:
    """"La capa de criterio quiere REDUCE/EXIT/HOLD." Nunca produce
    CLOSED — eso solo lo hace `apply_execution_update`."""

    if decision.position_id != record.position_id:
        raise ValueError(
            f"decision.position_id={decision.position_id!r} no coincide "
            f"con record.position_id={record.position_id!r}"
        )
    if record.status not in _STATUSES_ACCEPTING_DECISIONS:
        raise ValueError(
            f"apply_decision inválido en status={record.status.value} — "
            "ya hay una orden en curso, está sin reconciliar, o la "
            "posición es terminal"
        )

    if decision.action == TradeAction.ENTER:
        raise ValueError("ENTER no aplica sobre una posición existente — usar open_position()")

    if decision.action == TradeAction.HOLD:
        # La tesis sigue vigente: ni status ni intent cambian.
        return replace(record, updated_at=now)

    # REDUCE o EXIT: ambos emiten un OrderIntent nuevo.
    if not can_send_new_intent(record.last_execution):
        raise ValueError(
            "ya existe una orden sin resolver para esta posición "
            f"(last_execution={record.last_execution}) — reconciliar antes de "
            "emitir un intent nuevo"
        )
    if intent is None:
        raise ValueError(f"decision.action={decision.action.value} requiere un OrderIntent")
    if intent.position_id != record.position_id:
        raise ValueError(
            f"intent.position_id={intent.position_id!r} no coincide "
            f"con record.position_id={record.position_id!r}"
        )

    if decision.action == TradeAction.REDUCE:
        if intent.action != OrderAction.REDUCE:
            raise ValueError(f"decision REDUCE requiere OrderIntent(action=REDUCE), recibió {intent.action}")
        if intent.quantity > record.remaining_quantity:
            raise ValueError(
                f"intent.quantity={intent.quantity} excede remaining_quantity="
                f"{record.remaining_quantity} de la posición"
            )
        new_status = PositionStatus.REDUCING
    else:  # EXIT
        if intent.action != OrderAction.CLOSE:
            raise ValueError(f"decision EXIT requiere OrderIntent(action=CLOSE), recibió {intent.action}")
        new_status = PositionStatus.CLOSING

    return replace(
        record,
        status=new_status,
        current_intent=intent,
        last_execution=None,
        updated_at=now,
    )


def apply_execution_update(
    record: PositionRecord,
    execution: OrderExecution,
    now: datetime,
) -> PositionRecord:
    """"El broker confirmó/actualizó algo." La única función que puede
    producir CLOSED — y solo ante un FILLED real, nunca al enviar la
    orden."""

    if record.status not in _STATUSES_WITH_IN_FLIGHT_INTENT:
        raise ValueError(
            f"apply_execution_update inválido en status={record.status.value} "
            "— no hay ningún intent en curso que actualizar"
        )
    if record.current_intent is None or execution.intent_id != record.current_intent.intent_id:
        raise ValueError(
            f"execution.intent_id={execution.intent_id!r} no coincide con el "
            f"intent en curso ({record.current_intent})"
        )

    action = record.current_intent.action

    if execution.status in (ExecutionStatus.PENDING, ExecutionStatus.ACKED, ExecutionStatus.PARTIAL):
        # Sigue en curso — mismo status, mismo intent_id, solo se
        # actualiza el registro de la última ejecución conocida.
        return replace(record, last_execution=execution, updated_at=now)

    if execution.status == ExecutionStatus.UNKNOWN:
        return replace(record, status=PositionStatus.RECONCILING, last_execution=execution, updated_at=now)

    if execution.status == ExecutionStatus.REJECTED:
        # Nada se ejecutó: OPEN rechazado -> nunca existió: CLOSED.
        # REDUCE/CLOSE rechazado -> la posición sigue como estaba: HOLDING.
        new_status = PositionStatus.CLOSED if action == OrderAction.OPEN else PositionStatus.HOLDING
        return replace(record, status=new_status, last_execution=execution, updated_at=now)

    # FILLED
    if action == OrderAction.OPEN:
        return replace(record, status=PositionStatus.HOLDING, last_execution=execution, updated_at=now)

    if action == OrderAction.REDUCE:
        new_remaining = max(record.remaining_quantity - execution.filled_quantity, 0.0)
        new_status = PositionStatus.CLOSED if new_remaining <= 0.0 else PositionStatus.HOLDING
        return replace(
            record,
            status=new_status,
            remaining_quantity=new_remaining,
            last_execution=execution,
            updated_at=now,
        )

    # CLOSE FILLED
    return replace(
        record,
        status=PositionStatus.CLOSED,
        remaining_quantity=0.0,
        last_execution=execution,
        updated_at=now,
    )
