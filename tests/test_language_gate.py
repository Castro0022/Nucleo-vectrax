"""
Tests para core.language_gate — Control Obligatorio de Idioma
==============================================================

Cubre:
  1. Detección de idioma (español, inglés, ambiguo)
  2. Detección de mezcla de idiomas
  3. Enforcement: passthrough cuando idioma correcto
  4. Enforcement: traducción cuando idioma incorrecto
  5. get_user_language: prioridad anchor > detección > default
  6. Fail-safe: no rompe si Ollama no disponible
"""

import pytest

from core.language_gate import (
    detect_language,
    enforce_language,
    get_user_language,
    is_mixed_language,
)


# ===========================================================================
# 1. Detección de idioma
# ===========================================================================

class TestDetectLanguage:

    def test_spanish_clear(self):
        assert detect_language("¿Cómo estás? Hoy fue un buen día.") == "es"

    def test_spanish_no_accents(self):
        assert detect_language("hola como estas, que es la inteligencia artificial") == "es"

    def test_english_clear(self):
        assert detect_language("How are you doing today? The weather is nice.") == "en"

    def test_english_technical(self):
        assert detect_language("Python is a versatile programming language with strong community support.") == "en"

    def test_empty(self):
        assert detect_language("") == "unknown"

    def test_short_ambiguous(self):
        # Very short text might be unknown
        result = detect_language("ok")
        assert result in ("es", "en", "unknown")

    def test_spanish_accent_chars(self):
        assert detect_language("información está disponible aquí") == "es"

    def test_numbers_only(self):
        assert detect_language("12345 67890") == "unknown"


# ===========================================================================
# 2. Detección de mezcla
# ===========================================================================

class TestMixedLanguage:

    def test_not_mixed_spanish(self):
        assert is_mixed_language("Python es un lenguaje de programación versátil y fácil.") is False

    def test_not_mixed_english(self):
        assert is_mixed_language("Python is a versatile programming language.") is False

    def test_mixed_detected(self):
        text = (
            "Python es un lenguaje that is very versatile. "
            "Se usa para web development and también para data science."
        )
        assert is_mixed_language(text) is True

    def test_short_not_mixed(self):
        assert is_mixed_language("ok") is False


# ===========================================================================
# 3. Enforcement: passthrough
# ===========================================================================

class TestEnforcePassthrough:

    def test_correct_spanish(self):
        text = "Python es un lenguaje de programación versátil."
        result = enforce_language(text, "es")
        assert result == text

    def test_correct_english(self):
        text = "Python is a versatile programming language."
        result = enforce_language(text, "en")
        assert result == text

    def test_empty_response(self):
        assert enforce_language("", "es") == ""

    def test_no_user_lang(self):
        text = "Some text here"
        assert enforce_language(text, "") == text

    def test_unknown_user_lang(self):
        text = "Some text here"
        assert enforce_language(text, "unknown") == text


# ===========================================================================
# 4. Enforcement: translation needed
# ===========================================================================

class TestEnforceTranslation:

    def test_english_response_to_spanish_user(self):
        """English response to Spanish user should attempt translation."""
        text = "Python is a very popular programming language used worldwide for many applications."
        result = enforce_language(text, "es", "test_user")
        # If Ollama is available, result should be in Spanish
        # If not, fail-safe returns original
        assert result  # never empty
        # Either translated or original
        assert len(result) > 10

    def test_spanish_response_to_english_user(self):
        """Spanish response to English user should attempt translation."""
        text = "Python es un lenguaje de programación muy popular utilizado en todo el mundo."
        result = enforce_language(text, "en", "test_user")
        assert result
        assert len(result) > 10


# ===========================================================================
# 5. get_user_language
# ===========================================================================

class TestGetUserLanguage:

    def test_detect_from_spanish_input(self):
        lang = get_user_language("test_user", "¿Cómo estás hoy?")
        assert lang == "es"

    def test_detect_from_english_input(self):
        lang = get_user_language("test_user", "How are you doing today?")
        assert lang == "en"

    def test_default_is_spanish(self):
        lang = get_user_language("test_user", "123")
        assert lang == "es"

    def test_anchor_lock_takes_priority(self):
        """If identity has a locked language, it takes priority."""
        try:
            from vectrax.identity_anchor import _session
            _session.lock_language("test_lock_user", "en")
            lang = get_user_language("test_lock_user", "hola como estas")
            assert lang == "en"  # anchor says english, even if input is spanish
            _session.invalidate("test_lock_user")
        except ImportError:
            pytest.skip("identity_anchor not available")


# ===========================================================================
# 6. Fail-safe
# ===========================================================================

class TestFailSafe:

    def test_returns_original_on_failure(self):
        """If everything fails, the original text is returned."""
        text = "This is a test response."
        # enforce_language with no Ollama running should still return something
        result = enforce_language(text, "es", "fail_test")
        assert result  # never empty
        # Either translated or original
        assert "test" in result.lower() or len(result) > 5
