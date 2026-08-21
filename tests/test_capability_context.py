"""
tests/test_capability_context.py — Fase 3, paso 5.

Fija el contrato de `core/self_observation/capability_context.py`:
  1. engine_registry.py sigue siendo la ÚNICA fuente canónica de motores:
     exactamente 48, distribución core=21/observe=17/gated_internal=5/
     external=5 — este módulo nunca la altera ni la duplica.
  2. Los 5 elementos identificados en la Fase 3 (online_search,
     llm_providers, domain_knowledge, auto_builder, telegram_gateway) se
     representan con `kind` != "engine" — nunca como EngineSpec.
  3. El contrato es compuesto (exists/connected/authorized/health
     independientes) — una capacidad puede existir+conectarse+estar
     disponible pero no autorizada, sin colapsar a una sola etiqueta.
  4. record_capability_gap() escribe en observation_ledger
     (domain="self_knowledge"), deduplicado por fingerprint determinista,
     sin generar ninguna CognitiveProposal/idea.
  5. Nada de esto cambia el comportamiento existente: no hay flag, no se
     conecta a external_gateway.py todavía.
"""

from __future__ import annotations

import pytest

from core.intent_ssot import resolve_intent
from core.orchestration import engine_registry as reg
from core.orchestration.bootstrap import HEALTH_AVAILABLE, HEALTH_DEGRADED, HEALTH_UNAVAILABLE
from core.self_observation.capability_context import (
    CapabilityContext,
    CapabilityEntry,
    build_capability_context,
    record_capability_gap,
)


# ===========================================================================
# 1. engine_registry.py sigue siendo la única fuente canónica (48 motores)
# ===========================================================================

class TestCanonicalEngineRegistryUntouched:
    def test_registry_still_has_exactly_48_unique_engines(self):
        names = [s.name for s in reg.all_specs()]
        assert len(names) == 48
        assert len(set(names)) == 48

    def test_tier_distribution_unchanged(self):
        from collections import Counter
        counts = Counter(s.tier.value for s in reg.all_specs())
        assert counts == {
            "core": 21, "observe": 17, "gated_internal": 5, "external": 5,
        }

    def test_five_capability_items_not_registered_as_engines(self):
        for name in (
            "online_search", "llm_providers", "domain_knowledge",
            "auto_builder", "telegram_gateway",
        ):
            assert reg.get(name) is None, f"{name} NO debe existir en engine_registry"

    def test_build_capability_context_does_not_mutate_registry(self):
        before = len(reg.all_specs())
        decision = resolve_intent("¿qué puedes hacer?")
        build_capability_context(decision)
        after = len(reg.all_specs())
        assert before == after == 48


# ===========================================================================
# 2. Contrato compuesto: entries de motores (kind="engine") + capacidades
#    (kind != "engine") representando los 5 elementos sin ser motores.
# ===========================================================================

class TestBuildCapabilityContextComposition:
    def test_exactly_48_engine_entries(self):
        decision = resolve_intent("¿qué puedes hacer?")
        ctx = build_capability_context(decision)
        engine_entries = [e for e in ctx.entries if e.kind == "engine"]
        assert len(engine_entries) == 48
        assert len({e.name for e in engine_entries}) == 48

    def test_five_capability_items_present_with_non_engine_kind(self):
        decision = resolve_intent("¿qué puedes hacer?")
        ctx = build_capability_context(decision)
        by_name = {e.name: e for e in ctx.entries}
        for name in (
            "online_search", "llm_providers", "domain_knowledge",
            "auto_builder", "telegram_gateway",
        ):
            assert name in by_name, f"falta {name} en el contrato"
            assert by_name[name].kind != "engine", f"{name} no debe tener kind='engine'"
            assert by_name[name].kind in ("capability", "integration", "service", "gateway")

    def test_capability_context_reflects_intent_decision(self):
        decision = resolve_intent("¿qué puedes hacer?")
        ctx = build_capability_context(decision)
        assert ctx.query_capability is True

        decision2 = resolve_intent("hola, cómo estás")
        ctx2 = build_capability_context(decision2)
        assert ctx2.query_capability is False

    def test_fallback_sources_are_real_available_authorized_entries(self):
        decision = resolve_intent("¿qué puedes hacer?")
        ctx = build_capability_context(decision)
        by_name = {e.name: e for e in ctx.entries}
        for name in ctx.fallback_sources:
            assert name in by_name
            assert by_name[name].health == HEALTH_AVAILABLE
            assert by_name[name].authorized is True


# ===========================================================================
# 3. Combinación compuesta no colapsada — exists/connected/health/authorized
#    son independientes (requisito explícito, no reducible a un enum único).
# ===========================================================================

class TestCompositeContractNotCollapsed:
    def test_gated_engine_without_active_flag_is_available_but_unauthorized(self, monkeypatch):
        """active_learning: connected=True, health=AVAILABLE (su health()
        solo verifica import, no el gated_by), pero authorized=False si
        VECTRAX_ACTIVE_LEARNING no está activo — exactamente la combinación
        que el enum único perdería."""
        monkeypatch.delenv("VECTRAX_ACTIVE_LEARNING", raising=False)
        decision = resolve_intent("¿qué motores tienes?")
        ctx = build_capability_context(decision)
        entry = next(e for e in ctx.entries if e.name == "active_learning")
        assert entry.exists is True
        assert entry.connected is True
        assert entry.health == HEALTH_AVAILABLE
        assert entry.authorized is False
        assert entry in ctx.gaps  # no disponible+autorizado -> es un gap

    def test_gated_engine_with_active_flag_is_authorized(self, monkeypatch):
        monkeypatch.setenv("VECTRAX_ACTIVE_LEARNING", "1")
        decision = resolve_intent("¿qué motores tienes?")
        ctx = build_capability_context(decision)
        entry = next(e for e in ctx.entries if e.name == "active_learning")
        assert entry.authorized is True

    def test_gated_internal_without_gated_by_is_authorized(self):
        """presencia_observer/operator_runtime son GATED_INTERNAL pero NO
        tienen gated_by declarado -> authorized=True (verificado en código,
        no deducido por el nombre del tier)."""
        decision = resolve_intent("¿qué motores tienes?")
        ctx = build_capability_context(decision)
        for name in ("presencia_observer", "operator_runtime"):
            entry = next(e for e in ctx.entries if e.name == name)
            assert entry.authorized is True

    def test_auto_executor_never_authorized(self):
        decision = resolve_intent("¿qué motores tienes?")
        ctx = build_capability_context(decision)
        entry = next(e for e in ctx.entries if e.name == "auto_executor")
        assert entry.authorized is False

    def test_degraded_vs_unavailable_distinct_via_fake_registry(self, monkeypatch):
        """Usa specs falsos (mismo patrón que tests/test_orchestration.py)
        para verificar que health() lanzando ImportError produce
        UNAVAILABLE (connected=False) y health() devolviendo False o
        lanzando otra excepción produce DEGRADED (connected=True) — nunca
        el mismo valor."""
        from core.orchestration.engine_registry import EngineSpec, Tier

        reg.clear()
        try:
            reg.register(EngineSpec(
                "fake_unavailable", "test", Tier.CORE,
                health=lambda: (_ for _ in ()).throw(ImportError("no module")),
            ))
            reg.register(EngineSpec(
                "fake_degraded", "test", Tier.CORE,
                health=lambda: False,
            ))
            reg.register(EngineSpec(
                "fake_available", "test", Tier.CORE,
                health=lambda: True,
            ))
            decision = resolve_intent("¿qué motores tienes?")
            ctx = build_capability_context(decision)
            by_name = {e.name: e for e in ctx.entries}
            assert by_name["fake_unavailable"].connected is False
            assert by_name["fake_unavailable"].health == HEALTH_UNAVAILABLE
            assert by_name["fake_degraded"].connected is True
            assert by_name["fake_degraded"].health == HEALTH_DEGRADED
            assert by_name["fake_available"].connected is True
            assert by_name["fake_available"].health == HEALTH_AVAILABLE
        finally:
            reg.clear()
            reg._register_defaults()


# ===========================================================================
# 4. No revela secretos/rutas/env en `reason`
# ===========================================================================

class TestNoSecretLeakage:
    def test_reason_never_contains_env_values_or_absolute_paths(self):
        decision = resolve_intent("¿qué motores tienes?")
        ctx = build_capability_context(decision)
        for entry in ctx.entries:
            assert "/Users/" not in entry.reason
            assert "/home/" not in entry.reason
            assert "sk-" not in entry.reason
            assert "Bearer " not in entry.reason


# ===========================================================================
# 5. record_capability_gap — hecho verificable, deduplicado, sin ideas
# ===========================================================================

@pytest.fixture
def temp_ledger(tmp_path, monkeypatch):
    from core.self_observation import observation_ledger as ol
    monkeypatch.setattr(ol, "_DB_PATH", str(tmp_path / "capability_test_ledger.db"))
    ol.init_ledger()
    yield ol


class TestRecordCapabilityGap:
    def test_writes_new_record_with_expected_evidence(self, temp_ledger):
        wrote = record_capability_gap(
            capability_name="online_search",
            health=HEALTH_UNAVAILABLE,
            authorized=False,
            reason="ImportError: no module",
            query_domain="market",
            query_task_type=None,
        )
        assert wrote is True
        records = temp_ledger.get_by_domain("self_knowledge", limit=10)
        assert len(records) == 1
        ev = records[0]["evidence"]
        assert ev["capability_name"] == "online_search"
        assert ev["health"] == HEALTH_UNAVAILABLE
        assert ev["authorized"] is False
        assert "fingerprint" in ev

    def test_deduplicates_same_fingerprint_within_window(self, temp_ledger):
        first = record_capability_gap(
            capability_name="x", health=HEALTH_DEGRADED, authorized=True, query_domain="d",
        )
        second = record_capability_gap(
            capability_name="x", health=HEALTH_DEGRADED, authorized=True, query_domain="d",
        )
        assert first is True
        assert second is False
        assert len(temp_ledger.get_by_domain("self_knowledge", limit=10)) == 1

    def test_different_capability_name_not_deduplicated(self, temp_ledger):
        record_capability_gap(capability_name="x", health=HEALTH_DEGRADED, authorized=True)
        record_capability_gap(capability_name="y", health=HEALTH_DEGRADED, authorized=True)
        assert len(temp_ledger.get_by_domain("self_knowledge", limit=10)) == 2

    def test_expired_window_allows_new_record(self, temp_ledger, monkeypatch):
        record_capability_gap(capability_name="z", health=HEALTH_DEGRADED, authorized=True)
        # Ventana de deduplicación = 0s -> el segundo registro ya no cuenta
        # como "reciente" y se vuelve a escribir.
        wrote_again = record_capability_gap(
            capability_name="z", health=HEALTH_DEGRADED, authorized=True,
            dedup_window_seconds=0.0,
        )
        assert wrote_again is True
        assert len(temp_ledger.get_by_domain("self_knowledge", limit=10)) == 2

    def test_never_generates_idea_or_proposal(self):
        """El módulo puede MENCIONAR `CognitiveProposal` en comentarios/
        docstrings (documentando que Fase 4 la usará después), pero nunca
        debe importarla ni instanciarla — eso sería generar una idea."""
        import inspect
        import core.self_observation.capability_context as cc
        source = inspect.getsource(cc)
        assert "import CognitiveProposal" not in source
        assert "CognitiveProposal(" not in source
        assert "ProposalStatus." not in source

    def test_defensive_when_ledger_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "core.self_observation.observation_ledger.init_ledger",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = record_capability_gap(capability_name="x", health=HEALTH_DEGRADED, authorized=True)
        assert result is False  # nunca lanza


# ===========================================================================
# 6. Sin flag, sin conexión al pipeline — comportamiento existente intacto
# ===========================================================================

class TestNoWiringYet:
    def test_flag_not_wired_anywhere_functionally(self):
        """`capability_context.py` puede MENCIONAR el nombre del flag en su
        docstring (documentando que el paso 7 lo usará después) pero nunca
        debe LEERLO como env var todavía (ningún `os.environ`/`getenv`).
        `external_gateway.py` no debe mencionarlo en absoluto — el paso 7
        (conectar el gate detrás del flag) sigue pendiente."""
        import inspect
        import core.self_observation.capability_context as cc
        import core.operator.external_gateway as gw
        cc_source = inspect.getsource(cc)
        assert 'getenv("VX_CAPABILITY_SELF_AWARENESS"' not in cc_source
        assert 'environ.get("VX_CAPABILITY_SELF_AWARENESS"' not in cc_source
        assert "VX_CAPABILITY_SELF_AWARENESS" not in inspect.getsource(gw)

    def test_external_gateway_does_not_import_capability_context(self):
        import inspect
        import core.operator.external_gateway as gw
        assert "capability_context" not in inspect.getsource(gw)

    def test_self_context_does_not_import_capability_context(self):
        import inspect
        import vectrax.self_context as sc
        assert "capability_context" not in inspect.getsource(sc)
