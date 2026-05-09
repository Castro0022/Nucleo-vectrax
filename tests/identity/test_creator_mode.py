"""
tests/identity/test_creator_mode.py — Creator Cognitive Mode tests.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.identity import (  # noqa: E402
    CreatorProfile,
    DEFAULT_PROFILE,
    is_creator,
    profile_for,
    compose_creator_context,
)


class TestIsCreator(unittest.TestCase):
    def test_default_creator(self):
        # Default VX_CREATOR_ID is 2030762343
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            self.assertTrue(is_creator("2030762343"))
            self.assertTrue(is_creator("tg:2030762343"))
            self.assertFalse(is_creator("99999"))
            self.assertFalse(is_creator("tg:99999"))
            self.assertFalse(is_creator(""))

    def test_custom_creator_id(self):
        with patch.dict(os.environ, {"VX_CREATOR_ID": "777"}):
            self.assertTrue(is_creator("777"))
            self.assertTrue(is_creator("tg:777"))
            self.assertFalse(is_creator("2030762343"))


class TestProfileFor(unittest.TestCase):
    def test_returns_profile_for_creator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            p = profile_for("tg:2030762343")
            self.assertIsNotNone(p)
            self.assertIsInstance(p, CreatorProfile)
            self.assertEqual(p.density, "alta")
            self.assertTrue(p.thread_persistence)

    def test_none_for_non_creator(self):
        self.assertIsNone(profile_for("tg:42"))


class TestComposeCreatorContext(unittest.TestCase):
    def test_returns_empty_for_non_creator(self):
        ctx = compose_creator_context("tg:42")
        self.assertEqual(ctx, "")

    def test_returns_block_for_creator_es(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            ctx = compose_creator_context(
                "tg:2030762343", lang="es", include_perception=False,
            )
            self.assertIn("CREATOR MODE", ctx)
            self.assertIn("Mario", ctx)
            self.assertIn("PROHIBIDO", ctx)
            self.assertIn("ayudarte", ctx)  # frase prohibida listada
            self.assertIn("OBLIGATORIO", ctx)
            self.assertIn("Densidad alta", ctx)

    def test_returns_block_for_creator_en(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            ctx = compose_creator_context(
                "tg:2030762343", lang="en", include_perception=False,
            )
            self.assertIn("CREATOR MODE", ctx)
            self.assertIn("FORBIDDEN", ctx)
            self.assertIn("Mario", ctx)

    def test_includes_perception_when_requested(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            ctx = compose_creator_context(
                "tg:2030762343", lang="es", include_perception=True,
            )
            # perception bloque puede o no aparecer dependiendo de
            # disponibilidad de fuentes, pero ctx no debe ser vacío
            self.assertGreater(len(ctx), 0)


class TestAntiAssistantFilter(unittest.TestCase):
    """Verifica que el filter anti-asistente bloquea para creator mode."""

    def setUp(self):
        from core.voice.anti_repetition import reset_for_tests
        reset_for_tests()

    def test_strip_cliches_default_keeps_assistant_phrases(self):
        # Para users normales, '¿cómo puedo ayudarte?' es UX legítima.
        from core.voice.anti_repetition import strip_cliches
        out = strip_cliches("Hola Pedro. ¿Cómo puedo ayudarte hoy?")
        self.assertIn("ayudarte", out.lower())

    def test_strip_cliches_creator_drops_assistant_phrases(self):
        from core.voice.anti_repetition import strip_cliches
        out = strip_cliches(
            "Hola Mario. ¿Cómo puedo ayudarte hoy?",
            creator_mode=True,
        )
        self.assertNotIn("ayudarte", out.lower())

    def test_strip_cliches_creator_drops_estoy_aqui(self):
        from core.voice.anti_repetition import strip_cliches
        out = strip_cliches(
            "Notas claras. Estoy aquí para ayudarte cuando quieras.",
            creator_mode=True,
        )
        self.assertNotIn("estoy aquí para ayudarte", out.lower())
        self.assertIn("notas claras", out.lower())

    def test_filter_response_creator_strips_assistant(self):
        # filter_response auto-detecta creator via core.identity
        from core.voice.anti_repetition import filter_response
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            out = filter_response(
                "Mario, OK. ¿En qué puedo ayudarte?",
                user_id="tg:2030762343",
            )
            self.assertIsNotNone(out)
            self.assertNotIn("ayudarte", (out or "").lower())


if __name__ == "__main__":
    unittest.main()
