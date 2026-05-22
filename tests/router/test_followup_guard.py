"""
tests/router/test_followup_guard.py
=====================================
Cobertura de las dos reparaciones:

  * Capa 1 — contextual_followup_guard:
      ExternalGateway._is_short_followup() y _has_recent_active_turn()
      detectan correctamente conversaciones activas y bloquean falsos
      enrutamientos a RESOLVE_ONLINE.

  * Capa 2 — memoria activa relevante:
      vectrax.user_memory.get_memory_context preserva más texto por
      turno (MAX_TURN_CHARS = 280, antes 80) y eleva MAX_CONTEXT_CHARS
      a 1500 (antes 800).

No se prueba el flujo completo del pipeline aquí — esa cobertura
ya vive en tests/observability/test_router_activation_quality.py.
"""
from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# Capa 2 — constantes vivas y truncation efectiva
# ---------------------------------------------------------------------------

def test_capa2_constants_match_spec():
    import vectrax.user_memory as um
    assert um.MAX_TURN_CHARS == 280, (
        f"MAX_TURN_CHARS debe ser 280 (Capa 2), actual={um.MAX_TURN_CHARS}"
    )
    assert um.MAX_CONTEXT_CHARS == 1500, (
        f"MAX_CONTEXT_CHARS debe ser 1500 (Capa 2), actual={um.MAX_CONTEXT_CHARS}"
    )


def test_capa2_context_preserves_long_turn(tmp_path, monkeypatch):
    """Un mensaje largo (~250 chars) debe sobrevivir el truncation."""
    # Aislar la DB del test
    db_path = tmp_path / "user_memory_capa2.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    # Reset del singleton para que recoja el monkeypatch
    import vectrax.user_memory as um
    um._store = None

    long_msg = (
        "Quiero discutir contigo, Vectrax, la relación entre tu "
        "personalidad, la continuidad conversacional y cómo "
        "mantienes coherencia entre turnos largos. Es un punto "
        "crítico para el diseño de la capa cognitiva."
    )
    assert 200 < len(long_msg) <= 280, "fixture debe estar en [200,280]"

    um.store_memory("test_user_capa2", long_msg, "ack corto")
    ctx = um.get_memory_context("test_user_capa2")

    # El mensaje completo debe estar (no se cortó a 80 chars)
    assert long_msg in ctx, (
        "El historial truncó el mensaje del usuario por debajo de 280 chars"
    )


# ---------------------------------------------------------------------------
# Capa 1 — _is_short_followup / _has_recent_active_turn
# ---------------------------------------------------------------------------

def test_capa1_no_history_no_fire(tmp_path, monkeypatch):
    """Sin historial previo, el guard no se activa."""
    db_path = tmp_path / "user_memory_capa1_empty.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    import vectrax.user_memory as um
    um._store = None

    from core.operator.external_gateway import ExternalGateway
    is_fu, reason = ExternalGateway._is_short_followup(
        "fresh_user", "Dime tu?",
    )
    assert is_fu is False
    assert "no_recent_turn" in reason


def test_capa1_short_msg_with_recent_history_fires(tmp_path, monkeypatch):
    """Mensaje corto + turno reciente = fire."""
    db_path = tmp_path / "user_memory_capa1_recent.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    import vectrax.user_memory as um
    um._store = None

    # Crear un turno reciente (ahora)
    um.store_memory("active_user", "hola, hablemos de continuidad", "ok")

    from core.operator.external_gateway import ExternalGateway
    is_fu, reason = ExternalGateway._is_short_followup(
        "active_user", "Dime tu?",
    )
    assert is_fu is True, f"esperaba fire, got {is_fu} ({reason})"
    assert "short_msg" in reason
    assert "recent_turn" in reason


def test_capa1_long_msg_does_not_fire(tmp_path, monkeypatch):
    """Mensaje largo no debe activar el guard aunque haya historial."""
    db_path = tmp_path / "user_memory_capa1_long.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    import vectrax.user_memory as um
    um._store = None

    um.store_memory("active_user2", "hola", "ok")

    from core.operator.external_gateway import ExternalGateway
    long_query = "qué piensas sobre el comportamiento térmico del aluminio"
    is_fu, reason = ExternalGateway._is_short_followup(
        "active_user2", long_query,
    )
    assert is_fu is False
    assert "too_long" in reason


def test_capa1_stale_history_does_not_fire(tmp_path, monkeypatch):
    """Si el último turno tiene más de window_seconds, el guard NO activa."""
    db_path = tmp_path / "user_memory_capa1_stale.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    import vectrax.user_memory as um
    um._store = None

    # Insertar manualmente un turno antiguo (1h atrás)
    import sqlite3
    um.store_memory("stale_user", "hola", "ok")
    stale_ts = time.time() - 3600
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE interactions SET timestamp = ? WHERE user_id = ?",
        (stale_ts, "stale_user"),
    )
    conn.commit()
    conn.close()

    from core.operator.external_gateway import ExternalGateway
    is_fu, reason = ExternalGateway._is_short_followup(
        "stale_user", "dime?",
    )
    assert is_fu is False
    assert "no_recent_turn" in reason


def test_capa1_max_words_boundary(tmp_path, monkeypatch):
    """Justo en max_words=7 (default actual) → fire. 8 palabras → no fire."""
    db_path = tmp_path / "user_memory_capa1_boundary.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    import vectrax.user_memory as um
    um._store = None

    um.store_memory("boundary_user", "hola", "ok")

    from core.operator.external_gateway import ExternalGateway

    # 7 palabras = exactamente max_words → fire
    is_fu_7, _ = ExternalGateway._is_short_followup(
        "boundary_user", "qué piensas sobre eso que te dije",  # 7 palabras
    )
    assert is_fu_7 is True

    # 8 palabras > max_words → too_long, no fire
    is_fu_8, reason_8 = ExternalGateway._is_short_followup(
        "boundary_user", "qué piensas sobre eso que te dije hoy",  # 8 palabras
    )
    assert is_fu_8 is False
    assert "too_long" in reason_8

    # Compatibilidad: llamada con max_words=4 explícito mantiene semántica
    # anterior (límite de 4 palabras) — el parámetro sigue funcionando
    is_fu_4, _ = ExternalGateway._is_short_followup(
        "boundary_user", "qué piensas sobre eso",  # 4 palabras ≤ 4 → fire
        max_words=4,
    )
    assert is_fu_4 is True

    is_fu_5_limited, reason_5 = ExternalGateway._is_short_followup(
        "boundary_user", "qué piensas sobre eso ahora",  # 5 palabras > max_words=4 → no fire
        max_words=4,
    )
    assert is_fu_5_limited is False
    assert "too_long" in reason_5


def test_capa1_custom_window(tmp_path, monkeypatch):
    """Pasar window_seconds custom afecta la decisión."""
    db_path = tmp_path / "user_memory_capa1_window.db"
    monkeypatch.setattr(
        "vectrax.user_memory._MEMORY_DB_PATH", str(db_path),
    )
    import vectrax.user_memory as um
    um._store = None

    um.store_memory("window_user", "hola", "ok")
    # Forzar el turno a hace 600s
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE interactions SET timestamp = ? WHERE user_id = ?",
        (time.time() - 600, "window_user"),
    )
    conn.commit()
    conn.close()

    from core.operator.external_gateway import ExternalGateway

    # default 300s: no fire
    is_fu_default, _ = ExternalGateway._is_short_followup(
        "window_user", "dime?",
    )
    assert is_fu_default is False

    # ventana ampliada a 900s: fire
    is_fu_wide, _ = ExternalGateway._is_short_followup(
        "window_user", "dime?", window_seconds=900,
    )
    assert is_fu_wide is True
