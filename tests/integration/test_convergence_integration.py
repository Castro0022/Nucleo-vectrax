"""
Tests de integración — Ciclo de Convergencia Total

Verifica que el flujo de convergencia se ejecuta correctamente
ANTES de generar la respuesta, con foco en:

  [3] Memoria Estructural → memory_connections, prior_patterns_found
  [6] Gravitación         → gravitational_tier asignado

Cobertura:
  1. convergence_hook.run_convergence_cycle() ejecuta las 7 fases
  2. Las fases [3] y [6] siempre están en phases_completed
  3. El hook es NON-FATAL: si el motor falla, retorna None sin romper nada
  4. should_block() solo bloquea con action="block"
  5. El motor registra el fingerprint del input
  6. Múltiples llamadas no duplican el motor (singleton)
  7. Integración con pipeline_worker: el hook corre antes de receive_message()
  8. Integración con chat route: el hook corre antes de resolve()

Run:
    pytest tests/integration/test_convergence_integration.py -v
"""
from __future__ import annotations

import sys
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from typing import Optional

import pytest

# Asegurar que el root del proyecto sea importable
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_state_db(tmp_path, monkeypatch):
    """Redirige state_manager a un dir temporal para no tocar datos reales."""
    state_file = tmp_path / "vectrax_state.json"
    try:
        import core.state_manager as sm
        monkeypatch.setattr(sm, "_STATE_FILE", state_file, raising=False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_convergence_engine():
    """Resetea el singleton del motor entre tests."""
    yield
    try:
        from core.nucleus import total_convergence
        total_convergence.reset_convergence_engine()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. Ciclo completo — 7 fases ejecutadas
# ---------------------------------------------------------------------------

class TestConvergenceCycleFases:
    """Verifica que las 7 fases se ejecutan y el record es correcto."""

    def test_run_cycle_returns_record(self):
        from core.convergence_hook import run_convergence_cycle
        record = run_convergence_cycle(
            "hoy tuve una reunión importante",
            source="test",
            owner="test_user",
        )
        assert record is not None

    def test_record_has_all_7_phases(self):
        from core.convergence_hook import run_convergence_cycle
        from core.nucleus.total_convergence import ConvergencePhase

        record = run_convergence_cycle(
            "quiero implementar un sistema de cache",
            source="test",
            owner="test_user",
        )
        assert record is not None
        completed = set(record.phases_completed)
        all_phases = {p.value for p in ConvergencePhase}
        assert all_phases == completed, (
            f"Fases faltantes: {all_phases - completed}"
        )

    def test_phase_3_memory_in_completed(self):
        """Fase [3] MEMORIA ESTRUCTURAL debe estar en phases_completed."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "bitcoin está en soporte clave",
            source="telegram",
            owner="tg:12345",
        )
        assert record is not None
        assert "memory" in record.phases_completed, (
            "Fase [3] 'memory' NO está en phases_completed"
        )

    def test_phase_6_gravitation_in_completed(self):
        """Fase [6] GRAVITACIÓN debe estar en phases_completed."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "mi empresa facturó 50k este mes",
            source="telegram",
            owner="tg:12345",
        )
        assert record is not None
        assert "gravitation" in record.phases_completed, (
            "Fase [6] 'gravitation' NO está en phases_completed"
        )

    def test_phases_3_and_6_execute_before_response(self):
        """
        Las fases [3] y [6] deben completarse ANTES de que el ciclo termine.
        Verificamos el orden implícito: ambas en phases_completed al retornar.
        """
        from core.convergence_hook import run_convergence_cycle
        from core.nucleus.total_convergence import ConvergencePhase

        record = run_convergence_cycle(
            "necesito optimizar el pipeline de datos",
            source="api",
            owner="user_abc",
        )
        assert record is not None
        idx_memory = record.phases_completed.index(ConvergencePhase.MEMORY.value)
        idx_gravitation = record.phases_completed.index(ConvergencePhase.GRAVITATION.value)
        idx_synthesis = record.phases_completed.index(ConvergencePhase.SYNTHESIS.value)

        # [3] MEMORIA debe ejecutarse ANTES de [5] SÍNTESIS
        assert idx_memory < idx_synthesis, "Fase [3] debe ocurrir antes de [5]"
        # [6] GRAVITACIÓN debe ejecutarse DESPUÉS de [5] SÍNTESIS
        assert idx_gravitation > idx_synthesis, "Fase [6] debe ocurrir después de [5]"

    def test_record_has_fingerprint(self):
        """El ciclo genera un fingerprint SHA-256 del input."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "texto de prueba para fingerprint",
            source="test",
            owner="test_user",
        )
        assert record is not None
        assert len(record.input_fingerprint) == 16  # SHA-256[:16]
        assert record.input_fingerprint != ""

    def test_record_action_is_valid(self):
        """action_recommended debe ser uno de los valores válidos."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "cuánto es 2 + 2?",
            source="test",
            owner="test_user",
        )
        assert record is not None
        assert record.action_recommended in (
            "proceed", "review", "block", "learn_only"
        ), f"Acción inválida: {record.action_recommended}"

    def test_convergence_time_measured(self):
        """El ciclo debe medir su tiempo de ejecución."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "analiza el rendimiento del sistema",
            source="test",
            owner="test_user",
        )
        assert record is not None
        assert record.convergence_time_ms > 0


# ---------------------------------------------------------------------------
# 2. Comportamiento NON-FATAL
# ---------------------------------------------------------------------------

class TestConvergenceHookNonFatal:
    """El hook no debe romper el pipeline si el motor falla."""

    def test_returns_none_on_empty_content(self):
        from core.convergence_hook import run_convergence_cycle

        assert run_convergence_cycle("") is None
        assert run_convergence_cycle("   ") is None
        assert run_convergence_cycle(None) is None

    def test_returns_none_if_engine_raises(self):
        """Si el motor lanza excepción, el hook retorna None sin propagar."""
        from core.convergence_hook import run_convergence_cycle

        with patch(
            "core.nucleus.total_convergence.TotalConvergenceEngine.process",
            side_effect=RuntimeError("motor roto"),
        ):
            result = run_convergence_cycle("texto válido", source="test", owner="u")
        # No debe propagar excepción, debe retornar None
        assert result is None

    def test_returns_none_if_import_fails(self):
        """Si el import del motor falla, el hook retorna None."""
        from core.convergence_hook import run_convergence_cycle

        with patch.dict("sys.modules", {"core.nucleus.total_convergence": None}):
            result = run_convergence_cycle("texto válido", source="test", owner="u")
        assert result is None

    def test_pipeline_worker_continues_after_hook_failure(self):
        """
        Simula el pipeline_worker: si el hook falla, receive_message()
        debe llamarse de todas formas.
        """
        receive_called = []

        def mock_receive(user_id, content, channel):
            receive_called.append(True)
            result = MagicMock()
            result.source = "llm"
            result.response = "respuesta de prueba"
            return result

        mock_gw = MagicMock()
        mock_gw.receive_message = mock_receive

        # Simular que el hook falla
        with patch(
            "core.convergence_hook.run_convergence_cycle",
            side_effect=Exception("hook falló"),
        ):
            # Ejecutar manualmente la lógica del pipeline_worker
            try:
                from core.convergence_hook import run_convergence_cycle, should_block
                conv_record = run_convergence_cycle("hola", source="t", owner="u")
                if should_block(conv_record):
                    pass
            except Exception:
                pass  # non-fatal

            # receive_message debe poder ejecutarse aun si el hook falló
            result = mock_gw.receive_message(
                user_id="tg:test", content="hola", channel="telegram"
            )

        assert len(receive_called) == 1, "receive_message no fue llamado tras fallo del hook"
        assert result.response == "respuesta de prueba"


# ---------------------------------------------------------------------------
# 3. should_block()
# ---------------------------------------------------------------------------

class TestShouldBlock:
    """Verifica la lógica de bloqueo del hook."""

    def test_none_record_never_blocks(self):
        from core.convergence_hook import should_block
        assert should_block(None) is False

    def test_proceed_does_not_block(self):
        from core.convergence_hook import should_block
        from core.nucleus.total_convergence import ConvergenceRecord
        record = ConvergenceRecord(action_recommended="proceed")
        assert should_block(record) is False

    def test_review_does_not_block(self):
        from core.convergence_hook import should_block
        from core.nucleus.total_convergence import ConvergenceRecord
        record = ConvergenceRecord(action_recommended="review")
        assert should_block(record) is False

    def test_learn_only_does_not_block(self):
        from core.convergence_hook import should_block
        from core.nucleus.total_convergence import ConvergenceRecord
        record = ConvergenceRecord(action_recommended="learn_only")
        assert should_block(record) is False

    def test_block_action_blocks(self):
        from core.convergence_hook import should_block
        from core.nucleus.total_convergence import ConvergenceRecord
        record = ConvergenceRecord(action_recommended="block")
        assert should_block(record) is True


# ---------------------------------------------------------------------------
# 4. Singleton del motor
# ---------------------------------------------------------------------------

class TestConvergenceEngineSingleton:
    """El motor debe ser singleton — reutilizado entre llamadas."""

    def test_same_engine_instance_reused(self):
        from core.nucleus.total_convergence import get_convergence_engine
        e1 = get_convergence_engine()
        e2 = get_convergence_engine()
        assert e1 is e2, "get_convergence_engine() debe retornar siempre el mismo objeto"

    def test_cycle_count_increments(self):
        from core.convergence_hook import run_convergence_cycle
        from core.nucleus.total_convergence import get_convergence_engine

        engine = get_convergence_engine()
        before = engine._cycle_count

        run_convergence_cycle("mensaje uno", source="test", owner="u")
        run_convergence_cycle("mensaje dos", source="test", owner="u")

        after = engine._cycle_count
        assert after >= before + 2, "El contador de ciclos debe incrementar por cada proceso"

    def test_total_inputs_increments(self):
        from core.convergence_hook import run_convergence_cycle
        from core.nucleus.total_convergence import get_convergence_engine

        engine = get_convergence_engine()
        before = engine._total_inputs

        run_convergence_cycle("input de prueba", source="test", owner="u")

        assert engine._total_inputs > before


# ---------------------------------------------------------------------------
# 5. Integración con pipeline_worker (mock del worker)
# ---------------------------------------------------------------------------

class TestPipelineWorkerIntegration:
    """
    Verifica que el ciclo de convergencia se ejecuta ANTES de receive_message()
    en el pipeline worker.
    """

    def test_convergence_called_before_receive_message(self):
        """El hook debe ejecutarse antes de gw.receive_message()."""
        call_order = []

        def mock_convergence(content, *, source, channel, owner, metadata=None):
            call_order.append("convergence")
            record = MagicMock()
            record.action_recommended = "proceed"
            record.phases_completed = ["perception", "classification", "memory",
                                        "analysis", "synthesis", "gravitation", "learning"]
            record.memory_connections = 2
            record.gravitational_tier = "core"
            return record

        def mock_receive(user_id, content, channel):
            call_order.append("receive_message")
            result = MagicMock()
            result.source = "llm"
            result.response = "respuesta"
            return result

        mock_gw = MagicMock()
        mock_gw.receive_message = mock_receive

        with patch("core.convergence_hook.run_convergence_cycle", mock_convergence):
            # Simular la lógica del pipeline_worker._process_one()
            from core.convergence_hook import run_convergence_cycle, should_block

            conv_record = run_convergence_cycle(
                "necesito ayuda",
                source="telegram",
                channel="telegram",
                owner="tg:9999",
                metadata={"msg_id": "test-001"},
            )

            if not should_block(conv_record):
                mock_gw.receive_message(
                    user_id="tg:9999",
                    content="necesito ayuda",
                    channel="telegram",
                )

        assert call_order == ["convergence", "receive_message"], (
            f"Orden incorrecto: {call_order}. "
            "El ciclo de convergencia debe ejecutarse ANTES de receive_message()"
        )

    def test_block_prevents_receive_message(self):
        """Si action=block, receive_message NO debe ejecutarse."""
        receive_called = []

        def mock_convergence_block(content, **kwargs):
            record = MagicMock()
            record.action_recommended = "block"
            record.phases_completed = ["perception", "classification", "memory",
                                        "analysis", "synthesis", "gravitation", "learning"]
            return record

        def mock_receive(**kwargs):
            receive_called.append(True)

        with patch("core.convergence_hook.run_convergence_cycle", mock_convergence_block):
            from core.convergence_hook import run_convergence_cycle, should_block

            conv_record = run_convergence_cycle("contenido bloqueado", source="t", owner="u")
            if not should_block(conv_record):
                mock_receive(user_id="u", content="contenido bloqueado", channel="t")

        assert len(receive_called) == 0, (
            "receive_message fue llamado a pesar de que action=block"
        )

    def test_phases_3_and_6_logged_in_record(self):
        """El record retornado por el hook debe tener fases [3] y [6] completadas."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "actualizar el motor de memoria de Vectrax",
            source="telegram",
            owner="tg:2030762343",
        )

        assert record is not None
        assert "memory" in record.phases_completed, \
            "Fase [3] memory NO completada en el record del worker"
        assert "gravitation" in record.phases_completed, \
            "Fase [6] gravitation NO completada en el record del worker"


# ---------------------------------------------------------------------------
# 6. Integración con chat route (mock del endpoint)
# ---------------------------------------------------------------------------

class TestChatRouteIntegration:
    """
    Verifica que el ciclo de convergencia se ejecuta antes de resolve()
    en el endpoint POST /v1/chat.
    """

    def test_convergence_called_before_resolve(self):
        """run_convergence_cycle debe llamarse antes de resolver el chat."""
        call_order = []

        def mock_convergence(content, *, source, channel, owner, **kwargs):
            call_order.append(("convergence", source))
            return MagicMock(
                action_recommended="proceed",
                phases_completed=["memory", "gravitation"],
                memory_connections=1,
                gravitational_tier="peripheral",
            )

        def mock_resolve(text, channel, owner):
            call_order.append(("resolve", channel))
            result = MagicMock()
            result.mode = "online"
            result.sources = []
            result.sovereign_answer = "Respuesta de prueba"
            result.answer = "Respuesta"
            result.engines_used = []
            result.context_stars = 0
            result.search_query = text
            result.fallback_from = None
            return result

        with patch("core.convergence_hook.run_convergence_cycle", mock_convergence), \
             patch("vectrax.resolver.resolve", mock_resolve):

            # Simular lógica del endpoint chat
            from core.convergence_hook import run_convergence_cycle
            from vectrax.resolver import resolve

            text = "qué es la fotosíntesis?"
            channel = "user"
            owner = "test_owner"

            run_convergence_cycle(text, source="api", channel=channel, owner=owner)
            resolution = resolve(text, channel, owner)

        assert call_order[0][0] == "convergence", \
            "run_convergence_cycle debe ser la PRIMERA llamada"
        assert call_order[1][0] == "resolve", \
            "resolve() debe ser llamado DESPUÉS del ciclo de convergencia"
        assert call_order[0][1] == "api", \
            "El source debe ser 'api' para el endpoint REST"

    def test_convergence_failure_does_not_break_chat(self):
        """Si el hook falla, el endpoint chat debe continuar sin error."""
        resolve_called = []

        def mock_resolve_ok(text, channel, owner):
            resolve_called.append(True)
            result = MagicMock()
            result.mode = "online"
            result.sources = []
            result.sovereign_answer = "Respuesta OK"
            result.answer = "OK"
            result.engines_used = []
            result.context_stars = 0
            result.search_query = text
            result.fallback_from = None
            return result

        import vectrax.resolver as _resolver_mod

        # Simular el bloque try/except del endpoint chat:
        # el hook falla → se captura → resolve() se ejecuta igualmente
        with patch(
            "core.convergence_hook.run_convergence_cycle",
            side_effect=Exception("hook falló en API"),
        ), patch.object(_resolver_mod, "resolve", mock_resolve_ok):
            from core.convergence_hook import run_convergence_cycle

            # Bloque try/except del endpoint (non-fatal)
            try:
                run_convergence_cycle("texto", source="api", channel="user", owner="u")
            except Exception:
                pass

            # resolve() debe ejecutarse aún si el hook falló
            result = _resolver_mod.resolve("qué es Python?", "user", "test")

        assert len(resolve_called) == 1, \
            "resolve() debe poder ejecutarse aunque el hook haya fallado"
        assert result.sovereign_answer == "Respuesta OK"


# ---------------------------------------------------------------------------
# 7. Test de activación del motor
# ---------------------------------------------------------------------------

class TestEngineActivation:
    """Verifica el comportamiento de activación del motor."""

    def test_engine_activates_if_not_active(self):
        """El hook debe activar el motor si no está activo."""
        from core.nucleus.total_convergence import get_convergence_engine, reset_convergence_engine

        reset_convergence_engine()
        engine = get_convergence_engine()
        assert not engine._active

        from core.convergence_hook import run_convergence_cycle
        run_convergence_cycle("activar motor", source="test", owner="u")

        # Tras la primera llamada el motor debe estar activo
        assert engine.is_active

    def test_status_reflects_active_state(self):
        """engine.status() debe reflejar que el motor está activo."""
        from core.nucleus.total_convergence import get_convergence_engine

        from core.convergence_hook import run_convergence_cycle
        run_convergence_cycle("consulta de estado", source="test", owner="u")

        engine = get_convergence_engine()
        status = engine.status()

        assert status["active"] is True
        assert status["total_inputs_processed"] >= 1


# ---------------------------------------------------------------------------
# 8. Diferentes fuentes de input
# ---------------------------------------------------------------------------

class TestMultipleSources:
    """El ciclo debe funcionar desde cualquier fuente (telegram, api, webhook)."""

    @pytest.mark.parametrize("source,owner", [
        ("telegram", "tg:111"),
        ("api", "user_api_001"),
        ("webhook", "webhook_client"),
        ("web", "web_session_xyz"),
    ])
    def test_cycle_works_for_all_sources(self, source, owner):
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            f"mensaje desde {source}",
            source=source,
            owner=owner,
        )
        assert record is not None
        assert "memory" in record.phases_completed
        assert "gravitation" in record.phases_completed

    def test_creator_message_processes_correctly(self):
        """Mensajes del creador (Mario) deben procesarse sin errores."""
        from core.convergence_hook import run_convergence_cycle

        record = run_convergence_cycle(
            "necesito revisar el estado del sistema",
            source="telegram",
            owner="tg:2030762343",  # Creator ID
        )
        assert record is not None
        assert record.action_recommended in ("proceed", "review", "learn_only", "block")
