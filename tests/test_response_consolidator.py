"""
Tests para core.response_consolidator — Capa de Unificación de Salida
=====================================================================

Cubre:
  1. Fragmento único → passthrough
  2. Duplicados textuales exactos
  3. Duplicados semánticos (>70% overlap)
  4. Prioridad por fuente (memory > places > online > llm)
  5. Fusión de fragmentos complementarios
  6. Duplicados internos en una sola respuesta (párrafos/oraciones)
  7. Casos mixtos: places+llm, memory+llm, fallback+semantic
  8. Límite de longitud
  9. Fragmentos vacíos
  10. Singleton
"""

import pytest

from core.response_consolidator import (
    ResponseConsolidator,
    ResponseFragment,
    SourcePriority,
    get_consolidator,
    get_priority,
)


@pytest.fixture
def c():
    return ResponseConsolidator()


def frag(text, source="llm", confidence=0.8, is_complete=True):
    return ResponseFragment(
        text=text, source=source, confidence=confidence, is_complete=is_complete,
    )


# ===========================================================================
# 1. Fragmento único
# ===========================================================================

class TestSingleFragment:

    def test_single_returns_as_is(self, c):
        text, trace = c.consolidate([frag("Python es un lenguaje de programación.", "online")])
        assert text == "Python es un lenguaje de programación."
        assert trace["strategy"] == "single_fragment"

    def test_empty_list(self, c):
        text, trace = c.consolidate([])
        assert text == ""
        assert trace["strategy"] == "empty_input"

    def test_all_empty_fragments(self, c):
        text, trace = c.consolidate([frag("", "llm"), frag("  ", "online")])
        assert text == ""
        assert trace["strategy"] == "all_empty"


# ===========================================================================
# 2. Duplicados textuales exactos
# ===========================================================================

class TestTextualDedup:

    def test_exact_duplicate_removed(self, c):
        f1 = frag("Python es un lenguaje versátil.", "llm", is_complete=False)
        f2 = frag("Python es un lenguaje versátil.", "llm", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        assert "versátil" in text
        assert trace["duplicates_removed"] >= 1

    def test_whitespace_normalized_duplicate(self, c):
        f1 = frag("Python es  un   lenguaje.", "llm", is_complete=False)
        f2 = frag("Python es un lenguaje.", "llm", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        assert trace["duplicates_removed"] >= 1

    def test_different_texts_kept(self, c):
        f1 = frag("Python es un lenguaje.", "llm", is_complete=False)
        f2 = frag("Rust es un lenguaje de sistemas.", "llm", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        assert trace["duplicates_removed"] == 0


# ===========================================================================
# 3. Duplicados semánticos
# ===========================================================================

class TestSemanticDedup:

    def test_high_overlap_removed(self, c):
        f1 = frag("Python es un lenguaje de programación versátil y fácil de aprender.", "llm", is_complete=False)
        f2 = frag("Python es un lenguaje de programación versátil, fácil de aprender y usar.", "llm", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        assert trace["duplicates_removed"] >= 1

    def test_low_overlap_kept(self, c):
        f1 = frag("Python se usa para desarrollo web y ciencia de datos.", "llm", is_complete=False)
        f2 = frag("Rust se enfoca en seguridad de memoria y rendimiento de sistemas.", "llm", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        assert "Python" in text
        assert "Rust" in text

    def test_semantic_keeps_higher_priority(self, c):
        f1 = frag(
            "Encontré 3 farmacias cerca de tu ubicación actual con horario abierto y servicio disponible.",
            "places", is_complete=False,
        )
        f2 = frag(
            "Hay 3 farmacias cercanas a tu ubicación actual con horario abierto y servicio de atención.",
            "llm", is_complete=False,
        )
        text, trace = c.consolidate([f1, f2])
        assert trace["duplicates_removed"] >= 1
        assert "farmacias" in text


# ===========================================================================
# 4. Prioridad por fuente
# ===========================================================================

class TestPriority:

    def test_memory_wins_over_llm(self, c):
        f1 = frag("Tu nombre es Mario, me lo dijiste ayer.", "memory", is_complete=True)
        f2 = frag("No tengo información sobre tu nombre.", "llm")
        text, trace = c.consolidate([f1, f2])
        assert "Mario" in text
        assert "No tengo" not in text
        assert trace["strategy"] == "best_complete:memory"

    def test_places_wins_over_llm(self, c):
        f1 = frag("Farmacia La Salud - Calle 5 #123. Abierta 24h.", "places", is_complete=True)
        f2 = frag("Te recomiendo buscar una farmacia en tu zona.", "llm")
        text, trace = c.consolidate([f1, f2])
        assert "Farmacia La Salud" in text
        assert trace["strategy"] == "best_complete:places"

    def test_online_wins_over_llm(self, c):
        f1 = frag("Bitcoin cotiza a $67,500 según datos de hoy.", "online", is_complete=True)
        f2 = frag("Bitcoin es una criptomoneda descentralizada.", "llm")
        text, trace = c.consolidate([f1, f2])
        assert "$67,500" in text

    def test_priority_ordering(self):
        assert get_priority("memory") < get_priority("identity")
        assert get_priority("identity") < get_priority("places")
        assert get_priority("places") < get_priority("online")
        assert get_priority("online") < get_priority("llm")
        assert get_priority("llm") < get_priority("fallback")


# ===========================================================================
# 5. Fusión complementaria
# ===========================================================================

class TestMerge:

    def test_complementary_merged(self, c):
        f1 = frag("Python es un lenguaje de programación.", "llm", is_complete=False)
        f2 = frag("Se usa ampliamente en machine learning y desarrollo web.", "online", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        assert "Python" in text
        assert "machine learning" in text
        assert "merged" in trace["strategy"]

    def test_merge_discards_redundant_sentences(self, c):
        f1 = frag(
            "Python es un lenguaje versátil. Se usa en ciencia de datos.",
            "online",
        )
        f2 = frag(
            "Python es un lenguaje versátil. También sirve para automatización.",
            "llm",
        )
        text, trace = c.consolidate([f1, f2])
        # "Python es un lenguaje versátil" should NOT appear twice
        count = text.lower().count("python es un lenguaje")
        assert count == 1


# ===========================================================================
# 6. Duplicados internos en una sola respuesta
# ===========================================================================

class TestInternalDedup:

    def test_duplicate_paragraphs_removed(self, c):
        text = (
            "Python es un lenguaje versátil.\n\n"
            "Se usa en ciencia de datos.\n\n"
            "Python es un lenguaje versátil."
        )
        cleaned, trace = c.consolidate_single(text, "llm")
        count = cleaned.lower().count("python es un lenguaje")
        assert count == 1

    def test_duplicate_sentences_across_paragraphs(self, c):
        text = (
            "Python es versátil. Se usa en web.\n\n"
            "Python es versátil. También en datos."
        )
        cleaned, trace = c.consolidate_single(text, "llm")
        count = cleaned.lower().count("python es versátil")
        assert count == 1

    def test_no_change_if_unique(self, c):
        text = "Python es versátil.\n\nSe usa en ciencia de datos."
        cleaned, trace = c.consolidate_single(text, "llm")
        assert "Python" in cleaned
        assert "ciencia de datos" in cleaned


# ===========================================================================
# 7. Casos mixtos realistas
# ===========================================================================

class TestMixedCases:

    def test_places_plus_llm(self, c):
        """Places resuelve con datos reales + LLM agrega texto genérico."""
        f1 = frag(
            "Farmacia San José - Av. 5 #200, abierta hasta las 10pm. "
            "Farmacia Central - Calle 3 #50, 24 horas.",
            "places", is_complete=True,
        )
        f2 = frag("Te recomiendo visitar farmacias de tu zona para más opciones.", "llm")
        text, trace = c.consolidate([f1, f2])
        assert "Farmacia San José" in text
        assert "recomiendo" not in text  # LLM discarded

    def test_memory_plus_llm(self, c):
        """Memory resuelve la identidad + LLM da respuesta genérica."""
        f1 = frag("Tu nombre es Mario. Me lo dijiste el 20 de marzo.", "memory", is_complete=True)
        f2 = frag("No tengo forma de saber tu nombre.", "llm")
        text, trace = c.consolidate([f1, f2])
        assert "Mario" in text
        assert "No tengo" not in text

    def test_fallback_plus_semantic(self, c):
        """Fallback genérico + resultado semántico bueno."""
        f1 = frag("Vectrax activo.", "fallback", is_complete=False)
        f2 = frag("Bitcoin cotiza hoy a $67,500 según CoinMarketCap.", "online", is_complete=True)
        text, trace = c.consolidate([f1, f2])
        assert "$67,500" in text
        assert trace["strategy"] == "best_complete:online"

    def test_local_plus_online(self, c):
        """Memoria local parcial + búsqueda web completa."""
        f1 = frag("Encontré 2 notas tuyas sobre trading.", "local", is_complete=False)
        f2 = frag("Bitcoin muestra soporte en $65,000 según análisis técnico.", "online", is_complete=False)
        text, trace = c.consolidate([f1, f2])
        # Both should be included (complementary)
        assert "notas" in text
        assert "soporte" in text or "$65,000" in text


# ===========================================================================
# 8. Límite de longitud
# ===========================================================================

class TestLengthLimit:

    def test_truncates_long_merge(self, c):
        long_text = "Esta es una oración de prueba. " * 100
        f1 = frag(long_text, "llm")
        f2 = frag("Información adicional completamente diferente para testing.", "online")
        text, trace = c.consolidate([f1, f2])
        assert len(text) <= c.MAX_FINAL_LENGTH + 10


# ===========================================================================
# 9. Singleton
# ===========================================================================

class TestSingleton:

    def test_get_consolidator_same_instance(self):
        import core.response_consolidator as rc
        rc._consolidator = None
        c1 = get_consolidator()
        c2 = get_consolidator()
        assert c1 is c2
        rc._consolidator = None
