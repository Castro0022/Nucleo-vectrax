"""
Vectrax Identity Anchor
==========================
Anclaje estricto de identidad por usuario. Garantiza que una vez que
Vectrax conoce la identidad de un usuario, jamás se pierde, se
contradice ni se vuelve a preguntar.

Componentes:
  get_anchored_identity(user_id) → IdentityAnchor
  guard_identity_denial(text, anchor) → str
  lock_language(user_id, lang)
  get_locked_language(user_id) → str
  invalidate_session(user_id)

Principio: la memoria tiene prioridad absoluta sobre el LLM.
El usuario se identifica una vez; Vectrax lo recuerda siempre.

Capa: External — Anclaje de identidad
Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.identity_anchor")


# ---------------------------------------------------------------------------
# Modelo de identidad anclada
# ---------------------------------------------------------------------------

@dataclass
class IdentityAnchor:
    """Identidad anclada de un usuario para la sesión activa."""
    user_id: str = ""
    name: str = ""
    language: str = ""
    language_locked: bool = False
    preferences: Dict[str, str] = field(default_factory=dict)
    interests: list = field(default_factory=list)
    loaded: bool = False

    @property
    def has_name(self) -> bool:
        return bool(self.name)

    @property
    def has_language(self) -> bool:
        return bool(self.language)

    def identity_context(self) -> str:
        """Genera línea de contexto de identidad para inyectar en prompts."""
        parts = []
        if self.name:
            parts.append(f"El usuario se llama {self.name}.")
        if self.language:
            lang_label = "español" if self.language == "es" else "inglés"
            parts.append(f"Idioma del usuario: {lang_label}.")
        if self.preferences:
            vals = ", ".join(list(self.preferences.values())[:5])
            parts.append(f"Preferencias: {vals}.")
        if self.interests:
            vals = ", ".join(self.interests[:5])
            parts.append(f"Intereses: {vals}.")
        return " ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "language": self.language,
            "language_locked": self.language_locked,
            "preferences": dict(self.preferences),
            "interests": list(self.interests),
            "loaded": self.loaded,
        }


# ---------------------------------------------------------------------------
# Session Cache — caché de identidad por sesión
# ---------------------------------------------------------------------------

class _SessionCache:
    """Caché en memoria de identidades ancladas por usuario."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Dict[str, IdentityAnchor] = {}
        self._language_locks: Dict[str, str] = {}

    def get(self, user_id: str) -> Optional[IdentityAnchor]:
        with self._lock:
            return self._cache.get(user_id)

    def put(self, anchor: IdentityAnchor) -> None:
        with self._lock:
            self._cache[anchor.user_id] = anchor

    def invalidate(self, user_id: str) -> None:
        with self._lock:
            self._cache.pop(user_id, None)
            self._language_locks.pop(user_id, None)

    def lock_language(self, user_id: str, lang: str) -> None:
        with self._lock:
            self._language_locks[user_id] = lang
            if user_id in self._cache:
                self._cache[user_id].language = lang
                self._cache[user_id].language_locked = True

    def get_locked_language(self, user_id: str) -> str:
        with self._lock:
            return self._language_locks.get(user_id, "")

    def is_language_locked(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._language_locks

    def clear_all(self) -> None:
        """Solo para testing."""
        with self._lock:
            self._cache.clear()
            self._language_locks.clear()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "cached_identities": len(self._cache),
                "language_locks": len(self._language_locks),
            }


_session = _SessionCache()


# ---------------------------------------------------------------------------
# API principal — cargar y anclar identidad
# ---------------------------------------------------------------------------

def get_anchored_identity(user_id: str) -> IdentityAnchor:
    """
    Obtiene la identidad anclada de un usuario.

    1. Busca en session cache → retorno inmediato si existe
    2. Si no, carga desde user_memory (perfil)
    3. Cachea para reutilización en la sesión
    4. Si hay language lock, lo aplica

    DEBE llamarse ANTES de cualquier procesamiento del mensaje.
    """
    if not user_id:
        return IdentityAnchor(loaded=False)

    # 1. Session cache hit
    cached = _session.get(user_id)
    if cached and cached.loaded:
        logger.debug(
            "Identity cache HIT | user=%s | name=%s",
            user_id[:20], cached.name or "(none)",
        )
        return cached

    # 2. Cargar desde memoria persistente
    anchor = IdentityAnchor(user_id=user_id)
    try:
        from vectrax.user_memory import get_user_profile
        profile = get_user_profile(user_id)
        if profile:
            anchor.name = profile.get("name", "")
            anchor.language = profile.get("language", "")
            anchor.preferences = profile.get("preferences", {})
            anchor.interests = profile.get("interests", [])
    except Exception as exc:
        logger.debug("Could not load profile for %s: %s", user_id[:20], exc)

    # 3. Aplicar language lock si existe
    locked_lang = _session.get_locked_language(user_id)
    if locked_lang:
        anchor.language = locked_lang
        anchor.language_locked = True

    anchor.loaded = True
    _session.put(anchor)

    logger.info(
        "Identity anchored | user=%s | name=%s | lang=%s | locked=%s",
        user_id[:20],
        anchor.name or "(none)",
        anchor.language or "(none)",
        anchor.language_locked,
    )
    return anchor


def refresh_anchor(user_id: str) -> IdentityAnchor:
    """Fuerza recarga de identidad desde memoria (invalida cache)."""
    _session.invalidate(user_id)
    return get_anchored_identity(user_id)


# ---------------------------------------------------------------------------
# Identity Guard — bloquear negación de identidad por el LLM
# ---------------------------------------------------------------------------

_IDENTITY_DENIAL_PATTERNS = re.compile(
    r"(?:"
    r"(?:no\s+(?:s[eé]|conozco|tengo)\s+(?:tu\s+nombre|c[oó]mo\s+te\s+llam|qui[eé]n\s+eres))"
    r"|(?:no\s+tengo\s+(?:esa?\s+)?(?:informaci[oó]n|dato)\s+(?:sobre\s+tu|de\s+tu)\s+(?:nombre|identidad))"
    r"|(?:d[ií]me\s+(?:tu\s+nombre|c[oó]mo\s+te\s+llam))"
    r"|(?:cu[aá]l\s+es\s+tu\s+nombre)"
    r"|(?:c[oó]mo\s+(?:te\s+llamas|debo\s+llamarte))"
    r"|(?:no\s+me\s+has\s+(?:dicho|dado)\s+tu\s+nombre)"
    r"|(?:I\s+don'?t\s+know\s+your\s+name)"
    r"|(?:I\s+(?:don'?t|do\s+not)\s+have\s+your\s+name)"
    r"|(?:(?:tell|what(?:'s|\s+is))\s+(?:me\s+)?your\s+name)"
    r"|(?:what\s+(?:is|should\s+I\s+call)\s+your\s+name)"
    r"|(?:you\s+haven'?t\s+told\s+me\s+your\s+name)"
    r"|(?:I\s+(?:don'?t|do\s+not)\s+(?:know|have)\s+(?:that|this)\s+(?:information|data))"
    r")",
    re.IGNORECASE,
)

# Patrones donde el LLM pide el nombre al usuario (confusión de roles)
_NAME_REQUEST_PATTERNS = re.compile(
    r"(?:"
    r"(?:¿?c[oó]mo\s+(?:te\s+llamas|quieres\s+que\s+te\s+llame)\??)"
    r"|(?:¿?cu[aá]l\s+es\s+tu\s+nombre\??)"
    r"|(?:¿?me\s+(?:dices|puedes\s+decir)\s+(?:tu\s+nombre|c[oó]mo\s+te\s+llam)\??)"
    r"|(?:what(?:'s|\s+is)\s+your\s+name\??)"
    r"|(?:what\s+(?:should|can)\s+I\s+call\s+you\??)"
    r"|(?:may\s+I\s+(?:ask|know)\s+your\s+name\??)"
    r")",
    re.IGNORECASE,
)


def guard_identity_denial(text: str, anchor: IdentityAnchor) -> str:
    """
    Intercepta respuestas del LLM que niegan conocer o piden la identidad
    del usuario, y las reemplaza con la identidad almacenada.

    Si el anchor no tiene nombre, retorna el texto original sin cambios.

    Args:
        text: Respuesta del LLM (o cualquier texto a verificar).
        anchor: Identidad anclada del usuario.

    Returns:
        Texto corregido con identidad inyectada, o el texto original.
    """
    if not anchor.has_name or not text:
        return text

    name = anchor.name
    modified = text
    was_guarded = False

    # 1. Reemplazar negación de identidad
    if _IDENTITY_DENIAL_PATTERNS.search(modified):
        lang = anchor.language or "es"
        if lang == "es":
            replacement = f"Sé que te llamas {name}."
        else:
            replacement = f"I know your name is {name}."
        modified = _IDENTITY_DENIAL_PATTERNS.sub(replacement, modified)
        was_guarded = True

    # 2. Reemplazar petición de nombre
    if _NAME_REQUEST_PATTERNS.search(modified):
        lang = anchor.language or "es"
        if lang == "es":
            replacement = f"Ya sé que eres {name}."
        else:
            replacement = f"I already know you're {name}."
        modified = _NAME_REQUEST_PATTERNS.sub(replacement, modified)
        was_guarded = True

    if was_guarded:
        # Limpiar espacios múltiples que puedan quedar
        modified = re.sub(r" {2,}", " ", modified).strip()
        logger.info(
            "Identity guard activated | user=%s | name=%s",
            anchor.user_id[:20], name,
        )

    return modified


# ---------------------------------------------------------------------------
# Language Lock — fijar idioma de respuesta
# ---------------------------------------------------------------------------

_LANG_DETECT = re.compile(
    r"[áéíóúñü¡¿]"
    r"|\b(?:el|la|los|las|del|una?|es|son|fue|tiene|est[aá]n?)\b"
    r"|\b(?:qu[eé]|c[oó]mo|qui[eé]n|cu[aá]l|cu[aá]ndo|d[oó]nde|por\s+qu[eé])\b"
    r"|\b(?:hola|buenas?|gracias|por|para|pero|tambi[eé]n|soy|eres|somos)\b",
    re.IGNORECASE,
)

_LANG_CHANGE_PATTERNS = re.compile(
    r"(?:"
    r"(?:resp[oó]ndeme\s+en\s+(?:inglés|english|español|spanish))"
    r"|(?:(?:switch|change)\s+(?:to|language\s+to)\s+(?:english|spanish|español|inglés))"
    r"|(?:(?:habla|háblame)\s+en\s+(?:inglés|english|español|spanish))"
    r"|(?:from\s+now\s+(?:on\s+)?(?:in|respond\s+in)\s+(?:english|spanish))"
    r")",
    re.IGNORECASE,
)

_LANG_EXTRACT = re.compile(
    r"(?:inglés|english)", re.IGNORECASE,
)


def detect_and_lock_language(user_id: str, user_input: str) -> str:
    """
    Detecta el idioma del mensaje actual y actualiza el lock.

    Comportamiento (no forzar idiomas):
      - Cada mensaje con marcadores claros actualiza el idioma.
      - Si el mensaje es ambiguo (sin marcadores), mantiene el último.
      - Cambio explícito ("respóndeme en inglés") siempre se respeta.
      - El idioma sigue al usuario, no se fuerza.

    Returns:
        Código de idioma detectado ("es" o "en").
    """
    # 1. Cambio explícito solicitado por el usuario
    if _LANG_CHANGE_PATTERNS.search(user_input):
        new_lang = "en" if _LANG_EXTRACT.search(user_input) else "es"
        _session.lock_language(user_id, new_lang)
        logger.info(
            "Language CHANGED by user request | user=%s | lang=%s",
            user_id[:20], new_lang,
        )
        return new_lang

    # 2. Detectar idioma del mensaje actual
    es_matches = len(_LANG_DETECT.findall(user_input))
    has_clear_markers = es_matches >= 1 or len(user_input.split()) >= 3

    if es_matches >= 1:
        detected = "es"
    else:
        detected = "en"

    # 3. Si hay marcadores claros → actualizar el lock al idioma detectado
    if has_clear_markers:
        current_lock = _session.get_locked_language(user_id)
        if current_lock != detected:
            logger.info(
                "Language UPDATED per message | user=%s | %s → %s",
                user_id[:20], current_lock or "(none)", detected,
            )
        _session.lock_language(user_id, detected)
        return detected

    # 4. Mensaje ambiguo ("ok", "123", emoji) → mantener el último
    locked = _session.get_locked_language(user_id)
    if locked:
        return locked

    # 5. Sin historial → default a español y guardar
    _session.lock_language(user_id, detected)
    logger.info(
        "Language INITIAL set | user=%s | lang=%s",
        user_id[:20], detected,
    )
    return detected


# ---------------------------------------------------------------------------
# Helpers para inyección de identidad en contexto
# ---------------------------------------------------------------------------

def build_identity_context(anchor: IdentityAnchor) -> str:
    """
    Construye bloque de identidad para inyectar en el prompt del LLM.

    Si el usuario tiene nombre, el bloque incluye una instrucción explícita
    para que el LLM NUNCA pregunte ni niegue conocer el nombre.
    """
    if not anchor.loaded:
        return ""

    parts = []

    if anchor.has_name:
        parts.append(
            f"[IDENTIDAD DEL USUARIO — NO NEGOCIABLE]\n"
            f"El usuario se llama {anchor.name}. "
            f"NUNCA preguntes su nombre. NUNCA digas que no lo conoces. "
            f"Si se refiere a sí mismo, usa su nombre."
        )

    if anchor.language:
        lang_label = "español" if anchor.language == "es" else "inglés"
        parts.append(
            f"[IDIOMA FIJADO]\n"
            f"Responde SIEMPRE en {lang_label}. "
            f"No cambies de idioma salvo que el usuario lo pida explícitamente."
        )

    if anchor.preferences:
        vals = ", ".join(list(anchor.preferences.values())[:5])
        parts.append(f"[Preferencias conocidas] {vals}")

    if anchor.interests:
        vals = ", ".join(anchor.interests[:5])
        parts.append(f"[Intereses conocidos] {vals}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# API de sesión
# ---------------------------------------------------------------------------

def lock_language(user_id: str, lang: str) -> None:
    """Fija el idioma de un usuario manualmente."""
    _session.lock_language(user_id, lang)


def get_locked_language(user_id: str) -> str:
    """Retorna el idioma fijado de un usuario, o cadena vacía."""
    return _session.get_locked_language(user_id)


def invalidate_session(user_id: str) -> None:
    """Invalida el caché de sesión de un usuario."""
    _session.invalidate(user_id)


def clear_all_sessions() -> None:
    """Limpia todo el caché de sesión (testing)."""
    _session.clear_all()


def session_stats() -> Dict[str, Any]:
    """Estadísticas del caché de sesión."""
    return _session.stats()
