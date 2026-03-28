"""
Vectrax — Política Conversacional Global
============================================
Reglas estructurales permanentes para toda interacción con usuarios.
Se aplica igual en fast-path, pipeline, memoria, voz, búsquedas y mapas.

Reglas:
  1. Prioridad absoluta del usuario (instrucciones explícitas mandan)
  2. Idioma persistente de sesión (detectar, guardar, mantener)
  3. Respuesta directa, no académica
  4. Contexto inmediato obligatorio
  5. Enfoque operativo
  6. Consistencia global
  7. Escala (miles de usuarios)

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger("vectrax.conversational_policy")


# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = {
    "es": "español",
    "en": "English",
    "fr": "français",
    "it": "italiano",
    "de": "Deutsch",
    "pt": "português",
    "nl": "Nederlands",
    "ca": "català",
    "ro": "română",
}

# ---------------------------------------------------------------------------
# Language detection markers (lightweight, no external deps)
# ---------------------------------------------------------------------------

_LANG_MARKERS: Dict[str, re.Pattern] = {
    "fr": re.compile(
        r"[àâçéèêëîïôùûü]"
        r"|\b(?:je|tu|il|elle|nous|vous|ils|elles|le|la|les|de|du|des|un|une"
        r"|est|sont|avec|pour|dans|sur|mais|que|qui|pas|très|aussi|bonjour"
        r"|merci|oui|non|comment|pourquoi|quand|où|quel|cette|ces|mon|ton"
        r"|c'est|j'ai|qu'est|n'est|l'on|d'accord|s'il|parce)\b",
        re.IGNORECASE,
    ),
    "it": re.compile(
        r"[àèéìòù]"
        r"|\b(?:io|tu|lui|lei|noi|voi|loro|il|la|lo|gli|le|di|del|della"
        r"|un|una|è|sono|con|per|che|non|come|questo|questa|molto|anche"
        r"|ciao|grazie|buongiorno|perché|quando|dove|quale|cosa|bene)\b",
        re.IGNORECASE,
    ),
    "de": re.compile(
        r"[äöüß]"
        r"|\b(?:ich|du|er|sie|wir|ihr|das|die|der|den|dem|ein|eine|ist|sind"
        r"|mit|für|und|aber|oder|nicht|von|auf|aus|nach|bei|über|hallo|danke"
        r"|bitte|warum|wann|wo|wie|was|wer|guten|morgen|abend|ja|nein)\b",
        re.IGNORECASE,
    ),
    "pt": re.compile(
        r"[ãõçáéíóúâêô]"
        r"|\b(?:eu|tu|ele|ela|nós|vós|eles|elas|o|a|os|as|de|do|da|um|uma"
        r"|é|são|com|para|que|não|como|este|esta|muito|também|olá|obrigad[oa]"
        r"|bom|boa|porque|quando|onde|qual|isso|você|tudo|bem)\b",
        re.IGNORECASE,
    ),
    "nl": re.compile(
        r"\b(?:ik|jij|hij|zij|wij|jullie|het|de|een|van|met|voor|dat|niet"
        r"|maar|ook|hoe|wat|waar|wanneer|wie|waarom|goed|hoi|bedankt"
        r"|goedemorgen|alsjeblieft|ja|nee|graag)\b",
        re.IGNORECASE,
    ),
    "es": re.compile(
        r"[áéíóúñü¿¡]"
        r"|\b(?:el|la|los|las|del|una|unos|un|es|fue|son|está|por|para|con"
        r"|como|que|pero|más|sobre|entre|desde|hasta|tiene|puede|también"
        r"|hola|gracias|buenas|buenos|dónde|cómo|cuándo|quién|qué)\b",
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"\b(?:the|is|are|was|were|have|has|had|do|does|did|will|would"
        r"|can|could|should|may|might|this|that|these|those|with|from"
        r"|what|where|when|who|why|how|hello|please|thank|thanks|yes|no)\b",
        re.IGNORECASE,
    ),
}

# ---------------------------------------------------------------------------
# Explicit language instruction detection
# ---------------------------------------------------------------------------

_LANG_INSTRUCTIONS: list[Tuple[re.Pattern, str]] = [
    # Spanish
    (re.compile(r"h[aá]blame\s+(?:solo\s+)?en\s+(\w+)", re.I), None),
    (re.compile(r"responde\s+(?:solo\s+)?en\s+(\w+)", re.I), None),
    (re.compile(r"quiero\s+que\s+(?:me\s+)?hables?\s+en\s+(\w+)", re.I), None),
    # English
    (re.compile(r"speak\s+(?:only\s+)?(?:in\s+)?(\w+)", re.I), None),
    (re.compile(r"respond\s+(?:only\s+)?in\s+(\w+)", re.I), None),
    (re.compile(r"(?:talk|write)\s+(?:to me\s+)?in\s+(\w+)", re.I), None),
    # French
    (re.compile(r"parle[- ]moi\s+(?:uniquement\s+)?en\s+(\w+)", re.I), None),
    (re.compile(r"r[eé]ponds?\s+en\s+(\w+)", re.I), None),
]

_LANG_NAME_MAP: Dict[str, str] = {
    # Español
    "español": "es", "espanol": "es", "castellano": "es", "spanish": "es",
    # Inglés
    "inglés": "en", "ingles": "en", "english": "en", "anglais": "en",
    # Francés
    "francés": "fr", "frances": "fr", "french": "fr", "français": "fr", "francais": "fr",
    # Italiano
    "italiano": "it", "italian": "it", "italien": "it",
    # Alemán
    "alemán": "de", "aleman": "de", "german": "de", "deutsch": "de", "allemand": "de",
    # Portugués
    "portugués": "pt", "portugues": "pt", "portuguese": "pt", "portugais": "pt",
    # Holandés
    "holandés": "nl", "holandes": "nl", "dutch": "nl", "néerlandais": "nl",
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """
    Detecta el idioma dominante de un texto.
    Retorna código ISO (es, en, fr, it, de, pt, nl).
    """
    if not text or not text.strip():
        return "es"

    scores: Dict[str, int] = {}
    for lang, pattern in _LANG_MARKERS.items():
        matches = pattern.findall(text)
        scores[lang] = len(matches)

    if not scores or max(scores.values()) == 0:
        return "en"  # default

    # Top language by marker count
    top = max(scores, key=scores.get)

    # Require minimum signal strength
    word_count = max(len(text.split()), 1)
    if scores[top] / word_count < 0.1 and word_count > 3:
        return "en"  # too weak, default to English

    return top


def detect_explicit_language_instruction(text: str) -> Optional[str]:
    """
    Detecta si el usuario da una instrucción explícita de idioma.
    Ej: "háblame en francés" → "fr"
    Retorna código ISO o None.
    """
    for pattern, _ in _LANG_INSTRUCTIONS:
        m = pattern.search(text)
        if m:
            lang_name = m.group(1).lower().strip()
            code = _LANG_NAME_MAP.get(lang_name)
            if code:
                return code
    return None


def apply_language_policy(
    user_id: str,
    text: str,
) -> str:
    """
    Aplica la política de idioma para un usuario:
      1. ¿Hay instrucción explícita? → guardar y usar
      2. ¿Ya tiene idioma guardado? → mantener
      3. Primer mensaje → detectar y guardar

    Retorna el código de idioma a usar.
    """
    # 1. Instrucción explícita tiene prioridad absoluta
    explicit = detect_explicit_language_instruction(text)
    if explicit:
        _save_user_language(user_id, explicit)
        logger.info("Language set by instruction: %s → %s", user_id[:20], explicit)
        return explicit

    # 2. Idioma ya guardado → mantener estable
    saved = _get_user_language(user_id)
    if saved:
        return saved

    # 3. Primer mensaje → detectar y guardar
    detected = detect_language(text)
    _save_user_language(user_id, detected)
    logger.info("Language detected: %s → %s", user_id[:20], detected)
    return detected


# ---------------------------------------------------------------------------
# Persistence (SQLite via user_memory)
# ---------------------------------------------------------------------------

def _save_user_language(user_id: str, lang: str) -> None:
    """Persiste el idioma del usuario en SQLite."""
    try:
        import sqlite3
        import os
        import time
        db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "vault", "user_memory.db",
        )
        conn = sqlite3.connect(db, timeout=3)
        # Update existing profile or create minimal one
        existing = conn.execute(
            "SELECT user_id FROM profiles WHERE user_id = ?", (user_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE profiles SET language = ?, updated_at = ? WHERE user_id = ?",
                (lang, time.time(), user_id),
            )
        else:
            conn.execute(
                "INSERT INTO profiles (user_id, name, language, preferences, interests, updated_at) "
                "VALUES (?, '', ?, '{}', '[]', ?)",
                (user_id, lang, time.time()),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Save language failed: %s", exc)


def _get_user_language(user_id: str) -> str:
    """Obtiene el idioma guardado del usuario. Retorna '' si no hay."""
    try:
        import sqlite3
        import os
        db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "vault", "user_memory.db",
        )
        conn = sqlite3.connect(db, timeout=3)
        row = conn.execute(
            "SELECT language FROM profiles WHERE user_id = ?", (user_id,),
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# System prompt injection
# ---------------------------------------------------------------------------

def build_language_context(user_id: str, text: str) -> str:
    """
    Genera la línea de contexto de idioma para inyectar en el prompt del LLM.
    Aplica la política de idioma y retorna la instrucción.
    """
    lang = apply_language_policy(user_id, text)
    lang_name = SUPPORTED_LANGUAGES.get(lang, lang)

    return (
        f"[POLÍTICA DE IDIOMA — OBLIGATORIA]\n"
        f"El idioma del usuario es: {lang_name} ({lang}).\n"
        f"TODA tu respuesta DEBE estar en {lang_name}.\n"
        f"No mezcles idiomas. No respondas en otro idioma.\n"
        f"Si el usuario cambia de idioma explícitamente, obedece."
    )
