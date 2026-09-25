"""
connectors/etoro/execution_adapter.py — OrderIntent → trade_executor
existente → OrderExecution.

    OrderIntent
        ↓
    ExecutionAdapter   (este archivo)
        ↓
    trade_executor.py   (sin cambios de lógica — solo gana el campo
                          `indeterminate` en ExecutionResult)
        ↓
    etoro_client.py      (NON_IDEMPOTENT_WRITE: timeout/excepción en
                          open_position()/close_position() ya NO
                          reintenta a ciegas — ver RetryMode ahí)

La trampa que este archivo existe para cerrar: un wrapper que solo
traduce resultados, sin más, NO basta — si `trade_executor` siguiera
reintentando internamente tras un timeout ambiguo, el wrapper llegaría
tarde. Por eso el cambio real está en `etoro_client._request()`
(`RetryMode.NON_IDEMPOTENT_WRITE`): ese es el que efectivamente impide
el segundo POST. Este archivo es la otra mitad: la que impide que
ALGUIEN MÁS, más arriba, vuelva a intentar con un `intent_id` nuevo
mientras el anterior sigue sin resolverse.

Regla que aplica ANTES de llamar a trade_executor, no después:
`can_send_new_intent()` (core/trading/order_idempotency.py) se consulta
contra el registro persistido de este `intent_id`. Si ya hay una
ejecución no terminal (PENDING/ACKED/PARTIAL) o `UNKNOWN`, NO se llama
al broker — se devuelve esa misma ejecución tal cual. Reconciliar esa
espera es responsabilidad de quien orquesta (la futura integración
LIVE de position_manager.py), no de este adaptador.

Registro persistido (no solo memoria RAM): si el proceso muere justo
después del POST, al reiniciar hay que poder saber que ese `intent_id`
ya se envió — por eso cada `OrderExecution` se persiste en
`~/.vectrax/etoro_order_executions.jsonl` (append-only; la última línea
por `intent_id` es el estado vigente) antes de devolverse al caller.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from connectors.etoro.trade_executor import ExecutionResult, execute_close, execute_open
from core.trading.contracts import ExecutionStatus, OrderAction, OrderExecution, OrderIntent
from core.trading.order_idempotency import can_send_new_intent

logger = logging.getLogger("vectrax.etoro.execution_adapter")

_EXECUTIONS_LOG_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_order_executions.jsonl"
)


# ---------------------------------------------------------------------------
# Registro persistido
# ---------------------------------------------------------------------------

def latest_execution(intent_id: str) -> Optional[OrderExecution]:
    """La última `OrderExecution` conocida para este `intent_id`, o
    `None` si nunca se registró ninguna."""
    if not os.path.exists(_EXECUTIONS_LOG_FILE):
        return None
    latest: Optional[OrderExecution] = None
    try:
        with open(_EXECUTIONS_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("intent_id") != intent_id:
                    continue
                try:
                    latest = OrderExecution(
                        intent_id=d["intent_id"],
                        broker_order_id=d.get("broker_order_id"),
                        status=ExecutionStatus(d["status"]),
                        filled_quantity=float(d.get("filled_quantity", 0.0)),
                        avg_fill_price=d.get("avg_fill_price"),
                        last_update_at=datetime.fromisoformat(d["last_update_at"]),
                    )
                except Exception:
                    continue
    except Exception:
        pass
    return latest


def _persist(execution: OrderExecution) -> None:
    try:
        os.makedirs(os.path.dirname(_EXECUTIONS_LOG_FILE), exist_ok=True)
        with open(_EXECUTIONS_LOG_FILE, "a") as f:
            f.write(json.dumps(execution.to_dict(), ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("execution_adapter: fallo al persistir OrderExecution: %s", exc)


# ---------------------------------------------------------------------------
# Traducción ExecutionResult -> OrderExecution
# ---------------------------------------------------------------------------

def _translate(intent: OrderIntent, result: ExecutionResult, now: datetime) -> OrderExecution:
    if result.success:
        status = ExecutionStatus.FILLED
        filled_quantity = intent.quantity
    elif result.indeterminate:
        # Timeout/excepción ambigua en el envío — no se sabe si el
        # broker procesó la orden. NUNCA se trata como REJECTED.
        status = ExecutionStatus.UNKNOWN
        filled_quantity = 0.0
    else:
        # Desenlace CONOCIDO: el broker respondió explícitamente que no.
        status = ExecutionStatus.REJECTED
        filled_quantity = 0.0

    return OrderExecution(
        intent_id=intent.intent_id,
        broker_order_id=str(result.order_id) if result.order_id else None,
        status=status,
        filled_quantity=filled_quantity,
        avg_fill_price=None,  # trade_executor no expone precio de fill real hoy
        last_update_at=now,
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def submit_open(
    intent: OrderIntent,
    user_id: Any,
    symbol: str,
    is_buy: bool,
    leverage: int = 1,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> OrderExecution:
    """Envía un OrderIntent(OPEN) — o no, si ya hay uno sin resolver."""
    if intent.action != OrderAction.OPEN:
        raise ValueError(f"submit_open requiere OrderIntent(action=OPEN), recibió {intent.action}")

    now = datetime.now(timezone.utc)
    previous = latest_execution(intent.intent_id)
    if not can_send_new_intent(previous):
        logger.warning(
            "execution_adapter: intent_id=%s ya tiene una ejecución sin resolver "
            "(%s) — NO se llama al broker de nuevo. Reconciliar antes de reintentar.",
            intent.intent_id, previous.status.value if previous else None,
        )
        return previous  # nunca None aquí: can_send_new_intent(None) es True

    result = execute_open(
        user_id=user_id, symbol=symbol, amount=intent.quantity, is_buy=is_buy,
        leverage=leverage, stop_loss=stop_loss, take_profit=take_profit,
    )
    execution = _translate(intent, result, now)
    _persist(execution)
    return execution


def submit_close(
    intent: OrderIntent,
    user_id: Any,
    broker_position_id: str,
    instrument_id: Optional[int] = None,
    symbol: Optional[str] = None,
) -> OrderExecution:
    """Envía un OrderIntent(CLOSE) — o no, si ya hay uno sin resolver.

    Este es el caso crítico que motivó todo esto: un timeout en el
    CLOSE anterior no puede producir un segundo CLOSE con un intent_id
    nuevo.
    """
    if intent.action != OrderAction.CLOSE:
        raise ValueError(f"submit_close requiere OrderIntent(action=CLOSE), recibió {intent.action}")

    now = datetime.now(timezone.utc)
    previous = latest_execution(intent.intent_id)
    if not can_send_new_intent(previous):
        logger.warning(
            "execution_adapter: intent_id=%s (CLOSE) ya tiene una ejecución sin "
            "resolver (%s) — NO se manda un segundo CLOSE. Reconciliar primero.",
            intent.intent_id, previous.status.value if previous else None,
        )
        return previous

    result = execute_close(
        user_id=user_id, position_id=broker_position_id,
        instrument_id=instrument_id, symbol=symbol,
    )
    execution = _translate(intent, result, now)
    _persist(execution)
    return execution


def record_reconciled_execution(
    intent_id: str,
    status: ExecutionStatus,
    filled_quantity: float = 0.0,
    avg_fill_price: Optional[float] = None,
) -> OrderExecution:
    """Persiste una `OrderExecution` que viene de reconciliar contra el
    estado REAL del broker (`portfolio_reconciler.py`), no de una
    llamada nueva a `trade_executor`. Es la única otra manera legítima
    de que un `intent_id` en `UNKNOWN` avance: nunca reenviando la
    orden, siempre observando qué pasó de verdad."""
    execution = OrderExecution(
        intent_id=intent_id,
        broker_order_id=None,
        status=status,
        filled_quantity=filled_quantity,
        avg_fill_price=avg_fill_price,
        last_update_at=datetime.now(timezone.utc),
    )
    _persist(execution)
    return execution
