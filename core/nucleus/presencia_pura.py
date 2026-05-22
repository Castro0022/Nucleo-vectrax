"""
Vectrax — Presencia Pura
===========================
Modo del núcleo: cero tokens externos, máxima presencia interna.

Cuando activo:
  - Todo el ciclo cognitivo continúa: convergencia, memoria, gravedad, identidad
  - Ningún mensaje llega a OpenAI / Gemini / Claude / Intelligence Router
  - Ninguna búsqueda web externa (Tavily, Google CSE, resolve_online)
  - Solo rutas internas: memoria, identidad, fast-path, núcleo, convergencia
  - Si no hay respuesta interna → silencio (string vacío, sin ruido)

Activación: solo el creador puede activar/desactivar.
Persistencia: sobrevive reinicios mediante state_manager (cognition_state.json).

Módulos bloqueados cuando activo:
  - ExternalGateway._generate_cognitive_response() → route_single / OpenAI direct
  - ExternalGateway._resolve_via_pipeline() → RESOLVE_ONLINE, fallbacks externos
  - resolve_online() en todas sus llamadas dentro del pipeline

Módulos que siguen activos cuando activo:
  - TotalConvergenceEngine (7 fases completas)
  - resolve_with_memory / user_memory / fact_memory / core_memory
  - nucleus_resolver
  - identity_anchor / fast_path
  - ingest / ingest_v2 (gravitación sigue)
  - learning_cycle
  - ledger / audit

Creado: 2026-05-20
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from core import state_manager

logger = logging.getLogger("vectrax.nucleus.presencia_pura")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_MODE_KEY        = "nucleus_mode"
_ACTIVATED_AT    = "nucleus_mode_activated_at"
_ACTIVATED_BY    = "nucleus_mode_activated_by"

MODE_STANDARD      = "STANDARD"
MODE_PRESENCIA_PURA = "PRESENCIA_PURA"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def activate(activated_by: str = "creator") -> Dict[str, Any]:
    """
    Activar modo Presencia Pura.

    A partir de este momento, todas las llamadas a LLMs externos y búsquedas
    web son bloqueadas. El núcleo opera únicamente con rutas internas.
    """
    now = datetime.now(timezone.utc).isoformat()
    state_manager.update({
        _MODE_KEY:     MODE_PRESENCIA_PURA,
        _ACTIVATED_AT: now,
        _ACTIVATED_BY: activated_by,
    })
    logger.info("PRESENCIA_PURA activada | by=%s", activated_by)
    return {
        "mode": MODE_PRESENCIA_PURA,
        "activated_at": now,
        "activated_by": activated_by,
        "message": "Presencia Pura activa. Tokens externos bloqueados.",
    }


def deactivate(deactivated_by: str = "creator") -> Dict[str, Any]:
    """
    Desactivar modo Presencia Pura — volver a STANDARD.

    Todos los motores externos quedan disponibles nuevamente.
    """
    state_manager.update({
        _MODE_KEY:     MODE_STANDARD,
        _ACTIVATED_AT: None,
        _ACTIVATED_BY: None,
    })
    logger.info("PRESENCIA_PURA desactivada | by=%s", deactivated_by)
    return {
        "mode": MODE_STANDARD,
        "message": "Modo STANDARD restaurado. Todos los motores disponibles.",
    }


def is_active() -> bool:
    """True si el modo Presencia Pura está activo."""
    return state_manager.get(_MODE_KEY, MODE_STANDARD) == MODE_PRESENCIA_PURA


def status() -> Dict[str, Any]:
    """Estado completo del modo actual."""
    mode = state_manager.get(_MODE_KEY, MODE_STANDARD)
    active = (mode == MODE_PRESENCIA_PURA)
    return {
        "mode": mode,
        "active": active,
        "activated_at": state_manager.get(_ACTIVATED_AT),
        "activated_by": state_manager.get(_ACTIVATED_BY),
        "llm_blocked": active,
        "online_blocked": active,
        "convergence_active": True,   # siempre activo
        "memory_active": True,        # siempre activo
        "description": (
            "Núcleo activo. Tokens externos bloqueados. Solo rutas internas."
            if active else
            "Modo estándar. Todos los motores disponibles."
        ),
    }


def check_and_block_llm() -> bool:
    """
    Comprobar si debe bloquearse la generación LLM/externa.

    Diseñado para insertarse al inicio de _generate_cognitive_response().

    Returns:
        True  → bloquear (Presencia Pura activa → retornar "" inmediatamente)
        False → continuar normalmente
    """
    blocked = is_active()
    if blocked:
        logger.info("PRESENCIA_PURA: LLM bloqueado — zero tokens")
    return blocked


def check_and_block_online() -> bool:
    """
    Comprobar si debe bloquearse la búsqueda web/online externa.

    Diseñado para insertarse antes de llamadas a resolve_online().

    Returns:
        True  → bloquear (Presencia Pura activa)
        False → continuar normalmente
    """
    blocked = is_active()
    if blocked:
        logger.info("PRESENCIA_PURA: búsqueda externa bloqueada — zero tokens")
    return blocked


# ---------------------------------------------------------------------------
# Origen de emisión
# ---------------------------------------------------------------------------

class EmissionOrigin(str, Enum):
    """Clasifica el origen de una emisión del sistema."""
    NUCLEUS_CORE        = "nucleus_core"        # Núcleo central (layer 1)
    NUCLEUS_MEMORY      = "nucleus_memory"      # Memoria gravitacional (layer 3)
    NUCLEUS_IDENTITY    = "nucleus_identity"    # Identidad permanente (layer 11)
    NUCLEUS_CONVERGENCE = "nucleus_convergence" # Motor de convergencia
    NUCLEUS_PERCEPTION  = "nucleus_perception"  # Percepción (layer 2)
    NUCLEUS_HYPOTHESIS  = "nucleus_hypothesis"  # Hipótesis (layer 5)
    REFLEX_FAST_PATH    = "reflex_fast_path"    # Respuesta refleja interna
    INTERNAL_RESPONSE   = "internal_response"   # Respuesta interna procesada
    LLM_EXTERNAL        = "llm_external"        # LLM externo (OpenAI, Gemini…)
    SEARCH_EXTERNAL     = "search_external"     # Búsqueda web externa
    UNKNOWN             = "unknown"             # Motor no reconocido


# ---------------------------------------------------------------------------
# Decisión de inhibición
# ---------------------------------------------------------------------------

class InhibitionDecision(str, Enum):
    """Decisión tomada por PresenciaObserver sobre una emisión."""
    PERMIT  = "PERMIT"   # Permitir — emisión soberana y convergente
    PAUSE   = "PAUSE"    # Pausar — esperar señal más limpia
    SILENCE = "SILENCE"  # Silenciar — baja convergencia, no bloquear
    BLOCK   = "BLOCK"    # Bloquear — soberanía perdida o motor desconocido


# ---------------------------------------------------------------------------
# Señal de emisión
# ---------------------------------------------------------------------------

@dataclass
class EmissionSignal:
    """
    Señal de emisión observada. Representa la acción/output de un motor.

    Si origin es UNKNOWN, PresenciaObserver infiere el origen real
    desde engine_name y source_channel.
    La soberanía se calcula siempre desde el origin (no desde el signal).
    """
    engine_name:    str            = ""
    source_channel: str            = ""
    origin:         EmissionOrigin = EmissionOrigin.UNKNOWN
    convergence:    float          = 0.5   # 0.0=sin convergencia, 1.0=máxima
    noise:          float          = 0.0   # 0.0=señal limpia, 1.0=puro ruido
    payload_size:   int            = 0
    metadata:       Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registro de inhibición
# ---------------------------------------------------------------------------

@dataclass
class InhibitionRecord:
    """Registro inmutable de una decisión de inhibición tomada por PresenciaObserver."""
    timestamp:   str
    engine_name: str
    origin:      EmissionOrigin
    decision:    InhibitionDecision
    convergence: float
    sovereignty: float              # calculado desde origin
    noise:       float
    reason:      str
    enforced:    bool               # True en modo ACTIVE, False en modo OBSERVER


# ---------------------------------------------------------------------------
# Catálogo de motores reconocidos del sistema
# ---------------------------------------------------------------------------

_SOVEREIGN_NUCLEUS_MODULES: frozenset = frozenset({
    "core.nucleus.total_convergence",
    "core.nucleus.presencia_pura",
    "core.operator.nucleus",
    "core.operator.activation",
    "core.operator.identity",
    "core.operator.universal_bus",
    "core.operator.bus_reactor",
    "core.convergence_hook",
})

_KNOWN_INTERNAL_MODULES: frozenset = frozenset({
    # Capa 3 — Memoria
    "core.learn.gravity_engine", "core.learn.gravity_decay",
    "vectrax.gravity", "vectrax.memory",
    "cognition.memory.memory_engine", "cognition.memory.knowledge_graph",
    "cognition.memory.stores",
    "vectrax.user_memory", "vectrax.fact_memory", "vectrax.core_memory",
    # Capa 2 — Percepción
    "cognition.perception.perception_engine",
    "cognition.perception.event_classifier",
    "cognition.perception.signal_intake",
    "cognition.perception.change_detector",
    "cognition.perception.observation_buffer",
    "cognition.perception.context_builder",
    # Capa 4 — Comprensión
    "cognition.reasoning.reasoning_engine",
    "cognition.orchestrator.cognitive_loop",
    "cognition.reasoning.impact_analyzer",
    "cognition.reasoning.contradiction_detector",
    # Capa 5 — Hipótesis
    "cognition.reasoning.scenario_engine",
    "core.operator.hypothesis_engine",
    # Capa 6 — Acción
    "core.action_executor", "core.action_sandbox", "core.proposal_engine",
    # Capa 7 — Gobernador
    "core.governor", "core.risk_engine",
    "core.autonomy_policy", "core.policy_gates",
    # Capa 8 — Builder
    "core.operator.builder.code_reader",
    "core.operator.builder.pattern_extractor",
    "core.operator.builder.module_generator",
    "core.operator.builder.sandbox_validator",
    "core.operator.builder.integrator",
    # Capa 10 — Contratos
    "connectors.contracts.schema", "connectors.contracts.validator",
    "connectors.contracts.families", "core.operator.contracts_reader",
    # Capa 12 — Ledger
    "core.audit_ledger", "core.causal_ledger",
    # Operador
    "core.state_manager",
    "core.operator.load_governor", "core.operator.ledger_bridge",
    "core.operator.risk_bridge", "core.operator.memory_manifest",
    "core.operator.system_monitor", "core.operator.relevance_engine",
    "core.operator.failure_immunity", "core.operator.universe_evolution",
    "core.operator.decision_authority", "core.operator.conversational_policy",
    "core.operator.law_enforcement", "core.operator.user_tiers",
    "core.operator.approval_gate", "core.operator.perception_bridge",
    # Vectrax
    "vectrax.fast_path", "vectrax.identity_anchor", "vectrax.nucleus_resolver",
    "vectrax.engine.ingest", "vectrax.engine.ingest_v2",
    "vectrax.telegram_gateway",
    "services.core.routes.chat",
})

_INTERNAL_CHANNELS: frozenset = frozenset({
    "layer.1.nucleus", "layer.2.perception", "layer.3.memory",
    "layer.4.comprehension", "layer.5.hypothesis", "layer.6.action",
    "layer.7.governance", "layer.8.builder", "layer.9.bus",
    "layer.10.contracts", "layer.11.identity", "layer.12.ledger",
    "live.chat", "live.voice", "live.stars", "live.convergence",
    "live.watchdog", "live.universe", "operator.out",
    "broadcast", "integrity", "mode_change",
})

_EXTERNAL_CHANNELS: frozenset = frozenset({"external"})

# Score de soberanía por origen (inmutable)
_SOVEREIGNTY_SCORES: Dict[EmissionOrigin, float] = {
    EmissionOrigin.NUCLEUS_CORE:        1.00,
    EmissionOrigin.NUCLEUS_IDENTITY:    0.95,
    EmissionOrigin.NUCLEUS_MEMORY:      0.90,
    EmissionOrigin.NUCLEUS_CONVERGENCE: 0.85,
    EmissionOrigin.NUCLEUS_PERCEPTION:  0.80,
    EmissionOrigin.NUCLEUS_HYPOTHESIS:  0.75,
    EmissionOrigin.REFLEX_FAST_PATH:    0.70,
    EmissionOrigin.INTERNAL_RESPONSE:   0.65,
    EmissionOrigin.LLM_EXTERNAL:        0.20,
    EmissionOrigin.SEARCH_EXTERNAL:     0.10,
    EmissionOrigin.UNKNOWN:             0.00,
}


# ---------------------------------------------------------------------------
# Observador Inhibidor — PresenciaObserver
# ---------------------------------------------------------------------------

class PresenciaObserver:
    """
    Capa inhibidora de Presencia Pura.

    Observa los motores del sistema y evalúa cada señal emitida.
    Clasifica emisiones por origen, mide convergencia y soberanía,
    y toma una decisión: PERMIT / PAUSE / SILENCE / BLOCK.

    Modos:
      OBSERVER (default): registra decisiones, enforced=False. Nunca bloquea.
      ACTIVE:             registra decisiones, enforced=True. Listo para bloquear.

    Reglas (en orden de prioridad):
      1. Motor UNKNOWN (no reconocido)          → BLOCK
      2. Soberanía < 0.30 (muy externa)         → BLOCK
      3. Convergencia < 0.30 (baja coherencia)  → SILENCE
      4. Ruido > 0.90 y convergencia < 0.50     → BLOCK  (combinado)
      5. Ruido > 0.80                           → PAUSE
      6. Default                                → PERMIT

    Principio: PresenciaObserver no sustituye a los motores.
    Los observa. No ejecuta todo. Inhibe cuando hace falta.
    """

    OBSERVER_MODE = "OBSERVER"
    ACTIVE_MODE   = "ACTIVE"

    # Umbrales de inhibición
    THRESHOLD_SOVEREIGNTY_BLOCK   = 0.30
    THRESHOLD_CONVERGENCE_SILENCE = 0.30
    THRESHOLD_NOISE_PAUSE         = 0.80
    THRESHOLD_NOISE_BLOCK         = 0.90

    def __init__(
        self,
        *,
        mode: str = "OBSERVER",
        record_limit: int = 200,
    ) -> None:
        if mode not in (self.OBSERVER_MODE, self.ACTIVE_MODE):
            raise ValueError(f"Modo inválido: '{mode}'. Use OBSERVER o ACTIVE.")
        self._mode: str = mode
        self._records: deque = deque(maxlen=record_limit)
        self._bus: Optional[Any] = None
        self._sub_id: Optional[str] = None
        self._total_evaluated: int = 0
        self._counts: Dict[str, int] = {
            "PERMIT": 0, "PAUSE": 0, "SILENCE": 0, "BLOCK": 0,
        }
        logger.info("PresenciaObserver init | mode=%s", mode)

    # ── Evaluación ─────────────────────────────────────────────────────────

    def evaluate(self, signal: EmissionSignal) -> InhibitionRecord:
        """
        Evalúa una señal de emisión y retorna el registro de la decisión.

        - Si origin == UNKNOWN, se infiere desde engine_name + source_channel.
        - La soberanía se calcula siempre desde el origin final.
        - En modo OBSERVER: enforced=False (registra sin bloquear).
        - En modo ACTIVE:   enforced=True  (indica que la decisión se aplicaría).
        """
        self._total_evaluated += 1

        origin = (
            signal.origin
            if signal.origin != EmissionOrigin.UNKNOWN
            else self._infer_origin(signal)
        )
        sovereignty = _SOVEREIGNTY_SCORES.get(origin, 0.0)

        decision, reason = self._decide(
            signal.engine_name,
            origin,
            sovereignty,
            signal.convergence,
            signal.noise,
        )

        enforced = self._mode == self.ACTIVE_MODE

        record = InhibitionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            engine_name=signal.engine_name,
            origin=origin,
            decision=decision,
            convergence=signal.convergence,
            sovereignty=sovereignty,
            noise=signal.noise,
            reason=reason,
            enforced=enforced,
        )
        self._records.append(record)
        self._counts[decision.value] = self._counts.get(decision.value, 0) + 1

        logger.info(
            "PRESENCIA_INHIBITOR | engine=%s origin=%s decision=%s "
            "sovereignty=%.2f convergence=%.2f noise=%.2f enforced=%s | %s",
            signal.engine_name, origin.value, decision.value,
            sovereignty, signal.convergence, signal.noise, enforced, reason,
        )
        return record

    def _decide(
        self,
        engine_name: str,
        origin: EmissionOrigin,
        sovereignty: float,
        convergence: float,
        noise: float,
    ) -> tuple:
        """Aplica las reglas de inhibición. Retorna (InhibitionDecision, razón)."""

        # Regla 1: motor completamente desconocido → BLOCK
        if origin == EmissionOrigin.UNKNOWN:
            return (
                InhibitionDecision.BLOCK,
                f"Motor desconocido sin autoridad registrada: '{engine_name}'",
            )

        # Regla 2: soberanía insuficiente → BLOCK
        if sovereignty < self.THRESHOLD_SOVEREIGNTY_BLOCK:
            return (
                InhibitionDecision.BLOCK,
                f"Soberanía insuficiente: {sovereignty:.2f} < "
                f"{self.THRESHOLD_SOVEREIGNTY_BLOCK} (origen={origin.value})",
            )

        # Regla 3: baja convergencia → SILENCE
        if convergence < self.THRESHOLD_CONVERGENCE_SILENCE:
            return (
                InhibitionDecision.SILENCE,
                f"Convergencia baja: {convergence:.2f} < {self.THRESHOLD_CONVERGENCE_SILENCE}",
            )

        # Regla 4: ruido crítico + baja convergencia → BLOCK combinado
        if noise > self.THRESHOLD_NOISE_BLOCK and convergence < 0.5:
            return (
                InhibitionDecision.BLOCK,
                f"Ruido crítico ({noise:.2f}) combinado con convergencia baja ({convergence:.2f})",
            )

        # Regla 5: ruido elevado → PAUSE
        if noise > self.THRESHOLD_NOISE_PAUSE:
            return (
                InhibitionDecision.PAUSE,
                f"Ruido elevado: {noise:.2f} > {self.THRESHOLD_NOISE_PAUSE}",
            )

        # Default: emisión soberana y convergente
        return (
            InhibitionDecision.PERMIT,
            f"Emisión soberana (sovereignty={sovereignty:.2f}, convergence={convergence:.2f})",
        )

    def _infer_origin(self, signal: EmissionSignal) -> EmissionOrigin:
        """Infiere el EmissionOrigin desde engine_name y source_channel."""
        name    = signal.engine_name
        channel = signal.source_channel

        # Canal externo → fuente no soberana
        if channel in _EXTERNAL_CHANNELS or "external" in channel:
            if any(k in name for k in ("search", "tavily", "google", "bing")):
                return EmissionOrigin.SEARCH_EXTERNAL
            if any(k in name for k in ("llm", "openai", "gemini", "claude", "gpt")):
                return EmissionOrigin.LLM_EXTERNAL
            return EmissionOrigin.LLM_EXTERNAL  # externo sin tag → asume LLM

        # Módulos soberanos del núcleo
        if name in _SOVEREIGN_NUCLEUS_MODULES:
            if "convergence" in name:
                return EmissionOrigin.NUCLEUS_CONVERGENCE
            if "identity" in name:
                return EmissionOrigin.NUCLEUS_IDENTITY
            return EmissionOrigin.NUCLEUS_CORE

        # Canal de convergencia
        if channel == "live.convergence" or "convergence" in name:
            return EmissionOrigin.NUCLEUS_CONVERGENCE

        # Canal/módulo de identidad
        if channel == "layer.11.identity" or "identity" in name:
            return EmissionOrigin.NUCLEUS_IDENTITY

        # Canal/módulo de memoria
        if channel == "layer.3.memory" or any(
            k in name for k in ("memory", "gravity", "ingest", "fact_mem", "core_mem")
        ):
            return EmissionOrigin.NUCLEUS_MEMORY

        # Canal/módulo de percepción
        if channel == "layer.2.perception" or "perception" in name:
            return EmissionOrigin.NUCLEUS_PERCEPTION

        # Canal/módulo de hipótesis
        if channel == "layer.5.hypothesis" or "hypothesis" in name:
            return EmissionOrigin.NUCLEUS_HYPOTHESIS

        # Fast-path reflejo
        if any(k in name for k in ("fast_path", "fast-path", "fastpath")):
            return EmissionOrigin.REFLEX_FAST_PATH

        # Módulo interno reconocido
        if name in _KNOWN_INTERNAL_MODULES or channel in _INTERNAL_CHANNELS:
            return EmissionOrigin.INTERNAL_RESPONSE

        return EmissionOrigin.UNKNOWN

    # ── Bus — observación pasiva ────────────────────────────────────────────

    def observe(self) -> "PresenciaObserver":
        """
        Conecta el observador al UniversalBus (canal BROADCAST, solo lectura).
        Idempotente — múltiples llamadas no duplican suscripciones.
        Solo registra eventos; nunca modifica el bus ni bloquea handlers.
        """
        if self._bus is not None:
            return self
        try:
            from core.operator.universal_bus import Channels, get_universal_bus
            self._bus = get_universal_bus()
            self._sub_id = self._bus.subscribe(
                Channels.BROADCAST,
                self._on_bus_event,
                subscriber_name="presencia_pura.observer",
            )
            logger.info(
                "PresenciaObserver → BROADCAST | sub_id=%s mode=%s",
                self._sub_id, self._mode,
            )
        except Exception as exc:
            logger.warning("PresenciaObserver no pudo conectar al bus: %s", exc)
        return self

    def disconnect(self) -> None:
        """Desconecta del bus. Seguro si no está conectado."""
        if self._bus is not None and self._sub_id is not None:
            try:
                from core.operator.universal_bus import Channels
                self._bus.unsubscribe(Channels.BROADCAST, self._sub_id)
                logger.info("PresenciaObserver desconectado | sub_id=%s", self._sub_id)
            except Exception as exc:
                logger.warning("PresenciaObserver disconnect error: %s", exc)
            finally:
                self._bus = None
                self._sub_id = None

    def _on_bus_event(self, event: Any) -> None:
        """
        Handler para eventos del bus. Solo observa y registra.
        No bloquea el evento, no relanza excepciones.
        """
        try:
            payload = getattr(event, "payload", {}) or {}
            engine = (
                payload.get("engine", "")
                or payload.get("module", "")
                or payload.get("source", "")
                or f"layer.{getattr(event, 'source_layer', 0)}"
            )
            signal = EmissionSignal(
                engine_name=engine,
                source_channel=getattr(event, "channel", ""),
                payload_size=len(str(payload)),
            )
            self.evaluate(signal)
        except Exception as exc:
            logger.debug("PresenciaObserver bus handler error (non-fatal): %s", exc)

    # ── Consultas ───────────────────────────────────────────────────────────

    def get_records(self, limit: int = 50) -> List[InhibitionRecord]:
        """Retorna los últimos N registros de inhibición."""
        return list(self._records)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del observador."""
        return {
            "mode":            self._mode,
            "total_evaluated": self._total_evaluated,
            "by_decision":     dict(self._counts),
            "connected_to_bus": self._bus is not None,
            "records_stored":  len(self._records),
        }

    def set_mode(self, mode: str) -> None:
        """
        Cambia el modo operativo (OBSERVER | ACTIVE).
        OBSERVER → solo registra (enforced=False en cada decisión).
        ACTIVE   → marca enforced=True (listo para hacer cumplir inhibición).
        """
        if mode not in (self.OBSERVER_MODE, self.ACTIVE_MODE):
            raise ValueError(f"Modo inválido: '{mode}'. Use OBSERVER o ACTIVE.")
        old = self._mode
        self._mode = mode
        logger.warning("PresenciaObserver modo: %s → %s", old, mode)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_connected(self) -> bool:
        return self._bus is not None


# ---------------------------------------------------------------------------
# Singleton del observador
# ---------------------------------------------------------------------------

_observer: Optional[PresenciaObserver] = None


def get_observer() -> PresenciaObserver:
    """
    Singleton del PresenciaObserver.
    Inicia siempre en modo OBSERVER (seguro — solo registra, nunca bloquea).
    """
    global _observer
    if _observer is None:
        _observer = PresenciaObserver(mode=PresenciaObserver.OBSERVER_MODE)
    return _observer


def reset_observer() -> None:
    """Resetea el singleton. SOLO para testing. NO usar en producción."""
    global _observer
    if _observer is not None:
        _observer.disconnect()
    _observer = None
