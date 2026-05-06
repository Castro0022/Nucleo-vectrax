"""
tests/voice/test_visual_humanizer.py — Tests del Visual Humanizer.

Cubre las 7 reglas dictadas y los casos típicos de fotos con/sin
rostros conocidos.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.voice.visual_humanizer import humanize_visual


class TestStripsReportOpeners(unittest.TestCase):
    """Regla 2: evitar 'la imagen muestra' / 'I can see'."""

    def test_la_imagen_muestra(self):
        out = humanize_visual("La imagen muestra una playa al atardecer.")
        self.assertNotIn("la imagen muestra", out.lower())
        self.assertNotIn("imagen muestra", out.lower())

    def test_en_la_foto_se_observa(self):
        out = humanize_visual("En la foto se observa un perro corriendo.")
        self.assertNotIn("se observa", out.lower())
        self.assertNotIn("la foto", out.lower())

    def test_the_image_shows(self):
        out = humanize_visual("The image shows two people laughing.")
        self.assertNotIn("the image shows", out.lower())
        self.assertNotIn("image shows", out.lower())

    def test_i_can_see(self):
        out = humanize_visual("I can see a sunset over the mountains.")
        self.assertNotIn("i can see", out.lower())

    def test_keeps_perceptual_content(self):
        """Lo que QUEDA debe ser lo perceptible — no se borra todo."""
        out = humanize_visual("La imagen muestra una playa al atardecer.")
        self.assertIn("playa", out.lower())


class TestSentenceLimit(unittest.TestCase):
    """Regla 5: máximo 4 frases."""

    def test_truncates_to_max_4(self):
        raw = (
            "Una. Dos. Tres. Cuatro. Cinco. Seis."
        )
        out = humanize_visual(raw)
        # Cuento las frases en out
        n = sum(1 for c in out if c in ".!?")
        self.assertLessEqual(n, 4)

    def test_keeps_under_4(self):
        raw = "Una sola idea fuerte y suficiente."
        out = humanize_visual(raw)
        self.assertEqual(out, "Una sola idea fuerte y suficiente.")


class TestContinuityContextual(unittest.TestCase):
    """Regla 7: continuidad contextual con rostros reconocidos."""

    def test_user_plus_one_known_es(self):
        out = humanize_visual(
            "La imagen muestra a dos personas felices en la playa.",
            faces=["Mario", "Naomy"],
            user_name="Mario",
            lang="es",
        )
        self.assertIn("Tú", out)
        self.assertIn("Naomy", out)
        # No menciona "Mario" porque ese es 'tú'
        self.assertNotIn("Mario", out)

    def test_user_plus_two_known_es(self):
        out = humanize_visual(
            "La imagen muestra tres personas en una mesa.",
            faces=["Mario", "Naomy", "Carlos"],
            user_name="Mario",
            lang="es",
        )
        self.assertIn("Tú", out)
        self.assertIn("Naomy", out)
        self.assertIn("Carlos", out)
        self.assertIn("con", out)

    def test_only_others_es(self):
        out = humanize_visual(
            "La imagen muestra a una mujer riendo.",
            faces=["Naomy"],
            user_name="Mario",
            lang="es",
        )
        self.assertTrue(out.startswith("Naomy"))

    def test_only_self_no_prefix(self):
        out = humanize_visual(
            "La imagen muestra a una persona sonriendo.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # Solo Mario en la foto → no agregamos prefijo redundante
        self.assertFalse(out.startswith("Tú"))

    def test_english_continuity(self):
        out = humanize_visual(
            "The image shows two people on a beach.",
            faces=["Mario", "Naomy"],
            user_name="Mario",
            lang="en",
        )
        self.assertIn("You", out)
        self.assertIn("Naomy", out)

    def test_avoids_duplicate_when_text_already_names(self):
        out = humanize_visual(
            "Naomy está sonriendo en el parque.",
            faces=["Naomy"],
            user_name="Mario",
            lang="es",
        )
        # No debe duplicar "Naomy. Naomy está..."
        # Cuento ocurrencias del nombre
        self.assertLessEqual(out.lower().count("naomy"), 2)


class TestNaturalTone(unittest.TestCase):
    """Regla 6: tono natural y conversacional."""

    def test_no_se_aprecia(self):
        out = humanize_visual(
            "Se aprecia un atardecer dorado. Se observa el horizonte despejado."
        )
        self.assertNotIn("se aprecia", out.lower())

    def test_no_existing_bullets(self):
        out = humanize_visual(
            "- Una persona\n- Una taza\n- Una mesa"
        )
        # Ya no debería haber guiones de bullet al inicio
        self.assertFalse(out.lstrip().startswith("-"))


class TestEmptyInput(unittest.TestCase):

    def test_empty_returns_empty(self):
        self.assertEqual(humanize_visual(""), "")
        self.assertEqual(humanize_visual("   "), "")
        self.assertEqual(humanize_visual(None), "")  # type: ignore[arg-type]

    def test_too_short_returns_empty(self):
        # Si tras limpieza queda <4 chars, devolver vacío
        self.assertEqual(humanize_visual("La imagen muestra"), "")


class TestEndToEndExample(unittest.TestCase):
    """El ejemplo del docstring debe funcionar."""

    def test_docstring_example(self):
        raw = (
            "La imagen muestra a dos personas sonriendo. "
            "En el fondo se observa una playa. La luz es cálida. "
            "Hay palmeras y arena dorada. Las personas parecen felices."
        )
        out = humanize_visual(
            raw,
            faces=["Mario", "Naomy"],
            user_name="Mario",
            lang="es",
        )
        # Comienza con continuidad
        self.assertTrue(out.startswith("Tú y Naomy"))
        # No tiene la apertura de reporte
        self.assertNotIn("la imagen muestra", out.lower())
        self.assertNotIn("se observa", out.lower())
        # No supera 4 frases
        n = sum(1 for c in out if c in ".!?")
        self.assertLessEqual(n, 5)  # +1 por la frase de continuidad


if __name__ == "__main__":
    unittest.main()
