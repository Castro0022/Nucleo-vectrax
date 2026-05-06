"""
tests/voice/test_dispatch_dedup.py — Tests del dedup en dispatch_audio_async.

Garantía estructural: el bug "el audio anterior se repite" se cierra
con dedup por (chat_id, hash(text)) en una ventana corta. Estos tests
verifican que el segundo dispatch idéntico se SKIP.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.voice import telegram_dispatch as td


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"ok": True}
        return r


class TestDedup(unittest.TestCase):
    def setUp(self):
        td.reset_dedup_for_tests()

    def test_same_text_same_chat_dedupes(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test"},
            clear=False,
        ):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            os.environ.pop("VECTRAX_AUDIO_DISABLED", None)
            os.environ["VECTRAX_AUDIO_DEDUP_WINDOW_S"] = "60"

            with patch.object(
                td, "_synth_and_send",
                wraps=lambda *a, **k: None,
            ) as mock_synth:
                td.dispatch_audio_async(
                    chat_id=123, text="hola Mario",
                    http_client=FakeHttp(),
                )
                td.dispatch_audio_async(
                    chat_id=123, text="hola Mario",
                    http_client=FakeHttp(),
                )
                # Solo el primero debe haber submitted al pool
                # (el segundo se duplica y se skipea).
                # _synth_and_send corre en el pool, así que damos
                # un instante para que la primera tarea arranque.
                time.sleep(0.05)
                self.assertEqual(mock_synth.call_count, 1)

    def test_different_text_same_chat_passes(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test"},
            clear=False,
        ):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            os.environ.pop("VECTRAX_AUDIO_DISABLED", None)
            with patch.object(
                td, "_synth_and_send",
                wraps=lambda *a, **k: None,
            ) as mock_synth:
                td.dispatch_audio_async(
                    chat_id=123, text="hola",
                    http_client=FakeHttp(),
                )
                td.dispatch_audio_async(
                    chat_id=123, text="adiós",
                    http_client=FakeHttp(),
                )
                time.sleep(0.05)
                self.assertEqual(mock_synth.call_count, 2)

    def test_same_text_different_chat_passes(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test"},
            clear=False,
        ):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            os.environ.pop("VECTRAX_AUDIO_DISABLED", None)
            with patch.object(
                td, "_synth_and_send",
                wraps=lambda *a, **k: None,
            ) as mock_synth:
                td.dispatch_audio_async(
                    chat_id=123, text="hola",
                    http_client=FakeHttp(),
                )
                td.dispatch_audio_async(
                    chat_id=456, text="hola",
                    http_client=FakeHttp(),
                )
                time.sleep(0.05)
                self.assertEqual(mock_synth.call_count, 2)

    def test_window_zero_disables_dedup(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test",
             "VECTRAX_AUDIO_DEDUP_WINDOW_S": "0"},
            clear=False,
        ):
            os.environ.pop("VECTRAX_TTS_DISABLED", None)
            os.environ.pop("VECTRAX_AUDIO_DISABLED", None)
            with patch.object(
                td, "_synth_and_send",
                wraps=lambda *a, **k: None,
            ) as mock_synth:
                td.dispatch_audio_async(
                    chat_id=123, text="hola",
                    http_client=FakeHttp(),
                )
                td.dispatch_audio_async(
                    chat_id=123, text="hola",
                    http_client=FakeHttp(),
                )
                time.sleep(0.05)
                self.assertEqual(mock_synth.call_count, 2)


class TestKillSwitch(unittest.TestCase):
    def setUp(self):
        td.reset_dedup_for_tests()

    def test_audio_disabled_blocks_all(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test", "VECTRAX_AUDIO_DISABLED": "1"},
            clear=False,
        ):
            with patch.object(
                td, "_synth_and_send",
                wraps=lambda *a, **k: None,
            ) as mock_synth:
                td.dispatch_audio_async(
                    chat_id=123, text="hola",
                    http_client=FakeHttp(),
                )
                time.sleep(0.05)
                self.assertEqual(mock_synth.call_count, 0)
                self.assertFalse(td.should_speak("hola"))


class TestPerChatLock(unittest.TestCase):
    def setUp(self):
        td.reset_dedup_for_tests()

    def test_different_chats_get_different_locks(self):
        l1 = td._get_chat_lock(111)
        l2 = td._get_chat_lock(222)
        self.assertIsNot(l1, l2)

    def test_same_chat_returns_same_lock(self):
        l1 = td._get_chat_lock(111)
        l2 = td._get_chat_lock(111)
        self.assertIs(l1, l2)


if __name__ == "__main__":
    unittest.main()
