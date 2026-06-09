"""
Tests for Topic Lock — short referential messages stay in active thread.
Run: python -m pytest tests/test_topic_lock.py -v
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Replicate the regex from external_gateway.py
_TOPIC_LOCK_WORDS = re.compile(
    r"^\s*(?:"
    r"no\s+ahora|ahora\s+no|ma[n\u00f1]ana|despu[e\u00e9]s|luego|m[a\u00e1]s\s+tarde"
    r"|dale|perfecto|exacto|entendido|listo|claro|ok(?:e?y|a[y\u00e1])?"
    r"|s[i\u00ed]|no|ya|vale|va|vamos|bien|bueno|todo\s+bien"
    r"|genial|de\s+acuerdo|as[i\u00ed]\s+es|correcto|seguro"
    r"|lo\s+(?:dejamos|vemos|hablamos)(?:\s+.{0,20})?"
    r"|voy\s+a\s+(?:esperar|ver|pensar)"
    r"|let'?s\s+(?:do|wait|see|talk)|sure|fine|alright|got\s+it"
    r"|ok\s+.{0,20}$|y\s+(?:eso|ya)|nada\s+m[a\u00e1]s|eso\s+es\s+todo"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _should_lock(text: str) -> bool:
    words = text.strip().split()
    return len(words) <= 8 and bool(_TOPIC_LOCK_WORDS.match(text.strip()))


# === SHOULD LOCK (short referential messages) ===

class TestShouldLock:
    @pytest.mark.parametrize("text", [
        "no ahora",
        "ahora no",
        "mañana",
        "después",
        "luego",
        "más tarde",
        "dale",
        "perfecto",
        "exacto",
        "entendido",
        "listo",
        "claro",
        "ok",
        "okey",
        "sí",
        "no",
        "ya",
        "vale",
        "vamos",
        "bien",
        "bueno",
        "todo bien",
        "genial",
        "de acuerdo",
        "así es",
        "correcto",
        "seguro",
        "lo dejamos",
        "lo vemos",
        "lo hablamos",
        "voy a esperar",
        "voy a ver",
        "voy a pensar",
        "nada más",
        "eso es todo",
        "y eso",
        "y ya",
        # EN
        "sure",
        "fine",
        "alright",
        "got it",
        "let's wait",
        "lets do",
        "let's see",
        "let's talk",
        # With punctuation
        "dale!",
        "perfecto.",
        "ok!",
        "sí!",
        # Mixed
        "Ok mañana vemos eso",
        "lo vemos mañana",
    ])
    def test_locks(self, text):
        assert _should_lock(text), f"Expected lock for: {text!r}"


# === SHOULD NOT LOCK (real content) ===

class TestShouldNotLock:
    @pytest.mark.parametrize("text", [
        "Qué opinas del mercado",
        "Cómo está el BTC",
        "Despierta Vectrax",
        "Analiza el worker",
        "Necesito que revises el sistema",
        "Hola cómo estás",
        "Explícame qué es una convergencia",
        "Quiero crear un lead nuevo",
        "Háblame del mercado hoy",
        "Cómo va el universo",
        "Dime qué sabes de mí",
        "Precio de AAPL",
        "Revisa los errores del servidor",
        "Qué observas en el sistema",
        # Long messages should not lock even if they contain lock words
        "No ahora porque estoy ocupado con otras cosas del trabajo y necesito terminar primero",
    ])
    def test_does_not_lock(self, text):
        assert not _should_lock(text), f"Expected NO lock for: {text!r}"
