"""
Tests — Identity Layer
========================
Valida la capa de identidad de Vectrax:
  - Filtro de aperturas genéricas de asistente
  - Filtro de contenido religioso no solicitado
  - Filtro de frases Wikipedia
  - Filtro de relleno / cierre genérico
  - Truncado de respuestas largas
  - Estilo: emojis, espacios, puntuación
  - Pipeline completo apply_identity()
  - System prompt y prompt builder

Creado: 2026-03-19
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.expanduser("~/Vectrax"))

from vectrax.identity_layer import (
    SYSTEM_PROMPT,
    MAX_RESPONSE_LENGTH,
    apply_identity,
    build_prompt,
    enforce_final_answer,
    enforce_style,
    filter_response,
    generate_vectrax_replacement,
    is_generic,
    is_low_quality,
)


# ---------------------------------------------------------------------------
# 1. System Prompt
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """El system prompt debe definir identidad Vectrax completa."""

    def test_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_contains_identity(self):
        assert "Vectrax" in SYSTEM_PROMPT

    def test_contains_personality_rules(self):
        assert "directa" in SYSTEM_PROMPT.lower() or "resultado" in SYSTEM_PROMPT.lower()

    def test_no_generic_assistant(self):
        assert "IA genérica" in SYSTEM_PROMPT  # explicitly says NOT generic

    def test_prohibits_processing_narration(self):
        assert "procesando" in SYSTEM_PROMPT.lower()
        assert "consultando memoria" in SYSTEM_PROMPT.lower()

    def test_prohibits_pipeline_description(self):
        assert "pipeline" in SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# 2. Prompt Builder
# ---------------------------------------------------------------------------

class TestPromptBuilder:
    """build_prompt debe construir prompt contextual."""

    def test_basic_prompt(self):
        p = build_prompt("Hola")
        assert "[Mensaje]" in p
        assert "Hola" in p

    def test_prompt_with_memory(self):
        p = build_prompt("¿Qué sabes?", memory_context="El usuario es ingeniero.")
        assert "[Contexto de memoria relevante]" in p
        assert "ingeniero" in p
        assert "[Mensaje]" in p

    def test_prompt_without_memory(self):
        p = build_prompt("Test", memory_context="")
        assert "Contexto" not in p


# ---------------------------------------------------------------------------
# 3. Filtro de aperturas genéricas
# ---------------------------------------------------------------------------

class TestGenericOpenerFilter:
    """El filtro debe eliminar aperturas tipo asistente genérico."""

    def test_removes_hola_puedo_ayudar(self):
        raw = "¡Hola! ¿En qué puedo ayudarte? Tu mensaje fue procesado."
        filtered = filter_response(raw)
        assert "puedo ayudar" not in filtered
        assert "procesado" in filtered

    def test_removes_claro_con_gusto(self):
        raw = "Claro, con gusto. La respuesta es 42."
        filtered = filter_response(raw)
        assert "con gusto" not in filtered
        assert "42" in filtered

    def test_removes_soy_un_modelo(self):
        raw = "Como modelo de lenguaje, puedo decirte que Python es un lenguaje."
        filtered = filter_response(raw)
        assert "modelo de lenguaje" not in filtered

    def test_removes_great_question(self):
        raw = "Great question! The answer is relativity."
        filtered = filter_response(raw)
        assert "Great question" not in filtered
        assert "relativity" in filtered

    def test_preserves_normal_response(self):
        raw = "La gravedad es una fuerza fundamental."
        filtered = filter_response(raw)
        assert filtered == raw


# ---------------------------------------------------------------------------
# 4. Filtro de contenido religioso
# ---------------------------------------------------------------------------

class TestReligiousFilter:
    """El filtro debe eliminar contenido religioso no solicitado."""

    def test_removes_unsolicited_religious(self):
        raw = "Python es útil. Dios te bendiga en tu camino."
        filtered = filter_response(raw, user_input="¿Qué es Python?")
        assert "Dios" not in filtered
        assert "Python" in filtered

    def test_preserves_if_user_asked(self):
        raw = "La iglesia de Notre-Dame fue construida en 1163."
        filtered = filter_response(raw, user_input="Cuéntame sobre la iglesia de Notre-Dame")
        assert "iglesia" in filtered

    def test_no_filter_without_user_input(self):
        """Sin user_input, el filtro religioso no se aplica."""
        raw = "Texto sobre fe y esperanza."
        filtered = filter_response(raw)
        assert "fe" in filtered


# ---------------------------------------------------------------------------
# 5. Filtro Wikipedia / enciclopedia
# ---------------------------------------------------------------------------

class TestWikipediaFilter:
    """El filtro debe eliminar frases tipo Wikipedia."""

    def test_removes_segun_wikipedia(self):
        raw = "Según la Wikipedia, la gravedad atrae objetos."
        filtered = filter_response(raw)
        assert "Wikipedia" not in filtered

    def test_removes_se_define_como(self):
        raw = "Se define como un proceso que se refiere a la computación."
        filtered = filter_response(raw)
        assert "Se define como" not in filtered

    def test_removes_according_to(self):
        raw = "According to Wikipedia, AI is a field of computer science."
        filtered = filter_response(raw)
        assert "According to Wikipedia" not in filtered


# ---------------------------------------------------------------------------
# 6. Filtro de relleno
# ---------------------------------------------------------------------------

class TestFillerFilter:
    """El filtro debe eliminar frases de relleno genérico."""

    def test_removes_espero_que_ayude(self):
        raw = "Python es un lenguaje interpretado. Espero que esto te sea útil."
        filtered = filter_response(raw)
        assert "Espero" not in filtered
        assert "Python" in filtered

    def test_removes_no_dudes(self):
        raw = "La respuesta es 42. No dudes en preguntar más."
        filtered = filter_response(raw)
        assert "No dudes" not in filtered
        assert "42" in filtered

    def test_removes_feel_free(self):
        raw = "The answer is gravity. Feel free to ask more."
        filtered = filter_response(raw)
        assert "Feel free" not in filtered


# ---------------------------------------------------------------------------
# 7. Truncado
# ---------------------------------------------------------------------------

class TestTruncation:
    """Respuestas largas deben truncarse a MAX_RESPONSE_LENGTH."""

    def test_truncates_long_response(self):
        raw = "Frase completa. " * 200  # ~3200 chars
        filtered = filter_response(raw)
        assert len(filtered) <= MAX_RESPONSE_LENGTH

    def test_truncates_at_sentence_boundary(self):
        raw = "Primera oración. Segunda oración. " * 100
        filtered = filter_response(raw)
        assert filtered.endswith(".")

    def test_preserves_short_response(self):
        raw = "Respuesta corta y precisa."
        filtered = filter_response(raw)
        assert filtered == raw


# ---------------------------------------------------------------------------
# 8. Estilo
# ---------------------------------------------------------------------------

class TestStyleEnforcement:
    """enforce_style debe limpiar formato."""

    def test_removes_emojis(self):
        result = enforce_style("Hola 😊 mundo 🌍")
        assert "😊" not in result
        assert "🌍" not in result
        assert "Hola" in result

    def test_normalizes_spaces(self):
        result = enforce_style("texto   con    espacios")
        assert "   " not in result

    def test_normalizes_newlines(self):
        result = enforce_style("línea 1\n\n\n\n\nlínea 2")
        assert "\n\n\n" not in result

    def test_cleans_punctuation_spaces(self):
        result = enforce_style("palabra , otra ; final .")
        assert " ," not in result
        assert " ;" not in result


# ---------------------------------------------------------------------------
# 9. Detectores de calidad
# ---------------------------------------------------------------------------

class TestIsGeneric:
    """is_generic debe detectar respuestas fundamentalmente genéricas."""

    def test_pure_greeting_is_generic(self):
        assert is_generic("¡Hola! ¿En qué puedo ayudarte?") is True

    def test_filler_only_is_generic(self):
        assert is_generic("Espero que esto te sea útil. No dudes en preguntar.") is True

    def test_empty_is_generic(self):
        assert is_generic("") is True

    def test_substantive_not_generic(self):
        assert is_generic("Python es un lenguaje interpretado de alto nivel.") is False


class TestIsLowQuality:
    """is_low_quality debe detectar muros de texto, Wikipedia, irrelevancia."""

    def test_wall_of_text(self):
        wall = "Frase corta de prueba. " * 15  # 15 oraciones sin estructura
        assert is_low_quality(wall) is True

    def test_structured_text_ok(self):
        structured = "Punto 1.\n- Detalle A\n- Detalle B\nPunto 2."
        assert is_low_quality(structured) is False

    def test_wikipedia_heavy(self):
        wiki = (
            "Según la Wikipedia, X es importante. "
            "Se define como un proceso que se refiere a la computación. "
            "Es un término científico."
        )
        assert is_low_quality(wiki) is True

    def test_irrelevant_response(self):
        assert is_low_quality(
            "The weather in Tokyo is sunny today.",
            user_input="¿Qué es la gravedad cuántica?",
        ) is True

    def test_relevant_response_ok(self):
        assert is_low_quality(
            "La gravedad cuántica unifica relatividad y mecánica cuántica.",
            user_input="¿Qué es la gravedad cuántica?",
        ) is False


# ---------------------------------------------------------------------------
# 10. Generador de reemplazo
# ---------------------------------------------------------------------------

class TestVectraxReplacement:
    """generate_vectrax_replacement debe generar respuestas cortas y directas."""

    def test_greeting_es(self):
        r = generate_vectrax_replacement("Hola")
        assert "Vectrax" in r
        assert len(r) < 100

    def test_greeting_en(self):
        r = generate_vectrax_replacement("Hi")
        assert "Vectrax" in r

    def test_thanks(self):
        r = generate_vectrax_replacement("Gracias")
        assert len(r) < 100

    def test_question_es(self):
        r = generate_vectrax_replacement("¿Qué es la gravedad?")
        assert len(r) < 200
        # No debe contener frases vaga/meta bloqueadas
        assert "I have memory context" not in r
        assert "can't generate" not in r
        assert "try rephrasing" not in r.lower()
        assert "no tengo suficiente información" not in r
        assert "intenta con más contexto" not in r

    def test_statement(self):
        r = generate_vectrax_replacement("Mi proyecto usa Python 3.12")
        assert "recibido" in r.lower() or "received" in r.lower()


# ---------------------------------------------------------------------------
# 11. Pipeline completo
# ---------------------------------------------------------------------------

class TestApplyIdentity:
    """apply_identity con control total: evaluar → reemplazar o filtrar."""

    def test_generic_replaced(self):
        """Respuesta genérica se REEMPLAZA, no se filtra."""
        raw = "¡Hola! ¿En qué puedo ayudarte hoy?"
        result = apply_identity(raw, user_input="Hola")
        assert "puedo ayudar" not in result
        assert "Vectrax" in result  # reemplazo con voz Vectrax

    def test_low_quality_replaced(self):
        """Muro de texto sin estructura se REEMPLAZA."""
        wall = "Frase genérica sobre gravedad. " * 12
        result = apply_identity(wall, user_input="¿Qué es la gravedad?")
        assert len(result) < 200  # reemplazo corto

    def test_quality_response_preserved(self):
        """Respuesta de calidad pasa sin reemplazo."""
        clean = "La gravedad cuántica unifica relatividad y mecánica cuántica."
        result = apply_identity(clean, user_input="¿Qué es la gravedad cuántica?")
        assert "gravedad" in result
        assert "cuántica" in result

    def test_empty_response_gets_replacement(self):
        """Respuesta vacía genera reemplazo Vectrax."""
        result = apply_identity("", user_input="Hola")
        assert "Vectrax" in result

    def test_filters_then_styles(self):
        """Respuesta con algo de relleno pero sustancia se filtra, no reemplaza."""
        raw = "Python es un lenguaje interpretado de alto nivel. Espero que esto te sea útil."
        result = apply_identity(raw, user_input="¿Qué es Python?")
        assert "Python" in result
        assert "Espero" not in result


# ---------------------------------------------------------------------------
# 12. enforce_final_answer — puerta final absoluta
# ---------------------------------------------------------------------------

class TestEnforceFinalAnswer:
    """enforce_final_answer: control final total de toda salida."""

    # ── Respuestas canónicas de identidad ────────────────────────────

    def test_who_are_you_es(self):
        r = enforce_final_answer("Quién eres", "Soy ChatGPT.")
        assert "Vectrax" in r
        assert "ChatGPT" not in r

    def test_who_are_you_en(self):
        r = enforce_final_answer("Who are you", "I am an AI assistant.")
        assert "Vectrax" in r

    def test_que_eres(self):
        r = enforce_final_answer("Qué eres", "Soy un modelo de lenguaje.")
        assert "Vectrax" in r
        assert "modelo" not in r

    def test_creator_query(self):
        r = enforce_final_answer("Quién es tu creador", "Fui creado por OpenAI.")
        assert "Mario Bravo Castro" in r
        assert "OpenAI" not in r

    def test_creator_variant(self):
        r = enforce_final_answer("Quién te creó", "No lo sé.")
        assert "Mario Bravo Castro" in r

    def test_universe_query(self):
        r = enforce_final_answer("Explícame tu universo", "No entiendo tu pregunta.")
        assert "memoria" in r.lower() or "convergencias" in r.lower()

    def test_how_do_you_work(self):
        r = enforce_final_answer("Cómo funcionas", "I'm just an AI.")
        assert "estrellas" in r.lower() or "gravedad" in r.lower()

    def test_what_can_you_do(self):
        r = enforce_final_answer("Qué puedes hacer", "I can help with many things.")
        assert "Proceso" in r or "memoria" in r.lower()

    # ── Rechazo por idioma incorrecto ─────────────────────────────

    def test_rejects_english_for_spanish_input(self):
        """Respuesta en inglés para usuario español = rechazada."""
        r = enforce_final_answer(
            "¿Qué es la gravedad?",
            "Gravity is the force that attracts objects with mass towards each other.",
        )
        # Debe rechazar y reemplazar
        assert "Gravity is" not in r

    def test_accepts_spanish_for_spanish_input(self):
        """Respuesta en español para usuario español = aceptada."""
        r = enforce_final_answer(
            "¿Qué es la gravedad?",
            "La gravedad es la fuerza que atrae los cuerpos con masa entre sí.",
        )
        assert "gravedad" in r

    # ── Rechazo de saludos genéricos ──────────────────────────────

    def test_rejects_generic_greeting(self):
        r = enforce_final_answer("Hola", "¡Hola! ¿En qué puedo ayudarte?")
        assert "puedo ayudar" not in r

    # ── Rechazo de meta-respuestas vagas ──────────────────────────

    def test_rejects_vague_meta_answer(self):
        r = enforce_final_answer(
            "¿Qué es Python?",
            "I lack enough information to answer that question. Try rephrasing.",
        )
        assert "lack" not in r
        assert "rephrasing" not in r

    def test_rejects_no_puedo_generar(self):
        r = enforce_final_answer(
            "Dime algo",
            "No puedo generar una respuesta adecuada.",
        )
        assert "No puedo generar" not in r

    # ── Rechazo de contenido religioso no solicitado ──────────────

    def test_rejects_unsolicited_religious(self):
        r = enforce_final_answer(
            "¿Qué es Python?",
            "Python es un lenguaje. Que Dios te bendiga en tu camino de aprendizaje.",
        )
        assert "Dios" not in r

    def test_preserves_religious_if_asked(self):
        r = enforce_final_answer(
            "Quien fue Jesús Cristo",
            "Jesús de Nazaret fue una figura histórica central del cristianismo.",
        )
        assert "Jesús" in r

    # ── Rechazo de genéricos y baja calidad ───────────────────────

    def test_rejects_generic_content(self):
        r = enforce_final_answer(
            "Hola",
            "Espero que esto te sea útil. No dudes en preguntar más.",
        )
        assert "Espero" not in r

    def test_rejects_wall_of_text(self):
        wall = "Frase genérica sobre un tema. " * 12
        r = enforce_final_answer("¿Qué es Python?", wall)
        assert len(r) < 200

    # ── Respuesta vacía siempre produce algo ──────────────────────

    def test_empty_raw_generates_replacement(self):
        r = enforce_final_answer("Hola", "")
        assert len(r) > 0
        assert "Vectrax" in r

    def test_none_raw_generates_replacement(self):
        r = enforce_final_answer("Hola", None)
        assert len(r) > 0

    # ── Respuestas de calidad pasan ───────────────────────────────

    def test_quality_response_passes(self):
        good = "La gravedad cuántica unifica relatividad y mecánica cuántica."
        r = enforce_final_answer("¿Qué es la gravedad cuántica?", good)
        assert "gravedad" in r
        assert "cuántica" in r

    def test_never_returns_empty(self):
        """enforce_final_answer SIEMPRE retorna algo."""
        for ui in ["Hola", "Test", "¿Qué?", ""]:
            for raw in ["", None, "   "]:
                r = enforce_final_answer(ui, raw)
                assert r and len(r.strip()) > 0, f"Empty for ui={ui!r} raw={raw!r}"

    # ── Bloqueo de frases meta tóxicas ───────────────────────────

    def test_blocks_i_have_memory_context(self):
        r = enforce_final_answer(
            "Hola",
            "I have memory context on this, but can't generate a precise answer.",
        )
        assert "memory context" not in r
        assert "can't generate" not in r

    def test_blocks_tengo_contexto_en_memoria(self):
        r = enforce_final_answer(
            "Hola",
            "Tengo contexto en memoria sobre esto, pero no puedo generar una respuesta precisa.",
        )
        assert "contexto en memoria" not in r
        assert "no puedo" not in r.lower() or "no puedo" in r.lower()  # replaced entirely

    def test_blocks_try_rephrasing(self):
        r = enforce_final_answer(
            "¿Qué es Python?",
            "Try rephrasing your question.",
        )
        assert "rephrasing" not in r

    def test_blocks_processing_your_question(self):
        r = enforce_final_answer(
            "Dime algo",
            "Processing your question but I lack enough information.",
        )
        assert "Processing your" not in r
        assert "lack" not in r

    def test_replacement_never_contains_blocked_phrases(self):
        """generate_vectrax_replacement NO debe producir frases bloqueadas."""
        blocked = [
            "I have memory context",
            "can't generate",
            "try rephrasing",
            "no tengo suficiente información",
            "intenta con más contexto",
            "processing your question",
            "intenta reformular",
            "processed and stored in memory",
            "procesado y registrado en memoria",
        ]
        for ui in ["¿Qué es Python?", "Hola", "Dime algo", "Explain gravity",
                   "Mi proyecto usa Python"]:
            r = generate_vectrax_replacement(ui)
            for phrase in blocked:
                assert phrase.lower() not in r.lower(), (
                    f"Blocked phrase {phrase!r} found in replacement for {ui!r}: {r!r}"
                )

    def test_blocks_processed_and_stored(self):
        """'Processed and stored' nunca debe llegar al usuario."""
        r = enforce_final_answer("Ok", "Processed and stored in memory.")
        assert "stored in memory" not in r.lower()

    def test_blocks_procesado_y_registrado(self):
        """'Procesado y registrado' nunca debe llegar al usuario."""
        r = enforce_final_answer("Ok", "Procesado y registrado en memoria.")
        assert "registrado en memoria" not in r.lower()


# ---------------------------------------------------------------------------
# 13. memory_resolved — ruta soberana de memoria
# ---------------------------------------------------------------------------

class TestMemoryResolvedSovereign:
    """Cuando memory_resolved=True, la memoria tiene prioridad absoluta.

    NO pasa por identity matching, NO por rechazo, NO por filtros.
    Solo se aplica enforce_style.
    """

    # ── La respuesta de memoria pasa intacta ──────────────────────

    def test_memory_name_passes_unchanged(self):
        """'Eres Mario.' no debe ser rechazada ni reemplazada."""
        r = enforce_final_answer(
            "quien soy", "Eres Mario.", memory_resolved=True,
        )
        assert r == "Eres Mario."

    def test_memory_name_en_passes(self):
        r = enforce_final_answer(
            "who am i", "You are Mario.", memory_resolved=True,
        )
        assert r == "You are Mario."

    def test_memory_preferences_pass(self):
        r = enforce_final_answer(
            "qué prefiero",
            "Tus preferencias registradas: Python, café.",
            memory_resolved=True,
        )
        assert "Python" in r
        assert "café" in r

    def test_memory_interests_pass(self):
        r = enforce_final_answer(
            "mis intereses",
            "Tus intereses registrados: IA, astronomía.",
            memory_resolved=True,
        )
        assert "IA" in r
        assert "astronomía" in r

    def test_memory_no_name_message_passes(self):
        """Mensaje de 'no tengo tu nombre' no se rechaza."""
        r = enforce_final_answer(
            "quien soy",
            "Aún no tengo tu nombre registrado. Dime cómo te llamas.",
            memory_resolved=True,
        )
        assert "nombre" in r
        assert "llamas" in r

    # ── NO aplica identity matching en modo memoria ────────────────

    def test_memory_bypasses_identity_map(self):
        """Si la memoria resuelve, la identity_map NO la reemplaza."""
        # "Quién eres" normalmente dispararía respuesta canónica,
        # pero si memoria ya resolvió, debe pasar intacta.
        r = enforce_final_answer(
            "quién eres",
            "Mi respuesta desde la memoria del usuario.",
            memory_resolved=True,
        )
        assert r == "Mi respuesta desde la memoria del usuario."
        assert "Soy Vectrax" not in r

    # ── NO aplica reglas de rechazo en modo memoria ────────────────

    def test_memory_bypasses_vague_meta_rejection(self):
        """Respuestas cortas de memoria no se rechazan como 'vagas'."""
        r = enforce_final_answer(
            "algo", "Tu idioma detectado es español.", memory_resolved=True,
        )
        assert "español" in r

    def test_memory_bypasses_language_rejection(self):
        """Respuesta en inglés desde memoria no se rechaza por idioma."""
        r = enforce_final_answer(
            "¿quién soy?",
            "You are Mario.",
            memory_resolved=True,
        )
        assert "Mario" in r

    def test_memory_bypasses_generic_detection(self):
        """Respuestas cortas de memoria no se rechazan como genéricas."""
        r = enforce_final_answer(
            "test", "Recibido.", memory_resolved=True,
        )
        assert r == "Recibido."

    # ── Sí aplica enforce_style ────────────────────────────────────

    def test_memory_cleans_double_spaces(self):
        r = enforce_final_answer(
            "quien soy", "Eres  Mario.", memory_resolved=True,
        )
        assert "  " not in r
        assert "Mario" in r

    def test_memory_removes_emojis(self):
        r = enforce_final_answer(
            "quien soy", "Eres Mario. 😀", memory_resolved=True,
        )
        assert "😀" not in r
        assert "Mario" in r

    # ── memory_resolved=True con respuesta vacía cae al flujo normal ──

    def test_memory_resolved_empty_falls_through(self):
        """Si memory_resolved=True pero raw es vacío, genera reemplazo."""
        r = enforce_final_answer(
            "Hola", "", memory_resolved=True,
        )
        assert len(r) > 0
        assert "Vectrax" in r

    # ── Nunca retorna vacío ─────────────────────────────────────────

    def test_memory_never_returns_empty(self):
        for raw in ["Eres X.", "Tu idioma es español.", "Recibido."]:
            r = enforce_final_answer("test", raw, memory_resolved=True)
            assert r and len(r.strip()) > 0, f"Empty for raw={raw!r}"
