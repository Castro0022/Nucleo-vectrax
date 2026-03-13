"""
Vectrax Memory — motor de recall semántico para el loop conversacional.

Dado un texto de entrada, este módulo:
  1. Embeds el texto
  2. Busca stars similares en el canal creator (por cosine similarity)
  3. Busca mensajes previos de conversaciones que sean temáticamente cercanos
  4. Devuelve un contexto estructurado para que el loop chat pueda responder

No requiere API externa — todo se resuelve con los embeddings locales en SQLite.
"""
from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from vectrax import db
from vectrax.embeddings import embed, find_similar, decode_embedding
from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

def recall(
    text: str,
    top_stars: int = 3,
    top_messages: int = 5,
    star_threshold: float = 0.55,
) -> dict:
    """
    Dado un texto, devuelve el contexto de memoria relevante:

    Returns:
        {
          "similar_stars":    [Star, ...],        # stars semánticamente cercanas
          "recent_messages":  [dict, ...],         # últimos mensajes del historial
          "echo_star":        Star | None,         # star más cercana (si supera umbral)
        }
    """
    db.init_db()
    vec = embed(text)

    # Buscar stars similares en el canal creator
    creator_stars = db.get_all_stars(channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
    existing = [
        (s.id, decode_embedding(s.embedding))
        for s in creator_stars
        if s.embedding is not None
    ]
    similar_ids = find_similar(vec, existing, threshold=star_threshold)

    star_map = {s.id: s for s in creator_stars}
    similar_stars = [
        star_map[sid]
        for sid, _ in similar_ids[:top_stars]
        if sid in star_map
    ]

    # Echo star: la más cercana (para confirmar recuerdo)
    echo_star = similar_stars[0] if similar_stars else None

    # Mensajes recientes del historial conversacional
    recent_messages = db.get_recent_messages(limit=top_messages)

    return {
        "similar_stars": similar_stars,
        "recent_messages": recent_messages,
        "echo_star": echo_star,
    }


# ---------------------------------------------------------------------------
# Formato de respuesta de Vectrax
# ---------------------------------------------------------------------------

def build_response(text: str, context: dict, session_id: str) -> str:
    """
    Construye la respuesta textual de Vectrax basada en el contexto de memoria.
    Devuelve un string con la respuesta formateada (sin markup Rich).
    """
    echo = context.get("echo_star")
    similar = context.get("similar_stars", [])
    recent = context.get("recent_messages", [])

    parts: List[str] = []

    # Recuerdo semántico
    if echo:
        ts = datetime.fromtimestamp(echo.timestamp).strftime("%d/%m/%Y %H:%M")
        parts.append(f"Recuerdo algo relacionado [{echo.id[:8]}] — {ts}:\n  \"{echo.content[:120]}\"")

    # Otros recuerdos similares
    if len(similar) > 1:
        extras = [f"  · {s.content[:80]}" for s in similar[1:]]
        parts.append("También encuentro:\n" + "\n".join(extras))

    # Si no hay recuerdo semántico, mencionar el historial
    if not echo and recent:
        last = recent[-1]
        ts = datetime.fromtimestamp(last["timestamp"]).strftime("%d/%m/%Y %H:%M")
        parts.append(f"No hay recuerdo semántico cercano. Último mensaje ({ts}):\n  \"{last['content'][:100]}\"")

    if not parts:
        parts.append("Primera vez que registro esto. Lo grabo en el núcleo.")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Resumen de sesión
# ---------------------------------------------------------------------------

def session_summary(session_id: str) -> dict:
    """Genera un resumen de la sesión actual."""
    messages = db.get_session_messages(session_id)
    mario_msgs = [m for m in messages if m["role"] == "mario"]
    stars_created = [m["star_id"] for m in mario_msgs if m.get("star_id")]
    return {
        "message_count": len(messages),
        "mario_turns": len(mario_msgs),
        "stars_created": len(stars_created),
        "star_ids": stars_created,
    }


# ---------------------------------------------------------------------------
# Saludo de bienvenida
# ---------------------------------------------------------------------------

def build_welcome(current_session_id: str) -> str:
    """Construye el mensaje de bienvenida con resumen de la última sesión."""
    last = db.get_last_session()

    # Si la única sesión es la actual (primera vez), bienvenida simple
    if not last or last["session_id"] == current_session_id:
        return (
            "Primera sesión iniciada.\n"
            "Todo lo que digas quedará grabado en el núcleo.\n"
            "Ctrl-C para salir."
        )

    ts_start = datetime.fromtimestamp(last["started_at"]).strftime("%d/%m/%Y %H:%M")
    ts_last  = datetime.fromtimestamp(last["last_at"]).strftime("%H:%M")
    n        = last["message_count"]

    # Último mensaje de Mario en esa sesión
    prev_msgs = db.get_session_messages(last["session_id"])
    last_mario = next(
        (m["content"] for m in reversed(prev_msgs) if m["role"] == "mario"),
        None,
    )

    summary = (
        f"Última sesión: {ts_start} → {ts_last}  ({n} mensajes)\n"
    )
    if last_mario:
        summary += f"Último mensaje: \"{last_mario[:100]}\""

    return summary
