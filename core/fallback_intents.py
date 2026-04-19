"""
Vectrax Fallback Intents — Registro de Intenciones No Resueltas
================================================================
Captura cuándo el sistema no pudo resolver una consulta correctamente
y la envió a fallback. Guarda SOLO metadata abstracta, nunca texto literal.

Datos guardados por evento:
  - intent_category: qué intentaba hacer el usuario
  - resolve_mode:    qué ruta se intentó (online, local, llm, etc.)
  - reason:          por qué falló (empty_response, llm_fail, router_fail, etc.)
  - word_count:      longitud abstracta del mensaje (no el texto)
  - timestamp

Uso:
  record_fallback(intent_category, resolve_mode, reason, word_count)
  get_top_fallbacks(limit=10)   → lista de categorías más frecuentes
  get_summary()                  → resumen para /vx stats o /vx fallbacks

Creado: 2026-04-01
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Dict, List

logger = logging.getLogger("vectrax.fallback_intents")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "fallback_intents.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS fallback_intents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_category TEXT NOT NULL DEFAULT 'unknown',
    resolve_mode    TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    word_count      INTEGER NOT NULL DEFAULT 0,
    timestamp       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fb_intent   ON fallback_intents(intent_category);
CREATE INDEX IF NOT EXISTS idx_fb_ts       ON fallback_intents(timestamp);
"""

# Máx. registros — después de este límite se hace rotación automática
MAX_RECORDS = 5000


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE)
    return conn


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def record_fallback(
    intent_category: str = "unknown",
    resolve_mode: str = "",
    reason: str = "",
    word_count: int = 0,
) -> None:
    """
    Registra un evento de fallback con metadata abstracta.
    Nunca almacena el texto del mensaje.
    """
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO fallback_intents "
            "(intent_category, resolve_mode, reason, word_count, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                intent_category[:50],
                resolve_mode[:50],
                reason[:100],
                word_count,
                time.time(),
            ),
        )
        conn.commit()
        _rotate_if_needed(conn)
        conn.close()
        logger.debug(
            "Fallback recorded: intent=%s mode=%s reason=%s",
            intent_category, resolve_mode, reason,
        )
    except Exception as exc:
        logger.debug("record_fallback failed: %s", exc)


def _rotate_if_needed(conn: sqlite3.Connection) -> None:
    """Elimina registros antiguos si se supera el límite."""
    try:
        count = conn.execute("SELECT COUNT(*) FROM fallback_intents").fetchone()[0]
        if count > MAX_RECORDS:
            # Borrar el 20% más antiguo
            cutoff_id = conn.execute(
                f"SELECT id FROM fallback_intents ORDER BY id ASC LIMIT {MAX_RECORDS // 5}"
            ).fetchall()
            if cutoff_id:
                ids = [r[0] for r in cutoff_id]
                conn.execute(
                    f"DELETE FROM fallback_intents WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lectura y análisis
# ---------------------------------------------------------------------------

def get_top_fallbacks(limit: int = 10, days: int = 7) -> List[Dict[str, Any]]:
    """
    Retorna las intenciones que más veces fallaron en los últimos N días.

    Retorna: [{"intent_category": ..., "count": ..., "last_seen": ...}]
    """
    cutoff = time.time() - (days * 86400)
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT intent_category, COUNT(*) as cnt, MAX(timestamp) as last_ts "
            "FROM fallback_intents "
            "WHERE timestamp > ? "
            "GROUP BY intent_category "
            "ORDER BY cnt DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        conn.close()
        return [
            {"intent_category": r[0], "count": r[1], "last_seen": r[2]}
            for r in rows
        ]
    except Exception:
        return []


def get_summary(days: int = 7) -> Dict[str, Any]:
    """
    Resumen estadístico de fallbacks para el panel de monitoreo.

    Retorna:
      total         — total de fallbacks en los últimos N días
      top_intent    — intención que más falla
      top_reason    — razón más común
      top_mode      — modo de resolución que más falla
    """
    cutoff = time.time() - (days * 86400)
    try:
        conn = _conn()

        total = conn.execute(
            "SELECT COUNT(*) FROM fallback_intents WHERE timestamp > ?",
            (cutoff,),
        ).fetchone()[0]

        top_intent_row = conn.execute(
            "SELECT intent_category, COUNT(*) as c FROM fallback_intents "
            "WHERE timestamp > ? GROUP BY intent_category ORDER BY c DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        top_reason_row = conn.execute(
            "SELECT reason, COUNT(*) as c FROM fallback_intents "
            "WHERE timestamp > ? GROUP BY reason ORDER BY c DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        top_mode_row = conn.execute(
            "SELECT resolve_mode, COUNT(*) as c FROM fallback_intents "
            "WHERE timestamp > ? GROUP BY resolve_mode ORDER BY c DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        conn.close()
        return {
            "total": total,
            "top_intent": top_intent_row[0] if top_intent_row else "-",
            "top_reason": top_reason_row[0] if top_reason_row else "-",
            "top_mode": top_mode_row[0] if top_mode_row else "-",
        }
    except Exception:
        return {"total": 0, "top_intent": "-", "top_reason": "-", "top_mode": "-"}
