"""
Vectrax Language Gate — Control Obligatorio de Idioma
======================================================
Puerta final que FUERZA consistencia de idioma en toda respuesta.

Reglas (no negociables):
  1. Detectar idioma del usuario (último mensaje).
  2. Si existe idioma guardado en identity → prioridad absoluta.
  3. Detectar idioma de la respuesta.
  4. Si no coincide → traducir automáticamente vía LLM local.
  5. Prohibido mezclar idiomas en una misma respuesta.
  6. Si la traducción falla → devolver respuesta original con nota.

Integración:
  Se aplica como ÚLTIMO paso antes de enviar al usuario,
  después de enforce_style y antes del return final.

Privacidad:
  No almacena contenido. Solo procesa texto in-flight.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("vectrax.language_gate")


# ---------------------------------------------------------------------------
# Language detection (heuristic, fast, no external deps)
# ---------------------------------------------------------------------------

# Marcadores fuertes de español
_ES_MARKERS = re.compile(
    r"[áéíóúñü¡¿]"
    r"|\b(?:el|la|los|las|del|una?|unos?|unas?)\b"
    r"|\b(?:qué|cómo|cuándo|dónde|por\s+qué|cuál|quién)\b"
    r"|\b(?:es|son|fue|era|tiene|están|para|por|como|pero|también)\b"
    r"|\b(?:hola|gracias|buenas?|puede|hacer|tiene|desde|hasta|entre)\b",
    re.IGNORECASE,
)

# Marcadores fuertes de inglés
_EN_MARKERS = re.compile(
    r"\b(?:the|is|are|was|were|have|has|been|with|from)\b"
    r"|\b(?:this|that|which|would|could|should|will|can)\b"
    r"|\b(?:hello|thanks|please|because|about|their|there|where)\b"
    r"|\b(?:you|your|they|them|some|more|than|then|just|very)\b",
    re.IGNORECASE,
)

# Umbral: si >30% de las palabras son de un idioma, se considera ese idioma
_LANG_DOMINANCE_RATIO = 0.15


def detect_language(text: str) -> str:
    """
    Detect the dominant language of a text.

    Returns:
        "es" for Spanish, "en" for English, "unknown" if ambiguous.
    """
    if not text or not text.strip():
        return "unknown"

    word_count = max(len(text.split()), 1)
    es_hits = len(_ES_MARKERS.findall(text))
    en_hits = len(_EN_MARKERS.findall(text))

    es_ratio = es_hits / word_count
    en_ratio = en_hits / word_count

    # Si ambos son muy bajos → ambiguo (texto corto, código, números)
    if es_ratio < _LANG_DOMINANCE_RATIO and en_ratio < _LANG_DOMINANCE_RATIO:
        # Fallback: buscar caracteres específicos del español
        if re.search(r"[áéíóúñ¡¿]", text):
            return "es"
        return "unknown"

    if es_ratio > en_ratio:
        return "es"
    if en_ratio > es_ratio:
        return "en"

    return "unknown"


def is_mixed_language(text: str) -> bool:
    """
    Detect if a text contains significant mixing of Spanish and English.

    A text is mixed if both languages have >20% presence.
    """
    if not text or len(text.split()) < 5:
        return False

    word_count = max(len(text.split()), 1)
    es_hits = len(_ES_MARKERS.findall(text))
    en_hits = len(_EN_MARKERS.findall(text))

    es_ratio = es_hits / word_count
    en_ratio = en_hits / word_count

    return es_ratio > 0.15 and en_ratio > 0.15


# ---------------------------------------------------------------------------
# Language enforcement
# ---------------------------------------------------------------------------

def enforce_language(
    response: str,
    user_lang: str,
    user_id: str = "",
) -> str:
    """
    MANDATORY language enforcement on a response.

    Rules (non-negotiable):
      1. If user_lang is set → response MUST be in that language.
      2. If response is in wrong language → translate via local LLM.
      3. If response is mixed → translate via local LLM.
      4. If translation fails → return original (fail-safe).

    Args:
        response: The response text to enforce.
        user_lang: Target language ("es" or "en"). If empty, skip.
        user_id: For logging only.

    Returns:
        Response in the correct language.
    """
    if not response or not response.strip():
        return response

    if not user_lang or user_lang == "unknown":
        return response

    response_lang = detect_language(response)
    mixed = is_mixed_language(response)

    # Already correct and not mixed → passthrough
    if response_lang == user_lang and not mixed:
        return response

    # Language mismatch or mixed → translate
    if response_lang != user_lang and response_lang != "unknown":
        logger.info(
            "Language gate: MISMATCH | user=%s target=%s response=%s → translating",
            user_id[:20] if user_id else "?", user_lang, response_lang,
        )
        translated = _translate(response, user_lang)
        if translated:
            return translated
        # Fail-safe: return original
        logger.warning("Language gate: translation failed, returning original")
        return response

    if mixed:
        logger.info(
            "Language gate: MIXED detected | user=%s target=%s → translating",
            user_id[:20] if user_id else "?", user_lang,
        )
        translated = _translate(response, user_lang)
        if translated:
            return translated
        return response

    # Unknown response language but user_lang is set → trust it
    return response


# ---------------------------------------------------------------------------
# Get user language from anchor or detection
# ---------------------------------------------------------------------------

def get_user_language(user_id: str, user_input: str) -> str:
    """
    Determine the user's language with priority:
      1. Locked language from identity anchor (absolute priority)
      2. Detected from current message
      3. Default: "es"
    """
    # 1. Check identity anchor lock
    try:
        from vectrax.identity_anchor import _session
        locked = _session.get_locked_language(user_id)
        if locked:
            return locked
    except Exception:
        pass

    # 2. Detect from current message
    detected = detect_language(user_input)
    if detected != "unknown":
        return detected

    # 3. Default
    return "es"


# ---------------------------------------------------------------------------
# Translation via local LLM (Ollama)
# ---------------------------------------------------------------------------

def _translate(text: str, target_lang: str) -> str:
    """
    Translate text to target language using local Ollama.

    Fail-safe: returns empty string if translation fails.
    """
    lang_name = "español" if target_lang == "es" else "English"

    prompt = (
        f"Translate the following text to {lang_name}. "
        f"Return ONLY the translated text, nothing else. "
        f"Preserve the meaning, tone, and structure.\n\n"
        f"{text}"
    )

    # Try Ollama (local, fast, free)
    try:
        import httpx
        resp = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3},
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            translated = data.get("response", "").strip()
            if translated and len(translated) > 10:
                # Verify the translation is in the right language
                translated_lang = detect_language(translated)
                if translated_lang == target_lang or translated_lang == "unknown":
                    logger.info(
                        "Language gate: translated %d→%d chars to %s via Ollama",
                        len(text), len(translated), target_lang,
                    )
                    return translated
                else:
                    logger.warning(
                        "Language gate: Ollama translated to wrong lang (%s instead of %s)",
                        translated_lang, target_lang,
                    )
    except Exception as exc:
        logger.debug("Ollama translation failed: %s", exc)

    return ""
