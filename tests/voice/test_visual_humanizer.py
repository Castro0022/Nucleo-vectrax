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


class TestInteractionNotDescription(unittest.TestCase):
    """Reglas nuevas: la respuesta debe ser INTERACCIÓN, no descripción.

    Cuando el usuario está reconocido en la foto, Vectrax le habla a él
    directamente. Las menciones en tercera persona del usuario y el
    relleno atmosférico forense ("el clima es soleado") se eliminan.
    """

    def test_companion_opener_when_user_recognized_alone(self):
        out = humanize_visual(
            "Una persona sonriendo en una playa.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # Debe dirigirse a Mario directamente con un companion opener
        self.assertIn("Mario", out)
        # Y debe ser uno de los openers de compañero (al menos un
        # token característico).
        opener_signal = any(
            tok in out
            for tok in ("Sales genial", "Te ves bien", "Buena foto", "Qué buen momento")
        )
        self.assertTrue(opener_signal, f"opener missing in: {out!r}")

    def test_distant_third_person_about_user_rewritten(self):
        out = humanize_visual(
            "A Mario y otra persona en una terraza.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # No debe haber referencia distante "a Mario" o "Mario y otra persona"
        self.assertNotIn("a Mario", out)
        self.assertNotIn("Mario y otra persona", out)
        # Debe haber una segunda-persona ("tú y alguien")
        self.assertIn("alguien", out.lower())
        self.assertIn("tú", out.lower())

    def test_atmospheric_filler_dropped(self):
        out = humanize_visual(
            "Mario en la playa. El clima es soleado. El día está despejado.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # El relleno atmosférico debe haberse ido
        self.assertNotIn("el clima es soleado", out.lower())
        self.assertNotIn("el día está", out.lower())
        self.assertNotIn("despejado", out.lower())

    def test_user_alone_generic_subject_becomes_second_person(self):
        out = humanize_visual(
            "Una persona posando frente a un edificio.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # "una persona" debe convertirse a "tú" porque Mario está solo
        self.assertNotIn("una persona", out.lower())
        self.assertIn("tú", out.lower())

    def test_companion_opener_english(self):
        out = humanize_visual(
            "A person standing on a beach.",
            faces=["Mario"],
            user_name="Mario",
            lang="en",
        )
        self.assertIn("Mario", out)
        opener_signal = any(
            tok in out
            for tok in ("You look great", "Nice shot", "Good moment", "Looking sharp")
        )
        self.assertTrue(opener_signal, f"english opener missing in: {out!r}")

    def test_user_with_others_uses_classic_continuity(self):
        """Cuando el user está con otros, mantenemos 'Tú y X' (no companion opener).

        El companion opener es solo para foto del user solo.
        """
        out = humanize_visual(
            "La imagen muestra a dos personas felices en la playa.",
            faces=["Mario", "Naomy"],
            user_name="Mario",
            lang="es",
        )
        self.assertIn("Tú", out)
        self.assertIn("Naomy", out)
        # No debe usar el companion opener (ese es solo para user solo)
        self.assertNotIn("Sales genial", out)

    def test_atmospheric_does_not_drop_mixed_sentences(self):
        """Una oración que mezcla gente + ambiente NO debe dropearse."""
        out = humanize_visual(
            "Mario riendo bajo un cielo soleado.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # "riendo" o "cielo" debe seguir presente
        self.assertTrue(
            "riendo" in out.lower() or "cielo" in out.lower(),
            f"mixed sentence got dropped: {out!r}",
        )


class TestActivePartnerTone(unittest.TestCase):
    """Reglas nuevas: tono activo de socio. Prohibido pasivo, obligatorio
    pregunta o reacción activa al final."""

    def test_drops_parecen_disfrutar(self):
        out = humanize_visual(
            "Mario y Naomy en una terraza. Parecen disfrutar la tarde.",
            faces=["Mario", "Naomy"],
            user_name="Mario",
            lang="es",
        )
        self.assertNotIn("parecen disfrutar", out.lower())
        self.assertNotIn("disfrutar", out.lower())

    def test_drops_dan_un_toque(self):
        out = humanize_visual(
            "Mario en la playa. Las palmeras dan un toque relajado.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        self.assertNotIn("dan un toque", out.lower())
        self.assertNotIn("toque relajado", out.lower())

    def test_drops_parece_buena_ocasion(self):
        out = humanize_visual(
            "Mario en el parque. Parece una buena ocasión para celebrar.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        self.assertNotIn("buena ocasión", out.lower())
        self.assertNotIn("para celebrar", out.lower())

    def test_drops_passive_english(self):
        out = humanize_visual(
            "Mario at the beach. They seem to enjoy the sunset.",
            faces=["Mario"],
            user_name="Mario",
            lang="en",
        )
        self.assertNotIn("seem to enjoy", out.lower())
        self.assertNotIn("enjoying", out.lower())

    def test_appends_question_when_missing(self):
        """Si la salida final no tiene pregunta, debe agregarse una."""
        out = humanize_visual(
            "Una persona sonriendo en una terraza.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # debe haber algún signo de pregunta
        self.assertTrue(
            "?" in out or "¿" in out,
            f"esperaba pregunta de curiosidad en: {out!r}",
        )

    def test_does_not_duplicate_question_when_present(self):
        """Si ya hay una pregunta, no se agrega otra del pool."""
        # input que ya termina en pregunta
        out = humanize_visual(
            "Mario en la calle. ¿Qué hacías ahí?",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # solo debe haber una sola pregunta original
        n_questions = out.count("?")
        self.assertEqual(n_questions, 1, f"se duplicó pregunta en: {out!r}")
        # y debe ser la original (no el pool por defecto)
        self.assertIn("qué hacías", out.lower())

    def test_active_closure_marker_skips_question_append(self):
        """Si la salida ya tiene 'me da curiosidad' u otra reacción
        activa, NO se agrega pregunta del pool."""
        out = humanize_visual(
            "Mario en la terraza. Tengo ganas de ir a ese lugar.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        # No debe agregar pregunta del pool
        self.assertNotIn("¿dónde fue?", out.lower())
        self.assertNotIn("¿quién más", out.lower())
        self.assertIn("tengo ganas", out.lower())

    def test_question_appended_in_english(self):
        out = humanize_visual(
            "A person in a city street.",
            faces=["Mario"],
            user_name="Mario",
            lang="en",
        )
        self.assertIn("?", out)

    def test_full_passive_input_yields_companion_plus_question(self):
        """Caso real reportado por Mario: input pasivo + descriptivo.
        Output debe ser companion opener + pregunta o cierre activo."""
        out = humanize_visual(
            "A Mario y otra persona en una terraza. Parecen disfrutar el sol. "
            "Las palmeras dan un toque relajado al ambiente.",
            faces=["Mario"],
            user_name="Mario",
            lang="es",
        )
        self.assertIn("Mario", out)
        self.assertNotIn("parecen disfrutar", out.lower())
        self.assertNotIn("dan un toque", out.lower())
        # Debe haber pregunta o cierre activo
        has_q = "?" in out or "¿" in out
        has_active = any(
            tok in out.lower()
            for tok in (
                "me da curiosidad", "tengo ganas", "me gustaría",
            )
        )
        self.assertTrue(
            has_q or has_active,
            f"faltó cierre activo/pregunta en: {out!r}",
        )


if __name__ == "__main__":
    unittest.main()
