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

        if memory_resolved:
            # Memoria es soberana: enforce_final_answer sin rechazo/identidad
            try:
                from vectrax.identity_layer import enforce_final_answer
                response_text = enforce_final_answer(
                    content, response_text, memory_context,
                    memory_resolved=True,
                )
            except Exception:
                pass
        else:
            # ══════════════════════════════════════════════════════════════
            # FAST-PATH: saludos y mensajes triviales (sin LLM)
            # ══════════════════════════════════════════════════════════════
            source_path = ""  # rastrear de dónde viene la respuesta

            fast = self._try_fast_response(content, anchor)
            if fast:
                response_text = fast
                source_path = "fast"

            # 5.5 Pipeline cognitivo unificado (SmartRouter decide places/web/LLM)
            if not response_text:
                response_text, source_path = self._resolve_via_pipeline_v2(
                    user_id, content, channel,
                )

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

        # ══════════════════════════════════════════════════════════════
        # STEP 8: REFRESH ANCHOR — actualizar cache si usuario dio su nombre
        # ══════════════════════════════════════════════════════════════
        if anchor and not anchor.has_name:
            try:
                refreshed = refresh_anchor(user_id)
                if refreshed.has_name:
                    logger.info(
                        "Pipeline: identity updated post-store | name=%s",
                        refreshed.name,
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

        # Saludos
        if _re.match(
            r"^(?:hola|hi|hey|buenas?|buenos?\s+d[ií]as?|buenas\s+tardes?"
            r"|buenas\s+noches?|que tal|qu[eé] tal|saludos|hello|sup|yo)$",
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

        # Identidad propia de Vectrax
        if _re.search(
            r"(?:c[oó]mo te llamas|cu[aá]l es tu nombre|who are you"
            r"|what(?:'?s| is) your name|qui[eé]n eres|tu nombre"
            r"|your name|tienes nombre|ten[eé]s nombre"
            r"|c[oó]mo te digo|como te digo|dime tu nombre"
            r"|tell me your name)",
            t,
        ):
            return (
                "Soy Vectrax Core. Mi creador es Mario Bravo Castro."
            )

        if _re.search(
            r"(?:qu[eé] eres|what are you|q eres)",
            t,
        ):
            return (
                "Soy Vectrax, un sistema cognitivo autónomo con memoria gravitacional. "
                "Mi creador es Mario Bravo Castro."
            )

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
    ) -> Tuple[str, str]:
        """
        Pipeline cognitivo unificado con clasificación semántica.

        El SmartRouter usa el SemanticClassifier para decidir automáticamente
        entre Google Places, búsqueda web, memoria, identidad o LLM.

        Returns:
            (response_text, source_path) donde source_path es "places",
            "llm", "online", "local", "identity", etc.
        """
        answer, source_path = self._resolve_via_pipeline(user_id, content, channel)
        if answer:
            return answer, source_path
        return "", "llm"

    def _resolve_via_pipeline(
        self,
        user_id: str,
        content: str,
        channel: str,
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
                # Si local insuficiente → fallback a online
                from vectrax.resolver import resolve_online
                resolution = resolve_online(content, internal_channel, user_id)
                answer = resolution.sovereign_answer or resolution.answer or ""
                resolve_mode = "local_to_online"
                if answer:
                    sr.record_feedback(smart_route, success=True, used_fallback=True, fallback_strategy="online", word_count=word_count)
                    return answer, resolve_mode

            elif smart_route.strategy == Strategy.RESOLVE_MEMORY:
                resolve_mode = "memory"
                # Continúa al ingest + LLM más abajo

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

        # ── Ingest (registrar en memoria como estrella) ────────────
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
            logger.warning("Pipeline: no response generated for %r", content[:60])
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
    ) -> str:
        """Puerta final: enforce_final_answer decide toda salida."""
        try:
            from vectrax.identity_layer import enforce_final_answer
            return enforce_final_answer(user_input, response, memory_context)
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
    ) -> str:
        """
        Genera respuesta usando el Intelligence Router (multi-IA).
        Usa el system prompt y prompt builder del identity_layer.
        """
        from vectrax.identity_layer import SYSTEM_PROMPT, build_prompt

        system_prompt = SYSTEM_PROMPT
        prompt = build_prompt(content, memory_context, user_id)

        # Intentar vía Intelligence Bridge (multi-modelo)
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
                result = route_single(
                    prompt,
                    system_prompt=system_prompt,
                )
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
            return self._generate_openai_direct(
                prompt, system_prompt,
            )
        except Exception as exc:
            logger.warning("OpenAI direct fallback failed: %s", exc)

        return ""

    @staticmethod
    def _generate_openai_direct(
        prompt: str,
        system_prompt: str,
    ) -> str:
        """
        Fallback directo a OpenAI si el Intelligence Router no está disponible.
        """
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return ""

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
                    {"role": "system", "content": system_prompt},
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
