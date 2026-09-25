"""
connectors/etoro/portfolio_reconciler.py — Reconciliación activa contra
`get_portfolio()`, el único punto de verdad real que expone eToro
(no hay endpoint de "consultar orden por id").

Cierra el estado sin salida que quedaba tras el paso anterior:

    LIVE → CLOSE enviado → timeout → UNKNOWN/RECONCILING → se queda ahí
    para siempre (el guard de idempotencia impide el reenvío ciego,
    correctamente, pero nada hacía avanzar el estado).

CLOSE/REDUCE son reconciliables porque ya se conoce el `position_id`
del broker ANTES de mandar la orden — comparar es inequívoco:

    P123 ya no existe en el portfolio        -> CONFIRMED_CLOSED
    P123 sigue con la cantidad completa       -> STILL_OPEN_FULL (esperar)
    P123 existe con una cantidad MENOR        -> REDUCED_UNATTRIBUTED
    get_portfolio() no responde               -> PORTFOLIO_UNAVAILABLE (esperar)

Estado físico != causalidad — el matiz que separa `REDUCED_UNATTRIBUTED`
de "confirmo que mi CLOSE hizo un fill parcial": que el broker muestre
una cantidad menor a la esperada demuestra "la posición tiene esto
ahora", NUNCA demuestra "mi orden produjo ese cambio" — pudo ser un
stop del broker, una reducción manual, u otro evento externo. Por eso
este resultado nunca se traduce en una `OrderExecution(PARTIAL)` atada
al `intent_id` (eso sería inventar una atribución que no se puede
demostrar) — se traduce en `position_state.apply_observed_quantity()`,
que solo actualiza `remaining_quantity` al valor real observado, sin
tocar `current_intent`/`last_execution` ni el `status` (la posición
sigue `RECONCILING`: la causa del `CLOSE` original sigue sin resolver).

OPEN es deliberadamente DISTINTO y mucho más delicado: si el broker no
devolvió `position_id` antes del timeout, no hay forma de correlacionar
inequívocamente una posición nueva que aparezca en el portfolio con
ESE `intent_id` — podría ser esa orden, o cualquier apertura
concurrente del mismo símbolo. `reconcile_open()` NUNCA infiere: siempre
`CANNOT_CONFIRM`. Sacar una posición de ahí exige intervención humana,
no automatismo — inventar aquí sería peor que quedarse en RECONCILE.

Solo lectura. Nunca envía ninguna orden.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.etoro.portfolio_reconciler")

# Tolerancia para comparar montos de punta flotante — no una regla de
# negocio, solo evita falsos "reducido" por ruido de redondeo.
_AMOUNT_EPSILON = 0.01


class ReconciliationOutcome(str, Enum):
    CONFIRMED_CLOSED = "confirmed_closed"       # ya no existe en el portfolio
    STILL_OPEN_FULL = "still_open_full"         # sigue con la cantidad completa
    REDUCED_UNATTRIBUTED = "reduced_unattributed"  # existe con una cantidad MENOR — causa desconocida
    PORTFOLIO_UNAVAILABLE = "portfolio_unavailable"  # no se pudo leer el portfolio
    CANNOT_CONFIRM = "cannot_confirm"           # solo para OPEN — nunca se infiere


def _find_position(broker_position_id: str, positions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next(
        (p for p in positions if str(p.get("positionID")) == str(broker_position_id)),
        None,
    )


def reconcile_close_from_portfolio(
    broker_position_id: str,
    expected_quantity: float,
    portfolio_positions: List[Dict[str, Any]],
) -> Tuple[ReconciliationOutcome, Optional[float]]:
    """Lógica pura de comparación — separada de la llamada HTTP para que
    sea trivial de probar sin mockear `get_portfolio()`.

    Devuelve `(outcome, current_amount_observado_o_None)`.
    """
    match = _find_position(broker_position_id, portfolio_positions)
    if match is None:
        return ReconciliationOutcome.CONFIRMED_CLOSED, None

    current_amount = match.get("amount")
    if current_amount is None:
        # No se puede comparar de forma inequívoca — no se infiere nada,
        # se trata como si siguiera abierta con el tamaño completo.
        return ReconciliationOutcome.STILL_OPEN_FULL, None

    current_amount = float(current_amount)
    if current_amount >= expected_quantity - _AMOUNT_EPSILON:
        return ReconciliationOutcome.STILL_OPEN_FULL, current_amount
    return ReconciliationOutcome.REDUCED_UNATTRIBUTED, current_amount


def reconcile_close(
    broker_position_id: str,
    expected_quantity: float,
) -> Tuple[ReconciliationOutcome, Optional[float]]:
    """Wrapper con I/O real: consulta `get_portfolio()` y aplica
    `reconcile_close_from_portfolio`."""
    from connectors.etoro.etoro_client import get_portfolio

    result = get_portfolio()
    if not result.get("success"):
        logger.warning(
            "portfolio_reconciler: get_portfolio() falló (%s) — %s sigue sin "
            "reconciliar este ciclo",
            result.get("error"), broker_position_id,
        )
        return ReconciliationOutcome.PORTFOLIO_UNAVAILABLE, None

    return reconcile_close_from_portfolio(
        broker_position_id, expected_quantity, result.get("positions", []),
    )


def reconcile_open(intent_id: str) -> ReconciliationOutcome:
    """Ver docstring del módulo: OPEN nunca se reconcilia por inferencia.
    Siempre `CANNOT_CONFIRM` — deliberado, no un placeholder por
    completar. Bloquear para intervención es la respuesta correcta
    mientras no exista un identificador correlacionable real."""
    logger.warning(
        "portfolio_reconciler: reconcile_open(%s) — OPEN nunca se "
        "reconcilia automáticamente, requiere intervención humana",
        intent_id,
    )
    return ReconciliationOutcome.CANNOT_CONFIRM
