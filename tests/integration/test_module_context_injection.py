"""
tests/integration/test_module_context_injection.py
====================================================
Verifica que el nuevo bloque [NÚCLEO COGNITIVO] se genera con datos
reales y se inyecta correctamente en el prompt del sistema.

Cubre:
  1.  _collect_module_state() genera datos del Observer
  2.  _collect_module_state() genera datos del Learner
  3.  _collect_module_state() genera datos del Router
  4.  _collect_module_state() genera datos del Governor
  5.  _collect_module_state() es fail-safe (no rompe si un módulo falla)
  6.  compose_self_summary_for_prompt() incluye el bloque de módulos
  7.  El bloque contiene datos literales, no texto genérico
  8.  VECTRAX_SYSTEM_PROMPT ya no contiene la regla anti-introspección
  9.  VECTRAX_SYSTEM_PROMPT contiene la regla de percepción real
  10. compose_creator_context() incluye el bloque de módulos para creator
  11. _CREATOR_RULES_ES contiene directiva de introspección
  12. _CREATOR_RULES_ES contiene el ejemplo prohibido
  13. compose_self_summary_for_prompt() respeta max_chars
  14. Fail-safe total: si todos los módulos fallan, retorna string vacío

Run:
    pytest tests/integration/test_module_context_injection.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# 1–5. _collect_module_state()
# ---------------------------------------------------------------------------

class TestCollectModuleState:

    def test_retorna_encabezado_nucleo(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "COGNITIVO" in out or out == "", \
            "El bloque debe contener 'COGNITIVO' o estar vacío (fail-safe)"

    def test_incluye_observer(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        # Observer siempre disponible en este entorno
        assert "Observer" in out, \
            f"El bloque debe incluir 'Observer'. Got: {out[:200]}"

    def test_observer_tiene_mode(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "mode=" in out, \
            f"El bloque Observer debe incluir 'mode='. Got: {out[:200]}"

    def test_observer_tiene_enforced(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "enforced=" in out, \
            f"El bloque Observer debe incluir 'enforced='. Got: {out[:200]}"

    def test_incluye_learner(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "Learner" in out, \
            f"El bloque debe incluir 'Learner'. Got: {out[:200]}"

    def test_learner_tiene_phase(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "phase=" in out, \
            f"El bloque Learner debe incluir 'phase='. Got: {out[:200]}"

    def test_incluye_router(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "Router" in out, \
            f"El bloque debe incluir 'Router'. Got: {out[:200]}"

    def test_router_tiene_regex_fallback(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "regex_fallback=" in out, \
            f"El bloque Router debe incluir 'regex_fallback='. Got: {out[:200]}"

    def test_incluye_governor(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "Governor" in out, \
            f"El bloque debe incluir 'Governor'. Got: {out[:200]}"

    def test_governor_tiene_risk(self):
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        assert "risk=" in out, \
            f"El bloque Governor debe incluir 'risk='. Got: {out[:200]}"

    def test_fail_safe_si_observer_lanza(self):
        """Si observer_status() lanza excepción, el bloque no rompe."""
        from core.self_observation.self_summary import _collect_module_state
        with patch(
            "core.nucleus.presencia_pura.observer_status",
            side_effect=RuntimeError("observer simulado offline"),
        ):
            out = _collect_module_state()
            # No debe lanzar, debe retornar algo (con o sin Observer)
            assert isinstance(out, str)

    def test_fail_safe_si_learner_lanza(self):
        """Si get_learner() lanza excepción, el bloque no rompe."""
        from core.self_observation.self_summary import _collect_module_state
        with patch(
            "core.nucleus.convergence_learner.get_learner",
            side_effect=RuntimeError("learner simulado offline"),
        ):
            out = _collect_module_state()
            assert isinstance(out, str)
            assert "Observer" in out, "Observer debería seguir presente"

    def test_no_contiene_lenguaje_generico(self):
        """El bloque NO debe contener frases genéricas de consultoría."""
        from core.self_observation.self_summary import _collect_module_state
        out = _collect_module_state()
        forbidden = [
            "flujos de datos",
            "gestión de usuarios",
            "aprendizaje continuo",
            "motor de análisis",
            "necesidades de los usuarios",
        ]
        for phrase in forbidden:
            assert phrase not in out.lower(), \
                f"El bloque contiene lenguaje genérico prohibido: '{phrase}'"


# ---------------------------------------------------------------------------
# 6–7. compose_self_summary_for_prompt()
# ---------------------------------------------------------------------------

class TestComposeSelfSummaryForPrompt:

    def test_incluye_bloque_nucleo_cognitivo(self):
        from core.self_observation.self_summary import (
            compose_self_summary_for_prompt,
            invalidate_cache,
        )
        invalidate_cache()
        out = compose_self_summary_for_prompt()
        assert "COGNITIVO" in out or len(out) == 0, \
            "El prompt debe incluir el bloque de módulos cognitivos"

    def test_incluye_percepcion_operacional(self):
        from core.self_observation.self_summary import (
            compose_self_summary_for_prompt,
            invalidate_cache,
        )
        invalidate_cache()
        out = compose_self_summary_for_prompt()
        # Debe tener al menos uno de los dos bloques
        assert "PERCEPCIÓN" in out or "COGNITIVO" in out or len(out) == 0

    def test_respeta_max_chars(self):
        from core.self_observation.self_summary import (
            compose_self_summary_for_prompt,
            invalidate_cache,
        )
        invalidate_cache()
        MAX = 300
        out = compose_self_summary_for_prompt(max_chars=MAX)
        assert len(out) <= MAX, \
            f"La salida ({len(out)} chars) supera max_chars={MAX}"

    def test_retorna_string(self):
        from core.self_observation.self_summary import compose_self_summary_for_prompt
        out = compose_self_summary_for_prompt()
        assert isinstance(out, str)

    def test_datos_numericos_reales(self):
        """El bloque debe contener valores numéricos (no placeholders)."""
        from core.self_observation.self_summary import (
            compose_self_summary_for_prompt,
            invalidate_cache,
        )
        invalidate_cache()
        out = compose_self_summary_for_prompt()
        import re
        # Debe haber al menos un número en el bloque de módulos
        assert re.search(r"\d+", out), \
            "El bloque debe contener valores numéricos reales"


# ---------------------------------------------------------------------------
# 8–9. VECTRAX_SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSystemPromptRefactored:

    def test_no_contiene_regla_anti_introspeccion(self):
        """La regla que prohibía mencionar módulos debe haber sido eliminada."""
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        assert "Nunca describir tu procesamiento interno" not in VECTRAX_SYSTEM_PROMPT, \
            "La regla anti-introspección no debe estar en el system prompt"
        assert "ni mencionar módulos" not in VECTRAX_SYSTEM_PROMPT, \
            "La prohibición de mencionar módulos no debe estar en el system prompt"

    def test_contiene_regla_percepcion_real(self):
        """Debe existir la regla que obliga a responder desde datos reales."""
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        assert "PERCEPCIÓN OPERACIONAL" in VECTRAX_SYSTEM_PROMPT, \
            "El system prompt debe referenciar el bloque PERCEPCIÓN OPERACIONAL"

    def test_contiene_instruccion_datos_reales(self):
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        assert "datos reales" in VECTRAX_SYSTEM_PROMPT.lower(), \
            "El system prompt debe instruir responder desde datos reales"

    def test_contiene_prohibicion_lenguaje_generico(self):
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        assert "lenguaje genérico" in VECTRAX_SYSTEM_PROMPT.lower() or \
               "consultoría abstracta" in VECTRAX_SYSTEM_PROMPT.lower(), \
            "El system prompt debe prohibir el lenguaje genérico/consultoría"

    def test_sigue_teniendo_identidad_soberana(self):
        """Los cambios no deben haber roto la regla de identidad soberana."""
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        assert "IDENTIDAD SOBERANA" in VECTRAX_SYSTEM_PROMPT
        assert "Eres Vectrax" in VECTRAX_SYSTEM_PROMPT

    def test_sigue_prohibiendo_asistente(self):
        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        assert "asistente" in VECTRAX_SYSTEM_PROMPT.lower()
        assert "NUNCA" in VECTRAX_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 10–12. compose_creator_context() y _CREATOR_RULES_ES
# ---------------------------------------------------------------------------

class TestCreatorContextIntrospection:

    def test_creator_rules_contiene_directiva_introspeccion(self):
        from core.identity.creator_mode import _CREATOR_RULES_ES
        assert "PERCIBES" in _CREATOR_RULES_ES or "percibes" in _CREATOR_RULES_ES, \
            "_CREATOR_RULES_ES debe contener directiva para preguntas de percepción"

    def test_creator_rules_contiene_ejemplo_prohibido(self):
        from core.identity.creator_mode import _CREATOR_RULES_ES
        assert "PROHIBIDO" in _CREATOR_RULES_ES or "prohibido" in _CREATOR_RULES_ES

    def test_creator_rules_prohibe_tercera_persona(self):
        from core.identity.creator_mode import _CREATOR_RULES_ES
        assert "tercera persona" in _CREATOR_RULES_ES.lower() or \
               "consultor externo" in _CREATOR_RULES_ES.lower(), \
            "Las reglas deben prohibir hablar del sistema en tercera persona"

    def test_creator_rules_cita_nucleo_cognitivo(self):
        from core.identity.creator_mode import _CREATOR_RULES_ES
        assert "COGNITIVO" in _CREATOR_RULES_ES or "cognitivo" in _CREATOR_RULES_ES, \
            "Las reglas deben referenciar el bloque NÚCLEO COGNITIVO"

    def test_compose_creator_context_incluye_bloque(self):
        """compose_creator_context() para creator debe incluir reglas y percepción."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            from core.identity import compose_creator_context
            ctx = compose_creator_context(
                "tg:2030762343", lang="es", include_perception=False,
            )
            assert "CREATOR MODE" in ctx
            assert "PERCIBES" in ctx or "percibes" in ctx, \
                "El bloque debe tener la directiva de introspección"

    def test_compose_creator_context_con_percepcion(self):
        """Con include_perception=True, el output incluye datos de módulos."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX_CREATOR_ID", None)
            from core.identity import compose_creator_context
            from core.self_observation.self_summary import invalidate_cache
            invalidate_cache()
            ctx = compose_creator_context(
                "tg:2030762343", lang="es", include_perception=True,
            )
            assert len(ctx) > 100, "El contexto con percepción debe ser sustancial"
            # Debe haber datos de al menos un módulo (Observer, Learner, Router, Governor)
            modulos = ["Observer", "Learner", "Router", "Governor"]
            encontrados = [m for m in modulos if m in ctx]
            assert len(encontrados) >= 1, \
                f"El contexto debe incluir al menos un módulo. Got: {ctx[:300]}"


# ---------------------------------------------------------------------------
# 13–14. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_max_chars_1(self):
        """max_chars=1 retorna string de longitud ≤ 1."""
        from core.self_observation.self_summary import (
            compose_self_summary_for_prompt,
            invalidate_cache,
        )
        invalidate_cache()
        out = compose_self_summary_for_prompt(max_chars=1)
        assert len(out) <= 1

    def test_fail_safe_total_todos_los_modulos(self):
        """Si TODOS los módulos fallan, retorna string (vacío o parcial)."""
        from core.self_observation.self_summary import _collect_module_state
        with patch(
            "core.nucleus.presencia_pura.observer_status",
            side_effect=RuntimeError("offline"),
        ), patch(
            "core.nucleus.convergence_learner.get_learner",
            side_effect=RuntimeError("offline"),
        ), patch(
            "core.router_learning.RouterDecisionLedger.read_last",
            side_effect=RuntimeError("offline"),
        ), patch(
            "core.state_manager.load",
            side_effect=RuntimeError("offline"),
        ):
            out = _collect_module_state()
            # No debe lanzar excepción
            assert isinstance(out, str)

    def test_no_expone_credenciales_ni_tokens(self):
        """El bloque NO debe contener tokens, passwords ni credenciales."""
        from core.self_observation.self_summary import (
            compose_self_summary_for_prompt,
            invalidate_cache,
        )
        invalidate_cache()
        out = compose_self_summary_for_prompt()
        forbidden = ["password", "token", "api_key", "secret", "bearer"]
        for term in forbidden:
            assert term not in out.lower(), \
                f"El bloque expone '{term}' — riesgo de seguridad"
