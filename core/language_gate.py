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

# ---------------------------------------------------------------------------
# Multi-language markers
# ---------------------------------------------------------------------------

_LANG_MARKERS: dict = {
    "es": re.compile(
        r"[ñ¡¿ü]"
        r"|\b(?:el|la|los|las|del|una?|unos?|unas?)\b"
        r"|\b(?:qué|cómo|cuándo|dónde|por\s+qué|cuál|quién)\b"
        r"|\b(?:es|son|fue|era|tiene|están|pero|también|hay|ya|muy|algo|nada)\b"
        r"|\b(?:hola|gracias|buenas?|puede|hacer|desde|hasta|entre|sobre|todo|este|esta)\b"
        r"|\b(?:núcleo|sistema|información|respuesta|registro|memoria|activo|estable)\b",
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"\b(?:the|is|are|was|were|have|has|been|with|from)\b"
        r"|\b(?:this|that|which|would|could|should|will|can)\b"
        r"|\b(?:hello|thanks|please|because|about|their|there|where)\b"
        r"|\b(?:you|your|they|them|some|more|than|then|just|very)\b",
        re.IGNORECASE,
    ),
    "fr": re.compile(
        r"[àâçéèêëîïôùûü]"
        r"|\b(?:je|tu|il|elle|nous|vous|ils|elles|le|la|les|de|du|des|un|une)\b"
        r"|\b(?:est|sont|avec|pour|dans|sur|mais|que|qui|pas|très|aussi)\b"
        r"|\b(?:bonjour|merci|oui|non|comment|pourquoi|quand|où|quel|cette)\b"
        r"|\b(?:c'est|j'ai|qu'est|n'est|l'on|d'accord|s'il|parce|bien)\b",
        re.IGNORECASE,
    ),
    "it": re.compile(
        r"[àèéìòù]"
        r"|\b(?:io|tu|lui|lei|noi|voi|loro|il|la|lo|gli|le|di|del|della)\b"
        r"|\b(?:un|una|è|sono|con|per|che|non|come|questo|questa|molto|anche)\b"
        r"|\b(?:ciao|grazie|buongiorno|perché|quando|dove|quale|cosa|bene)\b",
        re.IGNORECASE,
    ),
    "de": re.compile(
        r"[äöüß]"
        r"|\b(?:ich|du|er|sie|wir|ihr|das|die|der|den|dem|ein|eine|ist|sind)\b"
        r"|\b(?:mit|für|und|aber|oder|nicht|von|auf|aus|nach|bei|über)\b"
        r"|\b(?:hallo|danke|bitte|warum|wann|wo|wie|was|wer|guten|ja|nein)\b",
        re.IGNORECASE,
    ),
    "pt": re.compile(
        r"[ãõ]"
        r"|\b(?:eu|ele|ela|nós|vós|eles|elas|você|vocês)\b"
        r"|\b(?:são|não|muito|isso|tudo|ainda|aqui|aquilo|então|agora|já|mais)\b"
        r"|\b(?:olá|obrigad[oa]|bom|boa|quando|onde|qual|sempre|depois|antes)\b",
        re.IGNORECASE,
    ),
    "nl": re.compile(
        r"\b(?:ik|jij|hij|zij|wij|jullie|het|de|een|van|met|voor|dat|niet)\b"
        r"|\b(?:maar|ook|hoe|wat|waar|wanneer|wie|waarom|goed|hoi|bedankt)\b",
        re.IGNORECASE,
    ),
}

_LANG_DOMINANCE_RATIO = 0.15

# Nombre legible de cada idioma
LANG_NAMES: dict = {
    "es": "español", "en": "English", "fr": "français",
    "it": "italiano", "de": "Deutsch", "pt": "português",
    "nl": "Nederlands",
}


# Exclusive markers that disambiguate ES vs PT when scores are close
_ES_EXCLUSIVE = re.compile(
    r"[ñ¡¿]"
    r"|\b(?:hola|gracias|tiene|puede|hacer|desde|hasta|entre|sobre|pero|hay"
    r"|núcleo|están|somos|tenemos|nosotros|ustedes|vosotros)\b",
    re.IGNORECASE,
)
_PT_EXCLUSIVE = re.compile(
    r"[ãõ]"
    r"|\b(?:você|vocês|não|são|isso|tudo|então|olá|obrigad[oa]"
    r"|ainda|aqui|depois|antes|agora|sempre)\b",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    """
    Detect the dominant language of a text.

    Returns ISO code: es, en, fr, it, de, pt, nl, or "unknown".
    """
    if not text or not text.strip():
        return "unknown"

    word_count = max(len(text.split()), 1)
    scores: dict = {}
    for lang, pattern in _LANG_MARKERS.items():
        scores[lang] = len(pattern.findall(text))

    if not scores or max(scores.values()) == 0:
        return "unknown"

    top = max(scores, key=scores.get)
    top_ratio = scores[top] / word_count

    # ── ES/PT disambiguation ─────────────────────────────────────
    # These languages share too many words. When both score high,
    # use exclusive markers to break the tie.
    es_score = scores.get("es", 0)
    pt_score = scores.get("pt", 0)
    if es_score > 0 and pt_score > 0:
        # Both triggered → use exclusive markers
        es_excl = len(_ES_EXCLUSIVE.findall(text))
        pt_excl = len(_PT_EXCLUSIVE.findall(text))
        if es_excl > 0 and pt_excl == 0:
            return "es"
        if pt_excl > 0 and es_excl == 0:
            return "pt"
        # Both have exclusive markers (rare) or neither → prefer ES
        # if scores are within 30% of each other
        if es_excl >= pt_excl and es_score >= pt_score * 0.7:
            return "es"
        if pt_excl > es_excl:
            return "pt"

    # Require minimum signal
    if top_ratio < _LANG_DOMINANCE_RATIO and word_count > 3:
        # Fallback: check for unique characters
        if re.search(r"[ñ¡¿]", text):
            return "es"
        if re.search(r"[ãõ]", text):
            return "pt"
        if re.search(r"[àâçèêëîïôùûü]", text):
            return "fr"
        if re.search(r"[äöüß]", text):
            return "de"
        # Shared accents (áéíóú) — default to ES (primary user language)
        if re.search(r"[áéíóú]", text):
            return "es"
        return "unknown"

    return top


def is_mixed_language(text: str) -> bool:
    """
    Detect if a text contains significant mixing of two or more languages.
    """
    if not text or len(text.split()) < 5:
        return False

    word_count = max(len(text.split()), 1)
    significant = 0
    for lang, pattern in _LANG_MARKERS.items():
        ratio = len(pattern.findall(text)) / word_count
        if ratio > 0.15:
            significant += 1
    return significant >= 2


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
    if (response_lang != user_lang and response_lang != "unknown") or mixed:
        logger.info(
            "Language gate: %s | user=%s target=%s response=%s → translating",
            "MIXED" if mixed else "MISMATCH",
            user_id[:20] if user_id else "?", user_lang, response_lang,
        )
        translated = _translate(response, user_lang)
        if translated:
            return translated
        # Ollama not available on this server — return original silently
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
      2. Saved language from DB (conversational policy)
      3. Detected from current message
      4. Default: "es"
    """
    # 1. Check identity anchor lock
    try:
        from vectrax.identity_anchor import _session
        locked = _session.get_locked_language(user_id)
        if locked:
            return locked
    except Exception:
        pass

    # 2. Check saved language from DB
    try:
        from core.operator.conversational_policy import _get_user_language
        saved = _get_user_language(user_id)
        if saved:
            return saved
    except Exception:
        pass

    # 3. Detect from current message (multi-language)
    detected = detect_language(user_input)
    if detected != "unknown":
        return detected

    # 4. Default
    return "es"


# ---------------------------------------------------------------------------
# Translation via local LLM (Ollama)
# ---------------------------------------------------------------------------

def _translate(text: str, target_lang: str) -> str:
    """
    Translate text to target language using local Ollama.

    Fail-safe: returns empty string if translation fails.
    """
    lang_name = LANG_NAMES.get(target_lang, target_lang)

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
