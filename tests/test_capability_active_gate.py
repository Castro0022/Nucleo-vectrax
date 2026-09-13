"""
tests/test_capability_active_gate.py — Cierre "Capability Self-Awareness →
ejecución real" (2026-09-13).

Fija el contrato de la promoción de VX_CAPABILITY_SELF_AWARENESS de
"solo sombra" a "activa" para rutas del SmartRouter que dependen de una
capacidad externa verificable (online_search, places_search):

  1. SmartRouter/IntentDecision siguen decidiendo la ruta — el gate nunca
     la sustituye, solo valida la capacidad que esa ruta necesita
     (core.self_observation.capability_context.capability_for_route).
  2. Capacidad disponible+autorizada -> se ejecuta la herramienta real
     existente exactamente como antes (resolve_online / search_places).
  3. Capacidad no disponible/no autorizada/degradada -> NUNCA se llama a
     la herramienta ni al LLM para esa ruta; se responde con la
     narración determinista existente (capability_narrator.narrate).
  4. Ambos flags (VX_CAPABILITY_SELF_AWARENESS + VX_CAPABILITY_RESPONSE_
     GROUNDING) en OFF -> comportamiento 100% idéntico al actual.
  5. Rutas sin capacidad mapeada (memory/local/identity/market/...)
     nunca activan el gate.
  6. Fail-safe: cualquier fallo del propio gate deja pasar (no bloquea).

Todas las pruebas ejecutan contra el pipeline real
(`ExternalGateway._resolve_via_pipeline` / `_active_capability_gate`),
no contra reimplementaciones. Solo se mockea la frontera real de I/O
(red/API externa) o, cuando se necesita determinismo de ruta, la
decisión de `SmartRouter.route()` — nunca la lógica del gate en sí.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.operator.external_gateway import ExternalGateway, reset_gateway
from core.operator.universal_bus import reset_bus
from core.orchestration.bootstrap import HEALTH_AVAILABLE, HEALTH_DEGRADED, HEALTH_UNAVAILABLE
from core.self_observation.capability_context import (
    CapabilityContext,
    CapabilityEntry,
    capability_for_route,
)


@pytest.fixture(autouse=True)
def clean_singletons():
    reset_bus()
    reset_gateway()
    yield
    reset_bus()
    reset_gateway()


@pytest.fixture(autouse=True)
def both_flags_default_off(monkeypatch):
    """Hermético por defecto: cada test enciende explícitamente lo que
    necesita."""
    monkeypatch.delenv("VX_CAPABILITY_SELF_AWARENESS", raising=False)
    monkeypatch.delenv("VX_CAPABILITY_RESPONSE_GROUNDING", raising=False)


def _both_flags_on(monkeypatch):
    monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
    monkeypatch.setenv("VX_CAPABILITY_RESPONSE_GROUNDING", "1")


def _fake_smart_route(strategy):
    from core.smart_router import SmartRoute, Intent, RiskLevel, Strategy

    intent_for_strategy = {
        Strategy.RESOLVE_ONLINE: Intent.ONLINE,
        Strategy.RESOLVE_PLACES: Intent.PLACE_SEARCH,
        Strategy.RESOLVE_LOCAL: Intent.LOCAL,
    }[strategy]
    return SmartRoute(
        intent=intent_for_strategy,
        topic="general",
        risk_level=RiskLevel.LOW,
        strategy=strategy,
        confidence=0.9,
        reason="test-forced-route",
    )


def _fake_router(strategy):
    """MagicMock de SmartRouter con .route() forzado a `strategy` y
    .record_feedback() como no-op — reemplaza SOLO la clasificación
    semántica (no lo que este archivo prueba), dejando `_resolve_via_
    pipeline` (el código real bajo prueba) ejecutar su lógica real de
    principio a fin."""
    sr = MagicMock()
    sr.route.return_value = _fake_smart_route(strategy)
    return sr


def _capability_ctx_with(name: str, health: str, authorized: bool) -> CapabilityContext:
    entry = CapabilityEntry(
        name=name, kind="capability", group="proveedores",
        exists=True, connected=(health != HEALTH_UNAVAILABLE),
        authorized=authorized, health=health,
        reason="forced-for-test", evidence_source="test", observed_at=0.0,
    )
    other = CapabilityEntry(
        name="_other_healthy", kind="capability", group="proveedores",
        exists=True, connected=True, authorized=True,
        health=HEALTH_AVAILABLE, reason="", evidence_source="test",
        observed_at=0.0,
    )
    gaps = [] if (health == HEALTH_AVAILABLE and authorized) else [entry]
    return CapabilityContext(
        query_domain=None, query_task_type=None, query_capability=False,
        entries=[entry, other], gaps=gaps, fallback_sources=[],
    )


# ===========================================================================
# 1. capability_for_route — mapeo mínimo reutilizado por el gate
# ===========================================================================

class TestCapabilityForRouteMapping:
    def test_resolve_online_maps_to_online_search(self):
        assert capability_for_route("resolve_online") == "online_search"

    def test_resolve_places_maps_to_places_search(self):
        assert capability_for_route("resolve_places") == "places_search"

    def test_routes_without_external_capability_are_unmapped(self):
        for route in (
            "resolve_local", "resolve_memory", "resolve_identity",
            "resolve_market", "route_single", "route_multi",
            "route_cognitive", "execute_command", "blocked",
        ):
            assert capability_for_route(route) is None

    def test_empty_or_none_route_is_unmapped(self):
        assert capability_for_route(None) is None
        assert capability_for_route("") is None

    def test_places_search_catalog_entry_is_real_and_healthy(self):
        """El módulo real vectrax.integrations.place_search existe e
        importa — la entrada nueva del catálogo no es un stub."""
        from core.self_observation.capability_context import build_capability_context
        ctx = build_capability_context(None)
        entry = next(e for e in ctx.entries if e.name == "places_search")
        assert entry.kind == "capability"
        assert entry.connected is True
        assert entry.health == HEALTH_AVAILABLE


# ===========================================================================
# 2. _active_capability_gate — contrato de flags y fail-safe (unitario,
#    pero sobre el método real de producción, no una reimplementación)
# ===========================================================================

class TestActiveCapabilityGateContract:
    def test_both_flags_off_is_noop(self):
        gw = ExternalGateway()
        text, mode = gw._active_capability_gate(
            "resolve_online", lang="es", user_id="u1",
        )
        assert (text, mode) == ("", "")

    def test_only_self_awareness_on_stays_shadow(self, monkeypatch):
        """Sin RESPONSE_GROUNDING, sigue siendo sombra — no promueve."""
        monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("online_search", HEALTH_UNAVAILABLE, True)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="es", user_id="u1",
            )
        assert (text, mode) == ("", "")

    def test_only_response_grounding_on_is_inert(self, monkeypatch):
        """Sin SELF_AWARENESS (observación), no hay contexto verificado
        que promover — mismo layering documentado que ya rige la Fase 4."""
        monkeypatch.setenv("VX_CAPABILITY_RESPONSE_GROUNDING", "1")
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("online_search", HEALTH_UNAVAILABLE, True)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="es", user_id="u1",
            )
        assert (text, mode) == ("", "")

    def test_unmapped_route_is_noop_even_with_both_flags(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        text, mode = gw._active_capability_gate(
            "resolve_local", lang="es", user_id="u1",
        )
        assert (text, mode) == ("", "")

    def test_healthy_authorized_capability_is_noop(self, monkeypatch):
        """Disponible -> el gate no interviene; el caller ejecuta la
        herramienta real exactamente como siempre."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("online_search", HEALTH_AVAILABLE, True)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="es", user_id="u1",
            )
        assert (text, mode) == ("", "")

    def test_unavailable_capability_blocks_with_grounded_text(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("online_search", HEALTH_UNAVAILABLE, True)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ), patch(
            "core.self_observation.capability_context.record_capability_gap",
        ) as mock_record:
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="es", user_id="u1",
            )
        assert text != ""
        assert mode == "capability_gap:online_search"
        mock_record.assert_called_once()
        _, kwargs = mock_record.call_args
        assert kwargs["capability_name"] == "online_search"
        assert kwargs["health"] == HEALTH_UNAVAILABLE

    def test_degraded_capability_blocks(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("places_search", HEALTH_DEGRADED, True)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_places", lang="es", user_id="u1",
            )
        assert text != ""
        assert mode == "capability_gap:places_search"

    def test_unauthorized_capability_blocks(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("places_search", HEALTH_AVAILABLE, False)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_places", lang="es", user_id="u1",
            )
        assert text != ""
        assert mode == "capability_gap:places_search"

    def test_unsupported_language_is_noop_even_with_gap(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        fake_ctx = _capability_ctx_with("online_search", HEALTH_UNAVAILABLE, True)
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=fake_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="fr", user_id="u1",
            )
        assert (text, mode) == ("", "")

    def test_gate_failure_is_fail_safe(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            side_effect=RuntimeError("boom"),
        ):
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="es", user_id="u1",
            )
        assert (text, mode) == ("", "")

    def test_no_evidence_for_mapped_capability_is_fail_safe(self, monkeypatch):
        """Si el contrato no trae la entrada esperada (evidencia
        insuficiente), el gate no bloquea — nunca inventa un gap."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        empty_ctx = CapabilityContext(
            query_domain=None, query_task_type=None, query_capability=False,
            entries=[], gaps=[], fallback_sources=[],
        )
        with patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=empty_ctx,
        ):
            text, mode = gw._active_capability_gate(
                "resolve_online", lang="es", user_id="u1",
            )
        assert (text, mode) == ("", "")


# ===========================================================================
# 3. End-to-end contra el pipeline real: _resolve_via_pipeline
#    (SmartRouter forzado por determinismo de ruta; el resto —gate,
#    ejecución de herramienta, grounding— es 100% código de producción).
# ===========================================================================

class TestActiveGateWiredIntoRealPipeline:
    def test_online_route_healthy_capability_calls_real_resolve_online(self, monkeypatch):
        """Escenario obligatorio: conocimiento faltante + búsqueda
        disponible -> ONLINE real. Verifica que resolve_online() se
        EJECUTÓ de verdad (spy), no que el texto lo afirme."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        from vectrax.resolver import Resolution

        fake_resolution = Resolution(
            mode="online", sovereign_answer="Respuesta real de búsqueda online.",
            answer="Respuesta real de búsqueda online.",
        )
        with patch(
            "core.smart_router.get_smart_router",
            return_value=_fake_router(__import__(
                "core.smart_router", fromlist=["Strategy"],
            ).Strategy.RESOLVE_ONLINE),
        ), patch(
            "vectrax.resolver.resolve_online", return_value=fake_resolution,
        ) as mock_online:
            answer, mode = gw._resolve_via_pipeline(
                "u_online", "busca información reciente sobre fusión nuclear",
                "telegram",
            )
        mock_online.assert_called_once()
        assert answer == "Respuesta real de búsqueda online."
        assert mode == "online"

    def test_online_route_unavailable_capability_blocks_and_never_calls_resolve_online(
        self, monkeypatch,
    ):
        """Escenario obligatorio: capability no disponible -> reconoce la
        limitación y NUNCA fabrica. resolve_online() NO debe llamarse."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        from core.smart_router import Strategy

        gap_ctx = _capability_ctx_with("online_search", HEALTH_UNAVAILABLE, True)
        with patch(
            "core.smart_router.get_smart_router",
            return_value=_fake_router(Strategy.RESOLVE_ONLINE),
        ), patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=gap_ctx,
        ), patch(
            "vectrax.resolver.resolve_online",
        ) as mock_online:
            answer, mode = gw._resolve_via_pipeline(
                "u_online_gap", "busca información reciente sobre fusión nuclear",
                "telegram",
            )
        mock_online.assert_not_called()
        assert answer  # respuesta honesta no vacía
        assert mode == "capability_gap:online_search"

    def test_places_route_healthy_capability_calls_real_search_places(self, monkeypatch):
        """Escenario obligatorio: restaurante/lugar -> PLACE_SEARCH real."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        from core.smart_router import Strategy

        with patch(
            "core.smart_router.get_smart_router",
            return_value=_fake_router(Strategy.RESOLVE_PLACES),
        ), patch(
            "vectrax.integrations.place_search.detect_place_intent",
            return_value=True,
        ), patch(
            "vectrax.integrations.place_search.search_places",
            return_value={"found": True, "message": "Restaurante real encontrado."},
        ) as mock_places:
            answer, mode = gw._resolve_via_pipeline(
                "u_places", "busca un restaurante italiano cerca de aquí",
                "telegram",
            )
        mock_places.assert_called_once()
        assert answer == "Restaurante real encontrado."
        assert mode == "places"

    def test_places_route_unavailable_capability_blocks_and_never_calls_search_places(
        self, monkeypatch,
    ):
        """Escenario obligatorio: capability inexistente/degradada ->
        reconoce la limitación, NUNCA llama a Google Places ni inventa."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        from core.smart_router import Strategy

        gap_ctx = _capability_ctx_with("places_search", HEALTH_UNAVAILABLE, True)
        with patch(
            "core.smart_router.get_smart_router",
            return_value=_fake_router(Strategy.RESOLVE_PLACES),
        ), patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=gap_ctx,
        ), patch(
            "vectrax.integrations.place_search.search_places",
        ) as mock_places:
            answer, mode = gw._resolve_via_pipeline(
                "u_places_gap", "busca un restaurante italiano cerca de aquí",
                "telegram",
            )
        mock_places.assert_not_called()
        assert answer
        assert mode == "capability_gap:places_search"

    def test_local_route_never_gated_even_with_both_flags_on(self, monkeypatch):
        """Escenario obligatorio: dato disponible en memoria -> LOCAL, sin
        que el gate interfiera (resolve_local no depende de capacidad
        externa verificable — capability_for_route la deja sin mapear)."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        from vectrax.resolver import Resolution
        from core.smart_router import Strategy

        fake_local = Resolution(
            mode="local", sovereign_answer="Respuesta real desde tu memoria.",
            answer="Respuesta real desde tu memoria.", context_stars=3, top_score=0.9,
        )
        with patch(
            "core.smart_router.get_smart_router",
            return_value=_fake_router(Strategy.RESOLVE_LOCAL),
        ), patch(
            "vectrax.resolver.resolve_local", return_value=fake_local,
        ) as mock_local:
            answer, mode = gw._resolve_via_pipeline(
                "u_local", "qué me dijiste ayer sobre el proyecto", "telegram",
            )
        mock_local.assert_called_once()
        assert answer == "Respuesta real desde tu memoria."
        assert mode == "local"

    def test_flags_off_preserves_current_behavior_online(self, monkeypatch):
        """Con ambos flags apagados (default), el comportamiento actual es
        idéntico: la herramienta real se ejecuta sin que el gate exista."""
        gw = ExternalGateway()
        from vectrax.resolver import Resolution
        from core.smart_router import Strategy

        fake_resolution = Resolution(
            mode="online", sovereign_answer="Respuesta sin gate activo.",
            answer="Respuesta sin gate activo.",
        )
        with patch(
            "core.smart_router.get_smart_router",
            return_value=_fake_router(Strategy.RESOLVE_ONLINE),
        ), patch(
            "vectrax.resolver.resolve_online", return_value=fake_resolution,
        ) as mock_online:
            answer, mode = gw._resolve_via_pipeline(
                "u_no_flags", "busca información reciente sobre fusión nuclear",
                "telegram",
            )
        mock_online.assert_called_once()
        assert answer == "Respuesta sin gate activo."
        assert mode == "online"


# ===========================================================================
# 4. Regresión: SmartRouter real (sin mockear) sigue clasificando estas
#    rutas naturalmente — confirma que el gate no rompió la clasificación.
# ===========================================================================

class TestRealSmartRouterClassificationUnaffected:
    def test_real_router_still_routes_place_query_to_places(self):
        from core.smart_router import SmartRouter, Strategy
        route = SmartRouter().route(
            "busca un restaurante italiano cerca de mi ubicación",
            "user", "u_real_places",
        )
        assert route.strategy == Strategy.RESOLVE_PLACES

    def test_real_router_still_routes_factual_query_to_online(self):
        from core.smart_router import SmartRouter, Strategy
        route = SmartRouter().route(
            "cuál es la capital de Mongolia", "user", "u_real_online",
        )
        assert route.strategy == Strategy.RESOLVE_ONLINE
