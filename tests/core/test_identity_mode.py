"""
Tests for core/mode_selector.py and core/identity_context.py

Covers:
  - Mode detection for all 4 modes (12 queries)
  - Priority: identidad > tecnico > observador > conversacional
  - Mixed-mode messages (tech + observer keywords)
  - Tone generation per mode
  - IdentityContext construction
  - Prompt block generation (creator vs non-creator)
  - Empty/edge cases
"""

import os
import unittest
from unittest.mock import patch, MagicMock

from core.mode_selector import detect_mode, get_tone, MODES
from core.identity_context import IdentityContext, build_identity_context


class TestModeDetection(unittest.TestCase):
    """Each mode should be detected from keywords in the message."""

    # ── Técnico ──

    def test_tecnico_error(self):
        self.assertEqual(detect_mode("Tengo un error en el worker"), "tecnico")

    def test_tecnico_log(self):
        self.assertEqual(detect_mode("Revisa el log del gateway"), "tecnico")

    def test_tecnico_deploy(self):
        self.assertEqual(detect_mode("Deploy al servidor"), "tecnico")

    def test_tecnico_fix(self):
        self.assertEqual(detect_mode("Implementa un fix para el bug"), "tecnico")

    # ── Observador ──

    def test_observador_mercado(self):
        self.assertEqual(detect_mode("Observa conmigo el mercado"), "observador")

    def test_observador_universo(self):
        self.assertEqual(detect_mode("Cómo está el universo gravitacional"), "observador")

    def test_observador_convergencia(self):
        self.assertEqual(detect_mode("Qué convergencias detectas"), "observador")

    def test_observador_patron(self):
        self.assertEqual(detect_mode("Analiza el patrón de mercado"), "observador")

    # ── Identidad ──

    def test_identidad_quien_soy(self):
        self.assertEqual(detect_mode("Quién soy"), "identidad")

    def test_identidad_memoria(self):
        self.assertEqual(detect_mode("Háblame de mi memoria"), "identidad")

    def test_identidad_que_sabes(self):
        self.assertEqual(detect_mode("Qué sabes de mí"), "identidad")

    def test_identidad_english(self):
        self.assertEqual(detect_mode("What do you know about me"), "identidad")

    # ── Conversacional ──

    def test_conversacional_hola(self):
        self.assertEqual(detect_mode("Hola cómo estás"), "conversacional")

    def test_conversacional_gracias(self):
        self.assertEqual(detect_mode("Gracias por todo"), "conversacional")

    def test_conversacional_empty(self):
        self.assertEqual(detect_mode(""), "conversacional")


class TestModePriority(unittest.TestCase):
    """Identity should always win. Tech should beat observer when tied."""

    def test_identidad_beats_tecnico(self):
        # "quién soy" (identity) + "error" (tech) → identity wins
        self.assertEqual(detect_mode("Quién soy que tengo un error"), "identidad")

    def test_identidad_beats_observador(self):
        self.assertEqual(detect_mode("Qué recuerdas del mercado"), "identidad")

    def test_tecnico_beats_observador_when_more_matches(self):
        # More tech keywords than observer
        self.assertEqual(
            detect_mode("Fix the bug in the worker pipeline"),
            "tecnico",
        )

    def test_observador_wins_alone(self):
        self.assertEqual(detect_mode("Observa las tendencias"), "observador")


class TestToneGeneration(unittest.TestCase):
    """Each mode should produce a non-empty tone string."""

    def test_all_modes_have_tones(self):
        for mode in MODES:
            tone = get_tone(mode)
            self.assertIsInstance(tone, str)
            self.assertGreater(len(tone), 20)

    def test_unknown_mode_defaults(self):
        tone = get_tone("nonexistent")
        self.assertIn("compañero", tone.lower())

    def test_tecnico_tone_content(self):
        tone = get_tone("tecnico")
        self.assertIn("directo", tone.lower())

    def test_observador_tone_content(self):
        tone = get_tone("observador")
        self.assertIn("reflexivo", tone.lower())


class TestIdentityContext(unittest.TestCase):
    """IdentityContext construction and prompt block generation."""

    def test_basic_construction(self):
        ctx = IdentityContext(user_id="tg:123")
        self.assertEqual(ctx.user_id, "tg:123")
        self.assertEqual(ctx.mode, "conversacional")
        self.assertFalse(ctx.is_creator)

    def test_prompt_block_basic(self):
        ctx = IdentityContext(user_id="tg:123", mode="tecnico", tone="Sé directo.")
        block = ctx.to_prompt_block()
        self.assertIn("[IDENTIDAD ACTIVA]", block)
        self.assertIn("tecnico", block)
        self.assertIn("Sé directo", block)

    def test_prompt_block_creator(self):
        ctx = IdentityContext(
            user_id="tg:2030762343",
            user_name="Mario",
            is_creator=True,
            mode="observador",
        )
        block = ctx.to_prompt_block()
        self.assertIn("Mario", block)
        self.assertIn("CREADOR", block)

    def test_prompt_block_non_creator(self):
        ctx = IdentityContext(user_id="tg:999", user_name="Ana", is_creator=False)
        block = ctx.to_prompt_block()
        self.assertIn("Ana", block)
        self.assertNotIn("CREADOR", block)

    def test_prompt_block_with_memory(self):
        ctx = IdentityContext(
            user_id="tg:123",
            memory_summary="novia: Joycelyn; trabajo: ingeniero",
        )
        block = ctx.to_prompt_block()
        self.assertIn("Joycelyn", block)

    def test_prompt_block_no_memory(self):
        ctx = IdentityContext(user_id="tg:123", memory_summary="")
        block = ctx.to_prompt_block()
        self.assertNotIn("Contexto de memoria", block)


class TestBuildIdentityContext(unittest.TestCase):
    """build_identity_context should gather user data and detect mode."""

    @patch.dict(os.environ, {"VX_CREATOR_ID": "2030762343"})
    def test_creator_detected(self):
        ctx = build_identity_context("tg:2030762343", "Hola")
        self.assertTrue(ctx.is_creator)

    @patch.dict(os.environ, {"VX_CREATOR_ID": "2030762343"})
    def test_non_creator(self):
        ctx = build_identity_context("tg:999999", "Hola")
        self.assertFalse(ctx.is_creator)

    def test_mode_set_from_content(self):
        ctx = build_identity_context("tg:123", "Tengo un error en el worker")
        self.assertEqual(ctx.mode, "tecnico")

    def test_mode_observador(self):
        ctx = build_identity_context("tg:123", "Observa el mercado")
        self.assertEqual(ctx.mode, "observador")

    def test_tone_not_empty(self):
        ctx = build_identity_context("tg:123", "Hola")
        self.assertGreater(len(ctx.tone), 0)

    def test_timestamp_set(self):
        ctx = build_identity_context("tg:123", "test")
        self.assertGreater(ctx.timestamp, 0)


if __name__ == "__main__":
    unittest.main()
