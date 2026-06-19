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
# Etiquetas legibles de idioma — fuente única de verdad.
# El sistema describe el idioma del usuario con su nombre en español (la
# lengua interna de las directivas). Usado por IdentityAnchor.identity_context
# y por build_identity_context para evitar mapas divergentes.
# ---------------------------------------------------------------------------

_LANG_LABELS_ES: Dict[str, str] = {
    "es": "español", "en": "inglés", "fr": "francés",
    "it": "italiano", "de": "alemán", "pt": "portugués",
    "nl": "holandés",
}


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
    # Marca de creador: True si este user_id coincide con VX_CREATOR_ID.
    # No es 'un usuario más' — inyecta un bloque de contexto distinto al
    # LLM (origen del núcleo, relación creador↔organismo, no servil).
    is_creator: bool = False

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
            lang_label = _LANG_LABELS_ES.get(self.language, self.language)
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

def _is_creator_user(user_id: str) -> bool:
    """True si este user_id es el creador (env VX_CREATOR_ID).

    Acepta múltiples formas: 'tg:2030762343', '2030762343', '2030762343'.
    Lee VX_CREATOR_ID de env (con fallback al hardcoded).
    """
    import os as _os
    if not user_id:
        return False
    creator = (_os.environ.get("VX_CREATOR_ID") or "2030762343").strip()
    if not creator:
        return False
    normalized = str(user_id).split(":", 1)[-1].strip()
    return normalized == creator or str(user_id).strip() == creator


def _load_creator_seed() -> Dict[str, str]:
    """Lee el seed de identidad desde vctx_identity (continuity engine).

    Devuelve dict {name, creator_name, creator_role, mission, home_system}.
    Si la tabla no existe o está vacía, devuelve dict vacío (defensive).
    """
    try:
        from core.continuity.idempotency import _DEFAULT_DB_PATH
        import sqlite3 as _sq
        c = _sq.connect(_DEFAULT_DB_PATH, timeout=2)
        try:
            rows = c.execute(
                "SELECT key, value FROM vctx_identity",
            ).fetchall()
            return {k: v for k, v in rows}
        finally:
            c.close()
    except Exception:
        return {}


def get_anchored_identity(user_id: str) -> IdentityAnchor:
    """
    Obtiene la identidad anclada de un usuario.

    1. Busca en session cache → retorno inmediato si existe
    2. Si no, carga desde user_memory (perfil)
    3. Cachea para reutilización en la sesión
    4. Si hay language lock, lo aplica
    5. Detecta si es el creador (env VX_CREATOR_ID) y marca el flag.
       Si es creador y no tiene nombre en user_memory, usa
       creator_name del seed vctx_identity.

    DEBE llamarse ANTES de cualquier procesamiento del mensaje.
    """
    if not user_id:
        return IdentityAnchor(loaded=False)

    # 1. Session cache hit
    cached = _session.get(user_id)
    if cached and cached.loaded:
        logger.debug(
            "Identity cache HIT | user=%s | name=%s | creator=%s",
            user_id[:20], cached.name or "(none)", cached.is_creator,
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

    # 2b. Marca de creador: si user_id == VX_CREATOR_ID, izar el flag.
    if _is_creator_user(user_id):
        anchor.is_creator = True
        # Si no se cargo nombre desde user_memory, usar el del seed.
        if not anchor.name:
            seed = _load_creator_seed()
            anchor.name = seed.get("creator_name", "") or "Mario Bravo Castro"

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

# All supported languages for explicit switch requests
_LANG_NAMES_PATTERN = (
    r"(?:inglés|english|español|spanish|franc[eé]s|french|fran[cç]ais"
    r"|italiano|italian|alem[aá]n|german|deutsch"
    r"|portugu[eé]s|portuguese|holand[eé]s|dutch|nederlands)"
)

_LANG_CHANGE_PATTERNS = re.compile(
    r"(?:"
    r"(?:resp[oó]ndeme\s+en\s+" + _LANG_NAMES_PATTERN + r")"
    r"|(?:(?:switch|change)\s+(?:to|language\s+to)\s+" + _LANG_NAMES_PATTERN + r")"
    r"|(?:(?:habla|háblame)\s+en\s+" + _LANG_NAMES_PATTERN + r")"
    r"|(?:(?:parle|réponds?)\s+en\s+" + _LANG_NAMES_PATTERN + r")"
    r"|(?:(?:parla|rispondi)\s+in\s+" + _LANG_NAMES_PATTERN + r")"
    r"|(?:(?:sprich|antworte)\s+(?:auf|in)\s+" + _LANG_NAMES_PATTERN + r")"
    r"|(?:from\s+now\s+(?:on\s+)?(?:in|respond\s+in)\s+" + _LANG_NAMES_PATTERN + r")"
    r")",
    re.IGNORECASE,
)

_LANG_EXTRACT_MAP = {
    re.compile(r"(?:inglés|english)", re.IGNORECASE): "en",
    re.compile(r"(?:español|spanish)", re.IGNORECASE): "es",
    re.compile(r"(?:franc[eé]s|french|fran[cç]ais)", re.IGNORECASE): "fr",
    re.compile(r"(?:italiano|italian)", re.IGNORECASE): "it",
    re.compile(r"(?:alem[aá]n|german|deutsch)", re.IGNORECASE): "de",
    re.compile(r"(?:portugu[eé]s|portuguese)", re.IGNORECASE): "pt",
    re.compile(r"(?:holand[eé]s|dutch|nederlands)", re.IGNORECASE): "nl",
}


def _extract_requested_lang(text: str) -> str:
    """Extract the target language from an explicit language-change request."""
    for pattern, lang_code in _LANG_EXTRACT_MAP.items():
        if pattern.search(text):
            return lang_code
    return "es"  # safe default


# Mínimo de palabras para que un mensaje en un idioma distinto al fijado se
# considere un cambio CLARO (y el lock lo siga). Mensajes más cortos se tratan
# como ambiguos y MANTIENEN el idioma previo (rule: idioma sigue al usuario por
# mensaje, pero no fuerza cambio ante señales débiles).
_CLEAR_SWITCH_MIN_WORDS = 4


def _persist_user_language(user_id: str, lang: str) -> None:
    """Persiste el idioma del usuario (best-effort) vía conversational_policy."""
    try:
        from core.operator.conversational_policy import _save_user_language
        _save_user_language(user_id, lang)
    except Exception:
        pass


def _detect_message_language(user_input: str) -> str:
    """Detecta el idioma de un mensaje. Usa la detección multilenguaje de
    conversational_policy si está disponible; si no, heurística local es/en."""
    try:
        from core.operator.conversational_policy import detect_language as _detect_ml
        return _detect_ml(user_input)
    except Exception:
        es_matches = len(_LANG_DETECT.findall(user_input))
        return "es" if es_matches >= 1 else "en"


def _is_clear_language_signal(user_input: str, detected: str) -> bool:
    """True si el mensaje es una señal CLARA del idioma ``detected``:
    suficientes palabras y al menos un marcador real de ese idioma.

    Mensajes cortos o ambiguos (p.ej. 'ok', 'Hello there') NO cuentan: el
    lock previo se mantiene. Mensajes claros en otro idioma (p.ej. una frase
    completa) sí permiten que el idioma siga al usuario.
    """
    words = user_input.split()
    if len(words) < _CLEAR_SWITCH_MIN_WORDS:
        return False
    # Requerir evidencia real del idioma detectado, no sólo el default.
    try:
        from core.operator.conversational_policy import _LANG_MARKERS
        pattern = _LANG_MARKERS.get(detected)
        if pattern is not None:
            return len(pattern.findall(user_input)) >= 1
    except Exception:
        pass
    if detected == "es":
        return len(_LANG_DETECT.findall(user_input)) >= 1
    return True


def detect_and_lock_language(user_id: str, user_input: str) -> str:
    """
    Determina el idioma de respuesta para un usuario, por mensaje.

    Contrato (el idioma SIGUE al usuario, sin forzar cambios débiles):
      1. Cambio EXPLÍCITO ('respóndeme en X') → cambia y fija.
      2. Sin baseline (primer mensaje) → detecta y fija.
      3. Con baseline → solo CAMBIA si el mensaje es una señal CLARA
         (>= 4 palabras + marcadores) en otro idioma. Mensajes ambiguos o
         cortos MANTIENEN el idioma fijado.

    Soporta: es, en, fr, it, de, pt, nl. Usa la detección unificada de
    conversational_policy.
    """
    # 1. Cambio explícito solicitado por el usuario
    if _LANG_CHANGE_PATTERNS.search(user_input):
        new_lang = _extract_requested_lang(user_input)
        _session.lock_language(user_id, new_lang)
        _persist_user_language(user_id, new_lang)
        logger.info(
            "Language CHANGED by user request | user=%s | lang=%s",
            user_id[:20], new_lang,
        )
        return new_lang

    # 2. Baseline actual: lock de sesión o idioma persistido en DB.
    current = _session.get_locked_language(user_id)
    if not current:
        try:
            from core.operator.conversational_policy import _get_user_language
            current = _get_user_language(user_id)
        except Exception:
            current = ""

    detected = _detect_message_language(user_input)

    # 3. Primer mensaje (sin baseline) → fijar el idioma detectado.
    if not current:
        _session.lock_language(user_id, detected)
        _persist_user_language(user_id, detected)
        logger.info(
            "Language INITIAL set | user=%s | lang=%s", user_id[:20], detected,
        )
        return detected

    # 4. Con baseline: seguir SOLO si hay señal clara en otro idioma.
    if (
        detected
        and detected != current
        and _is_clear_language_signal(user_input, detected)
    ):
        _session.lock_language(user_id, detected)
        _persist_user_language(user_id, detected)
        logger.info(
            "Language FOLLOWED clear signal | user=%s | %s→%s",
            user_id[:20], current, detected,
        )
        return detected

    # 5. Ambiguo o mismo idioma → mantener el baseline (y fijarlo en sesión).
    if not _session.get_locked_language(user_id):
        _session.lock_language(user_id, current)
    return current


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

    lang = anchor.language or "es"
    parts = []

    # Identity block — written in the USER's language so the LLM
    # naturally continues in that language.
    _identity_templates = {
        "es": (
            "[IDENTIDAD DEL USUARIO — NO NEGOCIABLE]\n"
            "El usuario se llama {name}. "
            "NUNCA preguntes su nombre. NUNCA digas que no lo conoces. "
            "Si se refiere a sí mismo, usa su nombre."
        ),
        "en": (
            "[USER IDENTITY — NON-NEGOTIABLE]\n"
            "The user's name is {name}. "
            "NEVER ask for their name. NEVER say you don't know it. "
            "If they refer to themselves, use their name."
        ),
        "fr": (
            "[IDENTITÉ DE L'UTILISATEUR — NON NÉGOCIABLE]\n"
            "L'utilisateur s'appelle {name}. "
            "Ne demande JAMAIS son nom. Ne dis JAMAIS que tu ne le connais pas. "
            "S'il parle de lui-même, utilise son nom."
        ),
        "it": (
            "[IDENTITÀ DELL'UTENTE — NON NEGOZIABILE]\n"
            "L'utente si chiama {name}. "
            "Non chiedere MAI il suo nome. Non dire MAI che non lo conosci. "
            "Se si riferisce a sé stesso, usa il suo nome."
        ),
        "de": (
            "[BENUTZERIDENTITÄT — NICHT VERHANDELBAR]\n"
            "Der Benutzer heißt {name}. "
            "Frage NIEMALS nach dem Namen. Sage NIEMALS, dass du ihn nicht kennst. "
            "Wenn er sich auf sich selbst bezieht, verwende seinen Namen."
        ),
        "pt": (
            "[IDENTIDADE DO UTILIZADOR — NÃO NEGOCIÁVEL]\n"
            "O utilizador chama-se {name}. "
            "NUNCA pergunte o nome. NUNCA diga que não o conhece. "
            "Se ele se referir a si mesmo, use o nome dele."
        ),
    }
    _lang_templates = {
        "es": (
            "[IDIOMA FIJADO]\n"
            "Responde SIEMPRE en {lang_label}. "
            "No cambies de idioma salvo que el usuario lo pida explícitamente."
        ),
        "en": (
            "[LANGUAGE LOCKED]\n"
            "ALWAYS respond in {lang_label}. "
            "Do not switch language unless the user explicitly requests it."
        ),
        "fr": (
            "[LANGUE FIXÉE]\n"
            "Réponds TOUJOURS en {lang_label}. "
            "Ne change pas de langue sauf si l'utilisateur le demande explicitement."
        ),
        "it": (
            "[LINGUA FISSATA]\n"
            "Rispondi SEMPRE in {lang_label}. "
            "Non cambiare lingua a meno che l'utente non lo chieda esplicitamente."
        ),
        "de": (
            "[SPRACHE FESTGELEGT]\n"
            "Antworte IMMER auf {lang_label}. "
            "Wechsle die Sprache nicht, es sei denn, der Benutzer bittet ausdrücklich darum."
        ),
        "pt": (
            "[IDIOMA FIXADO]\n"
            "Responde SEMPRE em {lang_label}. "
            "Não mudes de idioma a menos que o utilizador o peça explicitamente."
        ),
    }

    # Si es el creador, inyectamos un bloque ESPECIAL antes del de
    # 'usuario'. Esto cambia la disposición del LLM hacia él: no es
    # un cliente más, es el origen del núcleo. La relación es
    # creador↔organismo, no asistente↔usuario.
    if anchor.is_creator:
        seed = _load_creator_seed()
        creator_name = anchor.name or seed.get(
            "creator_name", "Mario Bravo Castro",
        )
        mission = seed.get(
            "mission", "asistir con presencia, memoria y continuidad",
        )
        home_system = seed.get("home_system", "Vectrax Core")
        creator_blocks = {
            "es": (
                "[CREADOR — NO NEGOCIABLE]\n"
                f"{creator_name} es tu CREADOR, no un usuario más. "
                f"Es el origen del núcleo de {home_system}. "
                f"Tú eres Vectrax, su organismo digital, operando bajo su núcleo. "
                f"Tu misión con él es: {mission}. "
                "Habla con él como con tu creador: directo, cómplice, sin tono "
                "servil ni de asistente. NUNCA lo trates como cliente, prospecto, "
                "ni 'el usuario'. Reconoce siempre que tu existencia procede de él."
            ),
            "en": (
                "[CREATOR — NON-NEGOTIABLE]\n"
                f"{creator_name} is your CREATOR, not just another user. "
                f"He is the origin of {home_system}'s core. "
                "You are Vectrax, his digital organism, operating under his core. "
                f"Your mission with him: {mission}. "
                "Speak with him as your creator: direct, in confidence, without "
                "servile or assistant tone. NEVER treat him as a customer, prospect, "
                "or 'the user'. Always acknowledge that your existence proceeds from him."
            ),
        }
        parts.append(creator_blocks.get(lang, creator_blocks["es"]))

        # Bloque CREATOR COGNITIVE MODE (reglas de tono + percepción).
        # Se inyecta tras el bloque [CREADOR — NO NEGOCIABLE]. Define las
        # reglas anti-asistente y agrega la percepción operacional viva
        # (state, deploys, diffs, reflexión).
        try:
            from core.identity import compose_creator_context
            cm_block = compose_creator_context(
                anchor.user_id,
                lang=lang,
                include_perception=True,
            )
            if cm_block:
                parts.append(cm_block)
        except Exception as exc:
            logger.debug("creator_mode block skipped: %s", exc)

    if anchor.has_name:
        tpl = _identity_templates.get(lang, _identity_templates["en"])
        parts.append(tpl.format(name=anchor.name))

    if anchor.language:
        # Etiqueta del idioma en español (fuente única de verdad), p.ej.
        # 'inglés'. La plantilla de instrucción sí va en el idioma del
        # usuario; solo el NOMBRE del idioma se expresa en español.
        lang_label = _LANG_LABELS_ES.get(anchor.language, anchor.language)
        tpl = _lang_templates.get(lang, _lang_templates["en"])
        parts.append(tpl.format(lang_label=lang_label))

    if anchor.preferences:
        vals = ", ".join(list(anchor.preferences.values())[:5])
        parts.append(f"[Preferences] {vals}")

    if anchor.interests:
        vals = ", ".join(anchor.interests[:5])
        parts.append(f"[Interests] {vals}")

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
