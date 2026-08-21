"""
tests/test_capability_narrator.py — Fase 3, paso 6.

Fija el contrato de `core/self_observation/capability_narrator.py::narrate()`:
  1. Función pura y determinista: mismo CapabilityContext -> mismo texto.
  2. Distingue con claridad exists/connected/authorized/health (incluyendo
     el estado compuesto "disponible pero no autorizado").
  3. Nunca muestra JSON crudo, nombres de función internos, rutas ni
     secretos — ni siquiera el campo `reason` del contrato.
  4. Ante evidencia insuficiente (sin entries), responde honesto sin
     fabricar.
  5. Español e inglés.
  6. Menciona fallback_sources solo cuando hay gaps; si no hay alternativa
     verificada, lo dice honestamente en vez de omitirlo o inventar una.
"""

from __future__ import annotations

from core.orchestration.bootstrap import HEALTH_AVAILABLE, HEALTH_DEGRADED, HEALTH_UNAVAILABLE
from core.self_observation.capability_context import CapabilityContext, CapabilityEntry
from core.self_observation.capability_narrator import narrate


def _entry(
    name,
    kind="engine",
    group="test",
    exists=True,
    connected=True,
    authorized=True,
    health=HEALTH_AVAILABLE,
    reason="",
    evidence_source="test",
    observed_at=0.0,
) -> CapabilityEntry:
    return CapabilityEntry(
        name=name, kind=kind, group=group, exists=exists, connected=connected,
        authorized=authorized, health=health, reason=reason,
        evidence_source=evidence_source, observed_at=observed_at,
    )


# ===========================================================================
# 1. Evidencia insuficiente — nunca fabrica
# ===========================================================================

class TestInsufficientEvidence:
    def test_empty_entries_es(self):
        ctx = CapabilityContext(entries=[], gaps=[], fallback_sources=[])
        text = narrate(ctx, lang="es")
        assert "no tengo evidencia suficiente" in text.lower()
        assert "verificar" in text.lower()

    def test_empty_entries_en(self):
        ctx = CapabilityContext(entries=[], gaps=[], fallback_sources=[])
        text = narrate(ctx, lang="en")
        assert "don't have enough evidence" in text.lower()

    def test_unrecognized_lang_defaults_to_es(self):
        ctx = CapabilityContext(entries=[], gaps=[], fallback_sources=[])
        text = narrate(ctx, lang="fr")
        assert "no tengo evidencia suficiente" in text.lower()


# ===========================================================================
# 2. Todo disponible — sin gaps, sin mencionar fallback
# ===========================================================================

class TestAllReady:
    def test_all_ready_no_gap_or_fallback_mention(self):
        entries = [_entry("smart_router"), _entry("user_memory")]
        ctx = CapabilityContext(entries=entries, gaps=[], fallback_sources=[])
        text_es = narrate(ctx, lang="es")
        assert "2/2" in text_es
        assert "apoyarme" not in text_es  # no fallback si no hay gaps
        assert "alternativa" not in text_es

        text_en = narrate(ctx, lang="en")
        assert "2/2" in text_en
        assert "rely on" not in text_en


# ===========================================================================
# 3. Estado compuesto: existe + conectado + disponible, pero NO autorizado
# ===========================================================================

class TestCompositeUnauthorizedState:
    def test_distinguishes_available_but_unauthorized(self):
        ready = _entry("smart_router")
        gated = _entry("active_learning", authorized=False, health=HEALTH_AVAILABLE)
        ctx = CapabilityContext(
            entries=[ready, gated], gaps=[gated], fallback_sources=[],
        )
        text = narrate(ctx, lang="es")
        assert "1/2" in text
        assert "active_learning" in text
        assert "sin autorización" in text.lower()
        # NO debe decir simplemente "disponible" sin matizar la falta de
        # autorización, ni confundirlo con "sin conexión" o "degradado".
        assert "sin conexión" not in text.lower()
        assert "chequeo de salud fallando" not in text.lower()

    def test_composite_state_in_english(self):
        gated = _entry("active_learning", authorized=False, health=HEALTH_AVAILABLE)
        ctx = CapabilityContext(entries=[gated], gaps=[gated], fallback_sources=[])
        text = narrate(ctx, lang="en")
        assert "not authorized to use right now" in text
        assert "active_learning" in text


# ===========================================================================
# 4. Distingue degraded de unavailable de unauthorized (nunca los mezcla)
# ===========================================================================

class TestDistinctHealthStates:
    def test_unavailable_degraded_unauthorized_all_distinct(self):
        down = _entry("gone", connected=False, health=HEALTH_UNAVAILABLE, authorized=True)
        broken = _entry("shaky", connected=True, health=HEALTH_DEGRADED, authorized=True)
        gated = _entry("locked", connected=True, health=HEALTH_AVAILABLE, authorized=False)
        ok = _entry("fine", connected=True, health=HEALTH_AVAILABLE, authorized=True)
        ctx = CapabilityContext(
            entries=[down, broken, gated, ok],
            gaps=[down, broken, gated],
            fallback_sources=["fine"],
        )
        text = narrate(ctx, lang="es")
        assert "1/4" in text
        assert "gone" in text and "sin conexión verificada" in text.lower()
        assert "shaky" in text and "chequeo de salud fallando" in text.lower()
        assert "locked" in text and "sin autorización" in text.lower()
        assert "fine" in text  # mencionado como fallback
        assert "apoyarme en: fine" in text.lower() or "apoyarme en fine" in text.lower()

    def test_does_not_exist_is_unavailable(self):
        missing = _entry("ghost", exists=False, connected=False, health=HEALTH_UNAVAILABLE)
        ctx = CapabilityContext(entries=[missing], gaps=[missing], fallback_sources=[])
        text = narrate(ctx, lang="es")
        assert "ghost" in text
        assert "sin conexión verificada" in text.lower()


# ===========================================================================
# 5. Fallback: mencionado solo si hay gaps; honesto si no hay alternativa
# ===========================================================================

class TestFallbackHonesty:
    def test_no_fallback_available_says_so_honestly(self):
        broken = _entry("shaky", health=HEALTH_DEGRADED)
        ctx = CapabilityContext(entries=[broken], gaps=[broken], fallback_sources=[])
        text_es = narrate(ctx, lang="es")
        assert "no tengo ahora mismo una alternativa verificada" in text_es.lower()

        text_en = narrate(ctx, lang="en")
        assert "don't have a verified alternative" in text_en.lower()

    def test_fallback_present_lists_real_sources(self):
        broken = _entry("shaky", health=HEALTH_DEGRADED)
        ctx = CapabilityContext(
            entries=[broken], gaps=[broken], fallback_sources=["criterion", "market_observer"],
        )
        text = narrate(ctx, lang="es")
        assert "criterion" in text
        assert "market_observer" in text


# ===========================================================================
# 6. Nunca JSON crudo, nombres de función internos, rutas ni secretos
# ===========================================================================

class TestNoTechnicalLeakage:
    def test_never_leaks_reason_field(self):
        """`reason` puede contener detalle de excepción (nombre de función,
        ruta, etc.) — el narrador NUNCA debe incluirlo en el texto."""
        broken = _entry(
            "shaky", health=HEALTH_UNAVAILABLE, connected=False,
            reason="ImportError: /Users/mariobravo/Vectrax/core/secret_module.py sk-ABCDEF1234567890",
        )
        ctx = CapabilityContext(entries=[broken], gaps=[broken], fallback_sources=[])
        text = narrate(ctx, lang="es")
        assert "/Users/" not in text
        assert "sk-ABCDEF1234567890" not in text
        assert "ImportError" not in text
        assert "secret_module" not in text

    def test_no_raw_json_structure(self):
        entries = [_entry("a"), _entry("b", health=HEALTH_DEGRADED)]
        ctx = CapabilityContext(entries=entries, gaps=[entries[1]], fallback_sources=[])
        text = narrate(ctx, lang="es")
        assert "{" not in text
        assert "}" not in text
        assert '"' not in text

    def test_no_internal_function_names(self):
        entries = [_entry("a")]
        ctx = CapabilityContext(entries=entries, gaps=[], fallback_sources=[])
        text = narrate(ctx, lang="es")
        for forbidden in ("_check_health", "_classify_single", "build_capability_context", "__init__"):
            assert forbidden not in text


# ===========================================================================
# 7. Pureza y determinismo
# ===========================================================================

class TestPurityAndDeterminism:
    def test_same_context_same_text(self):
        entries = [_entry("a"), _entry("b", authorized=False)]
        ctx = CapabilityContext(entries=entries, gaps=[entries[1]], fallback_sources=["a"])
        assert narrate(ctx, lang="es") == narrate(ctx, lang="es")
        assert narrate(ctx, lang="en") == narrate(ctx, lang="en")

    def test_does_not_mutate_context(self):
        entries = [_entry("a"), _entry("b", authorized=False)]
        gaps = [entries[1]]
        ctx = CapabilityContext(entries=list(entries), gaps=list(gaps), fallback_sources=["a"])
        narrate(ctx, lang="es")
        assert ctx.entries == entries
        assert ctx.gaps == gaps
        assert ctx.fallback_sources == ["a"]
