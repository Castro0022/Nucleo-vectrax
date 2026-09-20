"""
tests/test_star_deriver.py
==============================
PARTE 2 del contrato de memoria conversacional multiusuario (2026-09-20):
derivación selectiva de estrellas personales con procedencia.

Cubre:
  1. Mensajes insignificantes (saludos, confirmaciones, preguntas) NUNCA
     crean una estrella ni un registro de procedencia.
  2. Mensajes significativos (identidad, relación, preferencia, proyecto,
     decisión, compromiso, objetivo, hecho, cambio de estado) SÍ crean una
     estrella con procedencia completa (categoría, entidad, afirmación
     normalizada, fragmentos literales, confianza, estado).
  3. Repetir la misma afirmación refuerza el registro existente (no
     duplica procedencia).
  4. Una corrección explícita marca el registro anterior como
     'superseded' (nunca se borra) y crea uno nuevo 'active'.
  5. Un conflicto genuino (sin marcador de corrección) marca AMBOS
     registros como 'contradicted' -- nunca se elige un ganador en
     silencio.
  6. explicit_user_intent=True (comando /guardar) nunca descarta un
     mensaje, incluso si no matchea ninguna categoría.
  7. Aislamiento multiusuario: cada usuario tiene su propia procedencia.
  8. El vocabulario de detección es genérico -- no contiene nombres
     propios hardcodeados.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import vectrax.db as db  # noqa: E402
import vectrax.core_memory as core_memory  # noqa: E402
import vectrax.embeddings as embeddings  # noqa: E402
from core.memory import star_deriver  # noqa: E402


class _IsolatedDBMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vx_star_deriver_test_")
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

    def tearDown(self):
        self._embed_patch.stop()
        self._decode_patch.stop()
        db.DB_DIR = self._orig_dir
        db.DB_PATH = self._orig_path
        core_memory._DB_PATH = self._orig_core_memory_db_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Detección genérica -- sin nombres propios hardcodeados
# ---------------------------------------------------------------------------

class TestDetectSignificantVocabularyIsGeneric(unittest.TestCase):

    def test_no_hardcoded_names_in_patterns(self):
        import re as _re
        for _cat, pattern, _grp in star_deriver._CATEGORY_PATTERNS:
            blob = pattern.pattern.lower()
            for forbidden in ("mario", "dani", "joycelyn", "bravo"):
                self.assertIsNone(
                    _re.search(r"\b" + forbidden + r"\b", blob),
                    f"nombre propio hardcodeado {forbidden!r} encontrado en el patron",
                )

    def test_greetings_and_confirmations_are_not_significant(self):
        for text in (
            "hola", "Hola!", "gracias", "ok", "vale", "perfecto", "genial",
            "sí", "no", "adiós", "jaja", "hey", "thanks", "okay",
        ):
            with self.subTest(text=text):
                self.assertIsNone(star_deriver.detect_significant(text))

    def test_questions_are_not_significant(self):
        for text in (
            "¿Quién es mi novia?", "qué hablamos ayer?", "cómo estás?",
            "what is my name?", "vivo en Madrid?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(star_deriver.detect_significant(text))

    def test_short_noise_is_not_significant(self):
        self.assertIsNone(star_deriver.detect_significant("ja"))
        self.assertIsNone(star_deriver.detect_significant(""))
        self.assertIsNone(star_deriver.detect_significant("   "))


class TestDetectSignificantCategories(unittest.TestCase):

    def test_identity(self):
        d = star_deriver.detect_significant("Me llamo Roberto")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "identity")

    def test_relation(self):
        d = star_deriver.detect_significant("mi socia es Carla")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "relation")
        self.assertEqual(d.entity, "socia")

    def test_preference(self):
        d = star_deriver.detect_significant("me gusta el café por la mañana")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "preference")

    def test_project(self):
        d = star_deriver.detect_significant("estoy trabajando en un nuevo dashboard")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "project")

    def test_decision(self):
        d = star_deriver.detect_significant("decidí cambiar de proveedor de hosting")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "decision")

    def test_commitment(self):
        d = star_deriver.detect_significant("prometo entregar el reporte el viernes")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "commitment")

    def test_goal(self):
        d = star_deriver.detect_significant("mi objetivo es lanzar el producto en enero")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "goal")

    def test_fact(self):
        d = star_deriver.detect_significant("vivo en Barcelona")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "fact")

    def test_state_change(self):
        d = star_deriver.detect_significant("ya no trabajo en esa empresa")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "state_change")

    def test_english_preference(self):
        d = star_deriver.detect_significant("I love hiking on weekends")
        self.assertIsNotNone(d)
        self.assertEqual(d.category, "preference")


# ---------------------------------------------------------------------------
# 2. derive_and_store_star() -- creación selectiva + procedencia
# ---------------------------------------------------------------------------

class TestDeriveAndStoreStar(_IsolatedDBMixin, unittest.TestCase):

    def test_insignificant_message_creates_no_star_and_no_provenance(self):
        star = star_deriver.derive_and_store_star(
            text="hola, gracias", channel="user", owner="star_user_1",
        )
        self.assertIsNone(star)
        counts = db.get_counts()
        self.assertEqual(counts["stars"], 0)

    def test_significant_message_creates_star_with_provenance(self):
        star = star_deriver.derive_and_store_star(
            text="vivo en Barcelona", channel="user", owner="star_user_2",
            source_event_id="evt-1",
        )
        self.assertIsNotNone(star)
        prov_list = db.get_star_provenance_by_star(star.id)
        self.assertEqual(len(prov_list), 1)
        prov = prov_list[0]
        self.assertEqual(prov["category"], "fact")
        self.assertEqual(prov["status"], "active")
        self.assertEqual(prov["owner"], "star_user_2")
        self.assertIn("evt-1", prov["source_event_ids"])
        self.assertIn("vivo en Barcelona", prov["literal_fragments"])
        self.assertGreater(prov["confidence"], 0.0)

    def test_repeated_same_assertion_reinforces_not_duplicates(self):
        star_deriver.derive_and_store_star(
            text="mi socia es Carla", channel="user", owner="star_user_3",
            source_event_id="evt-a",
        )
        star_deriver.derive_and_store_star(
            text="mi socia es Carla", channel="user", owner="star_user_3",
            source_event_id="evt-b",
        )
        prov_rows = db.list_star_provenance(
            tenant_id="default", channel="user", owner="star_user_3",
        )
        self.assertEqual(len(prov_rows), 1, "no debe duplicar procedencia para la misma afirmación")
        self.assertIn("evt-a", prov_rows[0]["source_event_ids"])
        self.assertIn("evt-b", prov_rows[0]["source_event_ids"])
        self.assertEqual(len(prov_rows[0]["literal_fragments"]), 2)

    def test_explicit_correction_marks_previous_as_superseded(self):
        star_deriver.derive_and_store_star(
            text="vivo en Madrid", channel="user", owner="star_user_4",
        )
        star_deriver.derive_and_store_star(
            text="ya no vivo en Madrid, ahora vivo en Lisboa",
            channel="user", owner="star_user_4",
        )
        prov_rows = db.list_star_provenance(
            tenant_id="default", channel="user", owner="star_user_4",
        )
        statuses = sorted(r["status"] for r in prov_rows)
        self.assertIn("superseded", statuses)
        self.assertIn("active", statuses)
        active = [r for r in prov_rows if r["status"] == "active"][0]
        superseded = [r for r in prov_rows if r["status"] == "superseded"][0]
        self.assertEqual(active["previous_star_id"], superseded["star_id"])

    def test_genuine_conflict_without_correction_marks_both_contradicted(self):
        star_deriver.derive_and_store_star(
            text="mi jefe es Roberto", channel="user", owner="star_user_5",
        )
        # Segunda afirmación DISTINTA para la MISMA entidad+categoría, sin
        # ningún marcador de corrección -- conflicto genuino.
        star_deriver.derive_and_store_star(
            text="mi jefe es Fernando", channel="user", owner="star_user_5",
        )
        prov_rows = db.list_star_provenance(
            tenant_id="default", channel="user", owner="star_user_5",
        )
        self.assertEqual(len(prov_rows), 2)
        statuses = sorted(r["status"] for r in prov_rows)
        self.assertEqual(statuses, ["contradicted", "contradicted"])

    def test_explicit_user_intent_never_discards_message(self):
        star = star_deriver.derive_and_store_star(
            text="apunta esto para después", channel="user", owner="star_user_6",
            explicit_user_intent=True,
        )
        self.assertIsNotNone(star)
        prov_list = db.get_star_provenance_by_star(star.id)
        self.assertEqual(len(prov_list), 1)
        self.assertEqual(prov_list[0]["category"], "fact")

    def test_multiuser_isolation_of_provenance(self):
        star_deriver.derive_and_store_star(
            text="vivo en Bogotá", channel="user", owner="star_user_alpha",
        )
        star_deriver.derive_and_store_star(
            text="vivo en Lima", channel="user", owner="star_user_beta",
        )
        alpha_rows = db.list_star_provenance(
            tenant_id="default", channel="user", owner="star_user_alpha",
        )
        beta_rows = db.list_star_provenance(
            tenant_id="default", channel="user", owner="star_user_beta",
        )
        self.assertEqual(len(alpha_rows), 1)
        self.assertEqual(len(beta_rows), 1)
        self.assertIn("bogot", alpha_rows[0]["normalized_assertion"])
        self.assertIn("lima", beta_rows[0]["normalized_assertion"])

    def test_never_deletes_provenance_rows_on_correction(self):
        star_deriver.derive_and_store_star(
            text="vivo en Madrid", channel="user", owner="star_user_7",
        )
        star_deriver.derive_and_store_star(
            text="ya no vivo en Madrid, ahora vivo en Lisboa",
            channel="user", owner="star_user_7",
        )
        prov_rows = db.list_star_provenance(
            tenant_id="default", channel="user", owner="star_user_7",
        )
        # Ambos registros siguen existiendo -- ninguno se borró.
        self.assertEqual(len(prov_rows), 2)


if __name__ == "__main__":
    unittest.main()
