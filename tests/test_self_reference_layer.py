"""
Tests for vectrax.self_reference_layer
=======================================
Validates detection of self-referential input and first-person
context injection.

Spec test cases:
  1. Image of api.vectrax.app → self_reference=True, capture=True
  2. Logs from vectrax-core → self_reference=True, capture=True
  3. "¿Quién te creó?" → self_reference=True, subject=creator
  4. External image (random) → self_reference=False

Creador: Mario Bravo Castro
"""
import pytest
from vectrax.self_reference_layer import (
    evaluate,
    build_context_injection,
    SelfReferenceResult,
)


# ═══════════════════════════════════════════════════════════════════
# SPEC TEST CASES
# ═══════════════════════════════════════════════════════════════════

class TestSpecCases:
    """Los 4 casos de prueba definidos en la especificación."""

    def test_api_vectrax_app_image(self):
        """Imagen de api.vectrax.app → primera persona + capture."""
        r = evaluate("Mira esta captura de api.vectrax.app")
        assert r.self_reference is True
        assert r.subject == "self"
        assert r.voice == "first_person"
        assert r.is_system_capture is True
        assert "api.vectrax.app" in r.matched_term

    def test_vectrax_core_logs(self):
        """Logs de vectrax-core → primera persona + capture."""
        r = evaluate("Aquí están los logs de vectrax-core")
        assert r.self_reference is True
        assert r.subject == "self"
        assert r.voice == "first_person"
        assert r.is_system_capture is True

    def test_quien_te_creo(self):
        """'¿Quién te creó?' → subject=creator."""
        r = evaluate("¿Quién te creó?")
        assert r.self_reference is True
        assert r.subject == "creator"
        assert r.voice == "first_person"
        assert r.is_creator_mention is True

    def test_external_image_no_activation(self):
        """Imagen externa cualquiera → sin activación."""
        r = evaluate("Mira esta foto de mi gato en el jardín")
        assert r.self_reference is False
        assert r.subject == ""
        assert r.voice == ""


# ═══════════════════════════════════════════════════════════════════
# ACTIVATOR DETECTION — nombres del sistema
# ═══════════════════════════════════════════════════════════════════

class TestSystemNames:
    def test_vectrax_mention(self):
        r = evaluate("Qué pasa con Vectrax hoy")
        assert r.self_reference is True
        assert r.matched_term == "vectrax"

    def test_vectrax_core_hyphen(self):
        r = evaluate("Error en vectrax-core al arrancar")
        assert r.self_reference is True
        assert "vectrax-core" in r.matched_term

    def test_vectrax_core_underscore(self):
        r = evaluate("Reinicia vectrax_core")
        assert r.self_reference is True

    def test_api_url(self):
        r = evaluate("Revisa api.vectrax.app/health")
        assert r.self_reference is True


# ═══════════════════════════════════════════════════════════════════
# ACTIVATOR DETECTION — componentes internos
# ═══════════════════════════════════════════════════════════════════

class TestInternalComponents:
    @pytest.mark.parametrize("keyword", [
        "worker", "pipeline_worker", "universe engine",
        "stars", "star", "constellation", "constellations",
        "convergences", "convergencia", "convergencias",
        "observation ledger", "evolution memory",
        "market observatory", "self_context", "identity_layer",
        "gravitational", "gravitacional",
        "smart_router", "intake_filter", "core_identity",
        "learning_cycle", "word_gravity",
        "presencia_pura", "universal_bus",
        "núcleo cognitivo", "nucleo central",
    ])
    def test_component_activates(self, keyword):
        r = evaluate(f"Revisa el estado del {keyword}")
        assert r.self_reference is True, f"Failed for: {keyword}"
        assert r.voice == "first_person"

    def test_universe_panel_variant(self):
        r = evaluate("Muéstrame el universe panel")
        assert r.self_reference is True

    def test_constelaciones_spanish(self):
        r = evaluate("¿Cuántas constelaciones hay?")
        assert r.self_reference is True


# ═══════════════════════════════════════════════════════════════════
# ACTIVATOR DETECTION — creador
# ═══════════════════════════════════════════════════════════════════

class TestCreatorDetection:
    def test_creador_word(self):
        r = evaluate("¿Quién es tu creador?")
        assert r.is_creator_mention is True
        assert r.subject == "creator"

    def test_creator_english(self):
        r = evaluate("Who created you?")
        assert r.is_creator_mention is True

    def test_mario_name(self):
        r = evaluate("Dime algo sobre Mario")
        assert r.is_creator_mention is True
        assert r.subject == "creator"

    def test_mario_bravo(self):
        r = evaluate("Mario Bravo diseñó esto")
        assert r.is_creator_mention is True

    def test_who_built_vectrax(self):
        r = evaluate("Who built Vectrax?")
        assert r.is_creator_mention is True
        assert r.self_reference is True

    def test_quien_te_hizo(self):
        r = evaluate("¿Quién te hizo?")
        assert r.is_creator_mention is True

    def test_quien_diseno(self):
        r = evaluate("¿Quién diseñó esto?")
        assert r.is_creator_mention is True


# ═══════════════════════════════════════════════════════════════════
# SYSTEM CAPTURE DETECTION
# ═══════════════════════════════════════════════════════════════════

class TestSystemCapture:
    def test_panel_with_system(self):
        r = evaluate("Esta es una captura del panel de Vectrax")
        assert r.is_system_capture is True
        assert r.self_reference is True

    def test_dashboard_with_component(self):
        r = evaluate("El dashboard muestra las convergencias")
        assert r.is_system_capture is True
        assert r.self_reference is True

    def test_logs_alone_no_self_ref(self):
        """'logs' alone without a system keyword → no self_reference."""
        r = evaluate("Revisa los logs")
        # logs matches _SYSTEM_CAPTURE but not _SYSTEM_NAMES or _INTERNAL_COMPONENTS
        # (unless "logs" alone is ambiguous — it's a capture indicator, not a self-ref)
        assert r.is_system_capture is True
        # self_reference depends on whether another tier matched
        # "logs" alone shouldn't trigger self_reference
        assert r.self_reference is False

    def test_screenshot_with_vectrax(self):
        r = evaluate("Tomé un screenshot de Vectrax")
        assert r.is_system_capture is True
        assert r.self_reference is True


# ═══════════════════════════════════════════════════════════════════
# NEGATIVE CASES — no debería activar
# ═══════════════════════════════════════════════════════════════════

class TestNegativeCases:
    def test_random_question(self):
        r = evaluate("¿Cuánto cuesta un vuelo a Madrid?")
        assert r.self_reference is False

    def test_market_question(self):
        r = evaluate("¿Cómo está Bitcoin?")
        assert r.self_reference is False

    def test_empty_string(self):
        r = evaluate("")
        assert r.self_reference is False

    def test_none_content(self):
        r = evaluate(None)
        assert r.self_reference is False

    def test_personal_question(self):
        r = evaluate("¿Cómo me llamo?")
        assert r.self_reference is False

    def test_weather(self):
        r = evaluate("What's the weather like?")
        assert r.self_reference is False

    def test_generic_dashboard(self):
        """'dashboard' without system keyword → no self_reference."""
        r = evaluate("Muéstrame el dashboard de ventas")
        assert r.self_reference is False


# ═══════════════════════════════════════════════════════════════════
# CONTEXT INJECTION
# ═══════════════════════════════════════════════════════════════════

class TestContextInjection:
    def test_self_spanish(self):
        r = evaluate("Muéstrame el universe engine")
        ctx = build_context_injection(r, lang="es")
        assert "AUTO-REFERENCIA ACTIVA" in ctx
        assert "primera persona" in ctx.lower()
        assert "NUNCA" in ctx

    def test_self_english(self):
        r = evaluate("Show me the Vectrax universe")
        ctx = build_context_injection(r, lang="en")
        assert "SELF-REFERENCE ACTIVE" in ctx
        assert "first person" in ctx.lower()

    def test_capture_context_added(self):
        r = evaluate("Captura del dashboard de Vectrax")
        ctx = build_context_injection(r, lang="es")
        assert "captura" in ctx.lower()
        assert "estoy viendo" in ctx.lower()

    def test_creator_context_added(self):
        r = evaluate("¿Quién te creó?")
        ctx = build_context_injection(r, lang="es")
        assert "Mario Bravo Castro" in ctx
        assert "fundacional" in ctx

    def test_creator_context_english(self):
        r = evaluate("Who created you?")
        ctx = build_context_injection(r, lang="en")
        assert "Mario Bravo Castro" in ctx
        assert "foundational" in ctx

    def test_no_injection_for_negative(self):
        r = evaluate("¿Qué hora es?")
        ctx = build_context_injection(r, lang="es")
        assert ctx == ""

    def test_combined_self_and_capture(self):
        """System name + capture word → both flags active."""
        r = evaluate("Screenshot del panel de Vectrax")
        ctx = build_context_injection(r, lang="es")
        assert "AUTO-REFERENCIA ACTIVA" in ctx
        assert "captura" in ctx.lower() or "estoy viendo" in ctx.lower()
