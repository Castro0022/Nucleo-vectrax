"""
core/continuity/pending_actions.py — Acciones pendientes de confirmación.

Cuando Vectrax propone una acción al user (ej. "voy a borrar tu cache,
¿procedo?"), guardamos la acción en `vctx_pending_actions` con un TTL.
Si el user contesta "ok / dale / sí / procedé / adelante", consumimos
la pending y ejecutamos la acción real.

Tabla (definida en schema_continuity.sql):
    vctx_pending_actions(
        id          TEXT PRIMARY KEY,
        chat_id     TEXT NOT NULL,
        action_type TEXT NOT NULL,
        payload     TEXT NOT NULL,         -- JSON serializado
        created_at  INTEGER NOT NULL,
        expires_at  INTEGER NOT NULL,
        consumed_at INTEGER                -- NULL si no consumida
    )

API pública:
    propose(chat_id, action_type, payload, ttl=300) -> id
    list_pending(chat_id) -> list[dict]
    consume_if_confirmation(chat_id, user_text) -> Optional[dict]
        Si user_text matchea ok/dale/sí/procedé y hay pending viva,
        marca como consumida y retorna su payload.
    consume_by_id(action_id) -> Optional[dict]
    cancel(action_id) -> bool
    expire_old(now=None) -> int

Notas:
    - El TTL default es 5 minutos. El user pierde la oportunidad si
      tarda más; evita confirmaciones zombi.
    - Solo se consume la MÁS RECIENTE pending por chat (LIFO). Si hay
      varias propuestas en cola, el "ok" confirma la última.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.continuity.pending_actions")


def _db_path() -> str:
    return os.environ.get(
        "VECTRAX_CONTINUITY_DB",
        os.path.join(
            os.path.expanduser("~"),
            ".vectrax", "continuity.db",
        ),
    )

_lock = threading.Lock()
_DEFAULT_TTL_SECONDS = 300

# Patrones de confirmación user→bot.
# Conservadores: solo confirmaciones inequívocas (no "creo que sí", etc.).
_CONFIRMATION_PATTERNS = re.compile(
    r"^\s*(?:"
    r"ok|okay|dale|s[ií]|si|s[ií]\s+dale|adelante|proced[éeáa]|procede"
    r"|hac[ée]lo|hazlo|h[áa]galo|listo|perfecto|bueno|de\s+acuerdo|vale"
    r"|conf[íi]rmo|confirmado|👍|✅"
    r"|yes|go|do\s+it|proceed|confirmed|agreed"
    r")\s*[.!]*\s*$",
    re.IGNORECASE,
)

# Patrones de RECHAZO — si matchean, también se consume la pending pero
# devuelve {action_type:'__cancelled__', ...}.
_REJECTION_PATTERNS = re.compile(
    r"^\s*(?:"
    r"no|nope|cancela|cancelar|cancel|para|stop|frena|abort"
    r"|olv[íi]dalo|olvida|d[ée]jalo|nada\s+m[áa]s"
    r"|nope|don'?t|do\s+not"
    r")\s*[.!]*\s*$",
    re.IGNORECASE,
)


def _conn() -> sqlite3.Connection:
    p = _db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=3, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""
        CREATE TABLE IF NOT EXISTS vctx_pending_actions (
            id          TEXT PRIMARY KEY,
            chat_id     TEXT NOT NULL,
            action_type TEXT NOT NULL,
            payload     TEXT NOT NULL,
            created_at  INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL,
            consumed_at INTEGER
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_vctx_pending_chat "
        "ON vctx_pending_actions(chat_id, expires_at, consumed_at)"
    )
    return c


def propose(
    chat_id: str,
    action_type: str,
    payload: Optional[Dict[str, Any]] = None,
    ttl: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Crea una pending action. Devuelve su id.

    Si falla, devuelve cadena vacía.
    """
    if not chat_id or not action_type:
        return ""
    aid = uuid.uuid4().hex[:16]
    now = int(time.time())
    expires = now + max(30, int(ttl))
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    try:
        with _lock:
            c = _conn()
            try:
                c.execute(
                    "INSERT INTO vctx_pending_actions "
                    "(id, chat_id, action_type, payload, created_at, "
                    " expires_at, consumed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (aid, str(chat_id), action_type, payload_json,
                     now, expires),
                )
                c.commit()
            finally:
                c.close()
        logger.info(
            "pending: chat=%s type=%s id=%s ttl=%ds",
            chat_id, action_type, aid, ttl,
        )
        return aid
    except Exception as exc:
        logger.debug("propose failed: %s", exc)
        return ""


def _is_confirmation(text: str) -> bool:
    return bool(_CONFIRMATION_PATTERNS.match(text or ""))


def _is_rejection(text: str) -> bool:
    return bool(_REJECTION_PATTERNS.match(text or ""))


def list_pending(chat_id: str) -> List[Dict[str, Any]]:
    """Devuelve las pending NO consumidas y NO expiradas, más reciente primero."""
    if not chat_id:
        return []
    now = int(time.time())
    try:
        with _lock:
            c = _conn()
            try:
                rows = c.execute(
                    "SELECT id, action_type, payload, created_at, expires_at "
                    "FROM vctx_pending_actions "
                    "WHERE chat_id = ? AND consumed_at IS NULL "
                    "AND expires_at > ? "
                    # ROWID DESC garantiza orden de inserción cuando dos
                    # proposes ocurren en el mismo segundo (created_at
                    # tiene resolución 1s).
                    "ORDER BY created_at DESC, rowid DESC",
                    (str(chat_id), now),
                ).fetchall()
            finally:
                c.close()
        out = []
        for r in rows:
            try:
                pl = json.loads(r[2])
            except Exception:
                pl = {}
            out.append({
                "id": r[0],
                "action_type": r[1],
                "payload": pl,
                "created_at": int(r[3]),
                "expires_at": int(r[4]),
            })
        return out
    except Exception:
        return []


def consume_by_id(action_id: str) -> Optional[Dict[str, Any]]:
    """Marca como consumida y devuelve el payload (más action_type)."""
    if not action_id:
        return None
    now = int(time.time())
    try:
        with _lock:
            c = _conn()
            try:
                row = c.execute(
                    "SELECT chat_id, action_type, payload, expires_at, "
                    "       consumed_at "
                    "FROM vctx_pending_actions WHERE id = ?",
                    (action_id,),
                ).fetchone()
                if not row:
                    return None
                if row[3] <= now:
                    return None  # expired
                if row[4] is not None:
                    return None  # already consumed
                c.execute(
                    "UPDATE vctx_pending_actions SET consumed_at = ? "
                    "WHERE id = ?",
                    (now, action_id),
                )
                c.commit()
                try:
                    pl = json.loads(row[2])
                except Exception:
                    pl = {}
                return {
                    "id": action_id,
                    "chat_id": row[0],
                    "action_type": row[1],
                    "payload": pl,
                }
            finally:
                c.close()
    except Exception as exc:
        logger.debug("consume_by_id failed: %s", exc)
        return None


def consume_if_confirmation(
    chat_id: str, user_text: str,
) -> Optional[Dict[str, Any]]:
    """Si user_text es confirmación inequívoca y hay pending vivas,
    consume la más reciente y devuelve su payload.

    Si user_text es rechazo, marca la pending más reciente como
    consumed (cancelada) y devuelve {action_type: '__cancelled__'}.

    Si no es ni confirmación ni rechazo, devuelve None (sin tocar).
    """
    if not chat_id or not user_text:
        return None
    text = user_text.strip()
    pending = list_pending(chat_id)
    if not pending:
        return None
    latest = pending[0]

    if _is_confirmation(text):
        consumed = consume_by_id(latest["id"])
        if consumed:
            logger.info(
                "pending: confirmation consumed | chat=%s type=%s",
                chat_id, consumed["action_type"],
            )
        return consumed

    if _is_rejection(text):
        # Marcar como consumed pero indicar cancelación al caller.
        cancelled = consume_by_id(latest["id"])
        if cancelled:
            cancelled["action_type"] = "__cancelled__"
            logger.info(
                "pending: rejection \u2014 cancelled | chat=%s",
                chat_id,
            )
        return cancelled

    return None


def cancel(action_id: str) -> bool:
    """Cancela una pending sin ejecutar. Marca consumed_at."""
    return bool(consume_by_id(action_id))


def expire_old(now: Optional[int] = None) -> int:
    """Elimina filas con expires_at < now (solo limpieza física).

    Las pending consumidas también se borran si tienen >24h.
    Devuelve filas afectadas.
    """
    n = int(now or time.time())
    cutoff_old_consumed = n - 86400
    try:
        with _lock:
            c = _conn()
            try:
                cur = c.execute(
                    "DELETE FROM vctx_pending_actions "
                    "WHERE expires_at < ? "
                    "OR (consumed_at IS NOT NULL AND consumed_at < ?)",
                    (n, cutoff_old_consumed),
                )
                c.commit()
                return cur.rowcount
            finally:
                c.close()
    except Exception:
        return 0


def reset_for_tests() -> None:
    try:
        with _lock:
            c = _conn()
            try:
                c.execute("DELETE FROM vctx_pending_actions")
                c.commit()
            finally:
                c.close()
    except Exception:
        pass
