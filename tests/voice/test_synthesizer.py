"""
tests/voice/test_synthesizer.py — Tests del TTS synthesizer.

Mocks la llamada HTTP a OpenAI; ningún test toca el API real.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.voice import synthesizer


class TestSynthesizerGate(unittest.TestCase):
    """Reglas de habilitación del TTS."""

    def test_disabled_when_flag_set(self):
        with patch.dict(os.environ, {"VECTRAX_TTS_DISABLED": "1",
                                     "OPENAI_API_KEY": "x"}):
            self.assertFalse(synthesizer.is_tts_enabled())

    def test_disabled_when_no_api_key(self):
        env = dict(os.environ)
        env.pop("OPENAI_API_KEY", None)
        env.pop("VECTRAX_TTS_DISABLED", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(synthesizer.is_tts_enabled())

    def test_enabled_with_key_and_no_flag(self):
        env = dict(os.environ)
        env["OPENAI_API_KEY"] = "sk-test"
        env.pop("VECTRAX_TTS_DISABLED", None)
        with patch.dict(os.environ, env):
            self.assertTrue(synthesizer.is_tts_enabled())


class TestSynthesizeBehavior(unittest.TestCase):
    """Comportamiento de synthesize() — todo mockeado."""

    def setUp(self):
        synthesizer.clear_cache()

    def tearDown(self):
        synthesizer.clear_cache()

    def test_empty_text_returns_none(self):
        result = synthesizer.synthesize("")
        self.assertIsNone(result)

    def test_disabled_returns_none(self):
        with patch.dict(os.environ, {"VECTRAX_TTS_DISABLED": "1"}):
            result = synthesizer.synthesize("hola")
            self.assertIsNone(result)

    def test_calls_openai_with_correct_payload(self):
        fake_audio = b"\x4f\x67\x67\x53" + b"\x00" * 100  # OggS magic + filler
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            with patch.object(synthesizer, "_call_openai_tts",
                              return_value=fake_audio) as mock_call:
                result = synthesizer.synthesize("hola mundo")
                self.assertEqual(result, fake_audio)
                mock_call.assert_called_once()
                args = mock_call.call_args[0]
                self.assertEqual(args[0], "hola mundo")  # text
                self.assertEqual(args[1], "nova")        # voice default
                self.assertEqual(args[2], "tts-1")       # model default

    def test_cache_hit_avoids_second_api_call(self):
        fake_audio = b"\x4f\x67\x67\x53cached"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            with patch.object(synthesizer, "_call_openai_tts",
                              return_value=fake_audio) as mock_call:
                synthesizer.synthesize("hola")
                synthesizer.synthesize("hola")
                # Second call must come from cache
                self.assertEqual(mock_call.call_count, 1)

    def test_cache_separates_by_voice(self):
        fake_audio_a = b"\x4f\x67\x67\x53nova"
        fake_audio_b = b"\x4f\x67\x67\x53echo"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            with patch.object(synthesizer, "_call_openai_tts",
                              side_effect=[fake_audio_a, fake_audio_b]):
                a = synthesizer.synthesize("hola", voice="nova")
                b = synthesizer.synthesize("hola", voice="echo")
                self.assertNotEqual(a, b)

    def test_truncates_overly_long_text(self):
        long = "Hola. " * 500  # > MAX_TTS_CHARS
        captured = {}

        def fake(text, voice, model):
            captured["text"] = text
            return b"\x00ok"

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            with patch.object(synthesizer, "_call_openai_tts", side_effect=fake):
                synthesizer.synthesize(long)
                self.assertLessEqual(len(captured["text"]),
                                     synthesizer.MAX_TTS_CHARS)

    def test_returns_none_when_api_fails(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            with patch.object(synthesizer, "_call_openai_tts",
                              side_effect=Exception("boom")):
                # Should swallow and return None
                self.assertIsNone(synthesizer.synthesize("hola"))


class TestShouldSpeakGate(unittest.TestCase):
    """The _should_speak gate in TelegramGateway uses these rules."""

    def test_should_speak_when_enabled_and_short(self):
        from vectrax.telegram_gateway import TelegramGateway
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            self.assertTrue(TelegramGateway._should_speak("hola"))

    def test_should_not_speak_long_text(self):
        from vectrax.telegram_gateway import TelegramGateway
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            long = "x" * 2000
            self.assertFalse(TelegramGateway._should_speak(long))

    def test_should_not_speak_when_tts_disabled(self):
        from vectrax.telegram_gateway import TelegramGateway
        with patch.dict(os.environ, {"VECTRAX_TTS_DISABLED": "1"}):
            self.assertFalse(TelegramGateway._should_speak("hola"))

    def test_should_not_speak_empty(self):
        from vectrax.telegram_gateway import TelegramGateway
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            self.assertFalse(TelegramGateway._should_speak(""))
            self.assertFalse(TelegramGateway._should_speak("   "))


if __name__ == "__main__":
    unittest.main()
