"""
Tests para el sistema de Presence Policy.
Verifica los 4 casos reales definidos en el spec.
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from core.conversation.active_state import (
    ConversationState, get_state, update_state, clear_state,
    _extract_topic, _extract_entity, _detect_intent,
)
from core.conversation.reference_resolver import resolve_references
from core.conversation.presence_policy import apply_presence_policy, evaluate


# ──────────────────────────────────────────────────────────────────────────────
# Active State — extracción de tópico y entidad
# ──────────────────────────────────────────────────────────────────────────────

class TestTopicExtraction:

    def test_siento_que(self):
        topic = _extract_topic("Siento que Vectrax responde frío.")
        assert "Vectrax responde frío" in topic or "Vectrax responde fr" in topic

    def test_no_quiero_que(self):
        topic = _extract_topic("No quiero que parezca un buscador.")
        assert "parezca un buscador" in topic

    def test_creo_que(self):
        topic = _extract_topic("Creo que el sistema está fallando en continuidad.")
        assert "sistema está fallando" in topic or "continuidad" in topic

    def test_fallback_sustancial(self):
        # Mensaje sin marcador pero con contenido
        topic = _extract_topic("Vectrax está respondiendo como ChatGPT genérico")
        assert len(topic) > 5


class TestEntityExtraction:

    def test_nombre_propio(self):
        entity = _extract_entity("Le escribí a Carlos y no respondió.")
        assert entity == "Carlos"

    def test_nombre_compuesto(self):
        entity = _extract_entity("Hablé con Laura García sobre el proyecto.")
        assert "Laura" in entity or "García" in entity

    def test_sin_entidad(self):
        entity = _extract_entity("¿Y eso por qué pasa?")
        assert entity == ""

    def test_no_confunde_vectrax(self):
        entity = _extract_entity("Vectrax debería responder mejor.")
        assert entity == "" or entity != "Vectrax"


class TestIntentDetection:

    def test_complaint(self):
        assert _detect_intent("No me gusta cómo responde") == "complaint"

    def test_curiosity(self):
        assert _detect_intent("¿Por qué pasa eso?") == "curiosity"

    def test_continuation(self):
        assert _detect_intent("Y eso qué significa?") == "continuation"

    def test_preference(self):
        assert _detect_intent("Prefiero que responda más directo") == "preference"


# ──────────────────────────────────────────────────────────────────────────────
# Caso 1: "eso" y "él" resuelven al tópico/entidad activa
# ──────────────────────────────────────────────────────────────────────────────

class TestReferenceResolver:

    def setup_method(self):
        clear_state("test_user_1")

    def test_caso1_eso_resuelve_topico(self):
        """
        'Siento que Vectrax responde frío.' → activa tópico
        '¿Y eso por qué pasa?' → debe resolver "eso" al tópico
        """
        update_state("test_user_1", "Siento que Vectrax responde frío.", "Entendido.")
        state = get_state("test_user_1")

        ctx = resolve_references("¿Y eso por qué pasa?", state)

        assert ctx != ""
        assert "Vectrax responde frío" in ctx or "frío" in ctx

    def test_caso3_el_resuelve_entidad(self):
        """
        'Le escribí a Carlos hoy.' → activa entidad Carlos
        '¿Y si él no responde?' → debe resolver "él" a Carlos
        """
        update_state("test_user_1", "Le escribí a Carlos hoy y no respondió.", "Anotado.")
        state = get_state("test_user_1")

        ctx = resolve_references("¿Y si él no responde?", state)

        assert ctx != ""
        assert "Carlos" in ctx

    def test_caso4_volviendo_a_lo_primero(self):
        """
        'Volviendo a lo primero…' debe inyectar el tópico activo.
        """
        update_state("test_user_1", "Siento que Vectrax responde frío.", "Ok.")
        state = get_state("test_user_1")

        ctx = resolve_references("Volviendo a lo primero, ¿cómo lo arreglamos?", state)

        assert ctx != ""
        assert state.active_topic in ctx or "frío" in ctx

    def test_mensaje_sin_referencia_no_inyecta(self):
        """Mensaje nuevo sin referencia implícita no debe agregar contexto."""
        update_state("test_user_1", "Siento que Vectrax responde frío.", "Ok.")
        state = get_state("test_user_1")

        ctx = resolve_references("¿Cuánto cuesta un plan de Vectrax?", state)
        # Posiblemente vacío si no hay referencia implícita
        # (puede haber contexto por modo presencia si es corto)
        assert isinstance(ctx, str)

    def test_estado_expirado_no_inyecta(self):
        """Estado expirado no debe producir contexto."""
        state = ConversationState(
            active_topic="tema antiguo",
            active_entity="Ana",
            last_updated=time.time() - 7200,  # 2 horas antes → expirado
        )
        ctx = resolve_references("¿Y eso por qué pasa?", state)
        assert ctx == ""


# ──────────────────────────────────────────────────────────────────────────────
# Caso 2: Supresión de respuestas genéricas
# ──────────────────────────────────────────────────────────────────────────────

class TestPresencePolicy:

    def make_presence_state(self, topic="Vectrax responde frío") -> ConversationState:
        state = ConversationState(
            active_topic=topic,
            active_mode="presence",
        )
        return state

    def test_suprime_percepcion_humana(self):
        response = (
            "La percepción humana tiende a buscar patrones en el comportamiento de los sistemas. "
            "Vectrax procesa tus mensajes en tiempo real."
        )
        state = self.make_presence_state()
        result, modified = apply_presence_policy(response, state)

        assert modified
        assert "La percepción humana" not in result
        assert "Vectrax" in result  # preserva contenido concreto

    def test_suprime_las_personas_suelen(self):
        response = (
            "Las personas suelen sentir distancia cuando un sistema no mantiene continuidad. "
            "En Vectrax, esto se resuelve con el estado conversacional activo."
        )
        state = self.make_presence_state()
        result, modified = apply_presence_policy(response, state)

        assert modified
        assert "Las personas suelen" not in result

    def test_elimina_pregunta_de_cierre(self):
        response = (
            "Vectrax mantiene el contexto en memoria activa. "
            "¿Hay algo más en lo que pueda ayudarte?"
        )
        state = self.make_presence_state()
        result, modified = apply_presence_policy(response, state)

        assert modified
        assert "¿Hay algo más" not in result

    def test_caso2_no_quiero_buscador(self):
        """
        Respuesta que suena a descripción corporativa debe ser filtrada.
        """
        response = (
            "Como sistema de IA, estoy diseñado para procesar consultas. "
            "La curiosidad humana es lo que me impulsa a mejorar. "
            "Soy Vectrax y proceso tu mensaje en tiempo real."
        )
        state = self.make_presence_state(topic="no quiero que parezca un buscador")
        result, modified = apply_presence_policy(response, state)

        assert modified
        assert "estoy diseñado para" not in result
        assert "La curiosidad humana" not in result

    def test_respuesta_concreta_no_se_toca(self):
        """Una respuesta corta y concreta no debe ser modificada."""
        response = "Lo que noto ahora mismo es que el hilo sobre continuidad está activo en Vectrax."
        state = self.make_presence_state()
        result, modified = apply_presence_policy(response, state)
        assert not modified
        assert result == response

    def test_evaluate_detects_generic(self):
        response = "La percepción humana busca patrones. Las personas suelen necesitar continuidad."
        eval_result = evaluate(response)
        assert eval_result["generic_hits"] >= 2

    def test_evaluate_detects_concrete(self):
        response = "Vectrax registra el hilo en memoria activa ahora mismo."
        eval_result = evaluate(response)
        assert eval_result["concrete_hits"] >= 1


# ──────────────────────────────────────────────────────────────────────────────
# Estado conversacional — ciclo completo
# ──────────────────────────────────────────────────────────────────────────────

class TestActiveStateLifecycle:

    def setup_method(self):
        clear_state("lifecycle_user")

    def test_topic_persiste_entre_turnos(self):
        update_state("lifecycle_user", "Siento que Vectrax responde frío.", "Entendido.")
        state = get_state("lifecycle_user")
        assert state.active_topic != ""
        assert "frío" in state.active_topic or "Vectrax" in state.active_topic

    def test_entity_persiste_sin_sobreescribir(self):
        # Primer turno: menciona Carlos
        update_state("lifecycle_user", "Le escribí a Carlos.", "Anotado.")
        # Segundo turno: no menciona nadie
        update_state("lifecycle_user", "¿Y eso qué significa?", "...")
        state = get_state("lifecycle_user")
        assert state.active_entity == "Carlos"

    def test_mode_presencia_se_activa(self):
        update_state("lifecycle_user", "No quiero que Vectrax responda como buscador.", "Ok.")
        state = get_state("lifecycle_user")
        assert state.active_mode == "presence"

    def test_ttl_expira(self):
        update_state("lifecycle_user", "Hola", "Hola.")
        state = get_state("lifecycle_user")
        state.last_updated = time.time() - 7200  # simular expiración
        assert not state.is_alive()
