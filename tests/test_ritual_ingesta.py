"""Tests para ritual_escritura y argos_ingesta."""
import unittest

from core.ritual_escritura import (
    emit_receipt,
    format_user_confirmation,
    _is_principle,
    _initial_weight,
)
from core.argos_ingesta import (
    evaluar_ingesta,
    Origen,
    Veredicto,
    Intencion,
)


class RitualEscrituraTests(unittest.TestCase):
    def test_receipt_has_id_and_summary(self):
        r = emit_receipt(
            user_id="tg:1",
            user_input="Nota breve sobre volumen relativo",
            layer="interactions",
        )
        self.assertEqual(len(r.receipt_id), 10)
        self.assertIn("volumen", r.summary)
        self.assertTrue(r.reversible)

    def test_principle_is_detected_and_weighs_more(self):
        self.assertTrue(_is_principle("Principio: nunca forzar análisis antes de sentir"))
        self.assertFalse(_is_principle("cualquier cosa"))
        # Peso de un principio absorbido en core
        w = _initial_weight("Principio: X", 0, True, is_principle=True)
        self.assertGreaterEqual(w, 0.75)

    def test_user_confirmation_spanish(self):
        r = emit_receipt(
            user_id="tg:1", user_input="Principio: primero sentir",
            layer="core", absorbed_in_core=True,
        )
        msg = format_user_confirmation(r, lang="es")
        self.assertIn("✓ Grabado", msg)
        self.assertIn("peso=", msg)
        self.assertIn("reversible", msg)

    def test_facts_boost_weight(self):
        w0 = _initial_weight("texto", 0, False, False)
        w1 = _initial_weight("texto", 3, False, False)
        self.assertGreater(w1, w0)


class ArgosIngestaTests(unittest.TestCase):
    def test_discards_noise(self):
        r = evaluar_ingesta("ok", origen=Origen.TELEGRAM)
        self.assertEqual(r.veredicto, Veredicto.DESCARTAR)
        self.assertEqual(r.razon, "ruido conversacional")

    def test_discards_system_paths(self):
        r = evaluar_ingesta(
            "contenido",
            origen=Origen.IMPORTACION,
            source_path="/Users/x/Library/Caches/foo",
        )
        self.assertEqual(r.veredicto, Veredicto.DESCARTAR)

    def test_discards_shell_history(self):
        r = evaluar_ingesta(
            "comando cualquiera",
            origen=Origen.IMPORTACION,
            source_path="/Users/x/.zsh_history",
        )
        self.assertEqual(r.veredicto, Veredicto.DESCARTAR)

    def test_accepts_principle(self):
        r = evaluar_ingesta(
            "Principio: BTC zona 98K-100K es soporte clave del ciclo",
            origen=Origen.VOZ,
        )
        self.assertEqual(r.veredicto, Veredicto.ACEPTAR)
        self.assertEqual(r.intencion, Intencion.PRINCIPIO)
        self.assertGreaterEqual(r.peso, 0.70)

    def test_accepts_note_with_moderate_weight(self):
        r = evaluar_ingesta(
            "Hoy noté que el volumen relativo se disparó antes del movimiento.",
            origen=Origen.VOZ,
        )
        self.assertEqual(r.veredicto, Veredicto.ACEPTAR)
        self.assertEqual(r.intencion, Intencion.NOTA)

    def test_dedup_key_stable(self):
        a = evaluar_ingesta("Mismo contenido exacto.", origen=Origen.VOZ)
        b = evaluar_ingesta("Mismo contenido exacto.", origen=Origen.VOZ)
        self.assertEqual(a.dedup_key, b.dedup_key)

    def test_import_origin_reduces_weight(self):
        voice = evaluar_ingesta("Hecho: mi email es x@y.com", origen=Origen.VOZ)
        imported = evaluar_ingesta("Hecho: mi email es x@y.com", origen=Origen.IMPORTACION)
        self.assertGreater(voice.peso, imported.peso)


if __name__ == "__main__":
    unittest.main()
