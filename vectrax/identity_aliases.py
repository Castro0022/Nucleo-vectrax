"""
Vectrax — Identity Aliases (reunificación 2026-09-19)
========================================================
Corrige la identidad duplicada del canal creator SIN borrar ni fusionar
destructivamente ninguna cuenta ni token existente.

Hallazgo (auditoría 2026-09-19): existen dos filas `role=owner,
channel=creator` en `users`: `mario` (creador canónico, `vectrax/identity.py
::CREATOR_OWNER`) y `owner` (cuenta duplicada, creada 2026-06-19). Todos los
tokens activos hoy pertenecen a `owner`.

Este módulo NO modifica `users` ni `user_tokens`. Añade una tabla aditiva
`identity_aliases` que mapea un `alias_username` (p.ej. "owner") a su
`canonical_username` (p.ej. "mario"). `resolve_owner()` es la única función
que los llamadores (services/core/auth.py, core/nucleus/nucleus_authority.py)
deben usar para decidir bajo qué identidad se escribe en el canal creator.

Creado: 2026-09-19 — reunificación de Vectrax.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".vectrax" / "vectrax.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table() -> None:
    """Crea la tabla `identity_aliases` si no existe. Aditivo, idempotente."""
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_aliases (
                alias_username     TEXT PRIMARY KEY,
                canonical_username TEXT NOT NULL,
                linked_at          REAL NOT NULL,
                verified_by        TEXT NOT NULL DEFAULT ''
            )
            """
        )


def add_alias(alias_username: str, canonical_username: str, verified_by: str = "") -> None:
    """Registra (o actualiza) que `alias_username` resuelve a
    `canonical_username`. Nunca toca `users`/`user_tokens`."""
    ensure_table()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO identity_aliases (alias_username, canonical_username, linked_at, verified_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(alias_username) DO UPDATE SET
                canonical_username = excluded.canonical_username,
                linked_at = excluded.linked_at,
                verified_by = excluded.verified_by
            """,
            (alias_username, canonical_username, time.time(), verified_by),
        )


def resolve_owner(username: str) -> str:
    """Devuelve la identidad canónica para `username`.

    Si `username` tiene un alias registrado, devuelve `canonical_username`.
    En cualquier otro caso (incluida la ausencia de la tabla), devuelve
    `username` sin cambios — fail-safe, nunca lanza, nunca inventa una
    identidad no verificada.
    """
    if not username:
        return username
    try:
        ensure_table()
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT canonical_username FROM identity_aliases WHERE alias_username = ?",
                (username,),
            ).fetchone()
        if row and row["canonical_username"]:
            return row["canonical_username"]
    except Exception:
        pass
    return username


def get_all_aliases() -> list:
    """Lista todos los alias registrados (para auditoría/reportes)."""
    try:
        ensure_table()
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT alias_username, canonical_username, linked_at, verified_by "
                "FROM identity_aliases"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
