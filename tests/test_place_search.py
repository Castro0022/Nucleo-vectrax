"""
Tests — Place Search Integration
====================================
Valida el módulo de búsqueda de lugares reales:
  - Detección de intención de búsqueda de lugar
  - Búsqueda por texto (Text Search)
  - Búsqueda por cercanía (Nearby Search)
  - Manejo sin ubicación
  - Normalización y formateo de resultados
  - Distancia aproximada
  - Extracción de query
  - Manejo de API no disponible

Creado: 2026-03-19
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.expanduser("~/Vectrax"))

from vectrax.integrations.place_search import (
    detect_place_intent,
    search_places,
    format_results,
    _has_search_context,
    _wants_nearby,
    _extract_search_query,
    _normalize_place,
    _approx_distance_km,
    _distance_label,
    _no_results_message,
    _detect_place_type,
    PLACE_KEYWORDS,
    PLACE_INTENT_PATTERNS,
    KEYWORD_TO_PLACE_TYPE,
)


# ---------------------------------------------------------------------------
# 1. Detección de intención — positivos
# ---------------------------------------------------------------------------

class TestDetectPlaceIntentPositive:
    """Debe detectar intención de búsqueda de lugar en estas frases."""

    @pytest.mark.parametrize("msg", [
        "dónde hay un taller de mecánica cerca",
        "cuál es la tienda más cercana",
        "dónde puedo comer",
        "dónde compro esto",
        "cuál es la farmacia más cercana",
        "cuál es el supermercado más cercano",
        "qué lugar tengo cerca para comer",
        "dónde hay una gasolinera",
        "busco un banco cerca",
        "necesito un cajero cerca de mí",
        "hay un hospital cerca?",
        "dónde encuentro un dentista",
        "necesito una ferretería cerca",
        "busco un veterinario por aquí",
        "hay un hotel cerca?",
        "dónde hay un gimnasio?",
        "busco una barbería cerca",
        "dónde hay una cafetería?",
        "hay alguna farmacia abierta cerca?",
        "where is the nearest pharmacy?",
        "find a restaurant nearby",
        "closest atm near me",
        "hay una pizzería cerca de aquí?",
        "dónde reparo mi auto",
        "dónde lavo la ropa cerca",
    ])
    def test_positive_intent(self, msg):
        assert detect_place_intent(msg) is True


# ---------------------------------------------------------------------------
# 2. Detección de intención — negativos
# ---------------------------------------------------------------------------

class TestDetectPlaceIntentNegative:
    """NO debe detectar intención de lugar en estas frases."""

    @pytest.mark.parametrize("msg", [
        "",
        "   ",
        "hola cómo estás",
        "cuánto es 2+2",
        "quién es el presidente",
        "explícame la teoría de la relatividad",
        "qué es el machine learning",
        "mi nombre es Mario",
        "recuerda que me gusta el café",
    ])
    def test_negative_intent(self, msg):
        assert detect_place_intent(msg) is False


# ---------------------------------------------------------------------------
# 3. Contexto de búsqueda
# ---------------------------------------------------------------------------

class TestSearchContext:
    """_has_search_context discrimina mención casual vs búsqueda activa."""

    def test_search_context_positive(self):
        assert _has_search_context("dónde hay un banco") is True
        assert _has_search_context("busco un taller") is True
        assert _has_search_context("farmacia cerca?") is True
        assert _has_search_context("need to find a pharmacy nearby") is True

    def test_search_context_negative(self):
        # Mención informativa sin señales de búsqueda
        assert _has_search_context("trabajo en un banco desde hace años") is False


# ---------------------------------------------------------------------------
# 4. Detección de cercanía
# ---------------------------------------------------------------------------

class TestWantsNearby:
    """_wants_nearby detecta intención de proximidad."""

    @pytest.mark.parametrize("text,expected", [
        ("farmacia cerca de mí", True),
        ("taller cercano", True),
        ("restaurant nearby", True),
        ("closest atm near me", True),
        ("por aquí hay un banco", True),
        ("aquí cerca hay algo", True),
        ("farmacia en Santiago", False),
        ("mejor restaurante de Madrid", False),
    ])
    def test_nearby_detection(self, text, expected):
        assert _wants_nearby(text) is expected


# ---------------------------------------------------------------------------
# 5. Extracción de query
# ---------------------------------------------------------------------------

class TestExtractSearchQuery:
    """_extract_search_query limpia frases contextuales."""

    def test_removes_context_phrases(self):
        assert "farmacia" in _extract_search_query("dónde hay una farmacia cerca de mí")
        assert "taller" in _extract_search_query("busco un taller cerca")
        assert "restaurante" in _extract_search_query("cuál es el restaurante más cercano")

    def test_preserves_core_query(self):
        result = _extract_search_query("pizzería napolitana")
        assert "pizzería napolitana" in result

    def test_empty_input(self):
        result = _extract_search_query("")
        assert result == ""

    def test_short_query_preserved(self):
        # Si la limpieza deja algo muy corto, usar original
        result = _extract_search_query("hay un spa?")
        assert len(result) >= 3


# ---------------------------------------------------------------------------
# 6. Normalización de resultados
# ---------------------------------------------------------------------------

class TestNormalizePlace:
    """_normalize_place convierte resultado crudo de API a estructura Vectrax."""

    def test_basic_normalization(self):
        raw = {
            "id": "place123",
            "displayName": {"text": "Farmacia Cruz Verde"},
            "formattedAddress": "Av. Principal 456, Santiago",
            "rating": 4.5,
            "userRatingCount": 120,
            "types": ["pharmacy"],
            "primaryTypeDisplayName": {"text": "Farmacia"},
            "location": {"latitude": -33.45, "longitude": -70.66},
        }
        result = _normalize_place(raw)
        assert result["nombre"] == "Farmacia Cruz Verde"
        assert result["categoria"] == "Farmacia"
        assert result["rating"] == 4.5
        assert result["total_reviews"] == 120
        assert result["direccion"] == "Av. Principal 456, Santiago"
        assert result["place_id"] == "place123"
        assert result["distancia_km"] is None  # Sin ubicación de usuario

    def test_with_user_location(self):
        raw = {
            "id": "p1",
            "displayName": {"text": "Taller X"},
            "formattedAddress": "Calle 123",
            "location": {"latitude": -33.46, "longitude": -70.67},
        }
        user_loc = {"lat": -33.45, "lng": -70.66}
        result = _normalize_place(raw, user_loc)
        assert result["distancia_km"] is not None
        assert result["distancia_km"] > 0
        assert result["distancia_label"] != ""

    def test_missing_fields(self):
        raw = {"id": "p2"}
        result = _normalize_place(raw)
        assert result["nombre"] == ""
        assert result["direccion"] == ""
        assert result["rating"] is None
        assert result["place_id"] == "p2"

    def test_fallback_category_from_types(self):
        raw = {
            "id": "p3",
            "displayName": {"text": "Algo"},
            "types": ["restaurant", "food"],
        }
        result = _normalize_place(raw)
        assert result["categoria"] == "restaurant"


# ---------------------------------------------------------------------------
# 7. Distancia
# ---------------------------------------------------------------------------

class TestDistance:
    """Funciones de cálculo y etiquetado de distancia."""

    def test_same_point(self):
        d = _approx_distance_km(-33.45, -70.66, -33.45, -70.66)
        assert d == pytest.approx(0, abs=0.01)

    def test_known_distance(self):
        # Santiago centro → Providencia: ~3-5 km
        d = _approx_distance_km(-33.4489, -70.6693, -33.4264, -70.6109)
        assert 2 < d < 8

    def test_distance_label_meters(self):
        assert _distance_label(0.5) == "500 m"
        assert _distance_label(0.1) == "100 m"

    def test_distance_label_km(self):
        assert _distance_label(1.5) == "1.5 km"
        assert _distance_label(10.0) == "10.0 km"


# ---------------------------------------------------------------------------
# 8. Formateo de resultados
# ---------------------------------------------------------------------------

class TestFormatResults:
    """format_results genera salida limpia y directa."""

    def test_format_with_results(self):
        results = [
            {
                "nombre": "Taller San José",
                "rating": 4.6,
                "distancia_label": "800 m",
                "direccion": "Av. Principal 123",
            },
            {
                "nombre": "Taller Express",
                "rating": 4.4,
                "distancia_label": "1.2 km",
                "direccion": "Calle Sur 456",
            },
        ]
        output = format_results(results)
        assert "Encontré estos lugares:" in output
        assert "1. Taller San José" in output
        assert "4.6★" in output
        assert "800 m" in output
        assert "2. Taller Express" in output

    def test_format_empty(self):
        output = format_results([])
        assert "No encontré" in output

    def test_format_no_rating(self):
        results = [{"nombre": "Tienda X", "rating": None, "distancia_label": "", "direccion": "Dir 1"}]
        output = format_results(results)
        assert "1. Tienda X" in output
        assert "★" not in output

    def test_format_long_address_truncated(self):
        results = [{
            "nombre": "Lugar",
            "rating": 4.0,
            "distancia_label": "",
            "direccion": "A" * 100,
        }]
        output = format_results(results)
        assert "..." in output


# ---------------------------------------------------------------------------
# 9. No results message
# ---------------------------------------------------------------------------

class TestNoResultsMessage:
    def test_with_query(self):
        msg = _no_results_message("taller")
        assert "taller" in msg
        assert "No encontré" in msg

    def test_without_query(self):
        msg = _no_results_message("")
        assert "No encontré" in msg


# ---------------------------------------------------------------------------
# 10. search_places — sin API key
# ---------------------------------------------------------------------------

class TestSearchPlacesNoApiKey:
    """search_places debe manejar ausencia de API key."""

    def test_no_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = search_places("farmacia cerca")
            assert result["found"] is False
            assert "no disponible" in result["message"].lower() or "No encontré" in result["message"]

    def test_empty_query(self):
        result = search_places("")
        assert result["found"] is False
        assert result["query_used"] == ""


# ---------------------------------------------------------------------------
# 11. search_places — con API mock
# ---------------------------------------------------------------------------

class TestSearchPlacesWithMock:
    """search_places con respuestas mock de la API."""

    MOCK_PLACES_RESPONSE = {
        "places": [
            {
                "id": "ChIJ_mock1",
                "displayName": {"text": "Farmacia Salud"},
                "formattedAddress": "Calle Falsa 123, Ciudad",
                "rating": 4.5,
                "userRatingCount": 80,
                "types": ["pharmacy"],
                "primaryTypeDisplayName": {"text": "Farmacia"},
                "location": {"latitude": -33.45, "longitude": -70.66},
            },
            {
                "id": "ChIJ_mock2",
                "displayName": {"text": "Farmacia Vida"},
                "formattedAddress": "Av. Central 456, Ciudad",
                "rating": 4.2,
                "userRatingCount": 55,
                "types": ["pharmacy"],
                "primaryTypeDisplayName": {"text": "Farmacia"},
                "location": {"latitude": -33.46, "longitude": -70.67},
            },
        ]
    }

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_text_search_returns_results(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self.MOCK_PLACES_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = search_places("farmacia en Santiago")

        assert result["found"] is True
        assert len(result["results"]) == 2
        assert result["results"][0]["nombre"] == "Farmacia Salud"
        assert result["results"][0]["rating"] == 4.5
        assert result["results"][0]["place_id"] == "ChIJ_mock1"
        assert result["search_type"] == "text"
        assert "Encontré estos lugares:" in result["message"]

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_nearby_search_with_location(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self.MOCK_PLACES_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        user_loc = {"lat": -33.44, "lng": -70.65}
        result = search_places("farmacia cerca de mí", user_location=user_loc)

        assert result["found"] is True
        # Debe haber calculado distancias
        for r in result["results"]:
            assert r["distancia_km"] is not None
            assert r["distancia_label"] != ""

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_no_results_from_api(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = search_places("lugar inexistente xyz123")

        assert result["found"] is False
        assert "No encontré" in result["message"]

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_api_error_handled(self, mock_post):
        import httpx as _httpx
        mock_post.side_effect = _httpx.ConnectError("Connection failed")

        result = search_places("farmacia cerca")

        assert result["found"] is False


# ---------------------------------------------------------------------------
# 12. Mapeo de keywords a tipos de Google Places
# ---------------------------------------------------------------------------

class TestKeywordToPlaceType:
    """_detect_place_type mapea correctamente."""

    @pytest.mark.parametrize("text,expected", [
        ("restaurante", "restaurant"),
        ("farmacia", "pharmacy"),
        ("taller", "car_repair"),
        ("mecánico", "car_repair"),
        ("banco", "bank"),
        ("cajero", "atm"),
        ("hospital", "hospital"),
        ("gym", "gym"),
        ("cafe", "cafe"),
        ("aeropuerto", "airport"),
    ])
    def test_type_mapping(self, text, expected):
        assert _detect_place_type(text) == expected

    def test_unknown_type(self):
        assert _detect_place_type("algo desconocido") == ""


# ---------------------------------------------------------------------------
# 13. Integración con gateway — intent detection intercepts
# ---------------------------------------------------------------------------

class TestGatewayPlaceSearchIntegration:
    """Verifica que ExternalGateway._try_place_search funciona."""

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_gateway_intercepts_place_query(self, mock_post):
        from core.operator.external_gateway import ExternalGateway

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "places": [{
                "id": "gw_test",
                "displayName": {"text": "Taller Mario"},
                "formattedAddress": "Calle Test 1",
                "rating": 4.8,
                "types": ["car_repair"],
                "location": {"latitude": -33.45, "longitude": -70.66},
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = ExternalGateway._try_place_search("dónde hay un taller cerca")
        assert "Taller Mario" in result
        assert "4.8★" in result

    def test_gateway_skips_non_place_query(self):
        from core.operator.external_gateway import ExternalGateway
        result = ExternalGateway._try_place_search("hola cómo estás")
        assert result == ""


# ---------------------------------------------------------------------------
# 14. FieldMask y optimización de costos por SKU tier
# ---------------------------------------------------------------------------

class TestFieldMaskOptimization:
    """Verifica que los FieldMask están correctamente configurados por tier."""

    def test_search_pro_mask_no_rating(self):
        from vectrax.integrations.place_search import _SEARCH_FIELDMASK_PRO
        assert "places.id" in _SEARCH_FIELDMASK_PRO
        assert "places.displayName" in _SEARCH_FIELDMASK_PRO
        assert "places.formattedAddress" in _SEARCH_FIELDMASK_PRO
        assert "places.location" in _SEARCH_FIELDMASK_PRO
        assert "places.primaryTypeDisplayName" in _SEARCH_FIELDMASK_PRO
        # NO debe tener campos Enterprise
        assert "rating" not in _SEARCH_FIELDMASK_PRO
        assert "userRatingCount" not in _SEARCH_FIELDMASK_PRO
        assert "Phone" not in _SEARCH_FIELDMASK_PRO
        assert "Opening" not in _SEARCH_FIELDMASK_PRO

    def test_search_enterprise_mask_has_rating(self):
        from vectrax.integrations.place_search import _SEARCH_FIELDMASK_ENTERPRISE
        # Debe incluir todo lo de Pro + rating
        assert "places.id" in _SEARCH_FIELDMASK_ENTERPRISE
        assert "places.displayName" in _SEARCH_FIELDMASK_ENTERPRISE
        assert "places.rating" in _SEARCH_FIELDMASK_ENTERPRISE
        assert "places.userRatingCount" in _SEARCH_FIELDMASK_ENTERPRISE
        # NO debe tener campos Enterprise+Atmosphere
        assert "reviews" not in _SEARCH_FIELDMASK_ENTERPRISE
        assert "editorial" not in _SEARCH_FIELDMASK_ENTERPRISE.lower()

    def test_details_pro_mask_no_contact(self):
        from vectrax.integrations.place_search import _DETAILS_FIELDMASK_PRO
        assert "id" in _DETAILS_FIELDMASK_PRO
        assert "displayName" in _DETAILS_FIELDMASK_PRO
        assert "googleMapsUri" in _DETAILS_FIELDMASK_PRO
        # NO debe tener Enterprise
        assert "Phone" not in _DETAILS_FIELDMASK_PRO
        assert "Opening" not in _DETAILS_FIELDMASK_PRO
        assert "rating" not in _DETAILS_FIELDMASK_PRO

    def test_details_enterprise_mask_has_contact(self):
        from vectrax.integrations.place_search import _DETAILS_FIELDMASK_ENTERPRISE
        assert "rating" in _DETAILS_FIELDMASK_ENTERPRISE
        assert "nationalPhoneNumber" in _DETAILS_FIELDMASK_ENTERPRISE
        assert "currentOpeningHours" in _DETAILS_FIELDMASK_ENTERPRISE
        assert "websiteUri" in _DETAILS_FIELDMASK_ENTERPRISE

    def test_no_atmosphere_fields_anywhere(self):
        """Nunca debemos pedir campos Enterprise+Atmosphere (más caro)."""
        from vectrax.integrations.place_search import (
            _SEARCH_FIELDMASK_PRO,
            _SEARCH_FIELDMASK_ENTERPRISE,
            _DETAILS_FIELDMASK_PRO,
            _DETAILS_FIELDMASK_ENTERPRISE,
        )
        atmosphere_fields = [
            "reviews", "editorialSummary", "delivery", "dineIn",
            "takeout", "servesBeer", "servesWine", "outdoorSeating",
        ]
        all_masks = [
            _SEARCH_FIELDMASK_PRO,
            _SEARCH_FIELDMASK_ENTERPRISE,
            _DETAILS_FIELDMASK_PRO,
            _DETAILS_FIELDMASK_ENTERPRISE,
        ]
        for mask in all_masks:
            for field in atmosphere_fields:
                assert field not in mask, f"{field} found in mask: {mask}"


# ---------------------------------------------------------------------------
# 15. Parámetros corregidos de API
# ---------------------------------------------------------------------------

class TestCorrectedApiParams:
    """Verifica que las llamadas a la API usen los parámetros correctos."""

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_text_search_sends_included_type(self, mock_post):
        """Text Search debe enviar includedType cuando se detecta un tipo."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        search_places("farmacia en Santiago")

        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body.get("includedType") == "pharmacy"

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_nearby_search_no_keyword_param(self, mock_post):
        """Nearby Search (New) NO soporta 'keyword'. Debe usar includedTypes."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        user_loc = {"lat": -33.45, "lng": -70.66}
        search_places("farmacia cerca de mí", user_location=user_loc)

        # Primera llamada es Nearby Search (luego fallback a Text Search)
        nearby_call = mock_post.call_args_list[0]
        body = nearby_call.kwargs.get("json") or nearby_call[1].get("json")
        # NO debe tener keyword
        assert "keyword" not in body
        # Debe tener includedTypes
        assert body.get("includedTypes") == ["pharmacy"]

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_nearby_search_radius_is_float(self, mock_post):
        """La API New requiere radius como float."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        user_loc = {"lat": -33.45, "lng": -70.66}
        search_places("banco cerca", user_location=user_loc)

        # Primera llamada es Nearby Search
        nearby_call = mock_post.call_args_list[0]
        body = nearby_call.kwargs.get("json") or nearby_call[1].get("json")
        radius = body["locationRestriction"]["circle"]["radius"]
        assert isinstance(radius, float)

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_fieldmask_header_uses_enterprise_by_default(self, mock_post):
        """Por defecto se usa Enterprise (con rating) para resultados útiles."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        search_places("restaurante en Madrid")

        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
        field_mask = headers["X-Goog-FieldMask"]
        assert "places.rating" in field_mask
        assert "places.userRatingCount" in field_mask

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key-123"})
    @patch("vectrax.integrations.place_search.httpx.post")
    def test_location_bias_radius_is_float(self, mock_post):
        """Text Search con location_bias debe usar radius float."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"places": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        user_loc = {"lat": -33.45, "lng": -70.66}
        search_places("restaurante", user_location=user_loc)

        # Primer call es nearby (porque no tiene "cerca" explícito,
        # va a text search)
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        if "locationBias" in body:
            radius = body["locationBias"]["circle"]["radius"]
            assert isinstance(radius, float)
