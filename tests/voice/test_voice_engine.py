"""
tests/voice/test_voice_engine.py
=================================
Tests for the Vectrax Voice Engine — Motor de Voz Viva.

Covers the 8 cases dictated by the creator and the cross-cutting rules
defined in the spec.

Acceptance:
  - No casual response sounds like an internal log, a technical manual,
    or a system status report.
  - fallback_response is constructed from intent + tone + name (NOT a
    fixed sentence).
  - The auditor never replaces a valid response with robotic text.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.voice import (
    VoiceEngine,
    classify_tone,
    fallback_response,
    humanize_response,
    audit_without_replacing,
)
from core.voice.intent import (
    INTENT_GREETING, INTENT_THANKS, INTENT_FAREWELL, INTENT_CASUAL,
    INTENT_MEMORY_SAVE, INTENT_MEMORY_RECALL, INTENT_IDENTITY,
    INTENT_TECHNICAL, INTENT_CREATIVE, INTENT_CRISIS, INTENT_PRAGMATIC,
    INTENT_REMINDER,
    TONE_WARM, TONE_BRIEF, TONE_CASUAL, TONE_TECHNICAL,
    TONE_PLAYFUL, TONE_SUPPORTIVE,
)


# Phrases that disqualify a response as "human" — must never appear in a
# casual reply. (Technical replies may legitimately use some of these.)
_SYSTEM_JARGON = (
    "núcleo", "nucleo", "estado del núcleo", "estado del nucleo",
    "masa total", "estrellas (usuarios)", "patrones absorbidos",
    "coherencia", "gravedad semántica", "constelaciones",
    "posicionada en el núcleo", "core state", "node mass",
    "registrado en el núcleo", "información recibida",
    "vectrax activo",
)


def _is_humanlike(text: str) -> bool:
    """Soft check: response does not sound like a system status log."""
    if not text or not text.strip():
        return False
    low = text.lower()
    return not any(j in low for j in _SYSTEM_JARGON)


# ---------------------------------------------------------------------------
# Casos dictados por el creador
# ---------------------------------------------------------------------------

class TestEightCases(unittest.TestCase):
    """The 8 specific cases from Mario's spec."""

    def test_tengo_hambre(self):
        # "Tengo hambre" → pragmatic, brief, NOT casual log
        c = classify_tone("Tengo hambre")
        self.assertEqual(c["intent"], INTENT_PRAGMATIC)
        self.assertIn(c["tone"], (TONE_BRIEF,))
        # Fallback must sound human, not system-y
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="Tengo hambre")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")

    def test_quien_eres(self):
        # "¿Quién eres?" → identity, casual, primera persona
        c = classify_tone("¿Quién eres?")
        self.assertEqual(c["intent"], INTENT_IDENTITY)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="quien eres")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")
        # First-person identity language
        self.assertTrue(
            "soy" in fb.lower() or "i'm" in fb.lower() or "vectrax" in fb.lower(),
            msg=f"no first-person identity: {fb!r}",
        )

    def test_guarda_que_me_gusta_el_cafe(self):
        # "Guarda que me gusta el café" → memory_save
        c = classify_tone("Guarda que me gusta el café")
        self.assertEqual(c["intent"], INTENT_MEMORY_SAVE)
        self.assertEqual(c["tone"], TONE_BRIEF)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="Guarda que me gusta el café")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")
        # Should be SHORT (≤30 chars) — confirmation only
        self.assertLess(len(fb), 35)

    def test_que_sabes_de_mi(self):
        # "¿Qué sabes de mí?" → memory_recall
        c = classify_tone("¿Qué sabes de mí?")
        self.assertEqual(c["intent"], INTENT_MEMORY_RECALL)
        self.assertEqual(c["tone"], TONE_WARM)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="qué sabes de mí")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")

    def test_explicame_tu_arquitectura(self):
        # "Explícame tu arquitectura" → technical, technical tone allowed
        c = classify_tone("Explícame tu arquitectura")
        self.assertEqual(c["intent"], INTENT_TECHNICAL)
        self.assertEqual(c["tone"], TONE_TECHNICAL)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="explica tu arquitectura")
        # Technical fallback may use some structure but not jargon-marketing
        self.assertGreater(len(fb), 5)

    def test_hola(self):
        # "Hola" → greeting, brief or warm
        c = classify_tone("Hola")
        self.assertEqual(c["intent"], INTENT_GREETING)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="hola")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")
        # Must contain a greeting word
        self.assertTrue(
            any(w in fb.lower() for w in ("hola", "hey")),
            msg=f"no greeting word: {fb!r}",
        )

    def test_estoy_cansado(self):
        # "Estoy cansado" → casual + supportive tone
        c = classify_tone("Estoy cansado")
        self.assertEqual(c["intent"], INTENT_CASUAL)
        self.assertEqual(c["tone"], TONE_SUPPORTIVE)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="estoy cansado")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")
        # Not a system status report
        self.assertNotIn("núcleo", fb.lower())
        self.assertNotIn("estable", fb.lower())

    def test_recuerdame_llamar_a_carlos(self):
        # "Recuérdame llamar a Carlos" → reminder
        c = classify_tone("Recuérdame llamar a Carlos")
        self.assertEqual(c["intent"], INTENT_REMINDER)
        self.assertEqual(c["tone"], TONE_BRIEF)
        fb = fallback_response(c["intent"], c["tone"], "Mario", lang="es",
                               seed="recuérdame llamar a Carlos")
        self.assertTrue(_is_humanlike(fb), msg=f"non-human: {fb!r}")


# ---------------------------------------------------------------------------
# Reglas transversales del spec
# ---------------------------------------------------------------------------

class TestCrossRules(unittest.TestCase):

    def test_fallback_is_not_a_fixed_sentence(self):
        """fallback_response must vary by seed — not a single fixed string."""
        outputs = {
            fallback_response(INTENT_GREETING, TONE_WARM, "Mario",
                              lang="es", seed=s)
            for s in ("hola", "hey", "buenas", "saludos", "qué tal",
                      "hola vectrax")
        }
        # At least 2 distinct outputs across 6 different seeds
        self.assertGreaterEqual(len(outputs), 2,
                                msg=f"fallback is fixed: {outputs!r}")

    def test_fallback_uses_name_when_provided(self):
        out = fallback_response(INTENT_GREETING, TONE_WARM, "Mario",
                                lang="es", seed="hola")
        # Should contain the name (with at least one of the templates)
        # Across all greeting/warm/es templates, at least one places name.
        all_outputs = {
            fallback_response(INTENT_GREETING, TONE_WARM, "Mario",
                              lang="es", seed=str(i))
            for i in range(20)
        }
        with_name = [o for o in all_outputs if "Mario" in o]
        self.assertTrue(with_name, msg=f"name never used: {all_outputs!r}")

    def test_humanizer_does_not_replace_valid_response(self):
        """A clean, human LLM response must pass through humanizer unchanged
        (modulo whitespace normalization)."""
        clean = "Descansa. Mañana piensas mejor."
        out = humanize_response(clean, tone=TONE_SUPPORTIVE)
        self.assertEqual(out, clean)

    def test_humanizer_strips_robotic_opener(self):
        rob = ("Como modelo de lenguaje, te puedo decir que descansar "
               "es importante para tu salud.")
        out = humanize_response(rob, tone=TONE_BRIEF)
        self.assertNotIn("modelo de lenguaje", out.lower())
        self.assertGreater(len(out), 5)

    def test_audit_does_not_replace_valid(self):
        """Auditor never substitutes valid text by robotic placeholder."""
        ar = audit_without_replacing("Hola Mario. ¿Qué necesitas?",
                                     user_input="hola",
                                     expected_lang="es")
        self.assertFalse(ar.should_block)
        self.assertEqual(ar.text, "Hola Mario. ¿Qué necesitas?")

    def test_audit_blocks_only_on_empty_or_internal_leak(self):
        ar1 = audit_without_replacing("", user_input="hola")
        self.assertTrue(ar1.should_block)
        ar2 = audit_without_replacing(
            "Traceback (most recent call last): File '...' line 42",
            user_input="hola",
        )
        self.assertTrue(ar2.should_block)
        # Vague meta is a warning, NOT a block
        ar3 = audit_without_replacing(
            "No tengo suficiente información para responder eso bien, "
            "¿podrías reformular?",
            user_input="dale",
        )
        # Should NOT block: it's a soft warning
        self.assertFalse(ar3.should_block)
        self.assertIn("vague_meta", ar3.warnings)

    def test_compose_final_uses_llm_when_valid(self):
        ve = VoiceEngine()
        comp = ve.compose_final(
            user_message="hola",
            raw_llm_response="Hola Mario. Aquí estoy contigo.",
            identity_context={"name": "Mario", "lang": "es"},
        )
        self.assertEqual(comp.source, "humanized_llm")
        self.assertIn("Mario", comp.text)

    def test_compose_final_falls_back_when_llm_empty(self):
        ve = VoiceEngine()
        comp = ve.compose_final(
            user_message="hola",
            raw_llm_response="",
            identity_context={"name": "Mario", "lang": "es"},
        )
        self.assertEqual(comp.source, "fallback")
        self.assertIn("Mario", comp.text)

    def test_no_response_sounds_like_log(self):
        """Run all 8 cases through fallback and ensure none sounds like
        a log / status report / manual."""
        cases = [
            ("Tengo hambre", "es"),
            ("¿Quién eres?", "es"),
            ("Guarda que me gusta el café", "es"),
            ("¿Qué sabes de mí?", "es"),
            ("Explícame tu arquitectura", "es"),
            ("Hola", "es"),
            ("Estoy cansado", "es"),
            ("Recuérdame llamar a Carlos", "es"),
        ]
        for msg, lang in cases:
            c = classify_tone(msg)
            fb = fallback_response(c["intent"], c["tone"], "Mario",
                                   lang=lang, seed=msg)
            self.assertTrue(
                _is_humanlike(fb),
                msg=f"sounds like system: msg={msg!r} fb={fb!r}",
            )


# ---------------------------------------------------------------------------
# Persona directive sanity
# ---------------------------------------------------------------------------

class TestPersonaDirective(unittest.TestCase):

    def test_directive_has_tone_marker(self):
        from core.voice import build_voice_directive
        d = build_voice_directive(
            "hola",
            memory_context=None,
            identity_context={"name": "Mario", "lang": "es"},
        )
        self.assertIn("Tono activo", d)

    def test_directive_mentions_name_when_relevant(self):
        from core.voice import build_voice_directive
        d = build_voice_directive(
            "hola",
            memory_context=None,
            identity_context={"name": "Mario", "lang": "es"},
        )
        self.assertIn("Mario", d)

    def test_directive_no_memory_when_empty(self):
        from core.voice import build_voice_directive
        d = build_voice_directive(
            "hola",
            memory_context="",
            identity_context={"name": "Mario", "lang": "es"},
        )
        self.assertNotIn("memoria activa relevante", d)


if __name__ == "__main__":
    unittest.main()
