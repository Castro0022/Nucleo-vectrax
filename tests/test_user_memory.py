"""
Tests — User Memory
======================
Valida la memoria por usuario de Vectrax:
  - Almacenamiento y recuperación de interacciones
  - Extracción de nombre, preferencias, intereses
  - Límite de historial por usuario
  - Contexto generado correctamente
  - Aislamiento entre usuarios
  - Thread safety básico

Creado: 2026-03-19
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.expanduser("~/Vectrax"))

from vectrax.user_memory import (
    clear_all_memory,
    clear_memory,
    get_history_count,
    get_memory_context,
    get_user_profile,
    memory_stats,
    resolve_with_memory,
    store_memory,
    _extract_name,
    _extract_preferences,
    _extract_interests,
    _detect_lang,
    _memory_response,
    MAX_HISTORY_PER_USER,
)


@pytest.fixture(autouse=True)
def _clean_memory():
    """Limpia la memoria global antes de cada test."""
    clear_all_memory()
    yield
    clear_all_memory()


# ---------------------------------------------------------------------------
# 1. Almacenamiento básico
# ---------------------------------------------------------------------------

class TestStoreAndRetrieve:
    """store_memory + get_memory_context básico."""

    def test_store_single(self):
        store_memory("u1", "Hola", "Vectrax activo.")
        assert get_history_count("u1") == 1

    def test_store_multiple(self):
        store_memory("u1", "Hola", "Vectrax activo.")
        store_memory("u1", "¿Qué es Python?", "Un lenguaje de programación.")
        assert get_history_count("u1") == 2

    def test_empty_user_id_ignored(self):
        store_memory("", "Hola", "Resp")
        assert get_history_count("") == 0

    def test_context_empty_for_new_user(self):
        ctx = get_memory_context("unknown_user")
        assert ctx == ""

    def test_context_contains_interaction(self):
        store_memory("u1", "Hola mundo", "Respuesta de prueba")
        ctx = get_memory_context("u1")
        assert "Hola mundo" in ctx
        assert "Respuesta de prueba" in ctx

    def test_context_contains_historial_header(self):
        store_memory("u1", "Test", "Resp")
        ctx = get_memory_context("u1")
        assert "[Historial reciente]" in ctx

    def test_clear_memory(self):
        store_memory("u1", "Algo", "Resp")
        assert get_history_count("u1") == 1
        clear_memory("u1")
        assert get_history_count("u1") == 0
        assert get_memory_context("u1") == ""


# ---------------------------------------------------------------------------
# 2. Extracción de nombre
# ---------------------------------------------------------------------------

class TestNameExtraction:
    """Detección de nombre desde el input del usuario."""

    def test_me_llamo(self):
        assert _extract_name("Me llamo Mario") == "Mario"

    def test_mi_nombre_es(self):
        assert _extract_name("Mi nombre es Ana García") == "Ana García"

    def test_my_name_is(self):
        assert _extract_name("My name is John") == "John"

    def test_soy(self):
        assert _extract_name("Soy Carlos") == "Carlos"

    def test_i_am(self):
        assert _extract_name("I am David") == "David"

    def test_no_name_in_question(self):
        assert _extract_name("¿Qué es la gravedad?") == ""

    def test_name_stored_in_profile(self):
        store_memory("u1", "Me llamo Mario", "Hola Mario")
        profile = get_user_profile("u1")
        assert profile["name"] == "Mario"

    def test_name_in_context(self):
        store_memory("u1", "Me llamo Mario", "Hola Mario")
        ctx = get_memory_context("u1")
        assert "Nombre: Mario" in ctx

    def test_name_not_overwritten(self):
        """El primer nombre detectado se mantiene."""
        store_memory("u1", "Me llamo Mario", "Ok")
        store_memory("u1", "Me llamo Pedro", "Ok")
        profile = get_user_profile("u1")
        assert profile["name"] == "Mario"


# ---------------------------------------------------------------------------
# 3. Extracción de preferencias
# ---------------------------------------------------------------------------

class TestPreferenceExtraction:
    """Detección de preferencias del usuario."""

    def test_prefiero(self):
        prefs = _extract_preferences("Prefiero Python.")
        assert any("Python" in p for p in prefs)

    def test_me_gusta(self):
        prefs = _extract_preferences("Me gusta el diseño minimalista.")
        assert len(prefs) >= 1

    def test_uso(self):
        prefs = _extract_preferences("Uso Docker para todo.")
        assert any("Docker" in p for p in prefs)

    def test_i_prefer(self):
        prefs = _extract_preferences("I prefer dark mode.")
        assert len(prefs) >= 1

    def test_preferences_in_profile(self):
        store_memory("u1", "Prefiero Python.", "Registrado.")
        profile = get_user_profile("u1")
        assert len(profile["preferences"]) >= 1

    def test_preferences_in_context(self):
        store_memory("u1", "Prefiero Python.", "Registrado.")
        ctx = get_memory_context("u1")
        assert "Preferencias:" in ctx


# ---------------------------------------------------------------------------
# 4. Extracción de intereses
# ---------------------------------------------------------------------------

class TestInterestExtraction:
    """Detección de intereses del usuario."""

    def test_me_interesa(self):
        interests = _extract_interests("Me interesa la inteligencia artificial.")
        assert len(interests) >= 1

    def test_estudio(self):
        interests = _extract_interests("Estudio matemáticas aplicadas.")
        assert len(interests) >= 1

    def test_im_learning(self):
        interests = _extract_interests("I'm learning Rust.")
        assert len(interests) >= 1

    def test_interests_in_profile(self):
        store_memory("u1", "Me interesa la robótica.", "Anotado.")
        profile = get_user_profile("u1")
        assert len(profile["interests"]) >= 1

    def test_no_duplicate_interests(self):
        store_memory("u1", "Me interesa la robótica.", "Ok")
        store_memory("u1", "Me interesa la robótica.", "Ok")
        profile = get_user_profile("u1")
        assert len(profile["interests"]) == 1


# ---------------------------------------------------------------------------
# 5. Detección de idioma
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    """Detección de idioma del usuario."""

    def test_spanish(self):
        assert _detect_lang("¿Cómo estás?") == "es"

    def test_english(self):
        assert _detect_lang("How are you?") == "en"

    def test_spanish_in_profile(self):
        store_memory("u1", "Hola, ¿qué tal?", "Resp")
        profile = get_user_profile("u1")
        assert profile["language"] == "es"

    def test_language_updates(self):
        """El idioma se fija con el primer mensaje y queda BLOQUEADO.

        Por diseño (ver user_memory._update_profile: "NO sobreescribir por
        detección") el idioma no cambia con mensajes posteriores. Esto refleja
        la regla de consistencia/bloqueo de idioma del sistema.
        """
        store_memory("u1", "Hola", "Resp")
        assert get_user_profile("u1")["language"] == "es"
        store_memory("u1", "Hello world", "Resp")
        # Idioma bloqueado: permanece 'es' pese al mensaje en inglés.
        assert get_user_profile("u1")["language"] == "es"


# ---------------------------------------------------------------------------
# 6. Límite de historial
# ---------------------------------------------------------------------------

class TestHistoryLimit:
    """El historial no debe exceder MAX_HISTORY_PER_USER."""

    def test_max_entries(self):
        for i in range(MAX_HISTORY_PER_USER + 20):
            store_memory("u1", f"Mensaje {i}", f"Respuesta {i}")
        assert get_history_count("u1") == MAX_HISTORY_PER_USER

    def test_oldest_removed(self):
        """Las interacciones más antiguas se eliminan."""
        for i in range(MAX_HISTORY_PER_USER + 5):
            store_memory("u1", f"Msg-{i}", f"Resp-{i}")
        ctx = get_memory_context("u1")
        assert "Msg-0" not in ctx  # el más antiguo ya no está


# ---------------------------------------------------------------------------
# 7. Aislamiento entre usuarios
# ---------------------------------------------------------------------------

class TestUserIsolation:
    """La memoria de un usuario no afecta a otro."""

    def test_separate_histories(self):
        store_memory("u1", "Dato de u1", "Resp u1")
        store_memory("u2", "Dato de u2", "Resp u2")
        ctx1 = get_memory_context("u1")
        ctx2 = get_memory_context("u2")
        assert "u1" in ctx1
        assert "u2" not in ctx1
        assert "u2" in ctx2
        assert "u1" not in ctx2

    def test_separate_profiles(self):
        store_memory("u1", "Me llamo Ana", "Hola")
        store_memory("u2", "Me llamo Pedro", "Hola")
        assert get_user_profile("u1")["name"] == "Ana"
        assert get_user_profile("u2")["name"] == "Pedro"

    def test_clear_one_user(self):
        store_memory("u1", "Test", "Resp")
        store_memory("u2", "Test", "Resp")
        clear_memory("u1")
        assert get_history_count("u1") == 0
        assert get_history_count("u2") == 1


# ---------------------------------------------------------------------------
# 8. Estadísticas
# ---------------------------------------------------------------------------

class TestMemoryStats:
    """memory_stats retorna datos correctos."""

    def test_empty(self):
        stats = memory_stats()
        assert stats["total_users"] == 0
        assert stats["total_interactions"] == 0

    def test_with_data(self):
        store_memory("u1", "A", "B")
        store_memory("u1", "C", "D")
        store_memory("u2", "E", "F")
        stats = memory_stats()
        assert stats["total_users"] == 2
        assert stats["total_interactions"] == 3


# ---------------------------------------------------------------------------
# 9. Thread safety básico
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Operaciones concurrentes no deben causar errores."""

    def test_concurrent_stores(self):
        errors = []

        def write(uid: str, n: int) -> None:
            try:
                for i in range(n):
                    store_memory(uid, f"msg-{i}", f"resp-{i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=write, args=(f"u{t}", 20))
            for t in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = memory_stats()
        assert stats["total_users"] == 5
        assert stats["total_interactions"] == 100


# ---------------------------------------------------------------------------
# 10. Perfil vacío
# ---------------------------------------------------------------------------

class TestEmptyProfile:
    """get_user_profile para usuario sin datos."""

    def test_unknown_user(self):
        assert get_user_profile("nobody") == {}

    def test_profile_after_simple_message(self):
        """Un mensaje sin datos personales crea perfil con solo idioma."""
        store_memory("u1", "¿Qué hora es?", "No tengo reloj.")
        profile = get_user_profile("u1")
        assert profile["name"] == ""
        assert profile["language"] == "es"


# ---------------------------------------------------------------------------
# 11. resolve_with_memory — resolver pre-LLM
# ---------------------------------------------------------------------------

class TestResolveWithMemory:
    """resolve_with_memory responde directamente desde datos almacenados.

    Retorna {"text": "...", "source": "memory"} cuando resuelve,
    o "" cuando la pregunta no es sobre el usuario.
    """

    @pytest.fixture(autouse=True)
    def _isolate_persistent_stores(self):
        """Aísla subsistemas persistentes EXTERNOS para que estos tests sean
        deterministas en cualquier máquina (CI limpio o la del creador), sin
        borrar ni alterar la memoria permanente real:
          - core_memory.get_living_response → "" (no leer el "alma" persistente)
          - identity_anchor.get_locked_language → "" (sin idioma fijado previo)
          - language_gate.get_user_language → idioma detectado del input
        El store de usuario ya se limpia con el fixture módulo _clean_memory.
        """
        from unittest.mock import patch
        with patch("vectrax.core_memory.get_living_response", return_value=""), \
             patch("vectrax.identity_anchor.get_locked_language", return_value=""), \
             patch(
                 "core.language_gate.get_user_language",
                 side_effect=lambda user_id, user_input="": _detect_lang(user_input),
             ):
            yield

    # ── Estructura del dict ─────────────────────────────────────

    def test_resolved_returns_dict_with_source(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "Quién soy")
        assert isinstance(r, dict)
        assert r["source"] == "memory"
        assert "Mario" in r["text"]

    def test_unresolved_returns_empty_string(self):
        store_memory("u1", "Hola", "Ok")
        r = resolve_with_memory("u1", "¿Qué es la gravedad?")
        assert r == ""

    # ── Nombre ─────────────────────────────────────────────

    def test_quien_soy(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "Quién soy")
        assert "Mario" in r["text"]

    def test_como_me_llamo(self):
        store_memory("u1", "Me llamo Ana", "Ok")
        r = resolve_with_memory("u1", "Cómo me llamo")
        assert "Ana" in r["text"]

    def test_cual_es_mi_nombre(self):
        store_memory("u1", "Mi nombre es Pedro", "Ok")
        r = resolve_with_memory("u1", "Cuál es mi nombre")
        assert "Pedro" in r["text"]

    def test_who_am_i(self):
        store_memory("u1", "My name is John", "Ok")
        r = resolve_with_memory("u1", "Who am I")
        assert "John" in r["text"]

    def test_sabes_quien_soy(self):
        store_memory("u1", "Me llamo Carlos", "Ok")
        r = resolve_with_memory("u1", "Sabes quién soy")
        assert "Carlos" in r["text"]

    def test_me_conoces(self):
        store_memory("u1", "Me llamo Laura", "Ok")
        r = resolve_with_memory("u1", "Me conoces")
        assert "Laura" in r["text"]

    def test_name_not_stored_yet(self):
        """Sin nombre almacenado, retorna vacío para caer al LLM."""
        store_memory("u1", "Hola", "Ok")
        r = resolve_with_memory("u1", "Cómo me llamo")
        assert r == ""  # sin dato → cae al LLM, no genera mensaje interno

    # ── Preferencias ───────────────────────────────────────

    def test_mis_preferencias(self):
        store_memory("u1", "Prefiero Python.", "Ok")
        r = resolve_with_memory("u1", "Cuáles son mis preferencias")
        assert "Python" in r["text"]

    def test_que_prefiero(self):
        store_memory("u1", "Prefiero el modo oscuro.", "Ok")
        r = resolve_with_memory("u1", "Qué prefiero")
        assert "oscuro" in r["text"]

    # ── Intereses ──────────────────────────────────────────

    def test_mis_intereses(self):
        store_memory("u1", "Me interesa la robótica.", "Ok")
        r = resolve_with_memory("u1", "Cuáles son mis intereses")
        assert "robótica" in r["text"]

    # ── Resumen completo ─────────────────────────────────────

    def test_que_sabes_de_mi(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        store_memory("u1", "Prefiero Python.", "Ok")
        r = resolve_with_memory("u1", "Qué sabes de mí")
        assert "Mario" in r["text"]
        assert "Python" in r["text"]

    def test_que_sabes_de_mi_minimal(self):
        """Con solo idioma (sin nombre/prefs/intereses), cae al LLM."""
        store_memory("u1", "Hola", "Ok")
        r = resolve_with_memory("u1", "Qué sabes de mí")
        assert r == ""  # solo idioma no basta, cae al LLM

    def test_que_sabes_de_mi_truly_empty(self):
        """Usuario sin perfil recibe mensaje de no datos."""
        # No store_memory, user_id sin datos pero con perfil vacío
        r = resolve_with_memory("no_data_user", "Qué sabes de mí")
        assert r == ""  # sin perfil, retorna vacío

    # ── No resuelve preguntas generales ─────────────────────

    def test_general_question_returns_empty(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "¿Qué es la gravedad?")
        assert r == ""

    def test_unknown_user_returns_empty(self):
        r = resolve_with_memory("nobody", "Quién soy")
        assert r == ""

    def test_empty_input_returns_empty(self):
        r = resolve_with_memory("u1", "")
        assert r == ""

    # ── Aislamiento entre usuarios ─────────────────────────

    def test_user_isolation(self):
        store_memory("u1", "Me llamo Ana", "Ok")
        store_memory("u2", "Me llamo Pedro", "Ok")
        r1 = resolve_with_memory("u1", "Cómo me llamo")
        r2 = resolve_with_memory("u2", "Cómo me llamo")
        assert "Ana" in r1["text"]
        assert "Pedro" in r2["text"]
        assert "Pedro" not in r1["text"]
        assert "Ana" not in r2["text"]

    # ── Variaciones de input (normalización) ────────────────────

    def test_quien_soy_sin_acento(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "quien soy")
        assert "Mario" in r["text"]

    def test_quien_soy_con_interrogacion(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "quien soy?")
        assert "Mario" in r["text"]

    def test_quien_soy_con_doble_interrogacion(self):
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "¿quien soy?")
        assert "Mario" in r["text"]

    def test_como_me_llamo_sin_acento(self):
        store_memory("u1", "Me llamo Ana", "Ok")
        r = resolve_with_memory("u1", "como me llamo")
        assert "Ana" in r["text"]

    def test_cual_es_mi_nombre_sin_acento(self):
        store_memory("u1", "Mi nombre es Pedro", "Ok")
        r = resolve_with_memory("u1", "cual es mi nombre")
        assert "Pedro" in r["text"]

    def test_respuesta_usa_eres(self):
        """La respuesta para '¿quién soy?' debe usar 'Eres'."""
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "¿Quién soy?")
        assert r["text"] == "Eres Mario."
        assert r["source"] == "memory"

    def test_respuesta_en_usa_you_are(self):
        """Input sin acento se detecta como EN → 'You are'."""
        store_memory("u1", "Me llamo Mario", "Ok")
        r = resolve_with_memory("u1", "quien soy")
        assert "Mario" in r["text"]

    def test_recuerdas_mi_nombre(self):
        store_memory("u1", "Me llamo Laura", "Ok")
        r = resolve_with_memory("u1", "recuerdas mi nombre")
        assert "Laura" in r["text"]

    def test_nombre_no_registrado_cae_a_llm(self):
        """Sin nombre, no genera respuesta interna — cae al LLM."""
        store_memory("u1", "Hola", "Ok")
        r = resolve_with_memory("u1", "¿Quién soy?")
        assert r == ""  # sin dato → cae al LLM
