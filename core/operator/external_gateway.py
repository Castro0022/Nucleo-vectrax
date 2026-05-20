"""
Vectrax Core — External Gateway
===================================
Gateway externo que recibe mensajes desde canales externos (web, telegram,
API, etc.) y los convierte en eventos internos del Universal Bus.

Principio arquitectónico:
  - NINGÚN acceso directo al núcleo.
  - Todo pasa por el bus: external.message_received → procesamiento → external.message_response.
  - Todos los eventos quedan registrados en el ledger.

Flujo:
  1. receive_message(user_id, content, channel) — entrada externa
  2. Emite external.message_received al bus
  3. El BusReactor procesa el evento y genera respuesta
  4. Emite external.message_response al bus
  5. Retorna respuesta al caller

Canales soportados: web, telegram, api, webhook, custom

Capa: 9 — Bus Universal de Integración
Creado: 2026-03-19
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core.operator.universal_bus import (
    BusEvent,
    Channels,
    EventPriority,
    get_universal_bus,
)
from core.operator import ledger_bridge as ledger

logger = logging.getLogger("vectrax.operator.external_gateway")


# ---------------------------------------------------------------------------
# Canales externos permitidos
# ---------------------------------------------------------------------------

ALLOWED_CHANNELS = frozenset({"web", "telegram", "api", "webhook", "custom"})
DEFAULT_CHANNEL = "web"

# Mapeo de canal externo → canal interno de Vectrax (creator/user).
# El engine.ingest() y resolver solo aceptan "creator" o "user".
_INTERNAL_CHANNEL_MAP = {
    "telegram": "user",
    "web": "user",
    "api": "user",
    "webhook": "user",
    "custom": "user",
}

# ID del creador — Mario Bravo Castro. Hardcoded + env override.
_CREATOR_UID = os.environ.get("VX_CREATOR_ID", "2030762343")


def _is_creator_uid(user_id: str) -> bool:
    """True si el user_id corresponde al creador de Vectrax."""
    norm = user_id.replace("tg:", "") if user_id else ""
    return norm == _CREATOR_UID


# === LEGACY _CREATOR_CONTEXT ELIMINADO ===
# Reemplazado por identity_anchor.build_identity_context() que inyecta:
#   [CREADOR — NO NEGOCIABLE]      identidad + relación creador/organismo
#   [REGLAS DE TONO — CREATOR MODE] anti-asistente, anti-onboarding
#   [PERCEPCIÓN OPERACIONAL]        estado, commits, módulos vivos
# Single source of truth: vectrax/identity_anchor.py + core/identity/.
# Mantener un placeholder vacío evita ImportError en código legacy.
_CREATOR_CONTEXT = ""  # deprecated: ver identity_anchor.build_identity_context


# ---------------------------------------------------------------------------
# Resultado del gateway
# ---------------------------------------------------------------------------

@dataclass
class GatewayResult:
    """Resultado de un mensaje procesado por el gateway externo."""
    event_id: str = ""
    user_id: str = ""
    channel: str = ""
    response: str = ""
    source: str = ""  # origen: "memory", "llm", "resolver", ""
    timestamp: float = 0.0
    processed: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "channel": self.channel,
            "response": self.response,
            "source": self.source,
            "timestamp": self.timestamp,
            "processed": self.processed,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Helpers STORE path — confirmación natural sin datos técnicos
# ---------------------------------------------------------------------------
import re as _re_store  # evitar colisión con re importado localmente

_EXPLICIT_SAVE_RE = _re_store.compile(
    # Patrones con palabra completa (requieren \b)
    r"(?:\b(?:guardar?|guarda|recuerdame|recuérdame|recuerda\s+que|recuerda\s+esto|"
    r"remember(?:\s+that)?|save(?:\s+this)?|anota[r]?|apunta[r]?|"
    r"keep\s+in\s+mind|don'?t\s+forget|guarda\s+esto|toma\s+nota)\b"
    # Patrones que terminan en ':' (no requieren \b al final)
    r"|nota[r]?\s*:\s*\w)",
    _re_store.I,
)


def _is_explicit_save_request(content: str) -> bool:
    """True si el usuario pidió explicitamente guardar/recordar algo."""
    return bool(_EXPLICIT_SAVE_RE.search(content))


def _natural_store_confirmation(content: str, lang: str = "es") -> str:
    """
    Genera una confirmación natural para solicitudes explícitas de guardar.
    Nunca incluye IDs, pesos, capas ni información técnica interna.
    """
    # Intentar extraer qué se guardó
    _extract = _re_store.search(
        r"(?:recuerda\s+que|recuérdame\s+que|recuerda[r]?:\s*|nota:\s*|"
        r"anota[r]?:\s*|guardar?:\s*)(.{5,120})",
        content, _re_store.I | _re_store.DOTALL,
    )
    if _extract:
        fact = _extract.group(1).strip().rstrip(".!,")[:100]
        if lang == "en":
            return f"Got it: {fact}."
        return f"Lo tengo presente: {fact}."
    # Sin extracción específica
    if lang == "en":
        return "Got it."
    return "Lo tengo."


# ---------------------------------------------------------------------------
# External Gateway
# ---------------------------------------------------------------------------

class ExternalGateway:
    """
    Gateway externo de Vectrax.

    Convierte mensajes de canales externos en eventos internos del bus.
    No accede al núcleo directamente — todo transita por el Universal Bus.
    """

    def __init__(self) -> None:
        self._bus = get_universal_bus()
        self._total_received: int = 0
        self._total_responded: int = 0
        self._total_errors: int = 0
        self._response_buffer: Dict[str, str] = {}
        self._wire_response_listener()
        logger.info("ExternalGateway initialized")

    # -- Listener para capturar respuestas ----------------------------------

    def _wire_response_listener(self) -> None:
        """Suscribe al canal EXTERNAL para capturar respuestas del reactor."""
        self._bus.subscribe(
            Channels.EXTERNAL,
            self._on_response,
            subscriber_name="external_gateway.response_listener",
            filter_type="external.message_response",
        )

    def _on_response(self, event: BusEvent) -> None:
        """Captura respuestas emitidas por el reactor."""
        correlation_id = event.metadata.get("correlation_id", "")
        if correlation_id:
            self._response_buffer[correlation_id] = event.payload.get(
                "response", ""
            )

    # -- Entrada principal --------------------------------------------------

    def receive_message(
        self,
        user_id: str,
        content: str,
        channel: str = DEFAULT_CHANNEL,
    ) -> GatewayResult:
        """
        Recibe un mensaje externo y lo procesa a través del bus.

        Args:
            user_id: Identificador del usuario externo.
            content: Contenido del mensaje.
            channel: Canal de origen (web, telegram, api, webhook, custom).

        Returns:
            GatewayResult con la respuesta del sistema.
        """
        ts = time.time()
        correlation_id = uuid.uuid4().hex[:12]

        # Validar canal
        if channel not in ALLOWED_CHANNELS:
            channel = DEFAULT_CHANNEL

        # Validar contenido
        if not content or not content.strip():
            return GatewayResult(
                event_id=correlation_id,
                user_id=user_id,
                channel=channel,
                timestamp=ts,
                processed=False,
                error="Empty message content",
            )

        self._total_received += 1

        # === ROUTER ACTIVATION LOG — abrir snapshot por mensaje ===
        # No cambia la lógica del router. Solo observa.
        _act_log = None
        try:
            from core.observability.router_activation import (
                open_message_log,
            )
            _act_log = open_message_log(
                message_id=correlation_id,
                user_id=user_id,
                channel=channel,
                content=content,
            )
        except Exception as _exc:
            logger.debug("router_activation open failed: %s", _exc)

        # ══════════════════════════════════════════════════════════════
        # CICLO OPERATIVO — observador que sigue cada paso del ciclo
        # percibir → interpretar → decidir → actuar → verificar → responder → registrar
        # ══════════════════════════════════════════════════════════════
        _cycle_obs = None
        try:
            from core.operational_cycle import CycleObserver
            _user_tier = "free"
            try:
                from core.operator.user_tiers import get_tier
                _user_tier = get_tier(user_id).value
            except Exception:
                pass
            _cycle_obs = CycleObserver(channel=channel, user_tier=_user_tier)
        except Exception:
            pass

        # ══════════════════════════════════════════════════════════════
        # STEP 0: IDENTITY ANCHOR — cargar identidad ANTES de todo
        # ══════════════════════════════════════════════════════════════
        identity_ctx = ""
        anchor = None
        try:
            from vectrax.identity_anchor import (
                get_anchored_identity,
                detect_and_lock_language,
                build_identity_context,
                guard_identity_denial,
                refresh_anchor,
            )
            anchor = get_anchored_identity(user_id)
            # Detectar y fijar idioma en la sesión
            detect_and_lock_language(user_id, content)
            # Construir contexto de identidad para inyectar en LLM
            identity_ctx = build_identity_context(anchor)
            logger.info(
                "Pipeline: identity anchored | user=%s | name=%s | lang=%s",
                user_id[:20],
                anchor.name or "(none)",
                anchor.language or "(none)",
            )
        except Exception as exc:
            logger.debug("Identity anchor unavailable: %s", exc)

        # ══════════════════════════════════════════════════════════════
        # STEP 0.1: ACTIVE CONVERSATION STATE — cargar estado del hilo
        # In-memory, TTL 30min. Sin DB, sin Gravity.
        # ══════════════════════════════════════════════════════════════
        _conv_state = None
        try:
            from core.conversation.active_state import get_state
            _conv_state = get_state(user_id)
        except Exception as _exc:
            logger.debug("active_state load failed: %s", _exc)

        # ══════════════════════════════════════════════════════════════
        # STEP 0.5: INTAKE FILTER — triage antes de todo
        # ══════════════════════════════════════════════════════════════
        _intake_t0 = time.perf_counter()
        try:
            from core.intake_filter import evaluate_intake, Action
            intake = evaluate_intake(
                content, user_id,
                user_tier="free",
                has_memory=bool(anchor and anchor.has_name),
            )
            try:
                from core.observability.router_activation import (
                    record_activate as _rec_act,
                )
                if _act_log is not None:
                    _rec_act(
                        _act_log, "intake_filter",
                        reason=f"action={intake.action.value}|reason={intake.reason}",
                        latency_ms=(time.perf_counter() - _intake_t0) * 1000.0,
                    )
            except Exception:
                pass
            logger.info(
                "Pipeline: intake | importance=%s | action=%s | reason=%s",
                intake.importance.value, intake.action.value, intake.reason,
            )

            # PERCIBIR + INTERPRETAR — huella del ciclo operativo
            if _cycle_obs:
                try:
                    from vectrax.resolver import _detect_lang
                    _op_lang = _detect_lang(content)
                except Exception:
                    _op_lang = "es"
                _cycle_obs.set_perceive(
                    intent=intake.reason,
                    lang=_op_lang,
                    words=len(content.split()),
                )
                _cycle_obs.set_interpret(
                    action=intake.action.value,
                    reason=intake.context_hint or intake.reason,
                )

            # Ignorar ruido y duplicados
            if intake.action == Action.IGNORE:
                return GatewayResult(
                    event_id=correlation_id,
                    user_id=user_id,
                    channel=channel,
                    response="",
                    source="intake_filter",
                    timestamp=ts,
                    processed=True,
                )

            # === STORE path: guardar en memoria y confirmar naturalmente ===
            # Si el usuario pidió EXPLICITAMENTE guardar → confirmar natural.
            # Si fue clasificado implícitamente → guardar en silencio y caer
            # al pipeline conversacional (el LLM responde, la memoria ya fue guardada).
            if intake.action == Action.STORE and not intake.context_hint == "identity":
                # 1) Política de ingesta ARGOS — decide si entra
                _ingest_result = None
                try:
                    from core.argos_ingesta import (
                        evaluar_ingesta, Origen, Veredicto,
                    )
                    _origen = Origen.TELEGRAM if channel == "telegram" else Origen.WEB
                    _ingest_result = evaluar_ingesta(content, origen=_origen)
                except Exception as exc:
                    logger.debug("argos_ingesta unavailable: %s", exc)

                # 2) Si la política descarta, no escribimos ni respondemos.
                if _ingest_result and _ingest_result.veredicto.value == "descartar":
                    return GatewayResult(
                        event_id=correlation_id,
                        user_id=user_id,
                        channel=channel,
                        response="",
                        source="intake_store_discarded",
                        timestamp=ts,
                        processed=True,
                    )

                # 3) Escritura real (estrella gravitacional + memoria usuario)
                _facts = 0
                _absorbed = False
                try:
                    from vectrax.engine import ingest_v2
                    ingest_v2(text=content, user_id=user_id, topic="general")
                except Exception:
                    pass
                try:
                    from vectrax.user_memory import store_memory
                    store_memory(user_id, content, "")
                except Exception:
                    pass
                try:
                    from vectrax.fact_memory import store_facts
                    _facts = store_facts(user_id, content) or 0
                except Exception:
                    pass
                try:
                    from vectrax.core_memory import absorb
                    _absorbed = bool(absorb(user_id, content, ""))
                except Exception:
                    pass

                # 4) Confirmar solo si el usuario pidió guardar EXPLICITAMENTE.
                #    Mensaje implícito (ej: "Me siento raro hoy") → NO retornar.
                #    El pipeline LLM responderá conversacionalmente con la memoria ya guardada.
                _lang = (anchor.language if anchor and anchor.language else "es")
                if _is_explicit_save_request(content):
                    _confirmation = _natural_store_confirmation(content, lang=_lang)
                    logger.info(
                        "STORE explicit | user=%s | confirmed=%r",
                        user_id[:20], _confirmation[:40],
                    )
                    return GatewayResult(
                        event_id=correlation_id,
                        user_id=user_id,
                        channel=channel,
                        response=_confirmation,
                        source="intake_store",
                        timestamp=ts,
                        processed=True,
                    )
                # Implicit store: caer al pipeline conversacional
                logger.debug(
                    "STORE implicit — falling through to LLM | user=%s",
                    user_id[:20],
                )
        except Exception as exc:
            logger.debug("Intake filter failed (passthrough): %s", exc)

        # ══════════════════════════════════════════════════════════════
        # STEP 0.6: DOMINANT VECTOR — un foco por ciclo
        # ══════════════════════════════════════════════════════════════
        try:
            from core.dominant_vector import (
                evaluate_vector, build_state_from_context, Vector,
            )
            from core.operator.user_tiers import get_tier

            _tier = "free"
            try:
                _tier = get_tier(user_id).value
            except Exception:
                pass

            dv_state = build_state_from_context(
                has_pending_message=True,
                has_new_data=True,
                user_tier=_tier,
            )
            dv = evaluate_vector(dv_state)

            # MONETIZE: si el vector dominante es monetizar, inyectar hint
            if dv.vector == Vector.MONETIZE:
                content = content  # procesar normalmente, pero al final...
                # Se podría agregar un post-hook para sugerir upgrade
        except Exception as exc:
            logger.debug("Dominant vector failed (passthrough): %s", exc)

        # 1. Registrar entrada en ledger
        ledger.record_event(
            action="external.message_received",
            category=ledger.EventCategory.PERCEPTION,
            risk_zone=ledger.RiskZone.GREEN,
            reason=f"External message from {channel}:{user_id}",
            details={
                "correlation_id": correlation_id,
                "user_id": user_id,
                "channel": channel,
                "content_length": len(content),
                "identity_name": anchor.name if anchor else "",
            },
        )

        # 1.5 OPPORTUNITY OBSERVER — capa económica
        # Detecta señales de oportunidad abierta/cerrada, persiste
        # estado por tenant (owner_id = user_id del operador), y
        # programa reactivaciones. Fire-and-forget para no bloquear
        # la latencia del mensaje principal.
        _opp_suggestions: list = []
        _opp_t0 = time.perf_counter()
        _opp_error: Optional[str] = None
        try:
            from core.opportunities import get_default_service_sync
            _opp_svc = get_default_service_sync()
            _opp_svc.observe_message_fire_and_forget(
                owner_id=str(user_id),
                content=content,
                channel=channel,
            )
            # Surfacing de reactivaciones due (Fase 2): inyectar como
            # pre-context al LLM para que el copiloto las mencione
            # naturalmente. Timeout corto — si la DB tarda, no bloquea.
            _opp_suggestions = _opp_svc.pending_suggestions_for_owner_sync(
                owner_id=str(user_id), max_items=3, timeout_s=1.5,
            )
        except Exception as exc:
            _opp_error = f"{type(exc).__name__}: {exc}"
            logger.debug(
                "opportunity observer failed (passthrough): %s", exc,
            )
        try:
            from core.observability.router_activation import (
                record_activate as _rec_act_o,
            )
            if _act_log is not None:
                _rec_act_o(
                    _act_log, "opportunity_observer",
                    reason=("error" if _opp_error else "observed"),
                    latency_ms=(time.perf_counter() - _opp_t0) * 1000.0,
                    error=_opp_error,
                    suggestions=len(_opp_suggestions),
                )
        except Exception:
            pass

        # 2. Emitir evento external.message_received al bus
        self._bus.emit(
            channel=Channels.EXTERNAL,
            event_type="external.message_received",
            source_layer=0,  # Capa externa (fuera del operador)
            priority=EventPriority.NORMAL,
            payload={
                "user_id": user_id,
                "channel": channel,
                "content": content,
                "timestamp": ts,
            },
            metadata={
                "correlation_id": correlation_id,
                "origin": "external_gateway",
            },
        )

        # 3. Intentar obtener respuesta del buffer (síncrono)
        response_text = self._response_buffer.pop(correlation_id, "")

        # 4. Cargar contexto de memoria del usuario
        memory_context = ""
        try:
            from vectrax.user_memory import get_memory_context
            memory_context = get_memory_context(user_id)
        except Exception:
            pass
        if identity_ctx:
            memory_context = identity_ctx + ("\n\n" + memory_context if memory_context else "")

        # === CREATOR CONTEXT ya inyectado por build_identity_context() ===
        # Antes había una segunda inyección hardcoded aquí (legacy
        # _CREATOR_CONTEXT). Eliminada — build_identity_context ya
        # incluye [CREADOR] + [CREATOR MODE] + [PERCEPCIÓN] cuando
        # anchor.is_creator. Una sola fuente de verdad.
        if _is_creator_uid(user_id):
            logger.info(
                "Pipeline: CREATOR detected | user=%s | identity_ctx_len=%d",
                user_id[:20], len(identity_ctx),
            )

        # 4.0.1 TEMPORAL CONTEXT — anchor Vectrax to the present moment
        try:
            from vectrax.temporal_context import (
                build_temporal_context, register_user_fact,
                filter_echo_from_context,
            )
            from core.language_gate import get_user_language
            _user_lang = get_user_language(user_id, content)
            temporal_ctx = build_temporal_context(lang=_user_lang)
            memory_context = temporal_ctx + ("\n\n" + memory_context if memory_context else "")

            # Register what the user said (for echo filtering)
            register_user_fact(user_id, content)

            # Filter echo from memory context
            if memory_context:
                lines = memory_context.split("\n")
                lines = filter_echo_from_context(user_id, lines)
                memory_context = "\n".join(lines)
        except Exception as exc:
            logger.debug("Temporal context failed (passthrough): %s", exc)

        # 4.1 Contexto de equipo — se inyecta antes del contexto personal
        try:
            from vectrax.team_memory import get_team_context
            team_ctx = get_team_context(user_id)
            if team_ctx:
                memory_context = team_ctx + ("\n\n" + memory_context if memory_context else "")
                logger.info("Pipeline: team context injected | user=%s", user_id[:20])
        except Exception as exc:
            logger.debug("Team context injection failed (passthrough): %s", exc)

        # 4.1b Detección natural de actividad de lead — Clase G (unificado)
        # El handler vive en vectrax/lead_activity_handler.py y devuelve
        # un update declarativo. Ya no se producen strings "Actualizado..."
        # aquí: el context_note se inyecta al LLM para que responda con tono.
        _lead_update_done = False
        try:
            from vectrax.lead_activity_handler import process_lead_activity
            _lead_update = process_lead_activity(user_id, content)
            if _lead_update:
                _note = _lead_update.get("context_note", "")
                if _note:
                    memory_context = (memory_context + "\n\n" + _note) if memory_context else _note
                logger.info(
                    "Pipeline: lead_update kind=%s lead=%s",
                    _lead_update.get("kind"), _lead_update.get("lead_name"),
                )
        except Exception as _le:
            logger.debug("Lead natural update failed: %s", _le)

        # 4.1c OPORTUNIDADES PENDIENTES — surfacing al LLM
        # Si la capa económica detectó reactivaciones due, las
        # inyectamos como bloque etiquetado antes del LLM. El copiloto
        # las menciona naturalmente sin reglas hardcoded.
        if _opp_suggestions:
            try:
                _block = "[OPORTUNIDADES PENDIENTES — sugiere recontactar]\n"
                _block += "\n\n".join(_opp_suggestions)
                memory_context = (
                    _block + "\n\n" + memory_context
                    if memory_context else _block
                )
                logger.info(
                    "Pipeline: opportunity suggestions injected | n=%d | user=%s",
                    len(_opp_suggestions), user_id[:20],
                )
            except Exception as exc:
                logger.debug("opp suggestions injection failed: %s", exc)

        # 4.2 FAST-PATH (moved here — before self-context and nucleus)
        # Greetings, Vectrax identity questions, confirmations — instant, no LLM.
        # CREATOR BYPASS: el creador NUNCA usa fast_path. Sus mensajes
        # son técnicos/operacionales, no merecen respuestas hardcoded.
        # Pasan al LLM con creator_context completo (reglas + percepción).
        _fast_response = ""
        if not _is_creator_uid(user_id):
            try:
                _fast_response = self._try_fast_response(content, anchor)
                if _fast_response:
                    response_text = _fast_response
                    logger.info("Pipeline: FAST-PATH | %s", content[:30])
            except Exception:
                pass
        else:
            logger.debug(
                "Pipeline: fast_path BYPASSED for creator (will go to LLM)",
            )

        # 4.2b Auto-contexto — Vectrax se observa a sí mismo
        _self_resolved = False
        if not response_text:
            try:
                from vectrax.self_context import is_self_referential, resolve_self_aware
                if is_self_referential(content):
                    from core.language_gate import get_user_language
                    _lang = get_user_language(user_id, content)
                    _self_answer = resolve_self_aware(content, lang=_lang)
                    if _self_answer:
                        response_text = _self_answer
                        _self_resolved = True
                        logger.info(
                            "Pipeline: SELF-AWARE resolved | user=%s | len=%d",
                            user_id[:20], len(_self_answer),
                        )
            except Exception as exc:
                logger.debug("Self-aware resolution failed (passthrough): %s", exc)

        # ══════════════════════════════════════════════════════════════
        # STEP 4.3: NUCLEUS RESOLVER — respond from accumulated knowledge
        # If the nucleus KNOWS the answer (close to centroid + patterns),
        # respond from its own knowledge before calling the LLM.
        # CREATOR BYPASS: el creador no usa nucleus resolver — sus
        # mensajes pasan al LLM con creator_context para respuesta
        # operacional, no resolución clásica de patterns.
        # ══════════════════════════════════════════════════════════════
        _nucleus_resolved = False
        if (not response_text and not _self_resolved and not _lead_update_done
                and not _is_creator_uid(user_id)):
            try:
                from vectrax.nucleus_resolver import resolve_from_nucleus
                _nucleus_answer = resolve_from_nucleus(content, user_id)
                if _nucleus_answer:
                    response_text = _nucleus_answer
                    _nucleus_resolved = True
                    logger.info(
                        "Pipeline: NUCLEUS resolved | user=%s | len=%d",
                        user_id[:20], len(_nucleus_answer),
                    )
            except Exception as exc:
                logger.debug("Nucleus resolver failed (passthrough): %s", exc)

        # 4.5 Política conversacional: idioma persistente + instrucciones
        try:
            from core.operator.conversational_policy import (
                build_language_context, apply_language_policy,
                detect_explicit_language_instruction,
            )
            # Detectar instrucciones explícitas primero
            explicit_lang = detect_explicit_language_instruction(content)
            if explicit_lang:
                # El usuario pidió un idioma específico — confirmar
                from core.operator.conversational_policy import SUPPORTED_LANGUAGES
                lang_name = SUPPORTED_LANGUAGES.get(explicit_lang, explicit_lang)
                apply_language_policy(user_id, content)  # persiste

            lang_ctx = build_language_context(user_id, content)
            memory_context = lang_ctx + "\n\n" + memory_context if memory_context else lang_ctx
        except Exception as exc:
            logger.debug("Language policy failed: %s", exc)

        # ══════════════════════════════════════════════════════════════
        # STEP 5: RESOLVE FROM MEMORY — prioridad absoluta
        # ══════════════════════════════════════════════════════════════
        memory_resolved = False
        _mem_t0 = time.perf_counter()
        if not response_text:
            try:
                from vectrax.user_memory import resolve_with_memory
                memory_result = resolve_with_memory(user_id, content)
                if memory_result and isinstance(memory_result, dict):
                    response_text = memory_result["text"]
                    memory_resolved = True
                    logger.info(
                        "Pipeline: SOVEREIGN memory resolve | "
                        "source=%s | len=%d",
                        memory_result.get("source", "?"),
                        len(response_text),
                    )
            except Exception as exc:
                logger.debug("Memory resolve failed: %s", exc)
        try:
            from core.observability.router_activation import (
                record_activate as _rec_m, record_skip as _skip_m,
            )
            if _act_log is not None:
                if memory_resolved:
                    _rec_m(
                        _act_log, "memory_user", reason="resolved",
                        latency_ms=(time.perf_counter() - _mem_t0) * 1000.0,
                    )
                else:
                    _skip_m(
                        _act_log, "memory_user",
                        reason="no memory match",
                    )
        except Exception:
            pass

        # DECIDIR — registrar ruta elegida
        _resolve_start = time.time()
        _final_source = (
            "memory" if memory_resolved
            else "self_aware" if _self_resolved
            else ""
        )

        if memory_resolved:
            # Memoria es soberana: enforce_final_answer sin rechazo/identidad
            try:
                from vectrax.identity_layer import enforce_final_answer
                response_text = enforce_final_answer(
                    content, response_text, memory_context,
                    memory_resolved=True,
                    user_id=user_id,
                )
            except Exception:
                pass
        elif _self_resolved:
            # Auto-aware ya resolvió — aplicar language gate y listo
            source_path = "self_aware"

        else:
            # ══════════════════════════════════════════════════════════════
            # FAST-PATH: saludos y mensajes triviales (sin LLM)
            # ══════════════════════════════════════════════════════════════
            source_path = ""  # rastrear de dónde viene la respuesta

            _fp_t0 = time.perf_counter()
            fast = self._try_fast_response(content, anchor)
            if fast:
                response_text = fast
                source_path = "fast"
            try:
                from core.observability.router_activation import (
                    record_activate as _rec_fp, record_skip as _skip_fp,
                )
                if _act_log is not None:
                    if fast:
                        _rec_fp(
                            _act_log, "fast_path", reason="matched",
                            latency_ms=(time.perf_counter() - _fp_t0) * 1000.0,
                        )
                    else:
                        _skip_fp(
                            _act_log, "fast_path",
                            reason="no fast pattern matched",
                        )
            except Exception:
                pass

            # 5.5 Pipeline cognitivo unificado (SmartRouter decide places/web/LLM)
            if not response_text:
                # POINT B: Inyectar referencia implícita antes del LLM
                # Detecta "eso", "él", "aquello", preguntas cortas de continuación
                # y añade tópico/entidad activa al contexto. NO modifica content.
                if _conv_state and _conv_state.is_alive():
                    try:
                        from core.conversation.reference_resolver import resolve_references
                        _ref_ctx = resolve_references(content, _conv_state)
                        if _ref_ctx:
                            memory_context = (
                                _ref_ctx + "\n\n" + memory_context
                                if memory_context else _ref_ctx
                            )
                            logger.info(
                                "Pipeline: reference_resolver injected | user=%s | topic=%r",
                                user_id[:20],
                                _conv_state.active_topic[:40] if _conv_state.active_topic else "",
                            )
                    except Exception as _exc:
                        logger.debug("reference_resolver failed: %s", _exc)

                logger.info(
                    "Pipeline: pipeline_v2 with extra_context=%d chars "
                    "(creator=%s)",
                    len(memory_context or ""),
                    _is_creator_uid(user_id),
                )
                _v2_t0 = time.perf_counter()
                response_text, source_path = self._resolve_via_pipeline_v2(
                    user_id, content, channel,
                    extra_context=memory_context,
                    act_log=_act_log,
                )
                try:
                    from core.observability.router_activation import (
                        record_activate as _rec_v2,
                    )
                    if _act_log is not None:
                        _rec_v2(
                            _act_log, "smart_router",
                            reason=f"strategy={source_path}",
                            latency_ms=(time.perf_counter() - _v2_t0) * 1000.0,
                            response_len=len(response_text or ""),
                        )
                except Exception:
                    pass

            # 6. Consolidar respuesta (dedup, limpieza interna)
            if response_text:
                try:
                    from core.response_consolidator import get_consolidator
                    response_text, _trace = get_consolidator().consolidate_single(
                        response_text, source_path,
                    )
                except Exception as exc:
                    logger.debug("Response consolidator failed (passthrough): %s", exc)

            # 7. Filtros según el camino — NO MEZCLAR
            if source_path == "llm":
                # Solo el camino LLM pasa por identity layer (filtros de calidad)
                response_text = self._apply_identity_layer(
                    response_text, content, memory_context,
                    user_id=user_id,
                )
            elif source_path == "places":
                # Places solo pasa por enforce_style (datos reales, no filtrar)
                try:
                    from vectrax.identity_layer import enforce_style
                    response_text = enforce_style(response_text)
                except Exception:
                    pass

            # ══════════════════════════════════════════════════════════
            # IDENTITY GUARD — siempre activo si hay nombre anclado
            # ══════════════════════════════════════════════════════════
            if anchor and anchor.has_name:
                try:
                    response_text = guard_identity_denial(
                        response_text, anchor,
                    )
                except Exception as exc:
                    logger.debug("Identity guard failed: %s", exc)

        # ACTUAR — registrar latencia y si la respuesta fue vacía
        _act_latency = (time.time() - _resolve_start) * 1000
        _is_fallback = hasattr(locals(), 'source_path') and 'fallback' in str(locals().get('source_path', ''))
        if _cycle_obs:
            _cycle_obs.set_decide(
                route=_final_source or locals().get('source_path', ''),
                strategy=locals().get('source_path', ''),
                confidence=1.0 if memory_resolved or _self_resolved else 0.7,
            )
            _cycle_obs.set_act(
                latency_ms=_act_latency,
                empty=not bool(response_text),
                fallback=_is_fallback,
            )

        # ════════════════════════════════════════════════════════════
        # STEP 8: REFRESH ANCHOR — actualizar cache si usuario dio su nombre
        # Si el anchor acaba de obtener nombre por primera vez → enviar capacidades
        # ══════════════════════════════════════════════════════════════
        if anchor and not anchor.has_name:
            try:
                refreshed = refresh_anchor(user_id)
                if refreshed.has_name:
                    logger.info(
                        "Pipeline: identity updated post-store | name=%s",
                        refreshed.name,
                    )
                    # Primera vez que Vectrax sabe el nombre → mostrar capacidades
                    _name = refreshed.name.split()[0]  # solo primer nombre
                    _lang = refreshed.language or "es"
                    if _lang == "en":
                        response_text = (
                            f"Nice to meet you, {_name}.\n\n"
                            f"I'm Vectrax.\n"
                            f"Save a follow-up with:\n"
                            f"/lead add name"
                        )
                    else:
                        response_text = (
                            f"Mucho gusto, {_name}.\n\n"
                            f"Soy Vectrax.\n"
                            f"Guarda un seguimiento con:\n"
                            f"/lead add nombre"
                        )
            except Exception:
                pass

        # ══════════════════════════════════════════════════════════════
        # ALIMENTAR LEARNING CYCLE (Ley 1: Mentalismo + Ley 7: Generación)
        # ══════════════════════════════════════════════════════════════
        try:
            from core.learning_cycle.anomaly_detector import InputEvent
            from core.learning_cycle.pipeline import get_learning_pipeline
            _lc_intent = ""
            if not memory_resolved:
                _lc_intent = source_path if source_path else "unknown"
            else:
                _lc_intent = "memory"
            get_learning_pipeline().process_event(InputEvent(
                text=content[:200],
                intent=_lc_intent,
                source=channel,
                length=len(content),
            ))
        except Exception as exc:
            logger.debug("Learning cycle feed failed: %s", exc)

        # ══════════════════════════════════════════════════════════════
        # ENFORCEMENT DE LAS 7 LEYES FUNDAMENTALES
        # ══════════════════════════════════════════════════════════════
        try:
            from core.operator.law_enforcement import enforce_all_laws
            _smart_classified = bool(
                not memory_resolved and response_text
            )
            _gov_mode = "observe"
            try:
                from core.governor import get_current_policy
                _gov_mode = get_current_policy().get("mode", "observe")
            except Exception:
                pass

            law_result = enforce_all_laws(
                input_classified=_smart_classified,
                classification=source_path if not memory_resolved else "memory",
                interaction_recorded=True,  # store_memory se llama abajo
                action_logged=True,         # ledger se registra abajo
                has_correlation_id=bool(correlation_id),
                governor_mode=_gov_mode,
            )
            if not law_result.all_passed:
                logger.info(
                    "Pipeline: %d law violations detected",
                    len(law_result.violations),
                )
        except Exception as exc:
            logger.debug("Law enforcement check failed: %s", exc)

        # 9. Emitir evento external.message_response al bus
        self._bus.emit(
            channel=Channels.EXTERNAL,
            event_type="external.message_response",
            source_layer=0,
            priority=EventPriority.NORMAL,
            payload={
                "user_id": user_id,
                "channel": channel,
                "response": response_text,
                "timestamp": time.time(),
            },
            metadata={
                "correlation_id": correlation_id,
                "origin": "external_gateway",
            },
        )

        # 10. Almacenar interacción en memoria del usuario
        try:
            from vectrax.user_memory import store_memory
            store_memory(user_id, content, response_text)
        except Exception:
            pass

        # 10.1 Feed the user's star (gravitational v2) — BACKGROUND
        #   Runs in a thread so it doesn't block response delivery.
        #   The star grows with EVERY meaningful interaction.
        import threading
        def _bg_ingest(_content, _user_id, _channel):
            try:
                from vectrax.engine import ingest_v2
                _topic = "general"
                try:
                    from core.smart_router import get_smart_router
                    _ctx = get_smart_router().detect_context(_content, _channel, _user_id)
                    _topic = _ctx.get("topic", "general")
                except Exception:
                    pass
                ingest_v2(text=_content, user_id=_user_id, topic=_topic)
            except Exception as _e:
                logger.debug("bg ingest_v2 failed: %s", _e)
        threading.Thread(
            target=_bg_ingest, args=(content, user_id, channel),
            daemon=True,
        ).start()

        # 11. Registrar respuesta en ledger
        ledger.record_event(
            action="external.message_response",
            category=ledger.EventCategory.ACTION,
            risk_zone=ledger.RiskZone.GREEN,
            reason=f"Response to {channel}:{user_id}",
            details={
                "correlation_id": correlation_id,
                "user_id": user_id,
                "channel": channel,
                "response_length": len(response_text),
                "identity_anchored": bool(anchor and anchor.has_name),
                "memory_resolved": memory_resolved,
            },
        )

        self._total_responded += 1

        # ════════════════════════════════════════════════════════════
        # RESPONSE AUDITOR — evaluar y reescribir si es genérica
        # Solo para respuestas LLM o self-aware, no para memoria/fast-path
        # ════════════════════════════════════════════════════════════
        _audit_ran = False
        _audit_passed = True
        _audit_rewritten = False
        if response_text and not memory_resolved:
            try:
                from vectrax.response_auditor import run_audit, audit_fast
                from core.language_gate import get_user_language
                _audit_lang = get_user_language(user_id, content)
                _pre_audit = response_text
                response_text = run_audit(
                    response=response_text,
                    query=content,
                    user_id=user_id,
                    lang=_audit_lang,
                )
                _audit_ran = True
                _audit_rewritten = response_text != _pre_audit
                _audit_passed = not _audit_rewritten
            except Exception as exc:
                logger.debug("Response auditor failed (passthrough): %s", exc)

        # ── POINT C: Presence Policy — filtrar genérico, comprimir abstracto ─
        if response_text and _conv_state:
            try:
                from core.conversation.presence_policy import apply_presence_policy
                response_text, _presence_modified = apply_presence_policy(
                    response_text, _conv_state, query=content,
                )
                if _presence_modified:
                    logger.info(
                        "Pipeline: presence_policy modified response | user=%s",
                        user_id[:20],
                    )
            except Exception as _exc:
                logger.debug("presence_policy failed: %s", _exc)

        # VERIFICAR — registrar resultado del auditor
        if _cycle_obs:
            _cycle_obs.set_verify(
                ran=_audit_ran,
                passed=_audit_passed,
                rewritten=_audit_rewritten,
            )

        # ════════════════════════════════════════════════════════════
        # LANGUAGE GATE FINAL — OBLIGATORIO para TODA respuesta
        # Última puerta: fuerza idioma correcto sin importar la ruta.
        # Cubre: fast, places, online, identity, llm, memory, etc.
        # ══════════════════════════════════════════════════════════════
        if response_text:
            try:
                from core.language_gate import enforce_language, get_user_language
                _user_lang = get_user_language(user_id, content)
                response_text = enforce_language(response_text, _user_lang, user_id)
            except Exception as exc:
                logger.debug("Final language gate failed (passthrough): %s", exc)

        # ── POINT D: Actualizar estado conversacional tras el turno ───────────
        if response_text and _conv_state is not None:
            try:
                from core.conversation.active_state import update_state
                update_state(user_id, content, response_text)
            except Exception as _exc:
                logger.debug("active_state update failed: %s", _exc)

        # RESPONDER + REGISTRAR — última huella del ciclo
        if _cycle_obs:
            _final_source_path = (
                "memory" if memory_resolved
                else "self_aware" if _self_resolved
                else locals().get('source_path', '')
            )
            _cycle_obs.set_respond(
                length=len(response_text) if response_text else 0,
                source=_final_source_path,
            )
            try:
                _cycle_obs.commit()
            except Exception:
                pass

        # === ROUTER ACTIVATION LOG — cerrar snapshot ===
        try:
            from core.observability.router_activation import (
                close_message_log as _close_act,
            )
            if _act_log is not None:
                _final = (
                    "memory" if memory_resolved
                    else "self_aware" if _self_resolved
                    else locals().get("source_path", "") or "none"
                )
                _close_act(
                    _act_log,
                    final_source=_final,
                    final_decision_reason=(
                        f"audit_rewritten={_audit_rewritten}"
                        if _audit_ran else "no audit"
                    ),
                    intent=locals().get("_lc_intent", "") or "",
                )
        except Exception as _exc:
            logger.debug("router_activation close failed: %s", _exc)

        return GatewayResult(
            event_id=correlation_id,
            user_id=user_id,
            channel=channel,
            response=response_text,
            source="memory" if memory_resolved else "",
            timestamp=ts,
            processed=True,
        )

    # -- Fast-path: respuestas instantáneas sin LLM -------------------------

    @staticmethod
    def _try_fast_response(content: str, anchor=None) -> str:
        """
        Respuestas instantáneas para mensajes que NO necesitan LLM.

        Cubre:
          - Saludos (hola, hi, buenas, etc.)
          - Agradecimientos (gracias, thanks)
          - Despedidas (chao, bye, adiós)
          - Confirmaciones simples (ok, sí, listo)

        Retorna respuesta o cadena vacía si no aplica.
        """
        import re as _re
        t = content.strip().lower().rstrip("!?.")

        # Nombre del usuario para personalizar
        name = anchor.name if anchor and anchor.has_name else ""

        # Saludos (with optional 'vectrax' after)
        if _re.match(
            r"^(?:hola|hi|hey|buenas?|buenos?\s+d[ií]as?|buenas\s+tardes?"
            r"|buenas\s+noches?|que tal|qu[eé] tal|saludos|hello|sup|yo)"
            r"(?:\s+vectrax)?$",
            t,
        ):
            if name:
                return f"Hola {name}. ¿Qué necesitas?"
            return "Hola. ¿Qué necesitas?"

        # Agradecimientos
        if _re.match(
            r"^(?:gracias|thanks|thank you|thx|te agradezco|muchas gracias|mil gracias)$",
            t,
        ):
            return "De nada." if not name else f"De nada, {name}."

        # Despedidas
        if _re.match(
            r"^(?:chao|bye|adi[oó]s|hasta luego|nos vemos|see you|goodbye|chau)$",
            t,
        ):
            return "Hasta luego." if not name else f"Hasta luego, {name}."

        # Confirmaciones
        if _re.match(
            r"^(?:ok|okay|s[ií]|listo|entendido|perfecto|vale|got it|sure|claro)$",
            t,
        ):
            return "Entendido."

        # Temporal questions — respond from system clock, no LLM needed
        if _re.match(
            r"^(?:qu[eé]\s+(?:hora|d[ií]a|fecha)|what\s+(?:time|day|date)"
            r"|que\s+hora|que\s+dia|quelle\s+heure)",
            t,
        ):
            from datetime import datetime as _dt
            _now = _dt.now()
            _days_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
            _months_es = ["","enero","febrero","marzo","abril","mayo","junio",
                          "julio","agosto","septiembre","octubre","noviembre","diciembre"]
            _day = _days_es[_now.weekday()]
            _month = _months_es[_now.month]
            _time_str = _now.strftime("%H:%M")
            if "hora" in t or "time" in t or "heure" in t:
                return f"Son las {_time_str}."
            return f"Hoy es {_day} {_now.day} de {_month} de {_now.year}."

        # Identidad de Vectrax — respuesta fija desde core_identity, sin LLM
        from vectrax.identity_handler import respond_if_identity
        identity_response = respond_if_identity(t, lang=_lang if "_lang" in locals() else "es")
        if identity_response:
            return identity_response

        return ""

    # -- Market data resolve ------------------------------------------------

    @staticmethod
    def _try_market_resolve(content: str) -> str:
        """
        Resolve market queries via the Vectrax market module.

        Uses market_vigilance for analysis + classification,
        and market intents for natural language formatting.
        Returns formatted response or empty string.
        """
        try:
            from intents.market_intents import detect_market_intent, handle_market_intent

            detected = detect_market_intent(content)
            if detected is None:
                # Fallback: try a direct vigilance snapshot
                from services.market_vigilance import MarketVigilance
                v = MarketVigilance()
                state = v.fetch_state("BTCUSDT")
                if state is None:
                    return "Error: no se pudo obtener datos de mercado. Fuentes no disponibles."
                result = v.evaluate(state)
                sym = state.symbol.replace("USDT", "")
                lines = [
                    f"{sym}: ${state.price:,.2f}",
                    f"24h: {state.change_24h:+.2f}% | 7d: {state.change_7d:+.2f}%",
                    f"Rango: ${state.low_24h:,.0f} – ${state.high_24h:,.0f}",
                    f"Estado: {result.signal_state} ({result.core_score}/4 condiciones)",
                ]
                if result.missing:
                    lines.append("Falta: " + "; ".join(result.missing[:2]))
                return "\n".join(lines)

            intent_name, params = detected
            result = handle_market_intent(intent_name, params)

            if result.get("success"):
                # Prefer natural language response if available
                response = result.get("response", "")
                if response:
                    return response

                # Fallback: format data directly
                data = result.get("data", {})
                if isinstance(data, dict) and "price" in data:
                    sym = data.get("symbol", "?")
                    price = data.get("price", 0)
                    change = data.get("change_pct", 0)
                    return f"{sym}: ${price:,.2f} ({change:+.2f}%)"

                # If it's a status/test response, format the data
                if intent_name in ("vx_market_status", "vx_market_test"):
                    import json
                    return json.dumps(data, indent=2, ensure_ascii=False)

                return str(data)
            else:
                error = result.get("error", "Error desconocido")
                return f"Error de mercado: {error}"

        except Exception as exc:
            logger.warning("Market resolve failed: %s", exc)
            return f"Error técnico en módulo de mercado: {exc}"

    # -- Búsqueda de lugares reales ------------------------------------------

    @staticmethod
    def _try_place_search(content: str, user_id: str = "") -> str:
        """
        Detecta intención de búsqueda de lugar y resuelve vía Google Places.

        Intercepta ANTES del LLM: si el usuario pide un lugar físico,
        negocio, tienda o servicio, devuelve datos reales de la API.
        Usa la ubicación almacenada del usuario si está disponible.
        Nunca inventa datos.

        Returns:
            Respuesta formateada con lugares reales, o cadena vacía si
            no aplica.
        """
        try:
            from vectrax.integrations.place_search import (
                detect_place_intent,
                search_places,
            )

            if not detect_place_intent(content):
                return ""

            # Obtener ubicación real del usuario desde SQLite
            user_location = None
            if user_id:
                try:
                    from vectrax.user_memory import get_user_location
                    user_location = get_user_location(user_id)
                    if user_location:
                        logger.info(
                            "Place search using stored location: lat=%.4f lng=%.4f",
                            user_location["lat"], user_location["lng"],
                        )
                except Exception:
                    pass

            result = search_places(content, user_location=user_location)

            if result.get("found") and result.get("message"):
                return result["message"]

            return result.get("message", "")
        except Exception as exc:
            logger.debug("Place search failed: %s", exc)
            return ""

    # -- Pipeline de resolución cognitiva (v2 semántico) ---------------------

    def _resolve_via_pipeline_v2(
        self,
        user_id: str,
        content: str,
        channel: str,
        extra_context: str = "",
        act_log=None,
    ) -> Tuple[str, str]:
        """
        Pipeline cognitivo unificado con clasificación semántica.

        El SmartRouter usa el SemanticClassifier para decidir automáticamente
        entre Google Places, búsqueda web, memoria, identidad o LLM.

        Args:
            extra_context: additional context (temporal, identity, language)
                           to inject into LLM prompts. PASSED EXPLICITLY —
                           NO singleton mutable state (race-free).
            act_log: opcional, router_activation.RouterActivationLog para
                     registrar el guard de follow-up si se dispara.

        Returns:
            (response_text, source_path) donde source_path es "places",
            "llm", "online", "local", "identity", etc.
        """
        # Pasamos extra_context como ARG, no como atributo de instancia.
        # Antes había race: dos requests concurrentes pisaban
        # self._current_extra_context. Ahora flow explícito por la
        # cadena de llamadas.
        answer, source_path = self._resolve_via_pipeline(
            user_id, content, channel,
            extra_context=extra_context, act_log=act_log,
        )
        if answer:
            return answer, source_path
        return "", "llm"

    # ----- CAPA 1: continuidad inmediata (follow-up guard) -----------------

    @staticmethod
    def _has_recent_active_turn(
        user_id: str, window_seconds: int = 600,
    ) -> bool:
        """
        True si existe algún turno del usuario en los últimos `window_seconds`.

        Usa la tabla `interactions` de `vault/user_memory.db`. NO carga
        memoria profunda, NO toca Gravity, NO modifica nada.
        """
        if not user_id:
            return False
        try:
            import sqlite3
            from vectrax.user_memory import _MEMORY_DB_PATH
            conn = sqlite3.connect(_MEMORY_DB_PATH)
            try:
                row = conn.execute(
                    "SELECT MAX(timestamp) FROM interactions "
                    "WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            finally:
                conn.close()
            if not row or row[0] is None:
                return False
            return (time.time() - float(row[0])) <= float(window_seconds)
        except Exception as exc:
            logger.debug("_has_recent_active_turn failed: %s", exc)
            return False

    @classmethod
    def _is_short_followup(
        cls,
        user_id: str,
        content: str,
        max_words: int = 7,
        window_seconds: int = 600,
    ) -> Tuple[bool, str]:
        """
        Decide si el mensaje actual es probablemente un follow-up
        conversacional corto dentro de un hilo activo.

        Condiciones (TODAS deben cumplirse):
          * mensaje con `<= max_words` palabras  (era 4, causaba miss en ES)
          * existe turno previo dentro de `window_seconds`  (era 300s, muy corto)

        Returns:
            (True, reason) si se considera follow-up. False en otro caso.
        """
        words = (content or "").strip().split()
        if len(words) > max_words:
            return False, f"too_long({len(words)}w > {max_words})"
        if not cls._has_recent_active_turn(user_id, window_seconds):
            return False, f"no_recent_turn_in_{window_seconds}s"
        return True, (
            f"short_msg({len(words)}w)+recent_turn(<={window_seconds}s)"
        )

    def _resolve_via_pipeline(
        self,
        user_id: str,
        content: str,
        channel: str,
        extra_context: str = "",
        act_log=None,
    ) -> Tuple[str, str]:
        """
        Pipeline cognitivo completo para mensajes externos.

        Flujo (con SmartRouter + clasificación semántica):
          1. SmartRouter clasifica intent semántico + contexto + estrategia
          2. Según estrategia:
             - RESOLVE_PLACES   → Google Places
             - RESOLVE_IDENTITY → memoria de usuario
             - RESOLVE_ONLINE   → resolve_online (multi-motor)
             - RESOLVE_LOCAL    → resolve_local (memoria)
             - RESOLVE_MEMORY   → ingest como star
             - ROUTE_SINGLE/MULTI/COGNITIVE → LLM
          3. Fallback a resolver cognitivo si SmartRouter falló
          4. Fallback final a generación LLM

        Returns:
            (answer, source_path) donde source_path refleja la estrategia
            real del SmartRouter que resolvió el mensaje.
        """
        resolve_mode = "llm"
        answer = ""
        word_count = len(content.split())

        # Mapear canal externo a canal interno (creator/user)
        # Si el user_id es el creador, usar canal 'creator' para que
        # ingest y resolver operen en el núcleo, no en la periferia.
        if _is_creator_uid(user_id):
            internal_channel = "creator"
        else:
            internal_channel = _INTERNAL_CHANNEL_MAP.get(channel, "user")

        # ══════════════════════════════════════════════════════════════
        # SMART ROUTER — clasificación semántica unificada
        # ══════════════════════════════════════════════════════════════
        smart_route = None
        try:
            from core.smart_router import get_smart_router, Strategy
            sr = get_smart_router()
            smart_route = sr.route(content, internal_channel, user_id)
            logger.info(
                "Pipeline: SmartRouter → %s (topic=%s, risk=%s, conf=%.2f)",
                smart_route.strategy.value,
                smart_route.topic,
                smart_route.risk_level.value,
                smart_route.confidence,
            )

            # ── AUTO-EXECUTE según estrategia ──────────────────────

            # Búsqueda de lugar físico (semántico → Google Places)
            if smart_route.strategy == Strategy.RESOLVE_PLACES:
                answer = self._try_place_search(content, user_id=user_id)
                resolve_mode = "places"
                if answer:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                    logger.info("Pipeline: PLACE SEARCH resolved | len=%d", len(answer))
                    return answer, resolve_mode
                # Fallback explícito a ONLINE cuando Places falla
                logger.info("Pipeline: PLACE SEARCH empty, falling back to ONLINE")
                from vectrax.resolver import resolve_online
                resolution = resolve_online(content, internal_channel, user_id)
                answer = resolution.sovereign_answer or resolution.answer or ""
                resolve_mode = "places_to_online"
                if answer:
                    sr.record_feedback(smart_route, success=True, used_fallback=True, fallback_strategy="online", word_count=word_count)
                    return answer, resolve_mode

            # Consulta de identidad (semántico → memoria de usuario)
            # Early-return obligatorio: si no hay memoria, responder
            # explícitamente sin caer al LLM ni a fallback genérico.
            if smart_route.strategy == Strategy.RESOLVE_IDENTITY:
                resolve_mode = "identity"
                identity_resolved = False
                try:
                    from vectrax.user_memory import resolve_with_memory
                    memory_result = resolve_with_memory(user_id, content)
                    if memory_result and isinstance(memory_result, dict):
                        answer = memory_result.get("text", "")
                        if answer:
                            sr.record_feedback(smart_route, success=True, word_count=word_count)
                            logger.info("Pipeline: IDENTITY resolved | len=%d", len(answer))
                            return answer, resolve_mode
                except Exception as exc:
                    logger.debug("Identity resolve in pipeline failed: %s", exc)

                # Early-return: no identity in memory → explicit seed response
                # NUNCA caer al LLM para identidad desconocida
                try:
                    from vectrax.identity_anchor import get_anchored_identity
                    anchor = get_anchored_identity(user_id)
                    if anchor and anchor.has_name:
                        # Identity exists in anchor but memory didn't resolve
                        # (edge case: memory cleared but anchor cached)
                        lang = anchor.language or "es"
                        if lang == "es":
                            answer = f"Eres {anchor.name}. Eso ya lo tengo registrado."
                        else:
                            answer = f"You are {anchor.name}. I have that on record."
                        sr.record_feedback(smart_route, success=True, word_count=word_count)
                        return answer, resolve_mode
                except Exception:
                    pass

                # No identity anywhere → explicit seed request
                lang = self._detect_user_lang(user_id, content)
                if lang == "en":
                    answer = (
                        "I don't have enough information about you yet. "
                        "If you'd like, tell me who you are or how you want me to remember you."
                    )
                else:
                    answer = (
                        "A\u00fan no tengo informaci\u00f3n suficiente sobre ti. "
                        "Si quieres, puedes decirme qui\u00e9n eres o c\u00f3mo quieres que te recuerde."
                    )
                sr.record_feedback(
                    smart_route, success=True, word_count=word_count,
                    resolution_error="identity_seed_required",
                )
                logger.info("Pipeline: IDENTITY seed required | user=%s", user_id[:20])

                # Register seed event for learning
                try:
                    ledger.record_event(
                        action="identity.seed_required",
                        category=ledger.EventCategory.PERCEPTION,
                        risk_zone=ledger.RiskZone.GREEN,
                        reason=f"Identity seed required for {user_id[:20]}",
                        details={"user_id": user_id, "event": "identity_seed_required"},
                    )
                except Exception:
                    pass

                return answer, resolve_mode

            # Consulta de mercado (crypto, stocks, análisis técnico)
            if smart_route.strategy == Strategy.RESOLVE_MARKET:
                answer = self._try_market_resolve(content)
                resolve_mode = "market"
                if answer:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                    logger.info("Pipeline: MARKET resolved | len=%d", len(answer))
                    return answer, resolve_mode
                logger.info("Pipeline: MARKET resolve empty, falling through")

            if smart_route.strategy == Strategy.RESOLVE_ONLINE:
                # ── PRESENCIA PURA — bloquear búsqueda online externa ────────
                try:
                    from core.nucleus.presencia_pura import check_and_block_online
                    if check_and_block_online():
                        return "", "presencia_pura"
                except Exception:
                    pass
                # ────────────────────────────────────────────────
                # CAPA 1 — CONTEXTUAL FOLLOW-UP GUARD
                # Si el mensaje es corto Y hay turno reciente activo,
                # NO ir a web: el usuario casi seguro está continuando el
                # hilo. Redirigir al LLM con el historial inyectado en
                # extra_context. Mantiene la lógica del SmartRouter intacta:
                # solo intercepta ANTES de tocar el resolver web.
                is_fu, fu_reason = self._is_short_followup(
                    user_id, content,
                )
                if is_fu:
                    logger.info(
                        "Pipeline: contextual_followup_guard FIRED | "
                        "reason=%s | user=%s",
                        fu_reason, user_id[:20],
                    )
                    try:
                        from core.observability.router_activation import (
                            record_activate as _rec_g,
                        )
                        if act_log is not None:
                            _rec_g(
                                act_log, "contextual_followup_guard",
                                reason=fu_reason,
                                latency_ms=0.0,
                                strategy_before="resolve_online",
                                strategy_after="llm",
                            )
                    except Exception:
                        pass
                    # Generar via LLM con el contexto disponible
                    answer = self._generate_cognitive_response(
                        content, user_id, internal_channel, "",
                        extra_context=extra_context,
                    )
                    resolve_mode = "llm"
                    if answer:
                        sr.record_feedback(
                            smart_route, success=True,
                            used_fallback=True,
                            fallback_strategy="contextual_followup_guard",
                            word_count=word_count,
                        )
                        return answer, resolve_mode
                    # Si el LLM no respondió, dejar caer al ONLINE original
                    # como última opción (no perder respuesta).

                from vectrax.resolver import resolve_online
                resolution = resolve_online(content, internal_channel, user_id)
                answer = resolution.sovereign_answer or resolution.answer or ""
                resolve_mode = "online"
                if answer:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                    logger.info("Pipeline: ONLINE resolved | engines=%s | len=%d", resolution.engines_used, len(answer))
                    return answer, resolve_mode

            elif smart_route.strategy == Strategy.RESOLVE_LOCAL:
                from vectrax.resolver import resolve_local
                resolution = resolve_local(content, internal_channel, user_id)
                answer = resolution.sovereign_answer or resolution.answer or ""
                resolve_mode = "local"
                if answer and resolution.context_stars > 0:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                    logger.info("Pipeline: LOCAL resolved | stars=%d | len=%d", resolution.context_stars, len(answer))
                    return answer, resolve_mode
                # Si local insuficiente → fallback a online (bloqueado en Presencia Pura)
                try:
                    from core.nucleus.presencia_pura import check_and_block_online
                    if check_and_block_online():
                        return "", "presencia_pura"
                except Exception:
                    pass
                from vectrax.resolver import resolve_online
                resolution = resolve_online(content, internal_channel, user_id)
                answer = resolution.sovereign_answer or resolution.answer or ""
                resolve_mode = "local_to_online"
                if answer:
                    sr.record_feedback(smart_route, success=True, used_fallback=True, fallback_strategy="online", word_count=word_count)
                    return answer, resolve_mode

            elif smart_route.strategy == Strategy.RESOLVE_MEMORY:
                # RESOLVE_MEMORY = el SmartRouter no encontró ruta clara
                # Ir directo al LLM con el contexto de memoria disponible.
                # Esto evita que se cuente como fallback cuando el LLM sí responde.
                resolve_mode = "llm"
                local_ctx = ""
                try:
                    from vectrax.resolver import resolve_local
                    local_res = resolve_local(content, internal_channel, user_id)
                    if local_res.context_stars > 0:
                        local_ctx = local_res.sovereign_answer or ""
                except Exception:
                    pass
                answer = self._generate_cognitive_response(
                    content, user_id, internal_channel, local_ctx,
                    extra_context=extra_context,
                )
                if answer:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                    return answer, resolve_mode
                # Solo llega aquí si el LLM falló completamente

            elif smart_route.strategy in (
                Strategy.ROUTE_SINGLE, Strategy.ROUTE_MULTI, Strategy.ROUTE_COGNITIVE,
            ):
                resolve_mode = "llm"
                local_ctx = ""
                try:
                    from vectrax.resolver import resolve_local
                    local_res = resolve_local(content, internal_channel, user_id)
                    if local_res.context_stars > 0:
                        local_ctx = local_res.sovereign_answer or ""
                except Exception:
                    pass

                answer = self._generate_cognitive_response(
                    content, user_id, internal_channel, local_ctx,
                    extra_context=extra_context,
                )
                if answer:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                    return answer, resolve_mode

        except Exception as exc:
            logger.warning("SmartRouter pipeline failed: %s", exc)

        # ══════════════════════════════════════════════════════════════
        # FALLBACK — resolver cognitivo clásico (si SmartRouter falló)
        # ══════════════════════════════════════════════════════════════
        if not answer:
            # ── PRESENCIA PURA — bloquear fallback externo ────────────────
            try:
                from core.nucleus.presencia_pura import check_and_block_online
                if check_and_block_online():
                    return "", "presencia_pura"
            except Exception:
                pass
            try:
                from vectrax.resolver import resolve, resolve_local, _detect_lang
                resolution = resolve(content, internal_channel, user_id)
                resolve_mode = resolution.mode
                answer = resolution.sovereign_answer or resolution.answer or ""

                logger.info(
                    "Pipeline: fallback resolver → mode=%s | answer_len=%d",
                    resolve_mode, len(answer),
                )

                if answer:
                    # Record fallback in learning system
                    if smart_route:
                        try:
                            sr.record_feedback(
                                smart_route, success=True, used_fallback=True,
                                fallback_strategy=resolve_mode, word_count=word_count,
                            )
                        except Exception:
                            pass
                    return answer, resolve_mode
            except Exception as exc:
                logger.warning("Resolver fallback failed: %s", exc)

        # ── Ingest (registrar en memoria como estrella) ────────────────────────────
        memory_context = ""
        try:
            from vectrax.engine import ingest
            star = ingest(text=content, channel=internal_channel, owner=user_id)
            logger.info(
                "Pipeline: ingest OK | star=%s layer=%s gravity=%.3f",
                star.id[:8], star.layer, star.gravity_score,
            )
        except Exception as exc:
            logger.warning("Ingest failed: %s", exc)

        # ── Buscar contexto local relevante ────────────────────────
        try:
            from vectrax.resolver import resolve_local
            local_res = resolve_local(content, internal_channel, user_id)
            if local_res.context_stars > 0 and local_res.sovereign_answer:
                memory_context = local_res.sovereign_answer
                logger.info("Pipeline: local context found | stars=%d", local_res.context_stars)
        except Exception as exc:
            logger.debug("Local context search failed: %s", exc)

        # ── Generación LLM — respuesta real del sistema ────────────
        answer = self._generate_cognitive_response(
            content, user_id, internal_channel, memory_context,
            extra_context=extra_context,
        )
        resolve_mode = "llm"

        if answer:
            logger.info("Pipeline: LLM response generated | len=%d", len(answer))
            if smart_route:
                try:
                    sr.record_feedback(smart_route, success=True, word_count=word_count)
                except Exception:
                    pass
        else:
            # Solo aquí hubo un fallo REAL — ni SmartRouter ni LLM pudieron resolver
            logger.warning("Pipeline: no response generated for %r", content[:60])
            try:
                from core.fallback_intents import record_fallback
                record_fallback(
                    intent_category=smart_route.topic if smart_route else "unknown",
                    resolve_mode=smart_route.strategy.value if smart_route else "unknown",
                    reason="llm_empty_response",
                    word_count=len(content.split()),
                )
            except Exception:
                pass
            if smart_route:
                try:
                    sr.record_feedback(
                        smart_route, success=False,
                        resolution_error="no_response", word_count=word_count,
                    )
                except Exception:
                    pass

        return answer, resolve_mode

    # -- Detectar idioma del usuario ------------------------------------------

    @staticmethod
    def _detect_user_lang(user_id: str, content: str) -> str:
        """Detect user language from anchor or content. Fail-safe: returns 'es'."""
        try:
            from core.language_gate import get_user_language
            return get_user_language(user_id, content)
        except Exception:
            return "es"

    # -- Capa de identidad — puerta final absoluta ---------------------------

    @staticmethod
    def _apply_identity_layer(
        response: str,
        user_input: str,
        memory_context: str = "",
        user_id: str = "",
    ) -> str:
        """Puerta final: enforce_final_answer decide toda salida."""
        try:
            from vectrax.identity_layer import enforce_final_answer
            return enforce_final_answer(
                user_input, response, memory_context, user_id=user_id,
            )
        except Exception as exc:
            logger.debug("Identity layer failed (passthrough): %s", exc)
            return response

    # -- Generación cognitiva vía LLM ---------------------------------------

    def _generate_cognitive_response(
        self,
        content: str,
        user_id: str,
        channel: str,
        memory_context: str = "",
        extra_context: str = "",
    ) -> str:
        """
        Genera respuesta usando el Intelligence Router (multi-IA).
        Inyecta contexto temporal + identidad + memoria en el prompt.

        Args:
          memory_context: contexto local resuelto (resolve_local/identity_ctx).
          extra_context: contexto adicional pasado explícitamente desde
            _resolve_via_pipeline_v2 (incluye identity_anchor +
            CREATOR MODE + PERCEPCIÓN OPERACIONAL). Es race-safe —
            ya no se lee de self.
        """
        # ── PRESENCIA PURA — bloqueo de tokens externos ────────────────────
        # Si el modo está activo, retornar inmediatamente sin llamar
        # al Intelligence Router ni al fallback de OpenAI.
        try:
            from core.nucleus.presencia_pura import check_and_block_llm
            if check_and_block_llm():
                return ""
        except Exception:
            pass
        # ─────────────────────────────────────────────────────
        from vectrax.identity_layer import build_prompt

        # Merge contexts: extra (pipeline-level) primero, luego memory
        # (resolver-level). El bloque CREATOR queda al inicio del prompt.
        full_context = memory_context
        if extra_context:
            full_context = extra_context + (
                "\n\n" + full_context if full_context else ""
            )

        prompt = build_prompt(content, full_context, user_id)

        # Intentar vía Intelligence Bridge (multi-modelo)
        # system_prompt=None — los providers inyectan VECTRAX_SYSTEM_PROMPT
        try:
            from vectrax.intelligence_bridge import (
                initialize,
                is_ready,
                route_single,
            )
            if not is_ready():
                init_result = initialize()
                logger.info(
                    "Intelligence Router initialized: %s",
                    init_result.get("providers_detected", []),
                )

            if is_ready():
                result = route_single(prompt)
                if result.get("success") and result.get("content"):
                    logger.info(
                        "LLM response via %s (%s) | tokens=%s",
                        result.get("provider", "?"),
                        result.get("model", "?"),
                        result.get("tokens", "?"),
                    )
                    return result["content"].strip()
                else:
                    logger.warning(
                        "LLM route_single failed: %s",
                        result.get("error", "unknown"),
                    )
        except Exception as exc:
            logger.warning("Intelligence Bridge unavailable: %s", exc)

        # Fallback: OpenAI directo si el bridge falla
        try:
            return self._generate_openai_direct(prompt)
        except Exception as exc:
            logger.warning("OpenAI direct fallback failed: %s", exc)

        return ""

    @staticmethod
    def _generate_openai_direct(prompt: str) -> str:
        """
        Fallback directo a OpenAI si el Intelligence Router no está disponible.
        Usa VECTRAX_SYSTEM_PROMPT como único system message (identidad estricta).
        """
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return ""

        from vectrax.core_identity import VECTRAX_SYSTEM_PROMPT
        import httpx
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": VECTRAX_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.7,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("OpenAI direct fallback: response generated")
        return content.strip()

    # -- Estadísticas -------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Estadísticas del gateway externo."""
        return {
            "total_received": self._total_received,
            "total_responded": self._total_responded,
            "total_errors": self._total_errors,
            "pending_responses": len(self._response_buffer),
            "allowed_channels": sorted(ALLOWED_CHANNELS),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_gateway: Optional[ExternalGateway] = None


def get_external_gateway() -> ExternalGateway:
    """Obtener la instancia singleton del gateway externo."""
    global _gateway
    if _gateway is None:
        _gateway = ExternalGateway()
    return _gateway


def reset_gateway() -> None:
    """Reset para testing. NO usar en producción."""
    global _gateway
    _gateway = None
