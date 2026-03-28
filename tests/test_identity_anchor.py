"""
Tests — Identity Anchor
==========================
Valida el anclaje estricto de identidad:
  - Session cache: carga, reutilización, invalidación
  - Language lock: detección, fijado, cambio explícito
  - Identity guard: bloqueo de negación y petición de nombre
  - Integración con user_memory y identity_layer

Creado: 2026-03-19
"""

import pytest

from vectrax.identity_anchor import (
    IdentityAnchor,
    get_anchored_identity,
    refresh_anchor,
    guard_identity_denial,
    detect_and_lock_language,
    lock_language,
    get_locked_language,
    build_identity_context,
    invalidate_session,
    clear_all_sessions,
    session_stats,
)
from vectrax.user_memory import (
    store_memory,
    clear_all_memory,
    get_user_profile,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_state():
    """Limpia estado entre tests."""
    clear_all_sessions()
    clear_all_memory()
    yield
    clear_all_sessions()
    clear_all_memory()


USER_ID = "tg:12345"


# ---------------------------------------------------------------------------
# Session Cache
# ---------------------------------------------------------------------------

class TestSessionCache:
    """Tests de caché de identidad por sesión."""

    def test_empty_cache_returns_loaded_anchor(self):
        """Sin datos previos, retorna anchor cargado pero sin nombre."""
        anchor = get_anchored_identity(USER_ID)
        assert anchor.loaded is True
        assert anchor.user_id == USER_ID
        assert anchor.name == ""

    def test_cache_hit_after_first_load(self):
        """Segunda llamada usa el caché, no recarga."""
        a1 = get_anchored_identity(USER_ID)
        a2 = get_anchored_identity(USER_ID)
        assert a1 is a2  # misma instancia del caché

    def test_name_from_memory(self):
        """Si el usuario se presentó, el anchor lo tiene."""
        store_memory(USER_ID, "Me llamo Carlos", "Hola Carlos")
        anchor = get_anchored_identity(USER_ID)
        assert anchor.name == "Carlos"
        assert anchor.has_name is True

    def test_cache_reuse_preserves_name(self):
        """El nombre se mantiene en cache entre mensajes."""
        store_memory(USER_ID, "Me llamo Ana", "Hola Ana")
        a1 = get_anchored_identity(USER_ID)
        assert a1.name == "Ana"
        # Segundo mensaje, sin nombre
        store_memory(USER_ID, "Hola", "Hola")
        a2 = get_anchored_identity(USER_ID)
        assert a2.name == "Ana"

    def test_invalidate_clears_cache(self):
        """Invalidar sesión limpia el caché."""
        store_memory(USER_ID, "Me llamo Pedro", "Hola Pedro")
        get_anchored_identity(USER_ID)
        invalidate_session(USER_ID)
        # La recarga desde memoria sigue teniendo el nombre
        anchor = get_anchored_identity(USER_ID)
        assert anchor.name == "Pedro"

    def test_refresh_reloads_from_memory(self):
        """refresh_anchor fuerza recarga desde memoria."""
        a1 = get_anchored_identity(USER_ID)
        assert a1.name == ""
        # Simulamos que el usuario se presentó
        store_memory(USER_ID, "Soy Miguel", "Hola Miguel")
        # Sin refresh, el cache tiene el viejo
        a2 = get_anchored_identity(USER_ID)
        assert a2.name == ""  # cache viejo
        # Con refresh, se actualiza
        a3 = refresh_anchor(USER_ID)
        assert a3.name == "Miguel"

    def test_empty_user_id_returns_unloaded(self):
        """user_id vacío retorna anchor no cargado."""
        anchor = get_anchored_identity("")
        assert anchor.loaded is False

    def test_stats(self):
        """Estadísticas del caché."""
        get_anchored_identity(USER_ID)
        stats = session_stats()
        assert stats["cached_identities"] >= 1


# ---------------------------------------------------------------------------
# Language Lock
# ---------------------------------------------------------------------------

class TestLanguageLock:
    """Tests de fijado de idioma."""

    def test_first_message_spanish_locks_es(self):
        """Primer mensaje en español fija 'es'."""
        lang = detect_and_lock_language(USER_ID, "Hola, ¿cómo estás?")
        assert lang == "es"

    def test_first_message_english_locks_en(self):
        """Primer mensaje en inglés fija 'en'."""
        lang = detect_and_lock_language(USER_ID, "Hello there")
        assert lang == "en"

    def test_lock_persists_across_messages(self):
        """Una vez fijado, no cambia con mensajes en otro idioma."""
        detect_and_lock_language(USER_ID, "Hola")
        # Segundo mensaje en inglés NO cambia el lock
        lang = detect_and_lock_language(USER_ID, "Hello there")
        assert lang == "es"

    def test_explicit_change_overrides_lock(self):
        """Petición explícita de cambio sí actualiza el lock."""
        detect_and_lock_language(USER_ID, "Hola")
        assert get_locked_language(USER_ID) == "es"
        lang = detect_and_lock_language(USER_ID, "Respóndeme en inglés")
        assert lang == "en"

    def test_explicit_change_to_spanish(self):
        """Cambio explícito a español."""
        detect_and_lock_language(USER_ID, "Hello")
        lang = detect_and_lock_language(USER_ID, "Switch to español")
        assert lang == "es"

    def test_manual_lock(self):
        """lock_language fija manualmente."""
        lock_language(USER_ID, "en")
        assert get_locked_language(USER_ID) == "en"

    def test_lock_applies_to_anchor(self):
        """El language lock se refleja en el anchor."""
        lock_language(USER_ID, "es")
        anchor = get_anchored_identity(USER_ID)
        assert anchor.language == "es"
        assert anchor.language_locked is True

    def test_language_follows_user_per_message(self):
        """Si el usuario cambia de idioma con frase clara, el sistema sigue."""
        detect_and_lock_language(USER_ID, "Hola, ¿cómo estás?")
        assert get_locked_language(USER_ID) == "es"
        # Frase larga en inglés → actualiza a en
        lang = detect_and_lock_language(USER_ID, "Can you help me with something please?")
        assert lang == "en"

    def test_ambiguous_message_keeps_previous_lang(self):
        """Mensaje ambiguo (corto, sin marcadores) mantiene idioma previo."""
        detect_and_lock_language(USER_ID, "Hola amigo")
        assert get_locked_language(USER_ID) == "es"
        # "ok" es ambiguo → mantiene español
        lang = detect_and_lock_language(USER_ID, "ok")
        assert lang == "es"

    def test_no_force_language_on_switch(self):
        """No forzar: si usuario empieza en en y luego escribe en es, sigue."""
        detect_and_lock_language(USER_ID, "Hello, how are you?")
        assert get_locked_language(USER_ID) == "en"
        lang = detect_and_lock_language(USER_ID, "Ahora quiero hablar en español")
        assert lang == "es"


# ---------------------------------------------------------------------------
# Identity Guard
# ---------------------------------------------------------------------------

class TestIdentityGuard:
    """Tests del guardia de identidad contra negación del LLM."""

    def _make_anchor(self, name="Carlos", lang="es"):
        return IdentityAnchor(
            user_id=USER_ID, name=name, language=lang, loaded=True,
        )

    # -- Negación de identidad (español) --

    def test_guard_no_se_tu_nombre(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("No sé tu nombre", anchor)
        assert "Carlos" in result
        assert "No sé tu nombre" not in result

    def test_guard_no_conozco_tu_nombre(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("No conozco tu nombre", anchor)
        assert "Carlos" in result

    def test_guard_no_tengo_tu_nombre(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("No tengo tu nombre", anchor)
        assert "Carlos" in result

    def test_guard_dime_tu_nombre(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("Dime tu nombre", anchor)
        assert "Carlos" in result

    def test_guard_no_me_has_dicho_tu_nombre(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("No me has dicho tu nombre", anchor)
        assert "Carlos" in result

    # -- Negación de identidad (inglés) --

    def test_guard_dont_know_your_name(self):
        anchor = self._make_anchor(lang="en")
        result = guard_identity_denial("I don't know your name", anchor)
        assert "Carlos" in result
        assert "don't know" not in result

    def test_guard_dont_have_your_name(self):
        anchor = self._make_anchor(lang="en")
        result = guard_identity_denial("I don't have your name", anchor)
        assert "Carlos" in result

    def test_guard_tell_me_your_name(self):
        anchor = self._make_anchor(lang="en")
        result = guard_identity_denial("Tell me your name", anchor)
        assert "Carlos" in result

    def test_guard_havent_told_name(self):
        anchor = self._make_anchor(lang="en")
        result = guard_identity_denial("You haven't told me your name", anchor)
        assert "Carlos" in result

    # -- Petición de nombre (confusión de roles) --

    def test_guard_como_te_llamas(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("¿Cómo te llamas?", anchor)
        assert "Carlos" in result

    def test_guard_cual_es_tu_nombre(self):
        anchor = self._make_anchor()
        result = guard_identity_denial("¿Cuál es tu nombre?", anchor)
        assert "Carlos" in result

    def test_guard_whats_your_name(self):
        anchor = self._make_anchor(lang="en")
        result = guard_identity_denial("What's your name?", anchor)
        assert "Carlos" in result

    # -- No se activa sin nombre --

    def test_guard_no_name_passthrough(self):
        """Sin nombre almacenado, el texto pasa intacto."""
        anchor = IdentityAnchor(user_id=USER_ID, loaded=True)
        text = "No sé tu nombre"
        result = guard_identity_denial(text, anchor)
        assert result == text

    # -- Texto limpio no se modifica --

    def test_guard_clean_text_unchanged(self):
        """Texto sin negación de identidad no se modifica."""
        anchor = self._make_anchor()
        text = "El clima hoy es soleado."
        result = guard_identity_denial(text, anchor)
        assert result == text

    # -- Texto mixto --

    def test_guard_partial_replacement(self):
        """Solo la parte de negación se reemplaza."""
        anchor = self._make_anchor()
        text = "Hola. No sé tu nombre. ¿En qué te ayudo?"
        result = guard_identity_denial(text, anchor)
        assert "Carlos" in result
        assert "¿En qué te ayudo?" in result


# ---------------------------------------------------------------------------
# Identity Context Builder
# ---------------------------------------------------------------------------

class TestBuildIdentityContext:
    """Tests de construcción de contexto de identidad."""

    def test_empty_anchor(self):
        anchor = IdentityAnchor()
        assert build_identity_context(anchor) == ""

    def test_with_name(self):
        anchor = IdentityAnchor(
            user_id=USER_ID, name="Mario", language="es", loaded=True,
        )
        ctx = build_identity_context(anchor)
        assert "Mario" in ctx
        assert "NUNCA preguntes su nombre" in ctx
        assert "español" in ctx

    def test_with_english(self):
        anchor = IdentityAnchor(
            user_id=USER_ID, name="John", language="en", loaded=True,
        )
        ctx = build_identity_context(anchor)
        assert "John" in ctx
        assert "inglés" in ctx

    def test_with_preferences(self):
        anchor = IdentityAnchor(
            user_id=USER_ID, name="Ana", language="es",
            preferences={"python": "Python"}, loaded=True,
        )
        ctx = build_identity_context(anchor)
        assert "Python" in ctx

    def test_with_interests(self):
        anchor = IdentityAnchor(
            user_id=USER_ID, name="Ana", language="es",
            interests=["AI", "música"], loaded=True,
        )
        ctx = build_identity_context(anchor)
        assert "AI" in ctx
        assert "música" in ctx


# ---------------------------------------------------------------------------
# Integración: Memory → Anchor
# ---------------------------------------------------------------------------

class TestMemoryAnchorIntegration:
    """Tests de integración entre user_memory y identity_anchor."""

    def test_name_stored_then_anchored(self):
        """Nombre almacenado en memoria se refleja en anchor."""
        store_memory(USER_ID, "Me llamo Roberto", "Hola Roberto")
        anchor = get_anchored_identity(USER_ID)
        assert anchor.name == "Roberto"

    def test_language_lock_respected_in_profile(self):
        """El language lock se respeta al actualizar perfil."""
        lock_language(USER_ID, "en")
        store_memory(USER_ID, "Hola amigo", "Hello")  # Input en español
        profile = get_user_profile(USER_ID)
        # El perfil debe respetar el lock
        assert profile.get("language") == "en"

    def test_anchor_name_persists_no_re_ask(self):
        """Una vez que Vectrax sabe el nombre, nunca vuelve a pedir."""
        store_memory(USER_ID, "Soy Elena", "Hola Elena")
        anchor = get_anchored_identity(USER_ID)
        assert anchor.name == "Elena"

        # Simular 10 mensajes sin mencionar nombre
        for i in range(10):
            store_memory(USER_ID, f"Mensaje {i}", f"Respuesta {i}")

        # El anchor sigue teniendo el nombre
        anchor2 = refresh_anchor(USER_ID)
        assert anchor2.name == "Elena"
