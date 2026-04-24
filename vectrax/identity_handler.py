"""
Vectrax Identity Handler — Unified Detection & Response
========================================================
SINGLE source of truth for detecting Vectrax identity questions
and returning the product identity blurb.

This module enforces the "one failure, one engine" principle by centralizing
the identity detection regex and response logic. No other module should call
get_product_identity() directly or contain identity detection patterns.

Implementing: Class G of motor_recovery_spec_v1.md
Pre-commit static scan + runtime behavior centralization.

Creador: Mario Bravo Castro
Fecha: 2026-04-23
"""

from __future__ import annotations

import re
from typing import Optional

# The TIGHTENED regex pattern — passed 32/32 test suite.
# This is the SINGLE source of truth for identity question detection.
# Do NOT modify without re-running the test suite.
_IDENTITY_PATTERN = re.compile(
    r"(?:c[oó]mo te llamas|cu[áa]l es tu nombre|who are you"
    r"|what(?:'?s| is) your name|qui[eé]n eres"
    r"|c[oó]mo te digo\b|como te digo\b|dime tu nombre|tell me your name"
    r"|qu[eé] eres(?:\s+t[úu])?\b|qu[eé] es vectrax\b|qu[eé] hace vectrax\b"
    r"|qu[eé] puedes hacer\b|qu[eé] ofrece vectrax\b"
    r"|para qu[eé] sirves\b|c[oó]mo funcionas\b|c[oó]mo funciona vectrax\b"
    r"|what is vectrax|what are you|q eres\b"
    r"|cu[eé]ntame (?:sobre|de|acerca(?: de)?) vectrax"
    r"|h[aá]blame (?:sobre|de) vectrax"
    r"|para qu[eé] sirve vectrax"
    r"|describe vectrax|descr[ií]bete"
    r"|cosa sei|was bist du|qui es[- ]tu|wat ben je)",
    re.IGNORECASE,
)


def respond_if_identity(text: str, lang: str = "es") -> Optional[str]:
    """
    Returns the product identity blurb if `text` is an identity question,
    else None.

    This is the SINGLE source of truth for Vectrax identity detection.
    Any other code wanting to detect identity questions MUST import and
    call this function. NO exceptions.

    Args:
        text: Raw user message to check.
        lang: Language code ("es", "en", "fr", etc.). If not provided,
              auto-detects from text.

    Returns:
        String: The product identity response if text matches the identity pattern.
        None: If text does not match any identity question pattern.
    """
    if not text:
        return None

    # Normalize: strip and lowercase for matching
    normalized = text.strip()

    # Test against the tightened identity pattern
    if not _IDENTITY_PATTERN.search(normalized):
        return None

    # Pattern matched. Detect language if not provided.
    if lang == "es" or lang is None:
        lang = _detect_lang_internal(text)

    # Retrieve and return the product identity
    try:
        from vectrax.core_identity import get_product_identity

        return get_product_identity(lang)
    except Exception:
        # Fallback to hardcoded Spanish identity if import fails
        return "Vectrax es tu memoria inteligente. Recuerda todo lo que le dices y te ayuda a decidir mejor con el tiempo."


def _detect_lang_internal(text: str) -> str:
    """
    Internal language detection using vectrax.resolver._detect_lang.
    Falls back to "es" if import/execution fails.

    Args:
        text: User message to detect language from.

    Returns:
        Language code string ("es", "en", etc.). Defaults to "es".
    """
    try:
        from vectrax.resolver import _detect_lang

        return _detect_lang(text)
    except Exception:
        return "es"
