"""
Vectrax Smart Router — Router Inteligente Unificado
=====================================================
Pipeline cognitivo que unifica los 4 routers de Vectrax en un solo
punto de entrada para cualquier mensaje de usuario.

Compone (sin reemplazar):
  - vectrax.resolver       → clasificación de intent (memory/local/online)
  - core.strategic_router  → selección de proveedor AI + tópico
  - core.policy_router     → evaluación de seguridad/autonomía
  - core.intelligence      → orquestación multi-modelo

Flujo:
  1. classify_intent(text)       → intent expandido
  2. detect_context(text, ...)   → tópico + riesgo + señales
  3. evaluate_policy(...)        → check de política/autonomía
  4. select_strategy(...)        → ruta óptima
  5. route(text, channel, owner) → SmartRoute completo

Privacidad:
  record_outcome() almacena SOLO métricas abstractas:
  intent_category, strategy_used, success/failure, latency.
  Nunca contenido, identidades, credenciales ni datos personales.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.smart_router")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Intent(str, Enum):
    """Clasificación expandida de intención del mensaje."""
    MEMORY      = "memory"        # nota personal, statement para ingestar
    LOCAL       = "local"         # pregunta sobre la propia memoria del usuario
    ONLINE      = "online"        # pregunta factual que requiere búsqueda web
    AI_SINGLE   = "ai_single"    # ruta explícita a modelo AI único (/ai)
    AI_MULTI    = "ai_multi"     # ruta explícita a multi-modelo (/multi)
    COMMAND     = "command"       # comando del sistema (/router, /help, etc.)
    COGNITIVE   = "cognitive"    # pregunta compleja que requiere razonamiento profundo


class RiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class Strategy(str, Enum):
    """Estrategia de resolución seleccionada."""
    RESOLVE_MEMORY  = "resolve_memory"     # ingestar como star
    RESOLVE_LOCAL   = "resolve_local"      # buscar en memoria
    RESOLVE_ONLINE  = "resolve_online"     # buscar en la web
    ROUTE_SINGLE    = "route_single"       # enviar a mejor modelo AI
    ROUTE_MULTI     = "route_multi"        # consultar múltiples modelos + síntesis
    ROUTE_COGNITIVE = "route_cognitive"    # razonamiento profundo (perception→reason)
    EXECUTE_COMMAND = "execute_command"     # ejecutar comando del sistema
    BLOCKED         = "blocked"            # bloqueado por política


class PolicyAction(str, Enum):
    AUTO_EXECUTE      = "AUTO_EXECUTE"
    REQUIERE_REVISION = "REQUIERE_REVISION"
    BLOCKED           = "BLOCKED"
    NOT_APPLICABLE    = "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# Resultado de routing
# ---------------------------------------------------------------------------

@dataclass
class SmartRoute:
    """Decisión de routing completa del Smart Router."""
    intent: Intent
    topic: str
    risk_level: RiskLevel
    strategy: Strategy
    confidence: float                       # 0.0 – 1.0
    reason: str
    providers: List[str] = field(default_factory=list)
    policy_action: PolicyAction = PolicyAction.NOT_APPLICABLE
    command_name: str = ""                  # si intent == COMMAND
    command_args: str = ""                  # args del comando
    metadata: Dict[str, Any] = field(default_factory=dict)
    routed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "topic": self.topic,
            "risk_level": self.risk_level.value,
            "strategy": self.strategy.value,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "providers": self.providers,
            "policy_action": self.policy_action.value,
            "metadata": self.metadata,
        }

    def summary(self) -> str:
        """Resumen compacto legible."""
        return (
            f"[{self.intent.value}] → {self.strategy.value} "
            f"(topic={self.topic}, risk={self.risk_level.value}, "
            f"conf={self.confidence:.2f})"
        )


# ---------------------------------------------------------------------------
# Patterns de clasificación
# ---------------------------------------------------------------------------

# Comandos explícitos
_COMMAND_PATTERN = re.compile(
    r"^/(ai|multi|router|help|status|memory|history|exit|quit|salir)\b",
    re.IGNORECASE,
)

# Indicadores de que el usuario quiere AI explícita
_AI_SINGLE_PATTERN = re.compile(r"^/ai\s+", re.IGNORECASE)
_AI_MULTI_PATTERN = re.compile(r"^/multi\s+", re.IGNORECASE)

# Patrones de pregunta — reutiliza lógica del resolver
_QUESTION_STARTS = re.compile(
    r"^(what|who|where|when|why|how|is|are|was|were|do|does|did|can|could|"
    r"should|would|will|which|tell me|explain|describe|define|compare|analyze|summarize|"
    r"qué|quién|quien|cómo|como|cuándo|cuando|dónde|donde|por qué|por que|cuál|cual|cuánto|cuanto|"
    r"explica|explícame|explicame|dime|analiza|compara|resume|resúmeme|resumeme|describe)\b",
    re.IGNORECASE,
)

_QUERY_INTENT = re.compile(
    r"(?:"
    r"\b(?:explica|explícame|explicame|dime|analiza|compara|resume|resúmeme|resumeme|describe)\b"
    r"|\b(?:quién|quien|qué|cómo|cuándo|dónde|por qué|cuál|cuánto)\b"
    r"|\b(?:what is|who is|how does|how do|how is|what are|tell me|explain)\b"
    r")",
    re.IGNORECASE,
)

_LOCAL_KEYWORDS = re.compile(
    r"(\bwhat did i\b|\bwhat i said\b|\bwhat have i\b"
    r"|\bmy (messages|history|notes|data|stars|memory|conversations?)\b"
    r"|\bqué dije\b|\bqué te dije\b|\bqué hablamos\b|\bqué recuerdas\b"
    r"|\bmi (historial|memoria|mensajes|notas|conversación|datos)\b"
    r"|\besta conversación\b|\bthis conversation\b"
    r"|\bdo you remember\b|\brecuerdas\b"
    r"|\blo que te conté\b|\blo que escribí\b"
    r"|\bmy previous\b|\bmy last\b)",
    re.IGNORECASE,
)

# Indicadores de complejidad cognitiva profunda
_COGNITIVE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:paso a paso|step.?by.?step)\b"
    r"|\b(?:analiza.*profund|análisis.*detallad|deep.*analy)"
    r"|\b(?:evalúa.*riesgo|evaluate.*risk|risk.*assess)"
    r"|\b(?:compara.*detalle|compare.*detail)"
    r"|\b(?:estrategia|strategy|plan.*detallado|detailed.*plan)"
    r"|\b(?:arquitectura|architecture|diseño.*sistema|system.*design)"
    r")",
    re.IGNORECASE,
)

# Tópicos sensibles que elevan el riesgo
_SENSITIVE_TOPICS = {"trading", "health", "financial", "security"}

# Tópicos detectables por keywords rápidos
_TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "trading":      ["btc", "bitcoin", "entrada", "stop", "trade", "short", "long",
                     "cripto", "eth", "vela", "soporte", "resistencia"],
    "code":         ["código", "python", "bug", "deploy", "api", "función", "error",
                     "script", "github", "servidor", "docker", "database", "sql"],
    "relationship": ["relación", "pareja", "amor", "conflicto", "cita",
                     "comunicación", "pelea", "confianza"],
    "health":       ["salud", "dormir", "entreno", "gym", "dieta", "peso",
                     "correr", "meditar", "estrés", "ansiedad"],
    "financial":    ["dinero", "inversión", "ahorro", "gasto", "presupuesto",
                     "money", "investment", "budget", "savings"],
    "security":     ["contraseña", "password", "token", "secret", "vault",
                     "credential", "encrypt", "seguridad"],
}

# Umbrales
_LONG_PROMPT_CHARS = 300
_COGNITIVE_MIN_WORDS = 15


# ---------------------------------------------------------------------------
# Smart Router
# ---------------------------------------------------------------------------

class SmartRouter:
    """
    Router inteligente unificado de Vectrax.

    Compone los subsistemas de routing existentes sin reemplazarlos,
    proporcionando un pipeline cognitivo de decisión única.

    Usage::

        router = SmartRouter()
        route = router.route("qué dije ayer sobre bitcoin?", "creator", "mario")
        print(route.strategy)  # → resolve_local
        print(route.topic)     # → trading
    """

    def __init__(self) -> None:
        self._metrics: List[Dict[str, Any]] = []
        self._strategic_router = None  # lazy init — necesita embedder

    # -- 1. Clasificación de Intent -----------------------------------------

    def classify_intent(self, text: str) -> Tuple[Intent, Dict[str, Any]]:
        """
        Clasifica el intent expandido del mensaje.

        Returns:
            (Intent, signals_dict) donde signals contiene las señales usadas.
        """
        stripped = text.strip()
        signals: Dict[str, Any] = {}

        # --- Comandos explícitos (/ai, /multi, /router, etc.) ---
        cmd_match = _COMMAND_PATTERN.match(stripped)
        if cmd_match:
            cmd = cmd_match.group(1).lower()
            signals["command"] = cmd

            if _AI_SINGLE_PATTERN.match(stripped):
                signals["prompt"] = stripped[4:].strip()
                return Intent.AI_SINGLE, signals

            if _AI_MULTI_PATTERN.match(stripped):
                signals["prompt"] = stripped[7:].strip()
                return Intent.AI_MULTI, signals

            return Intent.COMMAND, signals

        # --- Señales textuales ---
        has_question_mark = "?" in stripped or "¿" in stripped
        has_question_start = bool(_QUESTION_STARTS.match(stripped))
        has_query_intent = bool(_QUERY_INTENT.search(stripped))
        has_local_keywords = bool(_LOCAL_KEYWORDS.search(stripped))
        has_cognitive = bool(_COGNITIVE_PATTERN.search(stripped))
        is_long = len(stripped) > _LONG_PROMPT_CHARS
        word_count = len(stripped.split())

        signals.update({
            "question_mark": has_question_mark,
            "question_start": has_question_start,
            "query_intent": has_query_intent,
            "local_keywords": has_local_keywords,
            "cognitive_pattern": has_cognitive,
            "is_long": is_long,
            "word_count": word_count,
        })

        is_question = has_question_mark or has_question_start or has_query_intent

        # Referencia a memoria propia → LOCAL
        if has_local_keywords:
            return Intent.LOCAL, signals

        # Patrón cognitivo profundo + pregunta larga → COGNITIVE
        if has_cognitive and word_count >= _COGNITIVE_MIN_WORDS:
            return Intent.COGNITIVE, signals

        # Pregunta general → ONLINE
        if is_question:
            return Intent.ONLINE, signals

        # Statement/nota → MEMORY
        return Intent.MEMORY, signals

    # -- 2. Detección de Contexto -------------------------------------------

    def detect_context(
        self, text: str, channel: str, owner: str,
    ) -> Dict[str, Any]:
        """
        Detecta tópico, riesgo y señales contextuales.

        Returns:
            {
                "topic": str,
                "topic_confidence": float,
                "risk_level": RiskLevel,
                "sensitive": bool,
                "signals": {...},
            }
        """
        lower = text.lower()

        # Detección de tópico por keywords (fast path sin embeddings)
        topic_scores: Dict[str, int] = {}
        for topic, keywords in _TOPIC_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in lower)
            if hits > 0:
                topic_scores[topic] = hits

        if topic_scores:
            best_topic = max(topic_scores, key=topic_scores.get)
            topic_confidence = min(1.0, topic_scores[best_topic] / max(len(_TOPIC_KEYWORDS[best_topic]), 1))
        else:
            best_topic = "general"
            topic_confidence = 0.5

        # Intentar detección por strategic_router si está disponible
        strategic_topic = None
        strategic_conf = 0.0
        try:
            sr = self._get_strategic_router()
            if sr is not None:
                strategic_topic, strategic_conf = sr.detect_topic(text)
                # Preferir strategic si tiene mayor confianza
                if strategic_conf > topic_confidence:
                    best_topic = strategic_topic
                    topic_confidence = strategic_conf
        except Exception as exc:
            logger.debug("Strategic router topic detection failed: %s", exc)

        # Evaluación de riesgo
        sensitive = best_topic in _SENSITIVE_TOPICS
        is_long = len(text) > _LONG_PROMPT_CHARS
        has_cognitive = bool(_COGNITIVE_PATTERN.search(text))

        if sensitive:
            risk_level = RiskLevel.HIGH
        elif is_long and has_cognitive:
            risk_level = RiskLevel.MEDIUM
        elif is_long:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        return {
            "topic": best_topic,
            "topic_confidence": round(topic_confidence, 4),
            "risk_level": risk_level,
            "sensitive": sensitive,
            "signals": {
                "keyword_scores": topic_scores,
                "strategic_topic": strategic_topic,
                "strategic_confidence": strategic_conf,
                "channel": channel,
                "owner": owner,
            },
        }

    # -- 3. Evaluación de Política ------------------------------------------

    def evaluate_policy(
        self,
        intent: Intent,
        topic: str,
        risk_level: RiskLevel,
    ) -> PolicyAction:
        """
        Evalúa la política de autonomía para la decisión de routing.

        Reglas:
        - MEMORY/LOCAL/ONLINE → siempre AUTO_EXECUTE (no hay acción autónoma)
        - COMMAND → AUTO_EXECUTE (el usuario lo pidió explícitamente)
        - AI_SINGLE/AI_MULTI → AUTO_EXECUTE si riesgo bajo, REVISION si alto
        - COGNITIVE → REQUIERE_REVISION si tópico sensible, AUTO si no
        - Si el PolicyRouter está disponible, delegar la evaluación
        """
        # Resoluciones pasivas: siempre permitidas
        if intent in (Intent.MEMORY, Intent.LOCAL, Intent.ONLINE, Intent.COMMAND):
            return PolicyAction.AUTO_EXECUTE

        # AI routing: evaluar riesgo
        if risk_level == RiskLevel.HIGH:
            # Intentar consultar policy_router si está disponible
            try:
                from core.policy_router import get_policy_router, RoutingInput
                pr = get_policy_router()
                ri = RoutingInput(
                    global_score=0.5,
                    risk_score=0.5 if risk_level == RiskLevel.HIGH else 0.01,
                    polarity_state="NEUTRO",
                    tension=0.4 if risk_level == RiskLevel.HIGH else 0.1,
                    precedent_confidence=0.5,
                    action_type="docs",  # safe whitelist action
                    affected_paths=[],
                )
                decision = pr.evaluate(ri)
                return PolicyAction(decision.action.value)
            except Exception:
                return PolicyAction.REQUIERE_REVISION

        return PolicyAction.AUTO_EXECUTE

    # -- 4. Selección de Estrategia -----------------------------------------

    def select_strategy(
        self,
        intent: Intent,
        topic: str,
        risk_level: RiskLevel,
        policy_action: PolicyAction,
        signals: Dict[str, Any],
    ) -> Tuple[Strategy, List[str], float, str]:
        """
        Selecciona la estrategia óptima de resolución.

        Returns:
            (strategy, providers, confidence, reason)
        """
        # Bloqueado por política
        if policy_action == PolicyAction.BLOCKED:
            return (
                Strategy.BLOCKED, [], 1.0,
                f"Bloqueado por política — topic={topic}, risk={risk_level.value}",
            )

        # Comandos del sistema
        if intent == Intent.COMMAND:
            return (
                Strategy.EXECUTE_COMMAND, [], 1.0,
                f"Comando del sistema: {signals.get('command', '?')}",
            )

        # Memoria (statement/nota)
        if intent == Intent.MEMORY:
            return (
                Strategy.RESOLVE_MEMORY, [], 0.9,
                "Statement/nota → ingestar como star en memoria",
            )

        # Consulta local (memoria propia)
        if intent == Intent.LOCAL:
            return (
                Strategy.RESOLVE_LOCAL, [], 0.85,
                "Referencia a memoria propia → búsqueda local",
            )

        # AI explícita (single)
        if intent == Intent.AI_SINGLE:
            providers = self._suggest_providers(topic, single=True)
            return (
                Strategy.ROUTE_SINGLE, providers, 0.9,
                f"Ruta AI explícita (/ai) → modelo único — topic={topic}",
            )

        # AI explícita (multi)
        if intent == Intent.AI_MULTI:
            providers = self._suggest_providers(topic, single=False)
            return (
                Strategy.ROUTE_MULTI, providers, 0.9,
                f"Ruta multi-modelo explícita (/multi) → síntesis — topic={topic}",
            )

        # Cognitivo profundo
        if intent == Intent.COGNITIVE:
            providers = self._suggest_providers(topic, single=False)
            strategy = Strategy.ROUTE_COGNITIVE
            # Si hay revisión pendiente, degradar a multi
            if policy_action == PolicyAction.REQUIERE_REVISION:
                strategy = Strategy.ROUTE_MULTI
                reason = (
                    f"Cognitivo degradado a multi (revisión requerida) — "
                    f"topic={topic}, risk={risk_level.value}"
                )
            else:
                reason = (
                    f"Razonamiento profundo — "
                    f"topic={topic}, risk={risk_level.value}"
                )
            return (strategy, providers, 0.75, reason)

        # Online (pregunta factual)
        if intent == Intent.ONLINE:
            # Si es tópico sensible con riesgo alto, escalar a multi-modelo
            if topic in _SENSITIVE_TOPICS and risk_level == RiskLevel.HIGH:
                providers = self._suggest_providers(topic, single=False)
                return (
                    Strategy.ROUTE_MULTI, providers, 0.7,
                    f"Pregunta sensible escalada a multi-modelo — "
                    f"topic={topic}, risk={risk_level.value}",
                )
            return (
                Strategy.RESOLVE_ONLINE, [], 0.8,
                f"Pregunta factual → búsqueda web — topic={topic}",
            )

        # Fallback
        return (
            Strategy.RESOLVE_ONLINE, [], 0.5,
            "Fallback → búsqueda web",
        )

    # -- 5. Entry point principal -------------------------------------------

    def route(
        self,
        text: str,
        channel: str = "user",
        owner: str = "",
    ) -> SmartRoute:
        """
        Pipeline completo de routing inteligente.

        Args:
            text: Mensaje del usuario.
            channel: Canal (creator/user).
            owner: Identidad del propietario.

        Returns:
            SmartRoute con la decisión completa.
        """
        t0 = time.time()

        # Paso 1: Clasificar intent
        intent, intent_signals = self.classify_intent(text)

        # Paso 2: Detectar contexto
        context = self.detect_context(text, channel, owner)
        topic = context["topic"]
        risk_level = context["risk_level"]

        # Paso 3: Evaluar política
        policy_action = self.evaluate_policy(intent, topic, risk_level)

        # Paso 4: Seleccionar estrategia
        strategy, providers, confidence, reason = self.select_strategy(
            intent, topic, risk_level, policy_action, intent_signals,
        )

        # Construir metadata agregada (sin contenido del mensaje)
        metadata = {
            "intent_signals": {
                k: v for k, v in intent_signals.items()
                if k not in ("prompt",)  # no almacenar contenido
            },
            "topic_confidence": context["topic_confidence"],
            "sensitive": context["sensitive"],
            "routing_latency_ms": round((time.time() - t0) * 1000, 2),
        }

        # Extraer comando si aplica
        command_name = intent_signals.get("command", "")
        command_args = ""
        if intent == Intent.AI_SINGLE:
            command_name = "ai"
            command_args = intent_signals.get("prompt", "")
        elif intent == Intent.AI_MULTI:
            command_name = "multi"
            command_args = intent_signals.get("prompt", "")

        route = SmartRoute(
            intent=intent,
            topic=topic,
            risk_level=risk_level,
            strategy=strategy,
            confidence=confidence,
            reason=reason,
            providers=providers,
            policy_action=policy_action,
            command_name=command_name,
            command_args=command_args,
            metadata=metadata,
        )

        logger.info("SmartRoute: %s", route.summary())
        return route

    # -- 6. Registro de métricas (abstractas, sin contenido) -----------------

    def record_outcome(
        self,
        route: SmartRoute,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """
        Registra métricas abstractas del resultado de un routing.

        Solo almacena:
        - Categoría de intent (no el contenido)
        - Estrategia utilizada
        - Éxito/fallo
        - Latencia
        - Tópico y nivel de riesgo

        NUNCA almacena: contenido del mensaje, identidad, credenciales,
        archivos, tokens ni prompts raw.
        """
        metric = {
            "intent_category": route.intent.value,
            "strategy_used": route.strategy.value,
            "topic_category": route.topic,
            "risk_level": route.risk_level.value,
            "success": success,
            "latency_ms": round(latency_ms, 2),
            "confidence": round(route.confidence, 4),
            "timestamp": time.time(),
        }
        self._metrics.append(metric)

        # Mantener un buffer limitado en memoria (últimas 500)
        if len(self._metrics) > 500:
            self._metrics = self._metrics[-500:]

        logger.debug(
            "SmartRouter metric: intent=%s strategy=%s success=%s latency=%.0fms",
            metric["intent_category"], metric["strategy_used"],
            metric["success"], metric["latency_ms"],
        )

    # -- 7. Estadísticas ---------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Resumen estadístico de las métricas acumuladas."""
        if not self._metrics:
            return {"total_routes": 0}

        total = len(self._metrics)
        successes = sum(1 for m in self._metrics if m["success"])
        avg_latency = sum(m["latency_ms"] for m in self._metrics) / total

        # Distribución por estrategia
        strategy_dist: Dict[str, int] = {}
        for m in self._metrics:
            s = m["strategy_used"]
            strategy_dist[s] = strategy_dist.get(s, 0) + 1

        # Distribución por intent
        intent_dist: Dict[str, int] = {}
        for m in self._metrics:
            i = m["intent_category"]
            intent_dist[i] = intent_dist.get(i, 0) + 1

        # Distribución por tópico
        topic_dist: Dict[str, int] = {}
        for m in self._metrics:
            t = m["topic_category"]
            topic_dist[t] = topic_dist.get(t, 0) + 1

        return {
            "total_routes": total,
            "success_rate": round(successes / total, 4) if total > 0 else 0,
            "avg_latency_ms": round(avg_latency, 2),
            "strategy_distribution": strategy_dist,
            "intent_distribution": intent_dist,
            "topic_distribution": topic_dist,
        }

    # -- Helpers internos ---------------------------------------------------

    def _get_strategic_router(self):
        """Lazy init del StrategicRouter (necesita embedder + DB)."""
        if self._strategic_router is not None:
            return self._strategic_router
        try:
            from core.strategic_router import StrategicRouter
            from vectrax.embeddings import get_embedder
            from vectrax.db import DB_PATH
            embedder = get_embedder()
            self._strategic_router = StrategicRouter(embedder, DB_PATH)
            return self._strategic_router
        except Exception as exc:
            logger.debug("StrategicRouter not available: %s", exc)
            return None

    def _suggest_providers(
        self, topic: str, single: bool = True,
    ) -> List[str]:
        """
        Sugiere proveedores AI basándose en el tópico y el strategic router.

        Si el strategic router está disponible, usa su decisión.
        Si no, devuelve defaults.
        """
        try:
            sr = self._get_strategic_router()
            if sr is not None:
                # Obtener perfil y decisión del strategic router
                profile = sr.evaluate_history(topic)
                # Construir un prompt dummy corto para la decisión
                decision = sr.decide(topic, profile, topic)
                providers = decision.providers
                if single:
                    return providers[:1]
                return providers
        except Exception:
            pass

        # Default fallback
        defaults = ["openai", "gemini"]
        if single:
            return defaults[:1]
        return defaults

    def __repr__(self) -> str:
        n = len(self._metrics)
        return f"SmartRouter(metrics={n})"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_smart_router: Optional[SmartRouter] = None


def get_smart_router() -> SmartRouter:
    """Return the global SmartRouter singleton."""
    global _smart_router
    if _smart_router is None:
        _smart_router = SmartRouter()
    return _smart_router
