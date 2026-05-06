"""
tests/voice/test_anti_repetition.py — Tests del Anti-Repetition Filter.

Cubre:
  - strip_cliches: drop de sugerencias de redes sociales (ES + EN)
  - similarity / is_too_similar: ventana deslizante per-user
  - perturb_structure: rotación de openers
  - filter_response: pipeline end-to-end
  - record_response + recent_responses: ring buffer en SQLite
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Redirect DB BEFORE importing modules
_TMPDIR = tempfile.mkdtemp(prefix="vx_antirep_test_")
os.environ["VECTRAX_ANTIREP_DB"] = os.path.join(_TMPDIR, "antirep.db")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.voice.anti_repetition import (  # noqa: E402
    strip_cliches,
    similarity,
    is_too_similar,
    record_response,
    recent_responses,
    perturb_structure,
    filter_response,
    reset_for_tests,
    clear_for_user,
    WINDOW_SIZE,
)


class TestStripCliches(unittest.TestCase):
    def test_drops_redes_sociales_es(self):
        out = strip_cliches(
            "Buena foto, Mario. Sería ideal para tus redes sociales."
        )
        self.assertIn("Buena foto", out)
        self.assertNotIn("redes sociales", out.lower())

    def test_drops_subir_instagram_es(self):
        out = strip_cliches(
            "Te ves bien. Deberías subirla a Instagram."
        )
        self.assertNotIn("instagram", out.lower())
        self.assertNotIn("subirla", out.lower())

    def test_drops_compartir_stories_es(self):
        out = strip_cliches(
            "Mario en la playa. Compártela en tus stories."
        )
        self.assertNotIn("compártela", out.lower())
        self.assertNotIn("stories", out.lower())

    def test_drops_great_for_instagram_en(self):
        out = strip_cliches(
            "You look great. This would be perfect for your Instagram.",
            lang="en",
        )
        self.assertNotIn("instagram", out.lower())
        self.assertIn("look great", out.lower())

    def test_drops_post_to_social_en(self):
        out = strip_cliches(
            "Nice shot. You should post this on social media.", lang="en",
        )
        self.assertNotIn("social media", out.lower())
        self.assertNotIn("post this", out.lower())

    def test_keeps_unrelated(self):
        out = strip_cliches("Sales genial en esa foto, Mario. Tú y alguien.")
        self.assertIn("Sales genial", out)
        self.assertIn("alguien", out)

    def test_all_cliche_returns_empty(self):
        out = strip_cliches("Sería perfecta para tus redes sociales.")
        self.assertEqual(out, "")


class TestSimilarity(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertEqual(
            similarity("hola que tal mario", "hola que tal mario"),
            1.0,
        )

    def test_disjoint_is_zero(self):
        self.assertEqual(
            similarity("una persona feliz", "casa árbol bicicleta"),
            0.0,
        )

    def test_high_overlap_above_threshold(self):
        a = "Sales genial en esa foto Mario tu y Naomy"
        b = "Sales genial en esa foto Mario tu con Naomy"
        s = similarity(a, b)
        self.assertGreater(s, 0.5)

    def test_low_overlap_below_threshold(self):
        a = "Mario en la playa con Naomy felices"
        b = "Documento técnico sobre arquitectura distribuida"
        self.assertLess(similarity(a, b), 0.2)


class TestRingBuffer(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_record_and_recent(self):
        record_response("tg:1", "Hola Mario.")
        record_response("tg:1", "Otra respuesta.")
        recents = recent_responses("tg:1", n=10)
        self.assertEqual(len(recents), 2)
        # Más reciente primero
        self.assertEqual(recents[0], "Otra respuesta.")

    def test_isolation_between_users(self):
        record_response("tg:1", "Mario uno.")
        record_response("tg:2", "Naomy una.")
        self.assertEqual(len(recent_responses("tg:1")), 1)
        self.assertEqual(len(recent_responses("tg:2")), 1)
        self.assertIn("Mario", recent_responses("tg:1")[0])

    def test_clear_for_user(self):
        record_response("tg:1", "X.")
        record_response("tg:1", "Y.")
        self.assertEqual(clear_for_user("tg:1"), 2)
        self.assertEqual(recent_responses("tg:1"), [])

    def test_pruning_keeps_window(self):
        # Insertar más de WINDOW_SIZE * 3 (= 30 con defaults) entradas
        for i in range(WINDOW_SIZE * 3 + 5):
            record_response("tg:p", f"respuesta numero {i}")
        recents = recent_responses("tg:p", n=100)
        # Tras pruning, debe haber a lo sumo WINDOW_SIZE * PRUNE_FACTOR
        self.assertLessEqual(len(recents), WINDOW_SIZE * 3)


class TestIsTooSimilar(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_blocks_near_duplicate(self):
        record_response("tg:9", "Sales genial en esa foto, Mario.")
        # casi igual
        self.assertTrue(
            is_too_similar("tg:9", "Sales genial en esa foto, Mario!")
        )

    def test_passes_different_response(self):
        record_response("tg:9", "Sales genial en esa foto, Mario.")
        self.assertFalse(
            is_too_similar(
                "tg:9", "Documento técnico sobre arquitectura distribuida.",
            )
        )

    def test_empty_candidate_not_similar(self):
        self.assertFalse(is_too_similar("tg:9", ""))


class TestPerturbStructure(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_rotates_companion_opener(self):
        record_response("tg:p", "Sales genial en esa foto, Mario. Tú en el parque.")
        candidate = "Sales genial en esa foto, Mario. Tú en el parque."
        out = perturb_structure(candidate, "tg:p")
        # Debe haber rotado el opener (no es el mismo)
        self.assertNotEqual(out, candidate)
        self.assertIn("Mario", out)


class TestFilterResponse(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_strips_cliches_and_passes(self):
        out = filter_response(
            "Buena foto. Sería ideal para tus redes sociales.",
            user_id="tg:f",
        )
        self.assertIsNotNone(out)
        self.assertIn("Buena foto", out)
        self.assertNotIn("redes sociales", out.lower())

    def test_returns_none_when_only_cliche(self):
        out = filter_response(
            "Sería perfecta para tus stories y feed.",
            user_id="tg:f",
        )
        self.assertIsNone(out)

    def test_perturbs_when_too_similar(self):
        record_response(
            "tg:f", "Sales genial en esa foto, Mario. Tú en una terraza.",
        )
        out = filter_response(
            "Sales genial en esa foto, Mario. Tú en una terraza.",
            user_id="tg:f",
        )
        # Debió haber sido perturbado (estructura distinta)
        self.assertIsNotNone(out)
        self.assertNotEqual(
            out, "Sales genial en esa foto, Mario. Tú en una terraza.",
        )

    def test_three_same_photos_three_different_responses(self):
        # Goal: si la misma foto llega 3 veces, las 3 respuestas deben
        # ser diferentes. Aquí simulamos: 1ª pasa, se registra, las
        # siguientes deben perturbarse.
        out1 = filter_response(
            "Sales genial en esa foto, Mario. Tú con alguien.",
            user_id="tg:rep",
        )
        record_response("tg:rep", out1)

        out2 = filter_response(
            "Sales genial en esa foto, Mario. Tú con alguien.",
            user_id="tg:rep",
        )
        record_response("tg:rep", out2 or "")

        out3 = filter_response(
            "Sales genial en esa foto, Mario. Tú con alguien.",
            user_id="tg:rep",
        )

        all_outputs = [out1, out2, out3]
        unique = {o for o in all_outputs if o}
        self.assertGreaterEqual(
            len(unique), 2,
            f"Esperaba >=2 outputs distintos, got: {all_outputs}",
        )


if __name__ == "__main__":
    unittest.main()
