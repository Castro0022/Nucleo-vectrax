"""
tests/test_personal_memory_retrieval.py
===========================================
PARTE 3 del contrato de memoria conversacional multiusuario (2026-09-20):
recuperación universal de memoria personal.

Cubre:
  1. Detección de rango temporal (relativo y explícito, ES/EN).
  2. Detección de pista de categoría (decisión/proyecto/relación/etc.).
  3. Recuperación por fecha, keyword, categoría y combinaciones, usando
     el ledger canónico (PARTE 1) + star_provenance (PARTE 2).
  4. Abstención honesta cuando no hay evidencia.
  5. Traza interna completa y correcta.
  6. Aislamiento multiusuario.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import vectrax.db as db  # noqa: E402
import vectrax.core_memory as core_memory  # noqa: E402
import vectrax.embeddings as embeddings  # noqa: E402
from core.memory import personal_memory_retrieval as pmr  # noqa: E402
from core.memory.conversation_ledger import record_user_message  # noqa: E402
from core.memory.star_deriver import derive_and_store_star  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Parseo de rango temporal -- puro, sin DB
# ---------------------------------------------------------------------------

class TestParseTemporalRange(unittest.TestCase):

    def setUp(self):
        # Fecha de referencia fija para pruebas deterministas: martes.
        self.now = datetime(2026, 9, 22, 15, 0, 0).timestamp()

    def test_hoy(self):
        rng = pmr.parse_temporal_range("¿qué hablamos hoy?", now=self.now)
        self.assertIsNotNone(rng)
        start = datetime.fromtimestamp(rng[0])
        self.assertEqual((start.year, start.month, start.day), (2026, 9, 22))

    def test_ayer(self):
        rng = pmr.parse_temporal_range("¿qué hablamos ayer?", now=self.now)
        self.assertIsNotNone(rng)
        start = datetime.fromtimestamp(rng[0])
        self.assertEqual((start.year, start.month, start.day), (2026, 9, 21))

    def test_semana_pasada(self):
        rng = pmr.parse_temporal_range("qué decidimos la semana pasada", now=self.now)
        self.assertIsNotNone(rng)
        start, end = datetime.fromtimestamp(rng[0]), datetime.fromtimestamp(rng[1])
        self.assertLess(start, end)
        self.assertLess(end.timestamp(), self.now)

    def test_explicit_spanish_date(self):
        rng = pmr.parse_temporal_range("qué hablamos el 12 de septiembre", now=self.now)
        self.assertIsNotNone(rng)
        start = datetime.fromtimestamp(rng[0])
        self.assertEqual((start.month, start.day), (9, 12))

    def test_no_temporal_marker(self):
        self.assertIsNone(pmr.parse_temporal_range("qué opinas del proyecto", now=self.now))

    def test_english_yesterday(self):
        rng = pmr.parse_temporal_range("what did we talk about yesterday?", now=self.now)
        self.assertIsNotNone(rng)


class TestDetectCategoryHint(unittest.TestCase):
    def test_decision(self):
        self.assertEqual(pmr.detect_category_hint("¿qué decidimos sobre el hosting?"), "decision")

    def test_project(self):
        self.assertEqual(pmr.detect_category_hint("cómo va el proyecto"), "project")

    def test_relation(self):
        self.assertEqual(pmr.detect_category_hint("quién es mi socia"), "relation")

    def test_none_generic(self):
        self.assertIsNone(pmr.detect_category_hint("qué me preocupa últimamente"))


# ---------------------------------------------------------------------------
# 2. Recuperación end-to-end con ledger + star_provenance aislados
# ---------------------------------------------------------------------------

class _IsolatedMixin:
    """Nota: el ledger conversacional (`core.memory.conversation_ledger`) ya
    queda aislado automáticamente por el fixture autouse `_hermetic_base`
    en `tests/conftest.py` (redirige `_DB_PATH` a un vault temporal por
    test y resetea el singleton `_repo`) -- no se gestiona aquí de nuevo
    para no divergir de esa única fuente de aislamiento."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vx_pmr_test_")
        self._orig_dir = db.DB_DIR
        self._orig_path = db.DB_PATH
        db.DB_DIR = Path(self._tmpdir)
        db.DB_PATH = Path(self._tmpdir) / "vectrax.db"
        db.init_db()

        self._orig_core_memory_db_path = core_memory._DB_PATH
        core_memory._DB_PATH = os.path.join(self._tmpdir, "user_memory.db")

        self._fixed_vec = __import__("numpy").array([1.0, 0.0], dtype="float32")
        self._embed_patch = patch.object(embeddings, "embed", return_value=self._fixed_vec)
        self._decode_patch = patch.object(embeddings, "decode_embedding", return_value=self._fixed_vec)
        self._embed_patch.start()
        self._decode_patch.start()
        # Síntesis determinista -- sin LLM real en tests.
        self._llm_patch = patch("vectrax.resolver._interpret_with_llm", return_value="")
        self._llm_patch.start()

    def tearDown(self):
        self._llm_patch.stop()
        self._embed_patch.stop()
        self._decode_patch.stop()
        db.DB_DIR = self._orig_dir
        db.DB_PATH = self._orig_path
        core_memory._DB_PATH = self._orig_core_memory_db_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestRetrievePersonalMemory(_IsolatedMixin, unittest.TestCase):

    def test_abstains_honestly_with_no_evidence(self):
        result = pmr.retrieve_personal_memory(
            "¿qué hablamos sobre el lanzamiento del cohete?",
            channel="user", owner="pmr_user_empty",
        )
        self.assertFalse(result.sufficient)
        self.assertTrue(result.trace.abstained)
        self.assertEqual(result.trace.events_retrieved, 0)
        self.assertIn(
            result.sovereign_answer,
            ("No tengo información sobre eso en mi memoria.",
             "I don't have information about that in my memory."),
        )

    def test_retrieves_by_keyword_from_ledger(self):
        record_user_message(
            user_id="pmr_user_kw", content="estoy trabajando en el rediseño del dashboard",
            channel="user", source="test",
        )
        result = pmr.retrieve_personal_memory(
            "qué dijiste del dashboard", channel="user", owner="pmr_user_kw",
        )
        self.assertTrue(result.sufficient)
        self.assertGreaterEqual(result.trace.events_retrieved, 1)
        self.assertIn("ledger", result.trace.sources_used)

    def test_retrieves_by_decision_category_from_star_provenance(self):
        derive_and_store_star(
            text="decidí cambiar de proveedor de hosting",
            channel="user", owner="pmr_user_decision",
        )
        result = pmr.retrieve_personal_memory(
            "¿qué decidimos sobre el hosting?", channel="user", owner="pmr_user_decision",
        )
        self.assertTrue(result.sufficient)
        self.assertEqual(result.trace.category_hint, "decision")
        self.assertIn("star_provenance", result.trace.sources_used)

    def test_temporal_range_filters_out_of_range_events(self):
        # Mensaje de "ahora" (dentro del rango 'hoy') debe encontrarse;
        # uno con timestamp de hace 10 días, filtrado por rango 'hoy'.
        now = time.time()
        old_ts = now - (10 * 86400)
        record_user_message(
            user_id="pmr_user_time", content="hablamos del proyecto viejo",
            channel="user", source="test",
        )
        # Insertar directamente un evento viejo vía el repo (ya aislado por
        # el fixture autouse) para controlar el timestamp.
        from core.memory.conversation_ledger import ConversationEvent, get_conversation_ledger
        get_conversation_ledger().append(ConversationEvent(
            user_id="pmr_user_time", role="user",
            content="hablamos del proyecto viejo hace mucho", timestamp=old_ts,
            message_id="old-msg-1",
        ))
        result = pmr.retrieve_personal_memory(
            "qué hablamos hoy del proyecto", channel="user", owner="pmr_user_time",
        )
        self.assertIsNotNone(result.trace.time_range)
        # Todos los fragmentos del ledger recuperados deben caer DENTRO del rango.
        since, until = result.trace.time_range
        for frag in result.fragments:
            if frag.source == "ledger":
                self.assertGreaterEqual(frag.timestamp, since)
                self.assertLessEqual(frag.timestamp, until)

    def test_multiuser_isolation(self):
        record_user_message(
            user_id="pmr_alpha", content="mi proyecto secreto es el cohete",
            channel="user", source="test",
        )
        record_user_message(
            user_id="pmr_beta", content="mi proyecto secreto es el submarino",
            channel="user", source="test",
        )
        alpha_result = pmr.retrieve_personal_memory(
            "qué dijiste de mi proyecto secreto", channel="user", owner="pmr_alpha",
        )
        beta_result = pmr.retrieve_personal_memory(
            "qué dijiste de mi proyecto secreto", channel="user", owner="pmr_beta",
        )
        alpha_contents = " ".join(f.content for f in alpha_result.fragments)
        beta_contents = " ".join(f.content for f in beta_result.fragments)
        self.assertIn("cohete", alpha_contents)
        self.assertNotIn("submarino", alpha_contents)
        self.assertIn("submarino", beta_contents)
        self.assertNotIn("cohete", beta_contents)

    def test_trace_reports_confidence_and_sources(self):
        record_user_message(
            user_id="pmr_user_trace", content="mi objetivo es lanzar el producto en enero",
            channel="user", source="test",
        )
        result = pmr.retrieve_personal_memory(
            "cuál es mi objetivo", channel="user", owner="pmr_user_trace",
        )
        self.assertTrue(result.sufficient)
        self.assertGreater(result.trace.confidence, 0.0)
        self.assertTrue(result.trace.memory_consulted)
        self.assertFalse(result.trace.abstained)


if __name__ == "__main__":
    unittest.main()
