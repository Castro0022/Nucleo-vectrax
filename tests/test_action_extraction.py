"""
tests/test_action_extraction.py — Diseño A/B/C, Frente C.

Fija el contrato de `core/action_extraction.py::extract_action_params()`:
  1. Una sola llamada LLM estructurada (JSON) produce par\u00e1metros TIPADOS,
     nunca texto libre — la integraci\u00f3n nunca vuelve a ver el mensaje
     conversacional completo.
  2. Generaliza a "quiero comer italiano cerca" -> category=restaurant,
     cuisine=italian SIN ninguna lista de cocinas/prefijos en este m\u00f3dulo.
  3. Defensivo: LLM no disponible / JSON inv\u00e1lido / dominio desconocido ->
     None, nunca lanza.
"""

from __future__ import annotations

from unittest.mock import patch

from core.action_extraction import (
    PlaceSearchParams,
    ReminderParams,
    extract_action_params,
)
from core.llm_call import LLMResult


def _llm_ok(json_text: str) -> LLMResult:
    return LLMResult(True, json_text, "ok")


def _llm_unavailable() -> LLMResult:
    return LLMResult(False, "", "no_key")


class TestPlaceSearchExtraction:
    def test_extracts_category_and_cuisine_without_keyword_list(self):
        """El módulo no enumera cocinas: el LLM infiere 'italiano' -> cuisine.
        Se verifica llamando extract_action_params con un LLM simulado que
        devuelve exactamente lo que un LLM real inferiría."""
        fake = _llm_ok(
            '{"domain": "place_search", "category": "restaurant", '
            '"cuisine": "italian", "location": "near_user"}'
        )
        with patch("core.llm_call.complete", return_value=fake):
            params = extract_action_params("Quiero comer italiano cerca")
        assert isinstance(params, PlaceSearchParams)
        assert params.category == "restaurant"
        assert params.cuisine == "italian"
        assert params.location == "near_user"

    def test_extracts_plain_category_no_cuisine(self):
        fake = _llm_ok('{"domain": "place_search", "category": "pharmacy", "cuisine": "", "location": "near_user"}')
        with patch("core.llm_call.complete", return_value=fake):
            params = extract_action_params("Búscame una farmacia cerca")
        assert isinstance(params, PlaceSearchParams)
        assert params.category == "pharmacy"
        assert params.cuisine == ""

    def test_tolerates_markdown_fenced_json(self):
        fake = _llm_ok('```json\n{"domain": "place_search", "category": "restaurant"}\n```')
        with patch("core.llm_call.complete", return_value=fake):
            params = extract_action_params("Búscame un restaurante")
        assert isinstance(params, PlaceSearchParams)
        assert params.category == "restaurant"


class TestReminderExtraction:
    def test_extracts_content_and_when_text(self):
        fake = _llm_ok(
            '{"domain": "reminder", "content": "comprar toallas", "when_text": "mañana"}'
        )
        with patch("core.llm_call.complete", return_value=fake):
            params = extract_action_params("Recuérdame comprar toallas mañana")
        assert isinstance(params, ReminderParams)
        assert params.content == "comprar toallas"
        assert params.when_text == "mañana"


class TestNoActionDomain:
    def test_domain_none_returns_none(self):
        fake = _llm_ok('{"domain": "none"}')
        with patch("core.llm_call.complete", return_value=fake):
            assert extract_action_params("hola, cómo estás") is None

    def test_unknown_domain_returns_none(self):
        fake = _llm_ok('{"domain": "market_trade"}')
        with patch("core.llm_call.complete", return_value=fake):
            assert extract_action_params("compra 1 BTC") is None


class TestDefensiveBehavior:
    def test_llm_unavailable_returns_none(self):
        with patch("core.llm_call.complete", return_value=_llm_unavailable()):
            assert extract_action_params("búscame un restaurante") is None

    def test_invalid_json_returns_none(self):
        with patch("core.llm_call.complete", return_value=_llm_ok("no soy json")):
            assert extract_action_params("búscame un restaurante") is None

    def test_llm_exception_never_propagates(self):
        with patch("core.llm_call.complete", side_effect=RuntimeError("boom")):
            assert extract_action_params("búscame un restaurante") is None

    def test_empty_content_returns_none(self):
        assert extract_action_params("") is None
        assert extract_action_params("   ") is None

    def test_place_search_without_category_returns_none(self):
        fake = _llm_ok('{"domain": "place_search", "category": ""}')
        with patch("core.llm_call.complete", return_value=fake):
            assert extract_action_params("algo") is None
