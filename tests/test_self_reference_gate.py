"""
tests/test_self_reference_gate.py — regresión puntual sobre `_SELF_REFERENCE`.

Fija el contrato de la corrección mínima aplicada a la alternativa
"cómo va/está/funciona/crece" en `vectrax/self_context.py::_SELF_REFERENCE`:
  1. Sigue disparando cuando el término va acompañado de un sustantivo de
     sistema/marca real (sistema, proyecto, vectrax, universo).
  2. Deja de disparar en frases casuales sin ninguna relación con Vectrax
     ("cómo va tu día", "cómo está tu mamá", "cómo funciona esto").

No se modifica ninguna otra rama del gate (en particular `\\bvectrax\\b`
suelto) — eso queda fuera de alcance de este parche, a propósito.
"""

from __future__ import annotations

from vectrax.self_context import _SELF_REFERENCE, is_self_referential


class TestComoVaStillFiresWithSystemContext:
    """El propósito original de la regla se preserva: sigue reconociendo
    preguntas reales sobre el sistema/proyecto/marca."""

    def test_como_va_el_sistema(self):
        assert _SELF_REFERENCE.search("Cómo va el sistema")
        assert is_self_referential("Cómo va el sistema")

    def test_como_esta_el_proyecto_with_punctuation(self):
        assert _SELF_REFERENCE.search("¿Cómo está el proyecto?")

    def test_como_funciona_el_sistema(self):
        assert _SELF_REFERENCE.search("cómo funciona el sistema")

    def test_como_crece_vectrax(self):
        assert _SELF_REFERENCE.search("cómo crece vectrax")

    def test_como_va_tu_universo(self):
        assert _SELF_REFERENCE.search("cómo va tu universo")

    def test_case_insensitive_and_accent_tolerant(self):
        # 'esta' sin tilde también debe seguir cubierto (como antes).
        assert _SELF_REFERENCE.search("como esta el sistema")


class TestComoVaNoLongerFiresOnCasualPhrasing:
    """El falso positivo real detectado: la construcción "cómo va/está/
    funciona/crece" NO puede disparar sin un término de sistema/marca cerca."""

    def test_como_va_tu_dia(self):
        assert not _SELF_REFERENCE.search("cómo va tu día")

    def test_como_esta_tu_mama(self):
        assert not _SELF_REFERENCE.search("cómo está tu mamá")

    def test_como_funciona_esto(self):
        assert not _SELF_REFERENCE.search("cómo funciona esto")

    def test_como_va_todo(self):
        assert not _SELF_REFERENCE.search("cómo va todo")

    def test_como_esta_el_clima(self):
        assert not _SELF_REFERENCE.search("cómo está el clima hoy")

    def test_como_crece_un_negocio_generico(self):
        assert not _SELF_REFERENCE.search("cómo crece un negocio como el mío")

    def test_full_message_not_self_referential(self):
        # Verifica el contrato end-to-end (is_self_referential), no solo el
        # sub-regex, para una frase casual real sin ningún otro activador.
        assert not is_self_referential("Oye, ¿cómo va tu día?")


class TestUnrelatedAlternativesUntouched:
    """El resto del gate (fuera de alcance de este parche) sigue intacto."""

    def test_bare_vectrax_mention_untouched(self):
        # El sub-regex `_SELF_REFERENCE` NO se tocó: sigue matcheando el
        # nombre suelto. El aislamiento del vocativo vive en
        # `is_self_referential()` (ver `TestVocativeGreetingIsolated` abajo),
        # no en este regex.
        assert _SELF_REFERENCE.search("Gracias vectrax")
        assert _SELF_REFERENCE.search("Hola vectrax")

    def test_universe_vocabulary_untouched(self):
        assert _SELF_REFERENCE.search("cuántas estrellas tienes")
        assert _SELF_REFERENCE.search("qué motores tienes activos")


class TestVocativeGreetingIsolated:
    """Defecto post-A/B/C #1: una simple mención vocativa del nombre
    ("Gracias, Vectrax", "Hola Vectrax, ¿cómo estás?") NO debe activar
    SELF_REFERENCE a nivel de `is_self_referential()` — debe comportarse como
    conversación normal. Preguntas real mente autorreferenciales deben seguir
    funcionando.
    """

    def test_gracias_vectrax_not_self_referential(self):
        assert not is_self_referential("Gracias, Vectrax")
        assert not is_self_referential("Gracias Vectrax")

    def test_hola_vectrax_como_estas_not_self_referential(self):
        assert not is_self_referential("Hola Vectrax, ¿cómo estás?")

    def test_greeting_variants_not_self_referential(self):
        assert not is_self_referential("Hey Vectrax")
        assert not is_self_referential("Buenos días Vectrax")
        assert not is_self_referential("Thanks Vectrax")
        assert not is_self_referential("Thank you, Vectrax")

    def test_real_self_reference_questions_still_work(self):
        # Preguntas reales sobre Vectrax (no vocativo de saludo) siguen
        # disparando, exactamente igual que antes del Fix 1.
        assert is_self_referential("¿Qué es Vectrax?")
        assert is_self_referential("¿Cómo funciona Vectrax?")
        assert is_self_referential("¿Quién eres?")
        assert is_self_referential("revisa el estado de vectrax")
        assert is_self_referential("¿la última respuesta de vectrax fue en inglés?")

    def test_vocative_followed_by_real_self_reference_still_fires(self):
        # Si DESPUÉS del vocativo hay contenido genuinamente autorreferencial,
        # sigue disparando (solo se excluye el saludo+nombre en sí).
        assert is_self_referential("Hola Vectrax, ¿cómo va el sistema?")
        assert is_self_referential("Gracias Vectrax, ¿qué motores tienes activos?")

    def test_como_va_el_sistema_unaffected(self):
        # Fix previo ("cómo va") no se ve afectado por este cambio.
        assert is_self_referential("¿Cómo va el sistema?")
        assert not is_self_referential("cómo va tu día")
