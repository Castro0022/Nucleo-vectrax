"""
Vectrax Team Memory
=====================
Memoria compartida por equipos de trabajo.

Tablas (en vault/user_memory.db):
  teams        — equipos con owner, nombre y código de invitación
  team_members — relación usuario ↔ equipo
  team_notes   — notas compartidas del equipo

API pública:
  create_team(owner_id, name)          → team_id, join_code
  join_team(user_id, join_code)        → bool
  leave_team(user_id)                  → bool
  add_team_note(user_id, text)         → bool
  get_team_context(user_id)            → str  (para inyectar en pipeline)
  get_team_info(user_id)               → dict
  list_members(team_id)               → list
  get_user_team(user_id)              → dict | None
  activate_team_tier(team_id)          → None  (activa tier TEAM a todos)

Creado: 2026-03-31
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import string
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.team_memory")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    join_code   TEXT NOT NULL UNIQUE,
    tier        TEXT NOT NULL DEFAULT 'free',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_teams_owner ON teams(owner_id);
CREATE INDEX IF NOT EXISTS idx_teams_code  ON teams(join_code);

CREATE TABLE IF NOT EXISTS team_members (
    team_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    joined_at   REAL NOT NULL,
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);

CREATE TABLE IF NOT EXISTS team_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     TEXT NOT NULL,
    author_id   TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_notes_team ON team_notes(team_id);
"""

MAX_CONTEXT_NOTES = 10      # notas recientes que se inyectan como contexto
MAX_CONTEXT_CHARS = 1200    # límite de caracteres del contexto de equipo


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE_TABLES)
    return conn


# ---------------------------------------------------------------------------
# Generadores de ID
# ---------------------------------------------------------------------------

def _team_id() -> str:
    return "team_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _join_code() -> str:
    """Código legible de 6 caracteres. Ej: VX-A3K9"""
    chars = random.choices(string.ascii_uppercase + string.digits, k=6)
    return "VX-" + "".join(chars)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def create_team(owner_id: str, name: str) -> Dict[str, str]:
    """
    Crea un equipo nuevo con el usuario como owner.

    Retorna: {"team_id": ..., "join_code": ..., "name": ...}
    Si el usuario ya tiene un equipo, lo abandona primero.
    """
    # Si ya tiene equipo, salir primero
    existing = get_user_team(owner_id)
    if existing:
        leave_team(owner_id)

    team_id = _team_id()
    join_code = _join_code()
    now = time.time()

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO teams (id, name, owner_id, join_code, tier, created_at) "
            "VALUES (?, ?, ?, ?, 'free', ?)",
            (team_id, name.strip()[:60], owner_id, join_code, now),
        )
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role, joined_at) "
            "VALUES (?, ?, 'owner', ?)",
            (team_id, owner_id, now),
        )
        conn.commit()
        logger.info("Team created: %s | owner=%s | code=%s", team_id, owner_id[:20], join_code)
        return {"team_id": team_id, "join_code": join_code, "name": name.strip()[:60]}
    finally:
        conn.close()


def join_team(user_id: str, join_code: str) -> Dict[str, Any]:
    """
    Une al usuario a un equipo usando el código de invitación.

    Retorna: {"ok": True, "team_name": ..., "team_id": ...}
    o {"ok": False, "error": ...}
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name FROM teams WHERE join_code = ?",
            (join_code.strip().upper(),),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Código inválido."}

        team_id, team_name = row

        # Verificar si ya es miembro
        existing = conn.execute(
            "SELECT user_id FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id),
        ).fetchone()
        if existing:
            return {"ok": True, "team_name": team_name, "team_id": team_id, "already_member": True}

        # Abandonar equipo anterior si tenía uno
        prev = get_user_team(user_id)
        if prev and prev["team_id"] != team_id:
            leave_team(user_id)

        conn.execute(
            "INSERT OR IGNORE INTO team_members (team_id, user_id, role, joined_at) "
            "VALUES (?, ?, 'member', ?)",
            (team_id, user_id, time.time()),
        )
        conn.commit()
        logger.info("User %s joined team %s", user_id[:20], team_id)
        return {"ok": True, "team_name": team_name, "team_id": team_id}
    finally:
        conn.close()


def leave_team(user_id: str) -> bool:
    """Elimina al usuario de su equipo actual."""
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM team_members WHERE user_id = ?", (user_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def add_team_note(user_id: str, content: str) -> Dict[str, Any]:
    """
    Agrega una nota compartida al equipo del usuario.

    Retorna: {"ok": True} o {"ok": False, "error": ...}
    """
    team = get_user_team(user_id)
    if not team:
        return {"ok": False, "error": "No perteneces a ningún equipo."}

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO team_notes (team_id, author_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (team["team_id"], user_id, content.strip()[:1000], time.time()),
        )
        conn.commit()
        logger.info("Team note added | team=%s | user=%s", team["team_id"], user_id[:20])
        return {"ok": True, "team_name": team["name"]}
    finally:
        conn.close()


def get_user_team(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Retorna el equipo del usuario o None si no pertenece a ninguno.

    Retorna: {"team_id", "name", "owner_id", "join_code", "tier", "role"}
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT t.id, t.name, t.owner_id, t.join_code, t.tier, tm.role "
            "FROM teams t "
            "JOIN team_members tm ON t.id = tm.team_id "
            "WHERE tm.user_id = ?",
            (user_id,),
        ).fetchone()
        if row:
            return {
                "team_id": row[0], "name": row[1], "owner_id": row[2],
                "join_code": row[3], "tier": row[4], "role": row[5],
            }
        return None
    finally:
        conn.close()


def get_team_info(user_id: str) -> Optional[Dict[str, Any]]:
    """Información completa del equipo incluyendo número de miembros y notas."""
    team = get_user_team(user_id)
    if not team:
        return None

    conn = _conn()
    try:
        member_count = conn.execute(
            "SELECT COUNT(*) FROM team_members WHERE team_id = ?",
            (team["team_id"],),
        ).fetchone()[0]

        note_count = conn.execute(
            "SELECT COUNT(*) FROM team_notes WHERE team_id = ?",
            (team["team_id"],),
        ).fetchone()[0]

        team["member_count"] = member_count
        team["note_count"] = note_count
        return team
    finally:
        conn.close()


def list_members(team_id: str) -> List[Dict[str, Any]]:
    """Lista de miembros del equipo con nombre si está disponible."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT tm.user_id, tm.role, tm.joined_at, p.name "
            "FROM team_members tm "
            "LEFT JOIN profiles p ON tm.user_id = p.user_id "
            "WHERE tm.team_id = ? "
            "ORDER BY tm.joined_at ASC",
            (team_id,),
        ).fetchall()
        return [
            {
                "user_id": r[0],
                "role": r[1],
                "joined_at": r[2],
                "name": r[3] or "(sin nombre)",
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_team_context(user_id: str) -> str:
    """
    Construye el contexto de equipo para inyectar en el pipeline cognitivo.

    Incluye:
      - Nombre del equipo
      - Últimas N notas compartidas (más recientes primero)

    Retorna cadena vacía si el usuario no tiene equipo o no hay notas.
    """
    team = get_user_team(user_id)
    if not team:
        return ""

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT tn.content, tn.author_id, tn.created_at, p.name "
            "FROM team_notes tn "
            "LEFT JOIN profiles p ON tn.author_id = p.user_id "
            "WHERE tn.team_id = ? "
            "ORDER BY tn.created_at DESC LIMIT ?",
            (team["team_id"], MAX_CONTEXT_NOTES),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return ""

    lines = [f"[Conocimiento del equipo '{team['name']}']"]
    for content, author_id, created_at, author_name in rows:
        author = author_name or author_id.split(":")[-1][:8]
        lines.append(f"• {content} (registrado por {author})")

    context = "\n".join(lines)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS].rsplit("\n", 1)[0]
    return context


def activate_team_tier(team_id: str) -> None:
    """
    Activa el tier TEAM para el owner y todos los miembros del equipo.
    Se llama después de un pago exitoso.
    """
    try:
        from core.operator.user_tiers import set_tier, Tier
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT user_id FROM team_members WHERE team_id = ?", (team_id,)
            ).fetchall()
            conn.execute(
                "UPDATE teams SET tier = 'team' WHERE id = ?", (team_id,)
            )
            conn.commit()
        finally:
            conn.close()

        for (uid,) in rows:
            set_tier(uid, Tier.TEAM)
            logger.info("TEAM tier activated | user=%s | team=%s", uid[:20], team_id)
    except Exception as exc:
        logger.error("activate_team_tier failed: %s", exc)


def deactivate_team_tier(team_id: str) -> None:
    """Revierte todos los miembros a FREE cuando el equipo cancela."""
    try:
        from core.operator.user_tiers import set_tier, Tier
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT user_id FROM team_members WHERE team_id = ?", (team_id,)
            ).fetchall()
            conn.execute(
                "UPDATE teams SET tier = 'free' WHERE id = ?", (team_id,)
            )
            conn.commit()
        finally:
            conn.close()

        for (uid,) in rows:
            set_tier(uid, Tier.FREE)
    except Exception as exc:
        logger.error("deactivate_team_tier failed: %s", exc)
