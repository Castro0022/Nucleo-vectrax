"""
Vectrax — Active Conversation State
======================================
Estado conversacional vivo por sesión de usuario.

Sin base de datos. Sin embeddings. Sin Gravity.
Solo estado activo inmediato en RAM con TTL.

Qué guarda:
  active_topic    — de qué está hablando el usuario ahora
  active_entity   — persona/entidad mencionada en el hilo (ej: "Carlos")
  active_referent — último referente anafórico resuelto
  active_mode     — "normal" | "presence" | "technical"
  last_user_intent — intención detectada en el último turno

Actualización: tras cada turno completado.
TTL: 1800s (30 min de inactividad → estado expirado).

Creado: 2026-05-12
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import re
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger("vectrax.conversation.active_state")

# ---------------------------------------------------------------------------
# Modelo de estado
# ---------------------------------------------------------------------------

@dataclass
class ConversationState:
    active_topic: str = ""
    active_entity: str = ""
    active_referent: str = ""
    active_mode: str = "normal"
    last_user_intent: str = ""
    last_updated: float = field(default_factory=time.time)

    def is_alive(self, ttl: int = 1800) -> bool:
        """True si el estado no ha expirado."""
        return (time.time() - self.last_updated) < ttl

    def touch(self) -> None:
        self.last_updated = time.time()

    def summary(self) -> str:
        """Resumen legible del estado actual."""
        parts = []
        if self.active_topic:
            parts.append(f"topic={self.active_topic!r}")
        if self.active_entity:
            parts.append(f"entity={self.active_entity!r}")
        if self.active_mode != "normal":
            parts.append(f"mode={self.active_mode}")
        return ", ".join(parts) or "vacío"


# ---------------------------------------------------------------------------
# Patrones de extracción de tópico
# ---------------------------------------------------------------------------

# Capturas de tópico desde el mensaje del usuario.
# Ordenados por especificidad (más específico primero).
_TOPIC_EXTRACTORS: list[Tuple[re.Pattern, int]] = [
    # "siento que X", "creo que X", "me parece que X"
    (re.compile(r"siento\s+que\s+(.{5,80}?)(?:[.!?]|$)", re.I), 1),
    (re.compile(r"creo\s+que\s+(.{5,80}?)(?:[.!?]|$)", re.I), 1),
    (re.compile(r"me\s+parece\s+que\s+(.{5,80}?)(?:[.!?]|$)", re.I), 1),
    (re.compile(r"noto\s+que\s+(.{5,80}?)(?:[.!?]|$)", re.I), 1),
    # "el problema es X", "el tema es X"
    (re.compile(r"el\s+(?:problema|tema|asunto)\s+es\s+(.{5,80}?)(?:[.!?]|$)", re.I), 1),
    # "no quiero que X"
    (re.compile(r"no\s+quiero\s+que\s+(.{5,80}?)(?:[.!?]|$)", re.I), 1),
    # "hablemos de X", "hablando de X"
    (re.compile(r"habl(?:emos|ando)\s+de\s+(.{5,60}?)(?:[.!?]|$)", re.I), 1),
    # "sobre X"
    (re.compile(r"\bsobre\s+(.{5,60}?)(?:[.!?]|$)", re.I), 1),
    # "Vectrax X" — captura descripción de comportamiento de Vectrax
    (re.compile(r"\bvectrax\s+(.{5,60}?)(?:[.!?]|$)", re.I), 0),
    # "que X parece/parece/suena" — descripción de algo
    (re.compile(r"que\s+(?:parece|suena|se\s+siente\s+como)\s+(.{5,60}?)(?:[.!?]|$)", re.I), 1),
]

# Entidades: nombres propios en el mensaje (no al inicio de frase).
# Captura nombres como "Carlos", "Mario", "Laura García".
_ENTITY_PATTERN = re.compile(
    r"(?<![.!?]\s)(?<!\A)\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})?)\b"
)

# Palabras que NO son entidades (palabras comunes con mayúscula).
_NON_ENTITIES = frozenset({
    "Vectrax", "Telegram", "Google", "OpenAI", "ChatGPT", "YouTube",
    "WhatsApp", "Instagram", "Twitter", "LinkedIn", "Python", "Docker",
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo",
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    "Mario",  # el creador — no queremos sobreescribir active_entity con él
})

# Patrones de intent
_INTENT_MARKERS = {
    "complaint":    re.compile(r"\b(no\s+me\s+gusta|está\s+mal|falla|no\s+funciona|problema|error)\b", re.I),
    "curiosity":    re.compile(r"\b(por\s+qué|cómo|qué\s+es|explícame|cuéntame)\b", re.I),
    "preference":   re.compile(r"\b(prefiero|quiero\s+que|no\s+quiero|me\s+gustaría)\b", re.I),
    "continuation": re.compile(r"\b(y\s+eso|y\s+él|y\s+ella|entonces|así|volviendo|retomando)\b", re.I),
    "identity":     re.compile(r"\b(vectrax|tú|vos|cómo\s+te|qué\s+eres)\b", re.I),
}


def _extract_topic(text: str) -> str:
    """Extrae el tópico principal del mensaje del usuario."""
    for pattern, group in _TOPIC_EXTRACTORS:
        m = pattern.search(text)
        if m:
            topic = m.group(group).strip().rstrip(".!?,;")
            if len(topic) >= 5:
                return topic[:80]
    # Fallback: si el mensaje es sustancial, usarlo completo truncado
    clean = text.strip().rstrip(".!?")
    if len(clean.split()) >= 4:
        return clean[:70]
    return ""


def _extract_entity(text: str) -> str:
    """Extrae la entidad (persona) mencionada en el mensaje."""
    for m in _ENTITY_PATTERN.finditer(text):
        candidate = m.group(1)
        if candidate not in _NON_ENTITIES and len(candidate) >= 3:
            return candidate
    return ""


def _detect_intent(text: str) -> str:
    """Detecta la intención dominante del mensaje."""
    for intent, pattern in _INTENT_MARKERS.items():
        if pattern.search(text):
            return intent
    return "general"


# ---------------------------------------------------------------------------
# Store global (in-memory, thread-safe)
# ---------------------------------------------------------------------------

class _StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: Dict[str, ConversationState] = {}

    def get(self, user_id: str) -> ConversationState:
        """Obtiene el estado del usuario. Crea uno nuevo si no existe."""
        with self._lock:
            state = self._states.get(user_id)
            if state is None or not state.is_alive():
                state = ConversationState()
                self._states[user_id] = state
            return state

    def update(self, user_id: str, user_input: str, bot_output: str) -> None:
        """
        Actualiza el estado con la información del turno actual.
        Extrae tópico, entidad e intent del mensaje del usuario.
        """
        with self._lock:
            state = self._states.get(user_id)
            if state is None:
                state = ConversationState()
                self._states[user_id] = state

            # Extraer tópico
            new_topic = _extract_topic(user_input)
            if new_topic:
                state.active_topic = new_topic

            # Extraer entidad (no sobreescribir si ya hay una y el nuevo msg no tiene)
            new_entity = _extract_entity(user_input)
            if new_entity:
                state.active_entity = new_entity

            # Intent del turno actual
            state.last_user_intent = _detect_intent(user_input)

            # Modo: si el tópico menciona Vectrax → presence mode
            topic_lower = state.active_topic.lower()
            if any(k in topic_lower for k in ("vectrax", "frío", "genérico", "buscador", "explica", "responde")):
                state.active_mode = "presence"
            else:
                state.active_mode = "normal"

            state.touch()

        logger.debug(
            "ActiveState updated | user=%s | %s",
            user_id[:20], state.summary(),
        )

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._states.pop(user_id, None)

    def purge_expired(self, ttl: int = 1800) -> int:
        """Limpia estados expirados. Retorna cantidad eliminada."""
        with self._lock:
            expired = [uid for uid, s in self._states.items() if not s.is_alive(ttl)]
            for uid in expired:
                del self._states[uid]
            return len(expired)


_store = _StateStore()


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def get_state(user_id: str) -> ConversationState:
    """Obtiene el estado conversacional activo del usuario."""
    return _store.get(user_id)


def update_state(user_id: str, user_input: str, bot_output: str) -> None:
    """Actualiza el estado tras un turno completado."""
    _store.update(user_id, user_input, bot_output)


def clear_state(user_id: str) -> None:
    """Limpia el estado del usuario (útil en tests)."""
    _store.clear(user_id)


def purge_expired() -> int:
    """Mantenimiento: elimina estados expirados."""
    return _store.purge_expired()
