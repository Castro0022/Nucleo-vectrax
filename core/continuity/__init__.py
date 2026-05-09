"""
core/continuity — Vectrax Continuity Engine.

Idempotencia y continuidad de procesamiento entre reinicios del gateway.

Tabla más crítica del schema:

    vctx_processed_updates(update_id PK, received_at, chat_id)

Cuando el supervisor reinicia el gateway de Telegram en medio de
procesar un mensaje, Telegram redelivera ese mismo update_id al
levantarse el bot. Sin idempotencia, el mensaje se procesa otra vez
y la respuesta (texto + audio) se manda DOS veces al usuario. Mario
percibe esto como "el audio anterior se reproduce".

`is_processed(update_id)` consulta el ledger; `mark_processed()` lo
graba antes de despachar el handler. Pareja atómica con el offset
de Telegram en el polling loop.

API pública:
    SQLiteUpdateLedger
    is_processed(update_id) -> bool
    mark_processed(update_id, chat_id="") -> bool
    prune_older_than(seconds=None) -> int
    reset_for_tests() -> None
"""

from core.continuity.idempotency import (  # noqa: F401
    SQLiteUpdateLedger,
    is_processed,
    mark_processed,
    prune_older_than,
    reset_for_tests,
)
from core.continuity import voice_forbidden  # noqa: F401
from core.continuity import working_memory  # noqa: F401
from core.continuity import pending_actions  # noqa: F401

__all__ = [
    "SQLiteUpdateLedger",
    "is_processed",
    "mark_processed",
    "prune_older_than",
    "reset_for_tests",
    "voice_forbidden",
    "working_memory",
    "pending_actions",
]
