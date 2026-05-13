"""
Vectrax — Onboarding en dos pasos
====================================
Estado de onboarding por usuario.

Flujo:
  none → [/start o primer mensaje] → awaiting_name
  awaiting_name → [usuario responde con nombre] → completed

Persistencia:
  Usa profiles.preferences JSON en vault/user_memory.db.
  Sin tablas nuevas. Sin arquitectura nueva.

Estados registrados:
  welcome_sent        — True cuando se envió el mensaje de presencia
  awaiting_name       — True mientras esperamos el nombre
  onboarding_completed — True cuando el usuario dio su nombre
  user_name           — nombre guardado

Creado: 2026-05-13
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger("vectrax.onboarding")

# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------
NONE          = "none"
AWAITING_NAME = "awaiting_name"
COMPLETED     = "completed"

# Clave en el JSON de preferences
_KEY = "_ob"


# ---------------------------------------------------------------------------
# Utilidades de DB
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vault", "user_memory.db",
    )


def _get_prefs(user_id: str) -> dict:
    try:
        import sqlite3
        conn = sqlite3.connect(_db_path(), timeout=3)
        row = conn.execute(
            "SELECT preferences FROM profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0] or "{}")
    except Exception as exc:
        logger.debug("onboarding _get_prefs failed: %s", exc)
    return {}


def _set_prefs(user_id: str, prefs: dict) -> None:
    try:
        import sqlite3
        conn = sqlite3.connect(_db_path(), timeout=3)
        prefs_json = json.dumps(prefs, ensure_ascii=False)
        conn.execute(
            "INSERT INTO profiles (user_id, name, language, preferences, interests, updated_at) "
            "VALUES (?, '', '', ?, '[]', ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "preferences=excluded.preferences, updated_at=excluded.updated_at",
            (user_id, prefs_json, time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("onboarding _set_prefs failed: %s", exc)


def _has_interactions(user_id: str) -> bool:
    """True si el usuario ya tiene historial (usuario migrado del sistema anterior)."""
    try:
        import sqlite3
        conn = sqlite3.connect(_db_path(), timeout=3)
        count = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def get_state(user_id: str) -> dict:
    """
    Retorna el estado de onboarding del usuario.

    Returns:
        {
            "state": "none" | "awaiting_name" | "completed",
            "name":  str,
            "welcome_sent": bool,
        }
    """
    prefs = _get_prefs(user_id)
    ob = prefs.get(_KEY)

    if ob:
        return {
            "state":        ob.get("state", NONE),
            "name":         ob.get("name", ""),
            "welcome_sent": ob.get("state", NONE) != NONE,
        }

    # Sin registro → verificar si es usuario migrado del sistema anterior
    if _has_interactions(user_id):
        # Usuario existente sin _ob → tratarlo como completado
        logger.debug("onboarding: migrated user detected | %s", user_id[:20])
        return {"state": COMPLETED, "name": "", "welcome_sent": True}

    return {"state": NONE, "name": "", "welcome_sent": False}


def set_welcome_sent(user_id: str) -> None:
    """Marcar que el mensaje de presencia fue enviado → pasar a awaiting_name."""
    prefs = _get_prefs(user_id)
    prefs[_KEY] = {
        "state":        AWAITING_NAME,
        "name":         "",
        "welcome_sent": True,
        "ts_welcome":   time.time(),
    }
    _set_prefs(user_id, prefs)
    logger.info("onboarding: welcome_sent | user=%s", user_id[:20])


def set_completed(user_id: str, name: str) -> None:
    """
    Guardar nombre y marcar onboarding como completado.
    Actualiza también profiles.name para que el resto del sistema lo vea.
    """
    name = name.strip()

    # 1. Actualizar _ob en preferences
    prefs = _get_prefs(user_id)
    prefs[_KEY] = {
        "state":                 COMPLETED,
        "name":                  name,
        "welcome_sent":          True,
        "onboarding_completed":  True,
        "ts_completed":          time.time(),
    }
    _set_prefs(user_id, prefs)

    # 2. Actualizar profiles.name directamente
    try:
        import sqlite3
        conn = sqlite3.connect(_db_path(), timeout=3)
        conn.execute(
            "UPDATE profiles SET name=?, updated_at=? WHERE user_id=?",
            (name, time.time(), user_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("onboarding: profile name update failed: %s", exc)

    logger.info("onboarding: completed | user=%s | name=%s", user_id[:20], name)
