"""
Test de Integración End-to-End
=================================
Simula el flujo completo: mensaje → SmartRouter → resolución → respuesta.
Verifica que todo el pipeline funciona sin errores.

Creado: 2026-03-28
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ===========================================================================
# Pipeline completo via ExternalGateway
# ===========================================================================

class TestFullPipeline:

    def test_greeting_processed(self):
        """Un saludo se procesa sin error."""
        from core.operator.external_gateway import ExternalGateway
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="test:e2e_001",
            content="hola",
            channel="web",
        )
        assert result.processed is True
        assert result.response != ""

    def test_identity_query(self):
        """Pregunta de identidad sin memoria responde sin crash."""
        from core.operator.external_gateway import ExternalGateway
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="test:e2e_002",
            content="quién soy",
            channel="web",
        )
        assert result.processed is True
        assert result.response != ""

    def test_memory_store_and_recall(self):
        """Almacena interacción y puede recuperar contexto."""
        from vectrax.user_memory import store_memory, get_memory_context, clear_memory

        uid = "test:e2e_memory"
        clear_memory(uid)

        store_memory(uid, "me llamo TestUser", "Registrado.")
        ctx = get_memory_context(uid)
        assert "TestUser" in ctx or "test" in ctx.lower()

        clear_memory(uid)


# ===========================================================================
# SmartRouter classification
# ===========================================================================

class TestSmartRouterClassification:

    def test_market_intent(self):
        """'btc' se clasifica como MARKET."""
        from core.smart_router import get_smart_router, Strategy
        sr = get_smart_router()
        route = sr.route("btc", "user", "test")
        assert route.strategy == Strategy.RESOLVE_MARKET

    def test_place_intent(self):
        """'restaurantes cerca' se clasifica como PLACES."""
        from core.smart_router import get_smart_router, Strategy
        sr = get_smart_router()
        route = sr.route("restaurantes cerca de mí", "user", "test")
        assert route.strategy == Strategy.RESOLVE_PLACES

    def test_online_intent(self):
        """Pregunta factual se clasifica como ONLINE."""
        from core.smart_router import get_smart_router, Strategy
        sr = get_smart_router()
        route = sr.route("qué es la computación cuántica", "user", "test")
        assert route.strategy in (
            Strategy.RESOLVE_ONLINE, Strategy.ROUTE_SINGLE,
            Strategy.ROUTE_COGNITIVE,
        )

    def test_identity_intent(self):
        """'quién soy' se clasifica como IDENTITY."""
        from core.smart_router import get_smart_router, Strategy
        sr = get_smart_router()
        route = sr.route("quién soy", "user", "test")
        assert route.strategy == Strategy.RESOLVE_IDENTITY


# ===========================================================================
# Market intents detection
# ===========================================================================

class TestMarketDetection:

    def test_standalone_btc(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("btc")
        assert result is not None
        assert result[1].get("symbol") == "BTCUSDT"

    def test_precio_btc(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("precio btc")
        assert result is not None

    def test_bitcoin_price(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("bitcoin price")
        assert result is not None

    def test_cuanto_vale(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("cuánto vale btc")
        assert result is not None


# ===========================================================================
# Place search context detection
# ===========================================================================

class TestPlaceContextDetection:

    def test_tengo_hambre(self):
        from vectrax.integrations.place_search import detect_place_intent
        assert detect_place_intent("tengo hambre") is True

    def test_algo_tranquilo(self):
        from vectrax.integrations.place_search import detect_place_intent
        assert detect_place_intent("quiero algo tranquilo") is True

    def test_normal_question_not_place(self):
        from vectrax.integrations.place_search import detect_place_intent
        assert detect_place_intent("qué es python") is False


# ===========================================================================
# Law enforcement
# ===========================================================================

class TestLawEnforcement:

    def test_all_laws_pass(self):
        from core.operator.law_enforcement import enforce_all_laws
        result = enforce_all_laws(
            input_classified=True,
            classification="market",
            interaction_recorded=True,
            action_logged=True,
            has_correlation_id=True,
        )
        assert result.all_passed is True
        assert len(result.checks) == 7

    def test_violations_detected(self):
        from core.operator.law_enforcement import enforce_all_laws
        result = enforce_all_laws(
            input_classified=False,
            interaction_recorded=False,
            action_logged=False,
            has_correlation_id=False,
        )
        assert result.all_passed is False
        assert len(result.violations) >= 2


# ===========================================================================
# Decision authority
# ===========================================================================

class TestDecisionAuthority:

    def test_internal_auto(self):
        from core.operator.decision_authority import can_auto_execute
        assert can_auto_execute("classify_message") is True
        assert can_auto_execute("store_memory") is True
        assert can_auto_execute("generate_response") is True

    def test_external_requires_auth(self):
        from core.operator.decision_authority import requires_creator
        assert requires_creator("apply_autopatch") is True
        assert requires_creator("execute_trade") is True

    def test_blocked_always(self):
        from core.operator.decision_authority import is_blocked
        assert is_blocked("modify_identity") is True
        assert is_blocked("modify_fundamental_laws") is True


# ===========================================================================
# Identity integrity
# ===========================================================================

class TestIdentityIntegrity:

    def test_identity_immutable(self):
        from core.operator.identity import IDENTITY, verify_identity_integrity
        assert IDENTITY.name == "Vectrax Core"
        assert IDENTITY.creator == "Mario Bravo Castro"
        assert verify_identity_integrity() is True

    def test_7_laws_exist(self):
        from core.operator.identity import FUNDAMENTAL_LAWS
        assert len(FUNDAMENTAL_LAWS) == 7

    def test_invariant_check(self):
        from core.invariants import check_identity_integrity
        result = check_identity_integrity()
        assert result is None  # None = no violation


# ===========================================================================
# Hypothesis persistence
# ===========================================================================

class TestHypothesisPersistence:

    def test_hypothesis_survives_reset(self):
        """Hipótesis se persiste en SQLite y sobrevive reset del singleton."""
        from core.operator.hypothesis_engine import (
            get_hypothesis_engine, reset_hypothesis_engine,
        )

        engine = get_hypothesis_engine()
        hyp = engine.propose(
            description="E2E test hypothesis",
            tags=["e2e_test"],
        )
        hyp_id = hyp.id

        # Reset singleton (simula reinicio)
        reset_hypothesis_engine()

        # Recargar — debe encontrar la hipótesis desde SQLite
        engine2 = get_hypothesis_engine()
        loaded = engine2.get(hyp_id)

        assert loaded is not None
        assert loaded.description == "E2E test hypothesis"
        assert "e2e_test" in loaded.tags

        # Cleanup
        reset_hypothesis_engine()
