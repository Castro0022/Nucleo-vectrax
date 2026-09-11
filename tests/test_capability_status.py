"""
tests/test_capability_status.py — estado de capacidad DERIVADO, nunca fijo.

Fija el contrato de `core/self_observation/capability_status.py`:
  1. Las tres categorías (confirmada / existe-sin-confirmar / condicional) se
     derivan del estado compuesto verificado — no hay ninguna tabla
     nombre→categoría escrita a mano en el código.
  2. Un cambio en el estado real (una env var que aparece) mueve la capacidad
     de categoría SOLO, en la siguiente consulta, sin editar texto: es el
     escenario "mañana se conecta X" del ticket.
  3. El mecanismo de consulta lee estado en vivo: sin caché entre llamadas.
  4. Un nombre desconocido no se inventa (None), y `is_capability_available()`
     solo dice True cuando está confirmada.
  5. El bloque para SELF_KNOWLEDGE no filtra secretos, rutas, nombres de
     credenciales ni JSON; y es vacío cuando no hay evidencia.
  6. `capability_narrator` clasifica con esta MISMA derivación (sin duplicar
     la regla).
"""

from __future__ import annotations

from core.orchestration.bootstrap import (
    HEALTH_AVAILABLE,
    HEALTH_DEGRADED,
    HEALTH_UNAVAILABLE,
)
from core.self_observation.capability_context import (
    CapabilityContext,
    CapabilityEntry,
)
from core.self_observation.capability_status import (
    DETAIL_DEGRADED,
    DETAIL_READY,
    DETAIL_UNAUTHORIZED,
    DETAIL_UNAVAILABLE,
    STATUS_CONDITIONAL,
    STATUS_CONFIRMED,
    STATUS_DECLARED,
    build_capability_status_context,
    derive_detail,
    derive_status,
    get_capability_status,
    is_capability_available,
    query_capability_status,
    render_status_block,
)


def _entry(
    name,
    kind="engine",
    group="test",
    exists=True,
    connected=True,
    authorized=True,
    health=HEALTH_AVAILABLE,
    reason="",
    condition="",
) -> CapabilityEntry:
    return CapabilityEntry(
        name=name, kind=kind, group=group, exists=exists, connected=connected,
        authorized=authorized, health=health, reason=reason,
        evidence_source="test", observed_at=0.0, condition=condition,
    )


# ===========================================================================
# 1. Derivación de las tres categorías desde el estado compuesto
# ===========================================================================

class TestStatusDerivation:
    def test_connected_available_authorized_is_confirmed(self):
        assert derive_status(_entry("ok")) == STATUS_CONFIRMED
        assert derive_detail(_entry("ok")) == DETAIL_READY

    def test_available_but_unauthorized_is_conditional(self):
        """La combinación compuesta: existe, conecta y su health pasa, pero
        una condición (flag/credencial) no se cumple hoy."""
        gated = _entry("locked", authorized=False, condition="SOME_FLAG")
        assert derive_status(gated) == STATUS_CONDITIONAL
        assert derive_detail(gated) == DETAIL_UNAUTHORIZED

    def test_degraded_is_declared_not_confirmed(self):
        shaky = _entry("shaky", health=HEALTH_DEGRADED)
        assert derive_status(shaky) == STATUS_DECLARED
        assert derive_detail(shaky) == DETAIL_DEGRADED

    def test_unconnected_is_declared_not_confirmed(self):
        gone = _entry("gone", connected=False, health=HEALTH_UNAVAILABLE)
        assert derive_status(gone) == STATUS_DECLARED
        assert derive_detail(gone) == DETAIL_UNAVAILABLE

    def test_unauthorized_and_unavailable_is_declared(self):
        """Si ni siquiera conecta, la falta de autorización es secundaria:
        no se puede afirmar que 'existe y funciona'."""
        both = _entry("dead", connected=False, authorized=False,
                      health=HEALTH_UNAVAILABLE, condition="SOME_FLAG")
        assert derive_status(both) == STATUS_DECLARED

    def test_no_hardcoded_name_to_category_table(self):
        """El mismo nombre cae en categorías distintas según su estado: la
        pertenencia no puede venir de una lista escrita a mano."""
        name = "same_capability"
        assert derive_status(_entry(name)) == STATUS_CONFIRMED
        assert derive_status(_entry(name, authorized=False)) == STATUS_CONDITIONAL
        assert derive_status(_entry(name, health=HEALTH_DEGRADED)) == STATUS_DECLARED


# ===========================================================================
# 2. El estado real manda: cambiar el entorno cambia la categoría SOLO
# ===========================================================================

class TestReactsToRealSystemState:
    def test_gated_engine_moves_conditional_to_confirmed_when_flag_appears(
        self, monkeypatch,
    ):
        """Escenario del ticket: hoy la capacidad está condicionada; en cuanto
        el estado real cambia (aparece la variable), la MISMA consulta la
        devuelve como confirmada. Nadie edita una frase de 'no puedo' a
        'puedo'."""
        monkeypatch.delenv("VECTRAX_ACTIVE_LEARNING", raising=False)
        before = query_capability_status("¿qué motores tienes?").status_of("active_learning")
        assert before is not None
        assert before.status == STATUS_CONDITIONAL
        assert before.condition == "VECTRAX_ACTIVE_LEARNING"

        monkeypatch.setenv("VECTRAX_ACTIVE_LEARNING", "1")
        after = query_capability_status("¿qué motores tienes?").status_of("active_learning")
        assert after is not None
        assert after.status == STATUS_CONFIRMED
        assert after.condition == ""

    def test_catalog_credential_gates_capability_dynamically(self, monkeypatch):
        """El gateway de Telegram exige TELEGRAM_BOT_TOKEN en el propio código
        (`_load_token()` aborta sin él): sin token es condicional, con token
        deja de serlo — derivado del entorno, no declarado en texto."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        gated = query_capability_status(names=["telegram_gateway"]).status_of(
            "telegram_gateway",
        )
        assert gated is not None
        assert gated.status == STATUS_CONDITIONAL
        assert gated.condition == "TELEGRAM_BOT_TOKEN"

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-value")
        opened = query_capability_status(names=["telegram_gateway"]).status_of(
            "telegram_gateway",
        )
        assert opened is not None
        assert opened.status != STATUS_CONDITIONAL
        assert opened.condition == ""

    def test_empty_credential_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
        entry = query_capability_status(names=["telegram_gateway"]).status_of(
            "telegram_gateway",
        )
        assert entry is not None
        assert entry.status == STATUS_CONDITIONAL

    def test_query_is_not_cached_between_calls(self, monkeypatch):
        """Dos consultas consecutivas con el entorno cambiado en medio NO
        pueden devolver el mismo resultado: se consulta estado real cada vez."""
        monkeypatch.delenv("VECTRAX_ROUTER_LEARNING", raising=False)
        first = query_capability_status(names=["router_learning_cycle"])
        monkeypatch.setenv("VECTRAX_ROUTER_LEARNING", "1")
        second = query_capability_status(names=["router_learning_cycle"])
        assert first.status_of("router_learning_cycle").status == STATUS_CONDITIONAL
        assert second.status_of("router_learning_cycle").status == STATUS_CONFIRMED
        assert second.generated_at >= first.generated_at


# ===========================================================================
# 3. Mecanismo de consulta por nombre — nunca inventa
# ===========================================================================

class TestQueryMechanism:
    def test_unknown_capability_returns_none(self):
        assert get_capability_status("google_calendar") is None
        assert get_capability_status("") is None
        assert is_capability_available("google_calendar") is False

    def test_known_capability_returns_real_state(self):
        entry = get_capability_status("smart_router")
        assert entry is not None
        assert entry.name == "smart_router"
        assert entry.status in (STATUS_CONFIRMED, STATUS_DECLARED, STATUS_CONDITIONAL)

    def test_never_authorized_engine_is_conditional_not_confirmed(self):
        """auto_executor tiene gated_by='__never__': existe, pero jamás se
        afirma como operativo."""
        entry = get_capability_status("auto_executor")
        assert entry is not None
        assert entry.status != STATUS_CONFIRMED
        assert is_capability_available("auto_executor") is False

    def test_report_counts_cover_every_entry(self):
        report = query_capability_status("¿qué puedes hacer?")
        counts = report.counts()
        assert sum(counts.values()) == len(report.entries)
        assert set(counts) == {STATUS_CONFIRMED, STATUS_DECLARED, STATUS_CONDITIONAL}
        assert report.query_capability is True

    def test_names_filter_limits_the_report(self):
        report = query_capability_status(names=["smart_router"])
        assert [e.name for e in report.entries] == ["smart_router"]

    def test_provided_context_is_reused_instead_of_rebuilding(self):
        ctx = CapabilityContext(
            entries=[_entry("only_one")], gaps=[], fallback_sources=[],
        )
        report = query_capability_status(context=ctx)
        assert [e.name for e in report.entries] == ["only_one"]
        assert report.status_of("only_one").status == STATUS_CONFIRMED


# ===========================================================================
# 4. Bloque para SELF_KNOWLEDGE — derivado, honesto, sin internals
# ===========================================================================

class TestStatusBlockRendering:
    def _mixed_report(self):
        ctx = CapabilityContext(
            entries=[
                _entry("ready_one"),
                _entry("locked_one", authorized=False, condition="SECRET_TOKEN"),
                _entry("shaky_one", health=HEALTH_DEGRADED),
            ],
            gaps=[],
            fallback_sources=["ready_one"],
        )
        return query_capability_status(context=ctx)

    def test_block_lists_each_capability_in_its_derived_category(self):
        text = render_status_block(self._mixed_report(), lang="es")
        assert "ready_one" in text
        assert "locked_one" in text
        assert "shaky_one" in text
        # Cada categoría aparece con su conteo real derivado.
        assert "(1)" in text

    def test_block_never_leaks_condition_names_or_secrets(self):
        text = render_status_block(self._mixed_report(), lang="es")
        assert "SECRET_TOKEN" not in text
        assert "{" not in text and "}" not in text
        assert "/Users/" not in text

    def test_block_is_empty_without_evidence(self):
        empty = query_capability_status(context=CapabilityContext())
        assert render_status_block(empty, lang="es") == ""
        assert render_status_block(empty, lang="en") == ""

    def test_block_is_deterministic_and_bilingual(self):
        report = self._mixed_report()
        assert render_status_block(report, lang="es") == render_status_block(report, lang="es")
        assert render_status_block(report, lang="en") != render_status_block(report, lang="es")
        # Idioma no soportado cae a español, nunca a texto vacío inesperado.
        assert render_status_block(report, lang="fr") == render_status_block(report, lang="es")

    def test_long_lists_are_truncated_with_a_derived_count(self):
        ctx = CapabilityContext(
            entries=[_entry(f"cap_{i:02d}") for i in range(15)],
            gaps=[], fallback_sources=[],
        )
        text = render_status_block(query_capability_status(context=ctx), lang="es", max_names=5)
        assert "+10 más" in text

    def test_build_context_from_live_state_mentions_real_engines(self):
        text = build_capability_status_context("¿qué puedes hacer?", lang="es")
        assert text
        assert "smart_router" in text


# ===========================================================================
# 5. Una sola regla de clasificación (narrador incluido)
# ===========================================================================

class TestSingleSourceOfClassification:
    def test_narrator_uses_the_same_derivation(self):
        from core.self_observation import capability_narrator

        for entry in (
            _entry("a"),
            _entry("b", authorized=False),
            _entry("c", health=HEALTH_DEGRADED),
            _entry("d", connected=False, health=HEALTH_UNAVAILABLE),
        ):
            assert capability_narrator._classify_single(entry) == derive_detail(entry)

    def test_detail_values_map_onto_the_three_categories(self):
        assert derive_status(_entry("x")) == STATUS_CONFIRMED
        for detail, entry in (
            (DETAIL_UNAUTHORIZED, _entry("y", authorized=False)),
            (DETAIL_DEGRADED, _entry("z", health=HEALTH_DEGRADED)),
            (DETAIL_UNAVAILABLE, _entry("w", connected=False, health=HEALTH_UNAVAILABLE)),
        ):
            assert derive_detail(entry) == detail
            assert derive_status(entry) in (STATUS_CONDITIONAL, STATUS_DECLARED)


# ===========================================================================
# 6. Integración con el auto-contexto (SELF_KNOWLEDGE)
# ===========================================================================

class TestNewCatalogEntriesDesignABC:
    """Diseño A/B/C, Frente B (5.4): `reminder` (core.scheduler) y
    `place_search` real (Google Places, distinto de `online_search`) deben
    existir en el catálogo y derivar su estado del entorno real."""

    def test_reminder_is_confirmed_scheduler_always_importable(self):
        entry = get_capability_status("reminder")
        assert entry is not None
        assert entry.status == STATUS_CONFIRMED

    def test_place_search_is_conditional_without_api_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        entry = get_capability_status("place_search")
        assert entry is not None
        assert entry.status == STATUS_CONDITIONAL
        assert entry.condition == "GOOGLE_PLACES_API_KEY"

    def test_place_search_becomes_confirmed_with_api_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key-value")
        entry = get_capability_status("place_search")
        assert entry is not None
        assert entry.status == STATUS_CONFIRMED
        assert entry.condition == ""

    def test_place_search_distinct_from_online_search(self):
        report = query_capability_status(names=["place_search", "online_search"])
        names = {e.name for e in report.entries}
        assert {"place_search", "online_search"} <= names


class TestSelfContextIntegration:
    def test_capability_query_injects_the_derived_block(self, monkeypatch):
        monkeypatch.delenv("VX_CAPABILITY_STATUS_CONTEXT", raising=False)
        from vectrax import self_context

        ctx = self_context.build_self_context(lang="es", query="¿qué puedes hacer?")
        assert "MIS CAPACIDADES" in ctx

    def test_non_capability_query_does_not_inject_the_block(self):
        from vectrax import self_context

        ctx = self_context.build_self_context(lang="es", query="¿cómo va el sistema?")
        assert "MIS CAPACIDADES" not in ctx

    def test_kill_switch_disables_the_block(self, monkeypatch):
        monkeypatch.setenv("VX_CAPABILITY_STATUS_CONTEXT", "0")
        from vectrax import self_context

        ctx = self_context.build_self_context(lang="es", query="¿qué puedes hacer?")
        assert "MIS CAPACIDADES" not in ctx
