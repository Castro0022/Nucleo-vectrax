"""
Tests de integración — PresenciaObserver (Capa Inhibidora)

Verifica el comportamiento del observador inhibidor de Presencia Pura:

  1.  Motor desconocido (UNKNOWN) → BLOCK
  2.  Convergencia baja (<0.30)   → SILENCE
  3.  Soberanía baja (<0.30)      → BLOCK   (LLM_EXTERNAL, sovereignty=0.20)
  4.  Ruido elevado (>0.80)       → PAUSE
  5.  Ruido crítico + baja convergencia → BLOCK combinado
  6.  Alta convergencia + soberanía → PERMIT
  7.  Modo OBSERVER: enforced=False en toda decisión
  8.  Modo ACTIVE:   enforced=True  en toda decisión
  9.  Registros guardados y recuperables con get_records()
 10.  Estadísticas (get_stats) reflejan evaluaciones correctamente
 11.  Bus — subscribe al canal BROADCAST al llamar observe()
 12.  Bus — unsubscribe al llamar disconnect()
 13.  Singleton get_observer() retorna misma instancia
 14.  reset_observer() limpia el singleton
 15.  Inferencia de origen: módulo interno conocido → INTERNAL_RESPONSE
 16.  Inferencia de origen: canal externo → LLM_EXTERNAL
 17.  Inferencia de origen: canal de memoria → NUCLEUS_MEMORY
 18.  Inferencia de origen: módulo del núcleo → NUCLEUS_CORE
 19.  set_mode() cambia modo y rechaza modos inválidos
 20.  InhibitionDecision y EmissionOrigin son enums con valores esperados

Run:
    pytest tests/integration/test_presencia_inhibitor.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# Imports del módulo bajo prueba
# ---------------------------------------------------------------------------

from core.nucleus.presencia_pura import (
    EmissionOrigin,
    EmissionSignal,
    InhibitionDecision,
    InhibitionRecord,
    PresenciaObserver,
    get_observer,
    reset_observer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Garantiza que cada test empieza con un singleton limpio."""
    reset_observer()
    yield
    reset_observer()


@pytest.fixture()
def observer() -> PresenciaObserver:
    """Observador en modo OBSERVER (default — seguro)."""
    return PresenciaObserver(mode=PresenciaObserver.OBSERVER_MODE)


@pytest.fixture()
def active_observer() -> PresenciaObserver:
    """Observador en modo ACTIVE."""
    return PresenciaObserver(mode=PresenciaObserver.ACTIVE_MODE)


# ---------------------------------------------------------------------------
# 1. Motor desconocido → BLOCK
# ---------------------------------------------------------------------------

class TestInhibicion_MotorDesconocido_Block:

    def test_motor_completamente_desconocido_es_bloqueado(self, observer):
        signal = EmissionSignal(
            engine_name="motor_xyz_desconocido_8472",
            source_channel="canal.inexistente",
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert record.origin == EmissionOrigin.UNKNOWN
        assert record.sovereignty == 0.0
        assert "desconocido" in record.reason.lower()

    def test_motor_con_nombre_vacio_es_bloqueado(self, observer):
        signal = EmissionSignal(engine_name="", source_channel="")
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert record.origin == EmissionOrigin.UNKNOWN

    def test_motor_parcialmente_conocido_en_nombre_pero_canal_desconocido(self, observer):
        """Nombre que no coincide con ningún módulo registrado → UNKNOWN → BLOCK."""
        signal = EmissionSignal(
            engine_name="mi_motor_custom_v3",
            source_channel="live.custom",
        )
        record = observer.evaluate(signal)

        # "live.custom" no está en _INTERNAL_CHANNELS
        # "mi_motor_custom_v3" no está en ningún módulo conocido
        assert record.decision == InhibitionDecision.BLOCK
        assert record.origin == EmissionOrigin.UNKNOWN


# ---------------------------------------------------------------------------
# 2. Baja convergencia → SILENCE
# ---------------------------------------------------------------------------

class TestInhibicion_BajaConvergencia_Silence:

    def test_convergencia_muy_baja_produce_silence(self, observer):
        signal = EmissionSignal(
            engine_name="core.nucleus.total_convergence",
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.05,   # muy baja
            noise=0.0,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.SILENCE
        assert record.sovereignty == 1.0  # NUCLEUS_CORE siempre soberano
        assert "convergencia" in record.reason.lower()

    def test_convergencia_exactamente_en_umbral_no_silencia(self, observer):
        """convergence == 0.30 NO es < 0.30, por lo que NO debe ser SILENCE."""
        signal = EmissionSignal(
            engine_name="core.nucleus.total_convergence",
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.30,
            noise=0.0,
        )
        record = observer.evaluate(signal)

        assert record.decision != InhibitionDecision.SILENCE

    def test_convergencia_justo_debajo_umbral_produce_silence(self, observer):
        signal = EmissionSignal(
            engine_name="core.governor",
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.29,
            noise=0.0,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.SILENCE


# ---------------------------------------------------------------------------
# 3. Baja soberanía → BLOCK
# ---------------------------------------------------------------------------

class TestInhibicion_BajaSoberania_Block:

    def test_llm_externo_bloqueado_por_baja_soberania(self, observer):
        """LLM_EXTERNAL tiene sovereignty=0.20, debajo del threshold 0.30."""
        signal = EmissionSignal(
            engine_name="openai_gateway",
            origin=EmissionOrigin.LLM_EXTERNAL,
            convergence=0.9,   # convergencia alta, pero soberanía insuficiente
            noise=0.0,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert record.sovereignty == 0.20
        assert "soberanía" in record.reason.lower()

    def test_busqueda_externa_bloqueada_por_soberania_minima(self, observer):
        """SEARCH_EXTERNAL tiene sovereignty=0.10."""
        signal = EmissionSignal(
            engine_name="tavily_search",
            origin=EmissionOrigin.SEARCH_EXTERNAL,
            convergence=0.8,
            noise=0.1,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert record.sovereignty == 0.10

    def test_soberania_exactamente_en_umbral_no_bloquea(self, observer):
        """sovereignty == 0.30 NO es < 0.30; debe pasar a la siguiente regla."""
        # REFLEX_FAST_PATH = 0.70, por encima del umbral
        signal = EmissionSignal(
            engine_name="vectrax.fast_path",
            origin=EmissionOrigin.REFLEX_FAST_PATH,
            convergence=0.9,
            noise=0.0,
        )
        record = observer.evaluate(signal)

        assert record.decision != InhibitionDecision.BLOCK


# ---------------------------------------------------------------------------
# 4. Ruido elevado → PAUSE
# ---------------------------------------------------------------------------

class TestInhibicion_RuidoElevado_Pause:

    def test_ruido_alto_produce_pause(self, observer):
        signal = EmissionSignal(
            engine_name="core.governor",
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.70,   # no baja (no SILENCE), >= 0.50 (no BLOCK combinado)
            noise=0.85,         # > 0.80 (PAUSE) pero < 0.90 (no BLOCK combinado)
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.PAUSE
        assert "ruido" in record.reason.lower()

    def test_ruido_justo_en_umbral_pause_produce_pause(self, observer):
        """noise == 0.81 > 0.80 → PAUSE."""
        signal = EmissionSignal(
            engine_name="core.governor",
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            noise=0.81,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.PAUSE

    def test_ruido_bajo_umbral_no_produce_pause(self, observer):
        signal = EmissionSignal(
            engine_name="core.governor",
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.8,
            noise=0.79,  # justo debajo del umbral PAUSE
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.PERMIT


# ---------------------------------------------------------------------------
# 5. Ruido crítico + baja convergencia → BLOCK combinado
# ---------------------------------------------------------------------------

class TestInhibicion_RuidoCritico_Block:

    def test_ruido_critico_con_baja_convergencia_produce_block(self, observer):
        signal = EmissionSignal(
            engine_name="core.governor",
            origin=EmissionOrigin.INTERNAL_RESPONSE,  # sovereignty=0.65 (pasa R2)
            convergence=0.35,   # >= 0.30 (pasa R3), pero < 0.50
            noise=0.95,         # > 0.90 → BLOCK combinado (R4)
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert "ruido" in record.reason.lower()
        assert "combinado" in record.reason.lower() or "crítico" in record.reason.lower()

    def test_ruido_critico_con_convergencia_alta_produce_pause(self, observer):
        """noise > 0.90 pero convergence >= 0.50 → no BLOCK combinado → cae a PAUSE."""
        signal = EmissionSignal(
            engine_name="core.governor",
            origin=EmissionOrigin.INTERNAL_RESPONSE,
            convergence=0.55,   # >= 0.50, por lo que R4 no aplica
            noise=0.95,         # > 0.90 pero convergencia >= 0.50
        )
        record = observer.evaluate(signal)

        # R4 requiere convergence < 0.50, aquí es 0.55 → no BLOCK combinado
        # R5: noise > 0.80 → PAUSE
        assert record.decision == InhibitionDecision.PAUSE


# ---------------------------------------------------------------------------
# 6. Alta convergencia y soberanía → PERMIT
# ---------------------------------------------------------------------------

class TestInhibicion_AltaConvergencia_Permit:

    def test_emision_soberana_convergente_produce_permit(self, observer):
        signal = EmissionSignal(
            engine_name="core.nucleus.total_convergence",
            origin=EmissionOrigin.NUCLEUS_CONVERGENCE,
            convergence=0.90,
            noise=0.05,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.PERMIT
        assert record.sovereignty == 0.85  # NUCLEUS_CONVERGENCE
        assert "soberana" in record.reason.lower()

    def test_nucleo_core_con_convergencia_neutra_produce_permit(self, observer):
        """Convergencia neutra (0.5) y soberanía total → PERMIT."""
        signal = EmissionSignal(
            engine_name="core.operator.nucleus",
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.50,
            noise=0.10,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.PERMIT
        assert record.sovereignty == 1.0

    def test_identidad_soberana_produce_permit(self, observer):
        signal = EmissionSignal(
            engine_name="core.operator.identity",
            origin=EmissionOrigin.NUCLEUS_IDENTITY,
            convergence=0.80,
            noise=0.02,
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.PERMIT
        assert record.sovereignty == 0.95


# ---------------------------------------------------------------------------
# 7. Modo OBSERVER — enforced=False
# ---------------------------------------------------------------------------

class TestModoObserver_NoEnforced:

    def test_observer_mode_enforced_false_para_permit(self, observer):
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
        )
        record = observer.evaluate(signal)

        assert record.enforced is False

    def test_observer_mode_enforced_false_incluso_para_block(self, observer):
        """En modo OBSERVER, incluso una decisión BLOCK tiene enforced=False."""
        signal = EmissionSignal(
            engine_name="motor_desconocido",
            source_channel="canal.raro",
        )
        record = observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert record.enforced is False, "En OBSERVER mode nunca se aplica la inhibición"

    def test_observer_mode_property(self, observer):
        assert observer.mode == PresenciaObserver.OBSERVER_MODE


# ---------------------------------------------------------------------------
# 8. Modo ACTIVE — enforced=True
# ---------------------------------------------------------------------------

class TestModoActive_Enforced:

    def test_active_mode_enforced_true_para_permit(self, active_observer):
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.9,
        )
        record = active_observer.evaluate(signal)

        assert record.enforced is True

    def test_active_mode_enforced_true_para_block(self, active_observer):
        signal = EmissionSignal(
            engine_name="motor_desconocido",
            source_channel="canal.raro",
        )
        record = active_observer.evaluate(signal)

        assert record.decision == InhibitionDecision.BLOCK
        assert record.enforced is True

    def test_active_mode_enforced_true_para_silence(self, active_observer):
        signal = EmissionSignal(
            origin=EmissionOrigin.NUCLEUS_CORE,
            convergence=0.05,  # muy baja → SILENCE
        )
        record = active_observer.evaluate(signal)

        assert record.decision == InhibitionDecision.SILENCE
        assert record.enforced is True


# ---------------------------------------------------------------------------
# 9. Registros guardados y recuperables
# ---------------------------------------------------------------------------

class TestRegistros:

    def test_records_se_almacenan_tras_evaluate(self, observer):
        assert len(observer.get_records()) == 0

        observer.evaluate(EmissionSignal(origin=EmissionOrigin.NUCLEUS_CORE, convergence=0.9))
        observer.evaluate(EmissionSignal(origin=EmissionOrigin.LLM_EXTERNAL))

        assert len(observer.get_records()) == 2

    def test_records_son_InhibitionRecord(self, observer):
        observer.evaluate(EmissionSignal(origin=EmissionOrigin.NUCLEUS_CORE, convergence=0.9))
        records = observer.get_records()

        assert isinstance(records[0], InhibitionRecord)

    def test_records_limit_respetado(self):
        obs = PresenciaObserver(record_limit=3)
        for i in range(5):
            obs.evaluate(EmissionSignal(origin=EmissionOrigin.NUCLEUS_CORE, convergence=0.9))

        assert len(obs.get_records()) == 3

    def test_get_records_limit_param(self, observer):
        for _ in range(10):
            observer.evaluate(EmissionSignal(origin=EmissionOrigin.NUCLEUS_CORE, convergence=0.9))

        recent = observer.get_records(limit=3)
        assert len(recent) == 3

    def test_records_contienen_timestamp(self, observer):
        observer.evaluate(EmissionSignal(origin=EmissionOrigin.NUCLEUS_CORE, convergence=0.9))
        record = observer.get_records()[0]

        assert record.timestamp is not None
        assert "T" in record.timestamp  # ISO format


# ---------------------------------------------------------------------------
# 10. Estadísticas
# ---------------------------------------------------------------------------

class TestEstadisticas:

    def test_stats_iniciales_todos_cero(self, observer):
        stats = observer.get_stats()

        assert stats["total_evaluated"] == 0
        assert stats["by_decision"]["PERMIT"] == 0
        assert stats["by_decision"]["BLOCK"] == 0

    def test_stats_reflejan_evaluaciones(self, observer):
        observer.evaluate(EmissionSignal(origin=EmissionOrigin.NUCLEUS_CORE, convergence=0.9))   # PERMIT
        observer.evaluate(EmissionSignal(engine_name="x", source_channel="x.raro"))              # BLOCK

        stats = observer.get_stats()
        assert stats["total_evaluated"] == 2
        assert stats["by_decision"]["PERMIT"] >= 1
        assert stats["by_decision"]["BLOCK"] >= 1

    def test_stats_contienen_modo(self, observer):
        stats = observer.get_stats()
        assert stats["mode"] == PresenciaObserver.OBSERVER_MODE

    def test_stats_not_connected_by_default(self, observer):
        assert observer.get_stats()["connected_to_bus"] is False


# ---------------------------------------------------------------------------
# 11. Bus — subscribe en observe()
# ---------------------------------------------------------------------------

class TestBus_Subscribe:

    def test_observe_suscribe_al_bus(self):
        from core.operator.universal_bus import reset_bus, get_universal_bus, Channels
        reset_bus()

        obs = PresenciaObserver()
        assert not obs.is_connected

        obs.observe()

        assert obs.is_connected
        bus = get_universal_bus()
        subs = bus.get_subscribers(Channels.BROADCAST)
        assert "presencia_pura.observer" in subs

        obs.disconnect()
        reset_bus()

    def test_observe_es_idempotente(self):
        """Llamar observe() dos veces no duplica suscripciones."""
        from core.operator.universal_bus import reset_bus, get_universal_bus, Channels
        reset_bus()

        obs = PresenciaObserver()
        obs.observe()
        obs.observe()  # segunda llamada

        bus = get_universal_bus()
        subs = [s for s in bus.get_subscribers(Channels.BROADCAST)
                if s == "presencia_pura.observer"]
        assert len(subs) == 1

        obs.disconnect()
        reset_bus()

    def test_observe_retorna_self(self):
        obs = PresenciaObserver()
        result = obs.observe()
        assert result is obs
        obs.disconnect()


# ---------------------------------------------------------------------------
# 12. Bus — disconnect()
# ---------------------------------------------------------------------------

class TestBus_Disconnect:

    def test_disconnect_elimina_suscripcion(self):
        from core.operator.universal_bus import reset_bus, get_universal_bus, Channels
        reset_bus()

        obs = PresenciaObserver()
        obs.observe()
        assert obs.is_connected

        obs.disconnect()
        assert not obs.is_connected

        bus = get_universal_bus()
        subs = bus.get_subscribers(Channels.BROADCAST)
        assert "presencia_pura.observer" not in subs

        reset_bus()

    def test_disconnect_sin_conexion_es_seguro(self):
        """Llamar disconnect() sin haber conectado no lanza excepción."""
        obs = PresenciaObserver()
        obs.disconnect()  # no debe lanzar

    def test_disconnect_doble_es_seguro(self):
        """Dos llamadas a disconnect() no deben lanzar excepción."""
        from core.operator.universal_bus import reset_bus
        reset_bus()

        obs = PresenciaObserver()
        obs.observe()
        obs.disconnect()
        obs.disconnect()  # segunda llamada segura

        reset_bus()


# ---------------------------------------------------------------------------
# 13. Singleton get_observer()
# ---------------------------------------------------------------------------

class TestSingleton:

    def test_get_observer_retorna_misma_instancia(self):
        obs1 = get_observer()
        obs2 = get_observer()
        assert obs1 is obs2

    def test_get_observer_inicia_en_modo_observer(self):
        obs = get_observer()
        assert obs.mode == PresenciaObserver.OBSERVER_MODE

    def test_reset_observer_limpia_singleton(self):
        obs1 = get_observer()
        reset_observer()
        obs2 = get_observer()
        assert obs1 is not obs2


# ---------------------------------------------------------------------------
# 14. Inferencia de origen
# ---------------------------------------------------------------------------

class TestInferenciaOrigen:

    def test_modulo_interno_conocido_infiere_internal_response(self, observer):
        signal = EmissionSignal(
            engine_name="core.governor",
            source_channel="",  # sin canal → usar nombre de módulo
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.INTERNAL_RESPONSE

    def test_canal_externo_infiere_llm_external(self, observer):
        signal = EmissionSignal(
            engine_name="alguna_respuesta",
            source_channel="external",
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.LLM_EXTERNAL

    def test_canal_memoria_infiere_nucleus_memory(self, observer):
        signal = EmissionSignal(
            engine_name="cognition.memory.memory_engine",
            source_channel="layer.3.memory",
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.NUCLEUS_MEMORY

    def test_modulo_nucleo_soberano_infiere_nucleus_core(self, observer):
        signal = EmissionSignal(
            engine_name="core.operator.nucleus",
            source_channel="layer.1.nucleus",
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.NUCLEUS_CORE

    def test_canal_busqueda_infiere_search_external(self, observer):
        signal = EmissionSignal(
            engine_name="tavily_search_engine",
            source_channel="external",
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.SEARCH_EXTERNAL

    def test_canal_convergencia_infiere_nucleus_convergence(self, observer):
        signal = EmissionSignal(
            engine_name="convergence_worker",
            source_channel="live.convergence",
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.NUCLEUS_CONVERGENCE

    def test_canal_percepcion_infiere_nucleus_perception(self, observer):
        signal = EmissionSignal(
            engine_name="perception_engine_v2",
            source_channel="layer.2.perception",
        )
        record = observer.evaluate(signal)
        assert record.origin == EmissionOrigin.NUCLEUS_PERCEPTION


# ---------------------------------------------------------------------------
# 15. set_mode()
# ---------------------------------------------------------------------------

class TestSetMode:

    def test_set_mode_cambia_a_active(self, observer):
        assert observer.mode == PresenciaObserver.OBSERVER_MODE
        observer.set_mode(PresenciaObserver.ACTIVE_MODE)
        assert observer.mode == PresenciaObserver.ACTIVE_MODE

    def test_set_mode_cambia_a_observer(self, active_observer):
        assert active_observer.mode == PresenciaObserver.ACTIVE_MODE
        active_observer.set_mode(PresenciaObserver.OBSERVER_MODE)
        assert active_observer.mode == PresenciaObserver.OBSERVER_MODE

    def test_set_mode_invalido_lanza_exception(self, observer):
        with pytest.raises(ValueError, match="Modo inválido"):
            observer.set_mode("INVALID_MODE")

    def test_init_invalido_lanza_exception(self):
        with pytest.raises(ValueError, match="Modo inválido"):
            PresenciaObserver(mode="TURBO")


# ---------------------------------------------------------------------------
# 16. Enums completos
# ---------------------------------------------------------------------------

class TestEnums:

    def test_emission_origin_tiene_todos_los_valores(self):
        expected = {
            "nucleus_core", "nucleus_memory", "nucleus_identity",
            "nucleus_convergence", "nucleus_perception", "nucleus_hypothesis",
            "reflex_fast_path", "internal_response",
            "llm_external", "search_external", "unknown",
        }
        actual = {e.value for e in EmissionOrigin}
        assert expected == actual

    def test_inhibition_decision_tiene_cuatro_valores(self):
        assert {e.value for e in InhibitionDecision} == {
            "PERMIT", "PAUSE", "SILENCE", "BLOCK"
        }

    def test_enums_son_string_enums(self):
        assert isinstance(EmissionOrigin.UNKNOWN, str)
        assert isinstance(InhibitionDecision.BLOCK, str)
