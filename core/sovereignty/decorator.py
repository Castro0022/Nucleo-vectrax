"""
core/sovereignty/decorator.py — @authorized_send decorator.

Envuelve cualquier función sender(chat_id, payload, ...) y la convierte
en un sender soberano: antes de ejecutar el envío real consulta
require_authorization(chat_id, reason). Si la decisión es BLOCKED
(modo enforce), el sender NO se ejecuta y se devuelve False/None.

En modo observe, el sender SÍ se ejecuta (la decisión queda en el
ledger como OBSERVED para análisis pre-enforce).

Ejemplo:

    from core.sovereignty import authorized_send, SendReason

    @authorized_send(SendReason.USER_REPLY)
    def send_text(chat_id: int, text: str) -> bool:
        ...

    # caller puede pasar reason explícito en kwargs para overridear:
    send_text(chat_id, text, _sovereignty_reason=SendReason.USER_ACK)

    # caller también puede pasar user_id explícito:
    send_text(chat_id, text, _sovereignty_user_id="tg:42")
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from core.sovereignty.authorizer import require_authorization
from core.sovereignty.policy import SendReason

logger = logging.getLogger("vectrax.sovereignty.decorator")


def authorized_send(default_reason: SendReason) -> Callable:
    """Decorator factory.

    El reason por defecto se declara aquí. El caller puede pasar
    `_sovereignty_reason=SendReason.X` en kwargs para overridear
    (útil cuando el mismo sender atiende varios casos).

    El primer arg posicional debe ser `chat_id`.
    """

    def decorator(send_fn: Callable) -> Callable:

        @functools.wraps(send_fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # extraer chat_id (primer arg posicional o kwarg)
            chat_id = None
            if args:
                chat_id = args[0]
            else:
                chat_id = kwargs.get("chat_id")

            # extraer overrides
            reason: SendReason = kwargs.pop(
                "_sovereignty_reason", default_reason
            )
            user_id = kwargs.pop("_sovereignty_user_id", None)

            try:
                chat_id_int = int(chat_id)
            except (TypeError, ValueError):
                # si no podemos parsear chat_id, no podemos auditar:
                # ejecutamos el sender (fallback seguro) sin loggear.
                logger.debug(
                    "authorized_send: invalid chat_id=%r, bypassing", chat_id,
                )
                return send_fn(*args, **kwargs)

            # estimar payload size si hay un text
            payload_size = 0
            try:
                if len(args) >= 2 and isinstance(args[1], str):
                    payload_size = len(args[1])
                elif "text" in kwargs and isinstance(kwargs["text"], str):
                    payload_size = len(kwargs["text"])
            except Exception:
                payload_size = 0

            decision = require_authorization(
                chat_id=chat_id_int,
                reason=reason,
                user_id=user_id,
                payload_size=payload_size,
            )

            if not decision.is_allowed:
                logger.warning(
                    "authorized_send: blocked send chat_id=%s reason=%s rule=%s",
                    chat_id_int, reason.value, decision.rule,
                )
                return False

            return send_fn(*args, **kwargs)

        # exponer metadata para introspección/tests
        wrapper.__sovereignty_reason__ = default_reason  # type: ignore[attr-defined]
        return wrapper

    return decorator
