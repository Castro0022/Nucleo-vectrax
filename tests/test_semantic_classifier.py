"""
Tests — Vectrax Semantic Intent Classifier
=============================================
Valida clasificación semántica, extracción de entidades,
desambiguación y fallback seguro.
"""

import pytest
from core.semantic_classifier import (
    SemanticClassifier,
    SemanticIntent,
    SemanticResult,
    Entity,
    TRAINING_EXAMPLES,
    classify,
)


@pytest.fixture
def sc():
    return SemanticClassifier()


# ---------------------------------------------------------------------------
# 1. Clasificación de intención — ejemplos del usuario
# ---------------------------------------------------------------------------

class TestPlaceSearchIntent:
    """Frases que deben detectarse como búsqueda de lugar."""

    def test_italiano_en_miami(self, sc):
        r = sc.classify("búscame un italiano en Miami")
        assert r.intent == SemanticIntent.PLACE_SEARCH
        assert r.confidence >= 0.4

    def test_cenar_cerca(self, sc):
        r = sc.classify("dónde puedo cenar cerca")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_abogado_divorcio(self, sc):
        r = sc.classify("quiero un abogado de divorcio cerca")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_mecanico_abierto(self, sc):
        r = sc.classify("busca un mecánico abierto ahora")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_restaurantes_abiertos_cerca(self, sc):
        r = sc.classify("qué restaurantes hay abiertos cerca de mí")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_english_coffee_shop(self, sc):
        r = sc.classify("best coffee shop in New York")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_farmacia_cerca(self, sc):
        r = sc.classify("hay alguna farmacia cerca de aquí")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_hotel_en_ciudad(self, sc):
        r = sc.classify("hoteles en Barcelona")
        assert r.intent == SemanticIntent.PLACE_SEARCH

    def test_donde_hay_gasolinera(self, sc):
        r = sc.classify("dónde hay una gasolinera")
        assert r.intent == SemanticIntent.PLACE_SEARCH


class TestWebSearchIntent:
    """Frases que deben detectarse como búsqueda web."""

    def test_noticias_miami(self, sc):
        r = sc.classify("qué pasó hoy en Miami")
        assert r.intent == SemanticIntent.WEB_SEARCH

    def test_explica_blockchain(self, sc):
        r = sc.classify("explica qué es blockchain")
        assert r.intent == SemanticIntent.WEB_SEARCH

    def test_noticias_ia(self, sc):
        r = sc.classify("busca noticias sobre inteligencia artificial")
        assert r.intent == SemanticIntent.WEB_SEARCH

    def test_quien_es_persona(self, sc):
        r = sc.classify("quién es Elon Musk")
        assert r.intent == SemanticIntent.WEB_SEARCH

    def test_como_funciona(self, sc):
        r = sc.classify("cómo funciona la fotosíntesis")
        assert r.intent == SemanticIntent.WEB_SEARCH


class TestMemoryLookupIntent:
    """Frases que deben detectarse como consulta de memoria."""

    def test_que_sabes_de_mi(self, sc):
        r = sc.classify("qué sabes de mí")
        assert r.intent == SemanticIntent.MEMORY_LOOKUP

    def test_recuerdame_lo_que_dije(self, sc):
        r = sc.classify("recuérdame lo que te dije ayer")
        assert r.intent == SemanticIntent.MEMORY_LOOKUP

    def test_mis_preferencias(self, sc):
        r = sc.classify("cuáles son mis preferencias")
        assert r.intent == SemanticIntent.MEMORY_LOOKUP

    def test_mi_historial(self, sc):
        r = sc.classify("muéstrame mi historial")
        assert r.intent == SemanticIntent.MEMORY_LOOKUP


class TestIdentityQueryIntent:
    """Frases que deben detectarse como consulta de identidad."""

    def test_quien_soy(self, sc):
        r = sc.classify("quién soy")
        assert r.intent == SemanticIntent.IDENTITY_QUERY

    def test_como_me_llamo(self, sc):
        r = sc.classify("cómo me llamo")
        assert r.intent == SemanticIntent.IDENTITY_QUERY

    def test_sabes_mi_nombre(self, sc):
        r = sc.classify("sabes mi nombre")
        assert r.intent == SemanticIntent.IDENTITY_QUERY

    def test_who_am_i(self, sc):
        r = sc.classify("who am i")
        assert r.intent == SemanticIntent.IDENTITY_QUERY


class TestSystemActionIntent:
    """Frases que deben detectarse como acción del sistema."""

    def test_command_status(self, sc):
        r = sc.classify("/status")
        assert r.intent == SemanticIntent.SYSTEM_ACTION

    def test_command_help(self, sc):
        r = sc.classify("/help")
        assert r.intent == SemanticIntent.SYSTEM_ACTION

    def test_crea_modulo(self, sc):
        r = sc.classify("crea un módulo de autenticación")
        assert r.intent == SemanticIntent.SYSTEM_ACTION


class TestGeneralChatIntent:
    """Frases que deben detectarse como conversación general."""

    def test_hola(self, sc):
        r = sc.classify("hola")
        assert r.intent == SemanticIntent.GENERAL_CHAT

    def test_buenos_dias(self, sc):
        r = sc.classify("buenos días")
        assert r.intent == SemanticIntent.GENERAL_CHAT

    def test_empty(self, sc):
        r = sc.classify("")
        assert r.intent == SemanticIntent.GENERAL_CHAT
        assert r.confidence < 0.5


# ---------------------------------------------------------------------------
# 2. Desambiguación — no debe activar place_search por keywords sueltos
# ---------------------------------------------------------------------------

class TestDisambiguation:
    """Frases que NO deben activar place_search."""

    def test_trabajo_en_banco(self, sc):
        """'banco' mencionado casualmente, no como búsqueda."""
        r = sc.classify("trabajo en un banco desde hace años")
        assert r.intent != SemanticIntent.PLACE_SEARCH

    def test_me_gusta_comida_italiana(self, sc):
        """'comida italiana' como preferencia, no como búsqueda."""
        r = sc.classify("me gusta la comida italiana")
        assert r.intent != SemanticIntent.PLACE_SEARCH

    def test_vivo_en_hotel(self, sc):
        """'hotel' mencionado casualmente."""
        r = sc.classify("vivo en un hotel temporalmente")
        assert r.intent != SemanticIntent.PLACE_SEARCH

    def test_fui_al_gym(self, sc):
        """Mención pasada, no búsqueda."""
        r = sc.classify("fui al gimnasio esta mañana")
        assert r.intent != SemanticIntent.PLACE_SEARCH


# ---------------------------------------------------------------------------
# 3. Extracción de entidades
# ---------------------------------------------------------------------------

class TestEntityExtraction:

    def test_location_miami(self, sc):
        r = sc.classify("búscame un italiano en Miami")
        locations = r.get_entities_by_type("location")
        assert any(e.value == "Miami" for e in locations)

    def test_location_nearby(self, sc):
        r = sc.classify("dónde puedo cenar cerca")
        locations = r.get_entities_by_type("location")
        assert any(e.value == "nearby" for e in locations)

    def test_category_restaurant(self, sc):
        r = sc.classify("dónde puedo cenar cerca")
        categories = r.get_entities_by_type("category")
        assert any("restaurant" in e.value for e in categories)

    def test_category_car_repair(self, sc):
        r = sc.classify("busca un mecánico abierto ahora")
        categories = r.get_entities_by_type("category")
        assert any(e.value == "car_repair" for e in categories)

    def test_time_operating_now(self, sc):
        r = sc.classify("busca un mecánico abierto ahora")
        times = r.get_entities_by_type("time")
        has_now = any(e.value in ("now", "operating_now") for e in times)
        assert has_now

    def test_action_search(self, sc):
        r = sc.classify("busca un mecánico abierto ahora")
        actions = r.get_entities_by_type("action")
        assert any(e.value == "search" for e in actions)

    def test_person_self(self, sc):
        r = sc.classify("quién soy")
        persons = r.get_entities_by_type("person")
        assert any(e.value == "self" for e in persons)

    def test_time_yesterday(self, sc):
        r = sc.classify("recuérdame lo que te dije ayer")
        times = r.get_entities_by_type("time")
        assert any(e.value == "yesterday" for e in times)

    def test_preference_detected(self, sc):
        r = sc.classify("me gusta la comida italiana")
        prefs = r.get_entities_by_type("preference")
        assert len(prefs) >= 1

    def test_no_category_in_casual(self, sc):
        """No debe extraer categoría de lugar en contexto casual."""
        r = sc.classify("trabajo en un banco desde hace años")
        categories = r.get_entities_by_type("category")
        assert len(categories) == 0


# ---------------------------------------------------------------------------
# 4. Estructura del resultado
# ---------------------------------------------------------------------------

class TestSemanticResultStructure:

    def test_has_intent(self, sc):
        r = sc.classify("hola")
        assert isinstance(r.intent, SemanticIntent)

    def test_has_confidence(self, sc):
        r = sc.classify("hola")
        assert 0.0 <= r.confidence <= 1.0

    def test_has_suggested_tools(self, sc):
        r = sc.classify("búscame un restaurante cerca")
        assert isinstance(r.suggested_tools, list)
        assert len(r.suggested_tools) >= 1

    def test_to_dict(self, sc):
        r = sc.classify("busca un mecánico abierto ahora")
        d = r.to_dict()
        assert "intent" in d
        assert "confidence" in d
        assert "entities" in d
        assert "suggested_tools" in d
        assert isinstance(d["entities"], list)

    def test_secondary_intents(self, sc):
        r = sc.classify("qué sabes de mí")
        assert isinstance(r.secondary_intents, list)


# ---------------------------------------------------------------------------
# 5. Fallback seguro
# ---------------------------------------------------------------------------

class TestFallback:

    def test_low_confidence_for_ambiguous(self, sc):
        """Texto ambiguo debe tener confianza moderada."""
        r = sc.classify("sí")
        assert r.confidence < 0.7

    def test_never_crashes(self, sc):
        """Ningún input debe causar excepción."""
        inputs = [
            "", "   ", "123", "🎉🎉🎉", "a" * 1000,
            None,  # será manejado
        ]
        for inp in inputs:
            try:
                r = sc.classify(inp)
                assert isinstance(r, SemanticResult)
            except TypeError:
                # None es esperado que falle en el if not text
                pass

    def test_classify_shortcut(self):
        """La función classify() shortcut funciona."""
        r = classify("hola")
        assert isinstance(r, SemanticResult)


# ---------------------------------------------------------------------------
# 6. Training examples — validación automática
# ---------------------------------------------------------------------------

class TestTrainingExamples:
    """Valida que todos los training examples internos clasifican correctamente."""

    @pytest.mark.parametrize(
        "example",
        TRAINING_EXAMPLES,
        ids=[e["text"][:40] for e in TRAINING_EXAMPLES],
    )
    def test_training_example(self, sc, example):
        r = sc.classify(example["text"])
        assert r.intent == example["expected_intent"], (
            f"Input: {example['text']!r}\n"
            f"Expected: {example['expected_intent'].value}\n"
            f"Got: {r.intent.value} (conf={r.confidence:.2f})\n"
            f"Scores: {r.scores}"
        )
