"""
tests/gravity/test_gravity_engine.py — 4 tests obligatorios + cobertura
adicional de mass_tracker y aislamiento RBAC.

Tests obligatorios (del spec):
  1. Crear memoria para User_A; User_B recibe lista vacía.
  2. 'Hola' no dispara llamada a query() (gate cierra antes).
  3. '¿Recuerdas la foto de Naomy?' → top score = la interacción de Naomy.
  4. Dos audios seguidos producen paths distintos en filesystem.

Tests adicionales:
  5. cosine_similarity numérico.
  6. Mass tracker: vision +5, persona +8, emocion +10.
  7. Universe.update_node_mass se invoca con el total nuevo.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Redirect store DB BEFORE importing modules — evita tocar ~/.vectrax/
_TMPDIR = tempfile.mkdtemp(prefix="vx_gravity_test_")
os.environ["VECTRAX_GRAVITY_DB"] = os.path.join(_TMPDIR, "gravity.db")
os.environ["VECTRAX_AUDIO_CACHE_DIR"] = os.path.join(_TMPDIR, "audio")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.gravity.vector_store import (  # noqa: E402
    SQLiteVectorStore, DeepMemoryRecord, cosine_similarity,
)
from core.gravity.gravity_engine import (  # noqa: E402
    should_use_deep_memory, DeepMemoryRouter,
)
from core.gravity.mass_tracker import (  # noqa: E402
    MassKind, MassTracker, add_mass, MASS_VALUES,
)
from core.voice.synthesizer import (  # noqa: E402
    get_unique_output_path,
)


# ===========================================================================
# Embeddings deterministas (sin red) para que los tests sean reproducibles
# ===========================================================================

def fake_embed(text: str, dim: int = 8) -> list:
    """Embedding determinista basado en la presencia de palabras-ancla.

    Cada palabra-ancla ocupa una dimensión fija. Esto da control total
    sobre similitudes para verificar el ranking del store sin pegarle
    al API real de embeddings.

    Anchors:
      0 -> 'naomy'
      1 -> 'foto'
      2 -> 'playa'
      3 -> 'ayer'
      4 -> 'mario'
      5 -> 'parque'
      6 -> 'cafe'
      7 -> 'libro'
    """
    anchors = ["naomy", "foto", "playa", "ayer", "mario",
               "parque", "cafe", "libro"]
    v = [0.0] * dim
    if not text:
        return v
    low = text.lower()
    for i, a in enumerate(anchors[:dim]):
        if a in low:
            v[i] = 1.0
    # Si no hay ningún anchor, devolvemos un vector neutro pequeño en
    # la última posición para evitar el [0,0,...,0] (cosine indefinido).
    if all(x == 0.0 for x in v):
        v[-1] = 0.001
    return v


# ===========================================================================
# Test 1 — Aislamiento por user_id (RBAC)
# ===========================================================================

class TestUserIsolation(unittest.TestCase):
    """Memoria de User_A NUNCA debe ser visible a User_B."""

    def setUp(self):
        self.store = SQLiteVectorStore(
            db_path=os.path.join(_TMPDIR, "isolation.db"),
        )
        self.store.reset_for_tests()

    def tearDown(self):
        self.store.close()

    def test_user_b_does_not_see_user_a_memory(self):
        # User_A graba una memoria sobre Naomy
        rec = DeepMemoryRecord(
            user_id="tg:user_a",
            raw_text="Foto de Naomy en la playa",
            embedding=fake_embed("naomy foto playa"),
            summary="Naomy beach",
            tags=["naomy", "playa"],
        )
        self.store.upsert(rec)

        # User_B busca la misma cosa: NO debe encontrar nada
        results_b = self.store.query(
            user_id="tg:user_b",
            query_embedding=fake_embed("naomy foto playa"),
            limit=5,
        )
        self.assertEqual(results_b, [])

        # User_A SÍ debe encontrarla
        results_a = self.store.query(
            user_id="tg:user_a",
            query_embedding=fake_embed("naomy foto playa"),
            limit=5,
        )
        self.assertEqual(len(results_a), 1)
        self.assertEqual(results_a[0]["text"], "Foto de Naomy en la playa")


# ===========================================================================
# Test 2 — Gate cierra para mensajes triviales ('Hola')
# ===========================================================================

class TestGate(unittest.TestCase):
    """should_use_deep_memory(message) → bool."""

    def test_hola_does_not_trigger_query(self):
        # Mock de embed_fn y store: si el gate cierra, no se invocan.
        embed_mock = MagicMock(return_value=fake_embed("hola"))
        store_mock = MagicMock()
        store_mock.query = MagicMock(return_value=[])

        router = DeepMemoryRouter(store=store_mock, embed_fn=embed_mock)
        results = router.route(
            user_id="tg:user_a", message="Hola", limit=5,
        )

        self.assertEqual(results, [])
        # Lo importante: nunca tocó el embed ni la DB
        embed_mock.assert_not_called()
        store_mock.query.assert_not_called()

    def test_recuerdas_naomy_does_trigger(self):
        embed_mock = MagicMock(return_value=fake_embed("naomy foto"))
        store_mock = MagicMock()
        store_mock.query = MagicMock(return_value=[
            {"id": "x", "text": "Naomy en la playa",
             "summary": "", "tags": [], "mass": 0, "ts": 0, "score": 0.9},
        ])

        router = DeepMemoryRouter(store=store_mock, embed_fn=embed_mock)
        results = router.route(
            user_id="tg:user_a",
            message="¿Recuerdas la foto de Naomy?",
            limit=5,
        )

        embed_mock.assert_called_once()
        store_mock.query.assert_called_once()
        self.assertEqual(len(results), 1)

    def test_basic_signals_unit(self):
        # cubre algunos triggers individualmente
        self.assertFalse(should_use_deep_memory(""))
        self.assertFalse(should_use_deep_memory("Hola"))
        self.assertFalse(should_use_deep_memory("¿qué tal?"))
        self.assertTrue(should_use_deep_memory("¿te acuerdas de eso?"))
        self.assertTrue(should_use_deep_memory("ayer fui al gym"))
        self.assertTrue(should_use_deep_memory("Do you remember that day?"))
        # nombre propio plausible
        self.assertTrue(should_use_deep_memory("Naomy estaba ahí"))
        # known_names del user (face_memory)
        self.assertTrue(should_use_deep_memory(
            "vi a naomy en la calle",
            known_names=["Naomy", "Carlos"],
        ))


# ===========================================================================
# Test 3 — Ranking: '¿Recuerdas la foto de Naomy?' → top = Naomy
# ===========================================================================

class TestRanking(unittest.TestCase):
    """El record más similar al query gana el top score."""

    def setUp(self):
        self.store = SQLiteVectorStore(
            db_path=os.path.join(_TMPDIR, "ranking.db"),
        )
        self.store.reset_for_tests()
        uid = "tg:user_a"
        # Cargamos varios records con embeddings distintos
        records = [
            DeepMemoryRecord(
                user_id=uid,
                raw_text="Foto de Naomy en la playa",
                embedding=fake_embed("naomy foto playa"),
                tags=["naomy", "foto"],
            ),
            DeepMemoryRecord(
                user_id=uid,
                raw_text="Café con Mario",
                embedding=fake_embed("cafe mario"),
                tags=["mario", "cafe"],
            ),
            DeepMemoryRecord(
                user_id=uid,
                raw_text="Libro nuevo en el parque",
                embedding=fake_embed("libro parque"),
                tags=["libro"],
            ),
        ]
        for r in records:
            self.store.upsert(r)

    def tearDown(self):
        self.store.close()

    def test_top_score_corresponds_to_naomy_interaction(self):
        # Query: ¿Recuerdas la foto de Naomy?
        q_emb = fake_embed("recuerdas la foto de Naomy")
        results = self.store.query(
            user_id="tg:user_a", query_embedding=q_emb, limit=3,
        )
        self.assertGreater(len(results), 0)
        top = results[0]
        self.assertIn("Naomy", top["text"])
        # Debe haber score positivo (hay overlap real)
        self.assertGreater(top["score"], 0.0)
        # Y debe ser estrictamente mayor que los otros (orden estable)
        for r in results[1:]:
            self.assertGreaterEqual(top["score"], r["score"])


# ===========================================================================
# Test 4 — Audios distintos: paths únicos en filesystem
# ===========================================================================

class TestAudioPathUniqueness(unittest.TestCase):
    """Dos audios seguidos producen paths distintos."""

    def test_two_back_to_back_paths_differ(self):
        p1 = get_unique_output_path("tg:2030762343")
        p2 = get_unique_output_path("tg:2030762343")
        self.assertNotEqual(p1, p2)
        self.assertTrue(p1.endswith(".mp3"))
        self.assertTrue(p2.endswith(".mp3"))
        # Ambos deben llevar el user_id sanitizado
        self.assertIn("tg_2030762343", p1)
        self.assertIn("tg_2030762343", p2)

    def test_concurrency_burst_all_unique(self):
        # 50 paths consecutivos: deben ser todos distintos
        paths = {get_unique_output_path("tg:u") for _ in range(50)}
        self.assertEqual(len(paths), 50)


# ===========================================================================
# Test 5 — cosine_similarity numérico
# ===========================================================================

class TestCosine(unittest.TestCase):
    def test_orthogonal_is_zero(self):
        self.assertAlmostEqual(
            cosine_similarity([1, 0, 0], [0, 1, 0]), 0.0,
        )

    def test_identical_is_one(self):
        self.assertAlmostEqual(
            cosine_similarity([1, 2, 3], [1, 2, 3]), 1.0,
        )

    def test_zero_vector_returns_zero(self):
        self.assertEqual(cosine_similarity([0, 0, 0], [1, 2, 3]), 0.0)

    def test_mismatched_dim_returns_zero(self):
        self.assertEqual(cosine_similarity([1, 2], [1, 2, 3]), 0.0)


# ===========================================================================
# Test 6 — Mass tracker (Vision +5, Persona +8, Emoción +10)
# ===========================================================================

class TestMassTracker(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteVectorStore(
            db_path=os.path.join(_TMPDIR, "mass.db"),
        )
        self.store.reset_for_tests()
        self.rec = DeepMemoryRecord(
            user_id="tg:u", raw_text="x",
            embedding=fake_embed("naomy"),
        )
        self.store.upsert(self.rec)

    def tearDown(self):
        self.store.close()

    def test_mass_values_are_correct(self):
        self.assertEqual(MASS_VALUES[MassKind.VISION], 5.0)
        self.assertEqual(MASS_VALUES[MassKind.PERSONA], 8.0)
        self.assertEqual(MASS_VALUES[MassKind.EMOCION], 10.0)

    def test_mass_accumulates(self):
        tracker = MassTracker(self.store)
        t1 = tracker.add(self.rec.id, MassKind.VISION)    # +5
        t2 = tracker.add(self.rec.id, MassKind.PERSONA)   # +8
        t3 = tracker.add(self.rec.id, MassKind.EMOCION)   # +10
        self.assertAlmostEqual(t1, 5.0)
        self.assertAlmostEqual(t2, 13.0)
        self.assertAlmostEqual(t3, 23.0)
        self.assertAlmostEqual(tracker.get(self.rec.id), 23.0)

    def test_universe_sync_called(self):
        universe = MagicMock()
        tracker = MassTracker(self.store, universe=universe)
        tracker.add(self.rec.id, MassKind.PERSONA)
        universe.update_node_mass.assert_called_once_with(
            self.rec.id, 8.0,
        )

    def test_unknown_kind_is_noop(self):
        tracker = MassTracker(self.store)
        before = tracker.get(self.rec.id)
        result = tracker.add(self.rec.id, "unknown_kind")
        # No crash y la masa no cambia
        self.assertEqual(result, before)


if __name__ == "__main__":
    unittest.main()
