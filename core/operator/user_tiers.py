"""
Vectrax User Tiers — Control de Acceso y Límites por Usuario
================================================================
Cada usuario tiene un tier que define qué puede hacer y cuánto.

Tiers:
  FREE    — default, 20 msgs/día, solo fast-path + búsqueda web
  PRO     — ilimitado, voz, mapas, mercado, memoria completa
  CREATOR — todo + aprobar reglas, monitor, alertas del sistema

Persistence: SQLite en vault/user_memory.db (tabla user_tiers)
Enforcement: gateway verifica tier antes de cada mensaje

Creado: 2026-03-29
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.user_tiers")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "user_memory.db",
)

_CREATE_TIERS = """
CREATE TABLE IF NOT EXISTS user_tiers (
    user_id     TEXT PRIMARY KEY,
    tier        TEXT NOT NULL DEFAULT 'free',
    daily_limit INTEGER NOT NULL DEFAULT 20,
    msgs_today  INTEGER NOT NULL DEFAULT 0,
    last_reset  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    CREATOR = "creator"


TIER_LIMITS = {
    Tier.FREE: {
        "daily_messages": 20,
        "voice": False,
        "maps": False,
        "market": False,
        "memory_full": False,
        "fast_path_only": True,
        "web_search": True,
    },
    Tier.PRO: {
        "daily_messages": -1,  # unlimited
        "voice": True,
        "maps": True,
        "market": True,
        "memory_full": True,
        "fast_path_only": False,
        "web_search": True,
    },
    Tier.CREATOR: {
        "daily_messages": -1,
        "voice": True,
        "maps": True,
        "market": True,
        "memory_full": True,
        "fast_path_only": False,
        "web_search": True,
        "approve_rules": True,
        "system_monitor": True,
        "system_alerts": True,
    },
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class UserAccess:
    """Resultado de verificar acceso de un usuario."""
    user_id: str
    tier: Tier
    allowed: bool
    reason: str = ""
    msgs_today: int = 0
    daily_limit: int = 20
    features: Dict[str, bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute(_CREATE_TIERS)
    conn.commit()
    return conn


def _today() -> str:
    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def get_tier(user_id: str) -> Tier:
    """Get user's tier. Returns FREE if not set."""
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT tier FROM user_tiers WHERE user_id = ?", (user_id,),
        ).fetchone()
        conn.close()
        if row:
            return Tier(row[0])
    except Exception:
        pass
    return Tier.FREE


def set_tier(user_id: str, tier: Tier) -> None:
    """Set user's tier. Creator-only operation."""
    now = time.time()
    try:
        conn = _conn()
        limits = TIER_LIMITS.get(tier, TIER_LIMITS[Tier.FREE])
        daily = limits["daily_messages"]

        existing = conn.execute(
            "SELECT user_id FROM user_tiers WHERE user_id = ?", (user_id,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE user_tiers SET tier = ?, daily_limit = ?, updated_at = ? "
                "WHERE user_id = ?",
                (tier.value, daily, now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO user_tiers (user_id, tier, daily_limit, msgs_today, last_reset, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, ?, ?, ?)",
                (user_id, tier.value, daily, _today(), now, now),
            )
        conn.commit()
        conn.close()
        logger.info("Tier set: %s → %s", user_id[:20], tier.value)
    except Exception as exc:
        logger.error("Failed to set tier: %s", exc)


def check_access(user_id: str) -> UserAccess:
    """
    Check if user can send a message right now.

    Verifies:
      1. User tier
      2. Daily message limit
      3. Auto-reset counter at midnight

    Returns UserAccess with allowed=True/False.
    """
    tier = get_tier(user_id)
    limits = TIER_LIMITS.get(tier, TIER_LIMITS[Tier.FREE])
    daily_limit = limits["daily_messages"]
    today = _today()

    # Unlimited tiers
    if daily_limit == -1:
        return UserAccess(
            user_id=user_id,
            tier=tier,
            allowed=True,
            msgs_today=0,
            daily_limit=-1,
            features=limits,
        )

    # Check and update counter
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT msgs_today, last_reset, daily_limit FROM user_tiers WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row is None:
            # First time user — create entry
            conn.execute(
                "INSERT INTO user_tiers (user_id, tier, daily_limit, msgs_today, last_reset, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?)",
                (user_id, tier.value, daily_limit, today, time.time(), time.time()),
            )
            conn.commit()
            conn.close()
            return UserAccess(
                user_id=user_id, tier=tier, allowed=True,
                msgs_today=1, daily_limit=daily_limit, features=limits,
            )

        msgs_today, last_reset, db_limit = row

        # Reset at midnight
        if last_reset != today:
            msgs_today = 0
            conn.execute(
                "UPDATE user_tiers SET msgs_today = 0, last_reset = ?, updated_at = ? "
                "WHERE user_id = ?",
                (today, time.time(), user_id),
            )

        # Check limit
        if msgs_today >= daily_limit:
            conn.close()
            return UserAccess(
                user_id=user_id, tier=tier, allowed=False,
                reason=f"Límite diario alcanzado ({msgs_today}/{daily_limit}). Vuelve mañana o actualiza a PRO.",
                msgs_today=msgs_today, daily_limit=daily_limit, features=limits,
            )

        # Increment
        conn.execute(
            "UPDATE user_tiers SET msgs_today = msgs_today + 1, updated_at = ? WHERE user_id = ?",
            (time.time(), user_id),
        )
        conn.commit()
        conn.close()

        return UserAccess(
            user_id=user_id, tier=tier, allowed=True,
            msgs_today=msgs_today + 1, daily_limit=daily_limit, features=limits,
        )

    except Exception as exc:
        logger.warning("Access check failed: %s", exc)
        # Fail open for now
        return UserAccess(
            user_id=user_id, tier=tier, allowed=True,
            features=limits,
        )


def can_use_feature(user_id: str, feature: str) -> bool:
    """Check if user's tier allows a specific feature."""
    tier = get_tier(user_id)
    limits = TIER_LIMITS.get(tier, TIER_LIMITS[Tier.FREE])
    return limits.get(feature, False)


# ---------------------------------------------------------------------------
# Creator management commands
# ---------------------------------------------------------------------------

def list_users() -> list:
    """List all users with tiers."""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT user_id, tier, msgs_today, daily_limit, last_reset FROM user_tiers "
            "ORDER BY updated_at DESC",
        ).fetchall()
        conn.close()
        return [
            {"user_id": r[0], "tier": r[1], "msgs_today": r[2],
             "daily_limit": r[3], "last_reset": r[4]}
            for r in rows
        ]
    except Exception:
        return []
