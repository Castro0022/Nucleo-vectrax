"""
Tests — Frontera Constitucional PRE-ejecución (P0)
=====================================================================
Parte 1: orquestación pura de `pre_execution_gate.authorize()` — mockea
`constitutional_filter.evaluate()` y `decision_authority.check_authority()`
para controlar el veredicto de forma determinista. Cubre la matriz de 7
tipos x modo, más cache y aislamiento entre fronteras.

Parte 2: integración por frontera contra los executors reales (o dobles
de prueba equivalentes a los reales — providers falsos que implementan
`BaseLLMProvider`, `httpx.post` monkeypatched, etc.), verificando conteo
exacto de invocaciones.

Parte 3: los 2 casos específicos de esta sesión — una sola autorización
para la cadena LLM de 3 pasos, e independencia real entre `online` y `llm`
cuando `_interpret_with_llm()` está anidado dentro de `resolve_online()`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncIterator, List, Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.operator import boundary_mode
from core.operator import pre_execution_gate as gate
from core.operator.execution_context import ExecutionContext, ORIGIN_SYSTEM, ORIGIN_USER
from core.operator.constitutional_filter import (
    ConstitutionalVerdict,
    PrincipleResult,
    PrincipleVerdict,
)
from core.operator.decision_authority import Authority, DecisionResult


# ---------------------------------------------------------------------------
# Helpers — construir veredictos falsos deterministas
# ---------------------------------------------------------------------------

def _fake_verdict(overall: PrincipleVerdict, action: str = "resolve_llm") -> ConstitutionalVerdict:
    results = tuple(
        PrincipleResult(number=i + 1, name=f"P{i+1}", verdict=PrincipleVerdict.PASS, reason="ok")
        for i in range(6)
    ) + (PrincipleResult(number=7, name="P7", verdict=overall, reason="forced"),)
    # Si overall no es el peor del set, forzamos el primero también.
    if overall != PrincipleVerdict.PASS:
        results = (PrincipleResult(number=1, name="P1", verdict=overall, reason="forced"),) + results[1:]
    return ConstitutionalVerdict(
        proposal_id="test", action=action, results=results, overall=overall, mode="enforce",
    )


def _decision_result(auto_approved: bool) -> DecisionResult:
    return DecisionResult(
        action="resolve_llm",
        authority=Authority.AUTO if auto_approved else Authority.AUTHORIZED,
        auto_approved=auto_approved,
        reason="test",
    )


@pytest.fixture(autouse=True)
def _reset_boundary_mode_cache():
    """Evita fugas de estado entre tests (cache TTL de boundary_mode)."""
    boundary_mode._cache["state"] = None
    boundary_mode._cache["loaded_at"] = 0.0
    yield
    boundary_mode._cache["state"] = None
    boundary_mode._cache["loaded_at"] = 0.0


def _ctx(boundary_action: str = "resolve_llm", **kwargs) -> ExecutionContext:
    defaults = dict(origin=ORIGIN_USER, action=boundary_action, actor_id="tg:123")
    defaults.update(kwargs)
    return ExecutionContext(**defaults)


# ---------------------------------------------------------------------------
# PARTE 1 — orquestación pura de authorize()
# ---------------------------------------------------------------------------

class TestGateOrchestration:

    def test_shadow_missing_context_executes_and_logs_would_block(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.SHADOW)
        ledger_calls = []
        monkeypatch.setattr(gate, "_record_ledger", lambda *a, **k: ledger_calls.append((a, k)))

        decision = gate.authorize("llm", None)

        assert decision.should_execute is True
        assert decision.execution == gate.WOULD_BLOCK_MISSING_CONTEXT
        assert len(ledger_calls) == 1

    def test_enforce_missing_context_blocks(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)

        decision = gate.authorize("llm", None)

        assert decision.should_execute is False
        assert decision.execution == gate.BLOCKED_MISSING_CONTEXT
        assert decision.authorized_targets == []

    def test_enforce_invalid_context_object_also_blocks(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        invalid_ctx = _ctx(boundary_action="")  # action vacío -> is_valid() False

        decision = gate.authorize("llm", invalid_ctx)

        assert decision.should_execute is False
        assert decision.execution == gate.BLOCKED_MISSING_CONTEXT

    def test_enforce_block_zero_execution(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        decision = gate.authorize("llm", _ctx())

        assert decision.should_execute is False
        assert decision.execution == gate.BLOCKED
        assert decision.decision == gate.DECISION_BLOCK
        assert decision.authorized_targets == []

    def test_enforce_pass_executes_with_full_targets(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.PASS))

        decision = gate.authorize("llm", _ctx(), requested_targets=["openai", "ollama"])

        assert decision.should_execute is True
        assert decision.execution == gate.EXECUTE
        assert decision.authorized_targets == ["openai", "ollama"]

    def test_enforce_caution_auto_approved_executes_full_set(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.CAUTION))
        monkeypatch.setattr(gate, "_consult_decision_authority", lambda p: _decision_result(True))

        decision = gate.authorize("llm", _ctx(), requested_targets=["openai"])

        assert decision.should_execute is True
        assert decision.execution == gate.EXECUTE
        assert decision.correction_applied is True
        assert decision.authorized_targets == ["openai"]

    def test_enforce_caution_denied_single_target_blocks(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.CAUTION))
        monkeypatch.setattr(gate, "_consult_decision_authority", lambda p: _decision_result(False))

        decision = gate.authorize("online", _ctx(boundary_action="resolve_online"))

        assert decision.should_execute is False
        assert decision.execution == gate.BLOCKED
        assert decision.correction_applied is True

    def test_enforce_caution_denied_fanout_empties_set_not_block(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.CAUTION))
        monkeypatch.setattr(gate, "_consult_decision_authority", lambda p: _decision_result(False))

        decision = gate.authorize("llm", _ctx(), requested_targets=["openai", "ollama"])

        assert decision.should_execute is False
        assert decision.execution == gate.SKIPPED_EMPTY_AUTHORIZED_SET
        assert decision.execution != gate.BLOCKED  # estado propio, no se reclasifica
        assert decision.authorized_targets == []
        assert decision.decision == gate.DECISION_CAUTION

    def test_shadow_valid_context_always_executes_regardless_of_verdict(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.SHADOW)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        decision = gate.authorize("places", _ctx(boundary_action="resolve_places"), requested_targets=None)

        assert decision.should_execute is True
        assert decision.execution == gate.EXECUTE

    def test_cache_reuses_decision_without_reevaluating(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        calls = {"n": 0}

        def _counting_evaluate(p, mode):
            calls["n"] += 1
            return _fake_verdict(PrincipleVerdict.PASS)

        monkeypatch.setattr(gate, "evaluate", _counting_evaluate)
        ctx = _ctx()

        d1 = gate.authorize("llm", ctx)
        d2 = gate.authorize("llm", ctx)

        assert calls["n"] == 1
        assert d1 is d2

    def test_boundary_isolation_one_enforce_others_shadow(self, monkeypatch):
        modes = {"places": boundary_mode.ENFORCE}
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: modes.get(b, boundary_mode.SHADOW))
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        places_decision = gate.authorize("places", _ctx(boundary_action="resolve_places"))
        llm_decision = gate.authorize("llm", None)
        online_decision = gate.authorize("online", None)
        market_decision = gate.authorize("market", None)

        assert places_decision.should_execute is False
        assert places_decision.execution == gate.BLOCKED
        for d in (llm_decision, online_decision, market_decision):
            assert d.should_execute is True
            assert d.execution == gate.WOULD_BLOCK_MISSING_CONTEXT

    def test_system_origin_fail_closed_no_exception(self, monkeypatch):
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)

        decision = gate.authorize("market", None)

        assert decision.should_execute is False
        assert decision.execution == gate.BLOCKED_MISSING_CONTEXT

    def test_authorize_never_raises_on_internal_error(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("simulated bug")

        monkeypatch.setattr(gate, "_authorize_impl", _boom)

        decision = gate.authorize("llm", _ctx())  # no debe lanzar

        assert decision.decision == gate.DECISION_ERROR


# ---------------------------------------------------------------------------
# PARTE 2 — integración por frontera contra executors reales
# ---------------------------------------------------------------------------

class TestLLMBoundaryIntegration:
    """core/intelligence/router.py::IntelligenceRouter.route()/query_parallel()"""

    @pytest.mark.asyncio
    async def test_route_enforce_block_zero_provider_calls(self, monkeypatch):
        from core.abstraction.base import BaseLLMProvider, GenerateRequest, GenerateResponse, ProviderType
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry
        from core.intelligence.router import IntelligenceRouter

        calls = {"n": 0}

        class SpyProvider(BaseLLMProvider):
            def __init__(self):
                super().__init__(provider_type=ProviderType.OLLAMA, endpoint="mock://")

            async def generate(self, request: GenerateRequest) -> GenerateResponse:
                calls["n"] += 1
                return GenerateResponse(content="x", model=request.model, provider="spy")

            async def stream(self, request): yield "x"
            async def health_check(self): return True
            async def list_models(self): return ["spy-model"]

        reg = IntelligenceConnectorRegistry()
        desc = reg.register("spy", SpyProvider(), None if False else __import__(
            "core.abstraction.base", fromlist=["ProviderType"]).ProviderType.OLLAMA, is_local=True)
        desc.healthy = True
        router = IntelligenceRouter(registry=reg)
        router._initialized = True

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        ctx = _ctx()
        result = await router.route("hola", execution_context=ctx)

        assert calls["n"] == 0
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_query_parallel_enforce_pass_calls_exactly_authorized(self, monkeypatch):
        from core.abstraction.base import BaseLLMProvider, GenerateRequest, GenerateResponse, ProviderType
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry
        from core.intelligence.router import IntelligenceRouter

        calls = {"a": 0, "b": 0}

        class SpyProvider(BaseLLMProvider):
            def __init__(self, name):
                super().__init__(provider_type=ProviderType.OLLAMA, endpoint="mock://")
                self._name = name

            async def generate(self, request: GenerateRequest) -> GenerateResponse:
                calls[self._name] += 1
                return GenerateResponse(content="x", model=request.model, provider=self._name)

            async def stream(self, request): yield "x"
            async def health_check(self): return True
            async def list_models(self): return [f"{self._name}-model"]

        reg = IntelligenceConnectorRegistry()
        for name in ("a", "b"):
            desc = reg.register(name, SpyProvider(name), ProviderType.OLLAMA, is_local=True)
            desc.healthy = True
        router = IntelligenceRouter(registry=reg)
        router._initialized = True

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.PASS))

        ctx = _ctx()
        result = await router.query_parallel("hola", execution_context=ctx)

        assert calls == {"a": 1, "b": 1}
        assert result.providers_queried == 2

    @pytest.mark.asyncio
    async def test_query_parallel_caution_denied_fanout_zero_calls(self, monkeypatch):
        from core.abstraction.base import BaseLLMProvider, GenerateRequest, GenerateResponse, ProviderType
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry
        from core.intelligence.router import IntelligenceRouter

        calls = {"n": 0}

        class SpyProvider(BaseLLMProvider):
            def __init__(self):
                super().__init__(provider_type=ProviderType.OLLAMA, endpoint="mock://")

            async def generate(self, request):
                calls["n"] += 1
                return GenerateResponse(content="x", model=request.model, provider="spy")

            async def stream(self, request): yield "x"
            async def health_check(self): return True
            async def list_models(self): return ["spy-model"]

        reg = IntelligenceConnectorRegistry()
        desc = reg.register("spy", SpyProvider(), ProviderType.OLLAMA, is_local=True)
        desc.healthy = True
        router = IntelligenceRouter(registry=reg)
        router._initialized = True

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.CAUTION))
        monkeypatch.setattr(gate, "_consult_decision_authority", lambda p: _decision_result(False))

        ctx = _ctx()
        result = await router.query_parallel("hola", execution_context=ctx)

        assert calls["n"] == 0
        assert result.providers_succeeded == 0

    @pytest.mark.asyncio
    async def test_route_shadow_missing_context_unchanged_behavior(self, monkeypatch):
        from core.abstraction.base import BaseLLMProvider, GenerateRequest, GenerateResponse, ProviderType
        from core.intelligence.connector_registry import IntelligenceConnectorRegistry
        from core.intelligence.router import IntelligenceRouter

        calls = {"n": 0}

        class SpyProvider(BaseLLMProvider):
            def __init__(self):
                super().__init__(provider_type=ProviderType.OLLAMA, endpoint="mock://")

            async def generate(self, request):
                calls["n"] += 1
                return GenerateResponse(content="x", model=request.model, provider="ollama")

            async def stream(self, request): yield "x"
            async def health_check(self): return True
            async def list_models(self): return ["ollama-model"]

        reg = IntelligenceConnectorRegistry()
        # Nombre "ollama" — el ModelRouter selecciona por nombre de provider
        # conocido; un nombre arbitrario como "spy" nunca es elegido como
        # primario/fallback (mismo patrón que tests/test_intelligence.py).
        desc = reg.register("ollama", SpyProvider(), ProviderType.OLLAMA, is_local=True)
        desc.healthy = True
        router = IntelligenceRouter(registry=reg)
        router._initialized = True

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.SHADOW)

        result = await router.route("hola")  # sin execution_context — no migrado

        assert calls["n"] == 1  # comportamiento idéntico a hoy
        assert result.success is True


class TestPlacesBoundaryIntegration:
    """vectrax/integrations/place_search.py::search_places()"""

    def test_enforce_block_zero_http_calls(self, monkeypatch):
        from vectrax.integrations import place_search

        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "fake-key")
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        calls = {"n": 0}
        monkeypatch.setattr(place_search, "_text_search", lambda **k: calls.__setitem__("n", calls["n"] + 1) or [])
        monkeypatch.setattr(place_search, "_nearby_search", lambda **k: calls.__setitem__("n", calls["n"] + 1) or [])

        ctx = _ctx(boundary_action="resolve_places")
        result = place_search.search_places("farmacia cerca de mi", execution_context=ctx)

        assert calls["n"] == 0
        assert result["found"] is False

    def test_shadow_missing_context_still_calls_search(self, monkeypatch):
        from vectrax.integrations import place_search

        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "fake-key")
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.SHADOW)

        calls = {"n": 0}

        def _fake_text_search(**k):
            calls["n"] += 1
            return []

        monkeypatch.setattr(place_search, "_text_search", _fake_text_search)

        result = place_search.search_places("farmacia cerca de mi")  # sin contexto

        assert calls["n"] == 1


class TestMarketBoundaryIntegration:
    """intents/market_intents.py::handle_market_intent() y
    services/market_vigilance.py::MarketVigilance.fetch_state()"""

    def test_handle_market_intent_enforce_block_zero_handler_calls(self, monkeypatch):
        from intents import market_intents

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        calls = {"n": 0}
        monkeypatch.setattr(market_intents, "_handle_price", lambda params: calls.__setitem__("n", calls["n"] + 1) or {"success": True})

        ctx = _ctx(boundary_action="resolve_market")
        result = market_intents.handle_market_intent("market_price", {"symbol": "BTCUSDT"}, execution_context=ctx)

        assert calls["n"] == 0
        assert result["success"] is False

    def test_fetch_state_enforce_block_zero_io(self, monkeypatch):
        from services.market_vigilance import MarketVigilance

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.BLOCK))

        calls = {"n": 0}

        def _fake_get_coin_details(symbol):
            calls["n"] += 1
            return {"success": True}

        monkeypatch.setattr("services.market_vigilance.get_coin_details", _fake_get_coin_details)

        v = MarketVigilance()
        ctx = _ctx(boundary_action="resolve_market")
        state = v.fetch_state("BTCUSDT", execution_context=ctx)

        assert calls["n"] == 0
        assert state is None

    def test_two_market_executors_independently_gated(self, monkeypatch):
        """Confirma que handle_market_intent() y fetch_state() son 2
        insertion points independientes — bloquear uno no requiere que el
        otro también lo esté (mutuamente excluyentes por request, sin cache
        compartido)."""
        from intents import market_intents
        from services.market_vigilance import MarketVigilance

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", lambda p, mode: _fake_verdict(PrincipleVerdict.PASS))

        handle_calls = {"n": 0}
        monkeypatch.setattr(
            market_intents, "_handle_price",
            lambda params: handle_calls.__setitem__("n", handle_calls["n"] + 1) or {"success": True},
        )
        fetch_calls = {"n": 0}
        monkeypatch.setattr(
            "services.market_vigilance.get_coin_details",
            lambda symbol: fetch_calls.__setitem__("n", fetch_calls["n"] + 1) or {"success": True},
        )

        market_intents.handle_market_intent(
            "market_price", {"symbol": "BTCUSDT"},
            execution_context=_ctx(boundary_action="resolve_market"),
        )
        MarketVigilance().fetch_state("BTCUSDT", execution_context=_ctx(boundary_action="resolve_market"))

        assert handle_calls["n"] == 1
        assert fetch_calls["n"] == 1


# ---------------------------------------------------------------------------
# PARTE 3 — casos específicos de esta sesión
# ---------------------------------------------------------------------------

class TestSessionSpecificFindings:

    def test_llm_three_step_fallback_single_authorization(self, monkeypatch):
        """Caso (8) del plan: la cadena LLM de 3 pasos evalúa UNA sola vez,
        aunque se intenten 2+ executors REALES distintos (no mockeados a
        nivel de método, para probar de verdad el cache de
        `resolved_decision` a través de `pre_execution_gate.authorize()`).
        """
        from core.operator import external_gateway as eg

        calls = {"evaluate": 0}

        def _counting_evaluate(p, mode):
            calls["evaluate"] += 1
            return _fake_verdict(PrincipleVerdict.PASS)

        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: boundary_mode.ENFORCE)
        monkeypatch.setattr(gate, "evaluate", _counting_evaluate)

        # Paso 1 (Intelligence Bridge): no listo -> nunca llega a llamar
        # authorize() por dentro (no se alcanza IntelligenceRouter.route()).
        import vectrax.intelligence_bridge as ib
        monkeypatch.setattr(ib, "is_ready", lambda: False)
        monkeypatch.setattr(ib, "initialize", lambda: {"providers_detected": []})

        # Paso 2 (OpenAI directo, REAL _generate_openai_direct -> core.llm_call.complete):
        # sin OPENAI_API_KEY, complete() retorna "no_key" ANTES de llegar a su
        # propio authorize() interno -> tampoco consume una evaluación real.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # Paso 3 (Ollama local, REAL _generate_ollama_local): SÍ llama a
        # authorize() de verdad -> debe golpear el cache (paso 0) y NO
        # volver a evaluar. Se mockea httpx.post para no depender de si hay
        # un servidor Ollama real corriendo en esta máquina (determinismo).
        import httpx as _httpx

        def _fake_post(*a, **k):
            raise ConnectionError("no ollama running (test double)")

        monkeypatch.setattr(_httpx, "post", _fake_post)
        monkeypatch.setattr(
            eg.ExternalGateway, "_synthesize_from_context",
            classmethod(lambda cls, *a, **k: ""),
        )

        answer = eg.ExternalGateway._generate_cognitive_response(
            eg.ExternalGateway.__new__(eg.ExternalGateway),
            "hola", "tg:1", "user", "",
        )

        assert calls["evaluate"] == 1

    def test_online_and_llm_boundaries_never_share_verdict(self, monkeypatch):
        """Caso (9) del plan: online PASS + llm BLOCK simultáneos ->
        _search_multi_engine() se ejecuta, los executors LLM de
        interpretación NO."""
        from vectrax import resolver

        modes = {"online": boundary_mode.ENFORCE, "llm": boundary_mode.ENFORCE}
        monkeypatch.setattr(boundary_mode, "get_mode", lambda b: modes.get(b, boundary_mode.SHADOW))

        def _verdict_by_boundary(p, mode):
            action = p.action
            if action == "resolve_online":
                return _fake_verdict(PrincipleVerdict.PASS, action=action)
            if action == "resolve_llm":
                return _fake_verdict(PrincipleVerdict.BLOCK, action=action)
            return _fake_verdict(PrincipleVerdict.PASS, action=action)

        monkeypatch.setattr(gate, "evaluate", _verdict_by_boundary)

        search_calls = {"n": 0}
        monkeypatch.setattr(
            resolver, "_search_multi_engine",
            lambda query, max_results=5: (
                search_calls.__setitem__("n", search_calls["n"] + 1)
                or ([resolver.Source(title="t", url="u", snippet="s")], ["duckduckgo"])
            ),
        )

        llm_calls = {"n": 0}
        import vectrax.intelligence_bridge as ib
        monkeypatch.setattr(ib, "is_ready", lambda: True)

        def _fake_route_single(prompt, **k):
            llm_calls["n"] += 1
            return {"success": True, "content": "interpreted"}

        monkeypatch.setattr(ib, "route_single", _fake_route_single)

        online_ctx = _ctx(boundary_action="resolve_online")
        resolution = resolver.resolve_online("que es bitcoin", "user", "tg:1", execution_context=online_ctx)

        assert search_calls["n"] == 1
        assert llm_calls["n"] == 0
        assert resolution.sovereign_answer  # cayó a _synthesize_online (sin LLM)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
