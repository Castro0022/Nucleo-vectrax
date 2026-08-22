"""
tests/test_external_gateway_capability_grounding.py — Fase 4.

Fija el contrato de la PROMOCIÓN de la evaluación verificada de capacidades a
la respuesta visible, detrás del flag `VX_CAPABILITY_RESPONSE_GROUNDING`
(default OFF), SEPARADO de `VX_CAPABILITY_SELF_AWARENESS` (que sigue
gobernando la observación de la Fase 3 y ya está encendido en producción).

Origen: el 2026-08-21 una pregunta real de capacidades evaluó 54 entradas y
detectó 5 gaps (auto_executor, recovery_engine, router_learning_cycle,
active_learning, quality_observer), pero la respuesta visible salió por
SELF-AWARE y nombró solo tres, porque el modo sombra no sustituye la salida.

Contrato de flags (los dos están EN CAPAS, no en paralelo):

  | SELF_AWARENESS | RESPONSE_GROUNDING | Comportamiento                     |
  |----------------|--------------------|------------------------------------|
  | OFF            | OFF                | legacy puro                        |
  | ON             | OFF                | shadow Fase 3 (ver test_..._shadow)|
  | OFF            | ON                 | INERTE (sin observación no hay     |
  |                |                    | contexto verificado que promover)  |
  | ON             | ON                 | shadow intacto + respuesta grounded|
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.operator import external_gateway as eg
from core.operator.external_gateway import ExternalGateway, reset_gateway
from core.operator.universal_bus import reset_bus
from core.orchestration.bootstrap import (
    HEALTH_AVAILABLE,
    HEALTH_DEGRADED,
    HEALTH_UNAVAILABLE,
)
from core.self_observation.capability_context import (
    CapabilityContext,
    CapabilityEntry,
)


@pytest.fixture(autouse=True)
def clean_singletons():
    reset_bus()
    reset_gateway()
    eg._GROUNDING_MISCONFIG_WARNED = False
    yield
    reset_bus()
    reset_gateway()
    eg._GROUNDING_MISCONFIG_WARNED = False


# Mismo content que el archivo de shadow: satisface AMBOS detectores reales
# (is_self_referential y capability_query) sin mockear ninguno.
_CAPABILITY_CONTENT = "¿qué motores tienes conectados?"

# Los 5 gaps REALES observados en producción el 2026-08-21.
_PROD_GAPS = [
    "auto_executor",
    "recovery_engine",
    "router_learning_cycle",
    "active_learning",
    "quality_observer",
]

# Respuesta legacy que reproduce la brecha: nombra solo 3 de los 5.
_LEGACY_PARTIAL = (
    "En este momento tengo capacidades operativas amplias. "
    "Están limitados auto_executor, recovery_engine y active_learning."
)
# Respuesta legacy materialmente completa: nombra los 5 como limitados.
_LEGACY_COMPLETE = (
    "Ahora mismo no están disponibles auto_executor, recovery_engine, "
    "router_learning_cycle, active_learning ni quality_observer."
)


def _entry(name, *, health=HEALTH_UNAVAILABLE, authorized=True, connected=False):
    return CapabilityEntry(
        name=name, kind="engine", group="test", exists=True,
        connected=connected, authorized=authorized, health=health,
        reason="", evidence_source="test", observed_at=0.0,
    )


def _prod_ctx(fallback_sources=None):
    """CapabilityContext que reproduce la evaluación real: domain=None,
    task_type='chat', los 5 gaps de producción."""
    gaps = [_entry(n) for n in _PROD_GAPS]
    ready = [_entry("telegram_gateway", health=HEALTH_AVAILABLE, connected=True)]
    return CapabilityContext(
        query_domain=None, query_task_type="chat", query_capability=True,
        entries=gaps + ready, gaps=gaps,
        fallback_sources=list(fallback_sources or []),
    )


_COMMON_PATCHES = dict(
    run_audit=patch(
        "vectrax.response_auditor.run_audit",
        side_effect=lambda **kw: kw.get("response", ""),
    ),
    enforce_language=patch(
        "core.language_gate.enforce_language",
        side_effect=lambda text, *a, **k: text,
    ),
    presence_policy=patch(
        "core.conversation.presence_policy.apply_presence_policy",
        side_effect=lambda text, *a, **k: (text, False),
    ),
)


def _send(gw, content=_CAPABILITY_CONTENT, user_id="tg:cap_grounding"):
    with _COMMON_PATCHES["run_audit"], _COMMON_PATCHES["enforce_language"], \
         _COMMON_PATCHES["presence_policy"]:
        return gw.receive_message(user_id=user_id, content=content, channel="telegram")


def _both_flags_on(monkeypatch):
    monkeypatch.setenv("VX_CAPABILITY_SELF_AWARENESS", "1")
    monkeypatch.setenv("VX_CAPABILITY_RESPONSE_GROUNDING", "1")


def _grounding_env(legacy=_LEGACY_PARTIAL, ctx=None, lang="es"):
    """Contexto de patches para el camino grounded completo."""
    ctx = ctx if ctx is not None else _prod_ctx()
    return [
        patch("vectrax.self_context.is_self_referential", return_value=True),
        patch("vectrax.self_context.resolve_self_aware", return_value=legacy),
        patch(
            "core.self_observation.capability_context.build_capability_context",
            return_value=ctx,
        ),
        patch("core.self_observation.capability_context.record_capability_gap"),
        patch("core.language_gate.get_user_language", return_value=lang),
    ]


def _send_grounded(gw, *, user_id, legacy=_LEGACY_PARTIAL, ctx=None, lang="es"):
    # Nota: cada test DEBE usar un user_id propio — el intake filter del
    # gateway mantiene estado por usuario y reutilizarlo desvía el mensaje
    # antes de llegar a STEP 4.2b.
    stack = _grounding_env(legacy=legacy, ctx=ctx, lang=lang)
    for cm in stack:
        cm.start()
    try:
        return _send(gw, user_id=user_id)
    finally:
        for cm in reversed(stack):
            cm.stop()


# ===========================================================================
# Fila 3 del contrato: promoción SIN observación → inerte por diseño.
# ===========================================================================

class TestCapabilityGroundingWithoutObservation:

    def test_grounding_inert_when_observation_disabled(self, monkeypatch):
        monkeypatch.delenv("VX_CAPABILITY_SELF_AWARENESS", raising=False)
        monkeypatch.setenv("VX_CAPABILITY_RESPONSE_GROUNDING", "1")
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_LEGACY_PARTIAL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
             ) as mock_build:
            result = _send(gw, user_id="tg:cap_no_obs")
        mock_build.assert_not_called()
        assert result.response == _LEGACY_PARTIAL
        assert result.evidence["capability_technical_fallback"] == "observation_disabled"

    def test_misconfiguration_warns_exactly_once(self, monkeypatch, caplog):
        monkeypatch.delenv("VX_CAPABILITY_SELF_AWARENESS", raising=False)
        monkeypatch.setenv("VX_CAPABILITY_RESPONSE_GROUNDING", "1")
        caplog.set_level("WARNING", logger="vectrax.operator.external_gateway")
        for i in range(3):
            gw = ExternalGateway()
            with patch("vectrax.self_context.is_self_referential", return_value=True), \
                 patch("vectrax.self_context.resolve_self_aware", return_value=_LEGACY_PARTIAL):
                _send(gw, user_id=f"tg:cap_no_obs_{i}")
        warnings = [
            r for r in caplog.records
            if "VX_CAPABILITY_RESPONSE_GROUNDING está encendido" in r.getMessage()
        ]
        assert len(warnings) == 1


# ===========================================================================
# Fila 4 del contrato: ambos flags encendidos.
# ===========================================================================

class TestCapabilityResponseGroundingFlagOn:

    def test_closes_the_production_gap_all_five_named(self, monkeypatch):
        """Regresión del hallazgo real: la legacy nombra 3 de 5; la respuesta
        final nombra los 5."""
        _both_flags_on(monkeypatch)
        result = _send_grounded(ExternalGateway(), user_id="tg:cap_gap_closed")
        for name in _PROD_GAPS:
            assert name in result.response, f"{name} ausente de la respuesta visible"
        assert result.source == "capability_grounded"

    def test_shadow_observation_preserved_under_grounding(self, monkeypatch):
        """El bloque de Fase 3 sigue corriendo con ambos flags: evalúa y
        registra gaps, además de que la respuesta quede grounded."""
        _both_flags_on(monkeypatch)
        ctx = _prod_ctx()
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_LEGACY_PARTIAL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
                 return_value=ctx,
             ) as mock_build, \
             patch(
                 "core.self_observation.capability_context.record_capability_gap",
             ) as mock_record, \
             patch("core.language_gate.get_user_language", return_value="es"):
            result = _send(gw, user_id="tg:cap_shadow_preserved")
        mock_build.assert_called_once()
        assert mock_record.call_count == len(_PROD_GAPS)
        assert result.response != _LEGACY_PARTIAL

    def test_reads_live_context_not_the_deduplicated_ledger(self, monkeypatch):
        """Trampa del dedup: record_capability_gap() devuelve False en los 5
        (duplicados dentro de la ventana de 24h) y la respuesta sigue
        completa, porque se deriva de ctx.gaps y no del ledger."""
        _both_flags_on(monkeypatch)
        ctx = _prod_ctx()
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_LEGACY_PARTIAL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
                 return_value=ctx,
             ), \
             patch(
                 "core.self_observation.capability_context.record_capability_gap",
                 return_value=False,  # todo deduplicado: cero escrituras nuevas
             ), \
             patch("core.language_gate.get_user_language", return_value="es"):
            result = _send(gw, user_id="tg:cap_dedup")
        for name in _PROD_GAPS:
            assert name in result.response

    def test_materially_complete_response_is_preserved(self, monkeypatch):
        _both_flags_on(monkeypatch)
        result = _send_grounded(
            ExternalGateway(), user_id="tg:cap_complete", legacy=_LEGACY_COMPLETE,
        )
        assert result.response == _LEGACY_COMPLETE
        assert result.source != "capability_grounded"

    def test_contradiction_triggers_replacement(self, monkeypatch):
        """Nombrar una limitación verificada como operativa es discrepancia."""
        _both_flags_on(monkeypatch)
        contradictory = (
            "Todo está operativo y disponible: auto_executor, recovery_engine, "
            "router_learning_cycle, active_learning y quality_observer."
        )
        result = _send_grounded(
            ExternalGateway(), user_id="tg:cap_contradiction", legacy=contradictory,
        )
        assert result.response != contradictory
        assert result.source == "capability_grounded"

    def test_replaces_never_appends(self, monkeypatch):
        _both_flags_on(monkeypatch)
        result = _send_grounded(ExternalGateway(), user_id="tg:cap_replace")
        assert _LEGACY_PARTIAL not in result.response
        assert "En este momento tengo capacidades operativas amplias" not in result.response

    def test_unsupported_language_keeps_legacy(self, monkeypatch):
        """narrate() solo tiene plantillas es/en; en pt se conserva la legacy,
        que sí cubre los 9 idiomas soportados."""
        _both_flags_on(monkeypatch)
        result = _send_grounded(ExternalGateway(), user_id="tg:cap_lang_pt", lang="pt")
        assert result.response == _LEGACY_PARTIAL
        assert result.evidence["capability_technical_fallback"] == "lang_unsupported"

    def test_context_build_failure_keeps_legacy_and_still_gates(self, monkeypatch):
        """Fallo técnico: legacy conservada y evaluada por el ÚNICO gate
        constitucional final."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        with patch("vectrax.self_context.is_self_referential", return_value=True), \
             patch("vectrax.self_context.resolve_self_aware", return_value=_LEGACY_PARTIAL), \
             patch(
                 "core.self_observation.capability_context.build_capability_context",
                 side_effect=RuntimeError("boom"),
             ), \
             patch("core.language_gate.get_user_language", return_value="es"), \
             patch(
                 "core.operator.constitutional_guard.shadow_check",
             ) as mock_gate:
            result = _send(gw, user_id="tg:cap_build_fail")
        assert result.response == _LEGACY_PARTIAL
        assert result.evidence["capability_technical_fallback"] == "context_build_failed"
        mock_gate.assert_called_once()  # atravesó el gate igualmente

    def test_empty_narration_keeps_legacy(self, monkeypatch):
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        stack = _grounding_env()
        stack.append(patch(
            "core.self_observation.capability_narrator.narrate", return_value="",
        ))
        for cm in stack:
            cm.start()
        try:
            result = _send(gw, user_id="tg:cap_empty_narration")
        finally:
            for cm in reversed(stack):
                cm.stop()
        assert result.response == _LEGACY_PARTIAL
        assert result.evidence["capability_technical_fallback"] == "empty_narration"

    def test_grounded_response_bypasses_the_three_post_processors(self, monkeypatch):
        """Ni auditor LLM, ni presence_policy, ni traducción LLM pueden tocar
        una respuesta grounded — son las tres vías por las que se perderían
        las limitaciones verificadas. Se comprueba con assert_not_called y no
        con side_effect, porque el pipeline envuelve las tres en try/except y
        se tragaría cualquier excepción lanzada desde ellas."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        stack = _grounding_env()
        audit = patch("vectrax.response_auditor.run_audit")
        lang_gate = patch("core.language_gate.enforce_language")
        presence = patch("core.conversation.presence_policy.apply_presence_policy")
        stack.extend([audit, lang_gate, presence])
        started = [cm.start() for cm in stack]
        mock_audit, mock_lang, mock_presence = started[-3], started[-2], started[-1]
        try:
            result = gw.receive_message(
                user_id="tg:cap_no_postproc", content=_CAPABILITY_CONTENT,
                channel="telegram",
            )
        finally:
            for cm in reversed(stack):
                cm.stop()
        mock_audit.assert_not_called()
        mock_lang.assert_not_called()
        mock_presence.assert_not_called()
        for name in _PROD_GAPS:
            assert name in result.response

    def test_narrator_emits_spanish_and_preserves_identifiers(self):
        """Confirmación exigida antes de saltarse enforce_language: el
        narrador ya devuelve prosa en el idioma pedido y conserva los
        identificadores de capacidad sin reescritura por LLM."""
        from core.self_observation.capability_narrator import narrate
        text_es = narrate(_prod_ctx(), lang="es")
        assert text_es
        # Prosa española, no inglesa.
        assert any(w in text_es.lower() for w in ("de", "no", "puedo", "tengo", "hay"))
        assert " and " not in text_es
        # Identificadores verbatim.
        for name in _PROD_GAPS:
            assert name in text_es
        text_en = narrate(_prod_ctx(), lang="en")
        assert text_en and text_en != text_es
        for name in _PROD_GAPS:
            assert name in text_en

    def test_shadow_mode_traverses_gate_without_enforcing(self, monkeypatch):
        """En shadow la respuesta grounded SÍ atraviesa el gate único: el gate
        observa y registra (shadow_check), pero no ejecuta (sin gate_check, sin
        sustitución)."""
        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        stack = _grounding_env()
        stack.extend([
            patch("core.operator.constitutional_mode.is_enforce", return_value=False),
            patch("core.operator.constitutional_guard.shadow_check"),
            patch("core.operator.constitutional_guard.gate_check"),
        ])
        started = [cm.start() for cm in stack]
        mock_shadow, mock_gate = started[-2], started[-1]
        try:
            result = _send(gw, user_id="tg:cap_shadow_mode")
        finally:
            for cm in reversed(stack):
                cm.stop()
        mock_shadow.assert_called_once()
        mock_gate.assert_not_called()
        for name in _PROD_GAPS:
            assert name in result.response


# ===========================================================================
# Cadena constitucional única, en modo enforce.
#
# Estos tests NO mockean `gate_check` para fabricar veredictos. Sustituyen UN
# evaluador real dentro de `constitutional_filter._EVALUATORS` (el patrón que
# ya usa tests/test_constitutional_filter.py) y dejan que `evaluate()` calcule
# el veredicto agregado de verdad, con `gate_check` y DecisionAuthority reales
# salvo donde se indique.
#
# Este PR NO implementa corrección constitucional: `ActionProposal` no
# transporta el texto de la respuesta ni `fallback_sources`, y ninguno de los 7
# evaluadores los inspecciona, así que re-narrar no cambiaría ninguna entrada
# de `evaluate()` y una segunda evaluación devolvería el mismo veredicto. El
# gate evalúa UNA vez.
# ===========================================================================


def _with_forced_law(law_number, verdict, reason="forzado por test"):
    """Sustituye el evaluador de UNA ley por uno que devuelve `verdict`. Los
    otros seis siguen siendo los reales y el agregado lo calcula evaluate()."""
    import core.operator.constitutional_filter as _cf
    from core.operator.constitutional_filter import PrincipleResult

    forced = lambda proposal: PrincipleResult(  # noqa: E731
        number=law_number, name=f"L{law_number}", verdict=verdict, reason=reason,
    )
    evaluators = list(_cf._EVALUATORS)
    evaluators[law_number - 1] = forced
    return patch.object(_cf, "_EVALUATORS", tuple(evaluators))


class TestCapabilityConstitutionalChain:

    def _run(self, monkeypatch, *, user_id, extra_patches=(),
             legacy=_LEGACY_PARTIAL, ctx=None):
        """Corre el camino grounded en enforce con el filtro REAL, espiando
        `gate_check` sin sustituir su comportamiento."""
        import core.operator.constitutional_guard as guard

        _both_flags_on(monkeypatch)
        gw = ExternalGateway()
        stack = _grounding_env(legacy=legacy, ctx=ctx)
        stack.append(
            patch("core.operator.constitutional_mode.is_enforce", return_value=True)
        )
        stack.append(
            patch.object(guard, "gate_check", wraps=guard.gate_check)
        )
        stack.extend(extra_patches)
        started = [cm.start() for cm in stack]
        spy_gate = started[len(stack) - len(extra_patches) - 1]
        try:
            return _send(gw, user_id=user_id), spy_gate
        finally:
            for cm in reversed(stack):
                cm.stop()

    def test_grounded_delivered_under_real_filter(self, monkeypatch):
        """Camino realista: filtro y DecisionAuthority reales. La respuesta
        grounded se entrega y el gate evalúa exactamente una vez."""
        result, spy_gate = self._run(monkeypatch, user_id="tg:cap_chain_real")
        assert spy_gate.call_count == 1, "el gate evalúa UNA sola vez"
        assert result.response != eg._CONSTITUTIONAL_FALLBACK_ES
        for name in _PROD_GAPS:
            assert name in result.response

    def test_forced_block_terminates_with_safe_response_never_legacy(self, monkeypatch):
        """BLOCK real (Ley 7 forzada, agregado calculado por evaluate()):
        termina en la respuesta constitucional segura, nunca en la legacy."""
        from core.operator.constitutional_filter import PrincipleVerdict
        result, spy_gate = self._run(
            monkeypatch, user_id="tg:cap_chain_block",
            extra_patches=[_with_forced_law(7, PrincipleVerdict.BLOCK)],
        )
        assert spy_gate.call_count == 1, "BLOCK es terminante"
        assert result.response == eg._CONSTITUTIONAL_FALLBACK_ES
        assert _LEGACY_PARTIAL not in result.response
        for name in _PROD_GAPS:
            assert name not in result.response
        assert result.source == "constitutional_block"

    def test_unauthorized_caution_terminates_with_safe_response(self, monkeypatch):
        """CAUTION real + DecisionAuthority que NO autoriza: respuesta
        constitucional segura. Se sustituye solo la autoridad (un componente
        externo que legítimamente puede denegar), no el veredicto."""
        import core.operator.constitutional_guard as guard
        from core.operator.constitutional_filter import PrincipleVerdict

        class _Denied:
            auto_approved = False

        result, spy_gate = self._run(
            monkeypatch, user_id="tg:cap_chain_caution_denied",
            extra_patches=[
                _with_forced_law(4, PrincipleVerdict.CAUTION),
                patch.object(
                    guard, "_real_decision_authority", return_value=_Denied(),
                ),
            ],
        )
        assert spy_gate.call_count == 1, "sin re-evaluación: no hay corrección"
        assert result.response == eg._CONSTITUTIONAL_FALLBACK_ES
        assert result.source == "constitutional_caution_denied"

    def test_trace_reports_one_verdict_and_zero_corrections(self, monkeypatch):
        result, _ = self._run(monkeypatch, user_id="tg:cap_chain_trace")
        ev = result.evidence
        for key in (
            "capability_original_len", "capability_grounded_len",
            "capability_discrepancy", "capability_verdicts",
            "capability_corrections", "capability_final_source",
            "capability_technical_fallback",
        ):
            assert key in ev, f"falta {key} en la traza"
        assert len(ev["capability_verdicts"]) == 1, "exactamente un veredicto"
        assert ev["capability_corrections"] == 0, "este PR no corrige"
