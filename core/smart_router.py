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
    MEMORY       = "memory"        # nota personal, statement para ingestar
    LOCAL        = "local"         # pregunta sobre la propia memoria del usuario
    ONLINE       = "online"        # pregunta factual que requiere búsqueda web
    AI_SINGLE    = "ai_single"    # ruta explícita a modelo AI único (/ai)
    AI_MULTI     = "ai_multi"     # ruta explícita a multi-modelo (/multi)
    COMMAND      = "command"       # comando del sistema (/router, /help, etc.)
    COGNITIVE    = "cognitive"    # pregunta compleja que requiere razonamiento profundo
    PLACE_SEARCH = "place_search" # búsqueda de lugar físico / negocio
    IDENTITY     = "identity"     # consulta de identidad (quién soy, mi nombre)
    MARKET       = "market"       # consulta de mercado (precio, análisis, tendencia)


class RiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class Strategy(str, Enum):
    """Estrategia de resolución seleccionada."""
    RESOLVE_MEMORY  = "resolve_memory"     # ingestar como star
    RESOLVE_LOCAL   = "resolve_local"      # buscar en memoria
    RESOLVE_ONLINE  = "resolve_online"     # buscar en la web
    RESOLVE_PLACES  = "resolve_places"     # buscar lugar físico vía Google Places
    RESOLVE_IDENTITY = "resolve_identity"  # responder desde identidad/memoria de usuario
    RESOLVE_MARKET   = "resolve_market"     # datos de mercado (crypto, stocks, análisis)
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
    r"|\bqu[eé] dije\b|\bqu[eé] te dije\b|\bqu[eé] hablamos\b|\bqu[eé] recuerdas\b"
    r"|\bqu[eé] te (cont[eé]|ped[ií]|pregunt[eé]|dije|mencion[eé])\b"
    r"|\bqu[eé] (sab[eé]s|sabes|tienes|guardaste) (de|sobre) m[ií]\b"
    r"|\b(sabes|recuerdas|tienes) (algo|info|datos?) (de|sobre)\b"
    r"|\bmi (historial|memoria|mensajes|notas|conversaci[oó]n|datos|nombre|perfil)\b"
    r"|\besta conversaci[oó]n\b|\bthis conversation\b"
    r"|\bdo you remember\b|\brecuerdas\b|\bacuerdate\b|\bno olvides\b"
    r"|\blo que te cont[eé]\b|\blo que escrib[ií]\b|\blo que te dije\b"
    r"|\bmy previous\b|\bmy last\b"
    r"|\b(te|le) (dije|cont[eé]|mencion[eé]|ped[ií])\b"
    r"|\bcu[aá]ndo (hablamos|te dije|te cont[eé])\b"
    r"|\b(qui[eé]n|quien) soy\b|\bc[oó]mo me llamo\b"
    r"|\bcu[aá]l es mi nombre\b|\bmi identidad\b)",
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

# Market data query patterns (detected before topic keywords)
_MARKET_PATTERN = re.compile(
    r"(?:"
    # "precio de btc", "price of bitcoin", "cotización del btc"
    r"\b(?:precio|price|cotizaci[oó]n|valor)\s+(?:de[l]?\s+)?(?:of\s+)?(?:btc|bitcoin|eth|ethereum|bnb|sol|spy|qqq|aapl|tsla|nvda)\b"
    # "btc precio", "bitcoin price", "eth precio" (reversed order)
    r"|\b(?:btc|bitcoin|eth|ethereum|bnb|sol|spy|qqq|aapl|tsla|nvda)\s+(?:precio|price|cotizaci[oó]n|valor)\b"
    # "cuánto vale btc", "cuánto cuesta bitcoin", "cuánto está eth"
    r"|\bcu[aá]nto\s+(?:vale|cuesta|est[aá])\s+(?:el\s+)?(?:btc|bitcoin|eth|ethereum|bnb|sol)\b"
    # "cómo está btc", "cómo va bitcoin"
    r"|\b(?:c[oó]mo\s+(?:est[aá]|va))\s+(?:el\s+)?(?:de[l]?\s+)?(?:btc|bitcoin|eth|ethereum|spy|qqq|aapl|tsla|nvda|el mercado)\b"
    # "tendencia de btc", "análisis btc"
    r"|\b(?:tendencia|trend|momentum|breakout|an[aá]lisis)\s+(?:de[l]?\s+)?(?:btc|bitcoin|eth|ethereum)\b"
    # "resumen del mercado"
    r"|\b(?:resumen|snapshot|estado)\s+(?:de[l]?\s+)?(?:mercado|market)\b"
    # "vx market"
    r"|\bvx\s+market\b"
    # "mercado crypto", "market stock"
    r"|\b(?:mercado|market)\s+(?:crypto|cripto|stock|bolsa)\b"
    # "bitcoin hoy", "btc now", "eth ahora", "btc status"
    r"|\b(?:bitcoin|btc|ethereum|eth)\s+(?:hoy|now|ahora|status|today)\b"
    # Standalone crypto tickers as full message ("btc", "bitcoin", "eth")
    r"|^\s*(?:btc|bitcoin|ethereum|eth|bnb|sol|ada|dot|avax|matic|link|xrp)\s*[?]?\s*$"
    # "watchlist"
    r"|\bwatchlist\b"
    # "a cuanto esta btc", "a cuanto esta el bitcoin"
    r"|\ba\s+cu[aá]nto\s+est[aá]\s+(?:el\s+)?(?:btc|bitcoin|eth|ethereum|bnb|sol)\b"
    r")",
    re.IGNORECASE,
)

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
# Mapeo de dominio → capacidades requeridas para selección de proveedor
# ---------------------------------------------------------------------------

# Importar Capability (lazy, fail-safe)
try:
    from core.routing.capabilities import (
        Capability as _Cap,
        CAPABILITY_REGISTRY as _CAP_REGISTRY,
        get_available_models as _get_available,
        refresh_availability as _refresh_avail,
        ModelProfile as _ModelProfile,
    )
    _CAP_AVAILABLE = True
except ImportError:
    _CAP_AVAILABLE = False

# Fortalezas por proveedor: capacidades que definen su dominio principal.
# Cada proveedor tiene "primary" (máxima afinidad) y "secondary".
_PROVIDER_STRENGTHS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "primary": {"coding", "tool_use", "agentic"},
        "secondary": {"structured_extraction", "reasoning"},
        "desc": "código, herramientas, agentes",
    },
    "gemini": {
        "primary": {"long_context", "vision", "summarization"},
        "secondary": {"translation", "coding"},
        "desc": "análisis largo, contexto grande, multimodal",
    },
    "anthropic": {
        "primary": {"reasoning", "safety", "planning"},
        "secondary": {"coding", "structured_extraction"},
        "desc": "razonamiento, seguridad, planificación",
    },
    "ollama": {
        "primary": set(),  # fortaleza = localidad, no capacidad
        "secondary": {"coding", "reasoning", "summarization"},
        "desc": "local, offline, privacidad",
        "locality_bonus": True,
    },
}

# Mapeo de señales del task → capacidades requeridas.
# El router usa tópico + señales de intent para inferir qué capacidades necesita.
_TASK_CAPABILITY_MAP: Dict[str, set] = {
    # -- Por tópico --
    "code":         {"coding", "tool_use", "agentic"},
    "trading":      {"reasoning", "safety", "structured_extraction"},
    "financial":    {"reasoning", "safety", "planning"},
    "security":     {"safety", "reasoning", "planning"},
    "health":       {"safety", "reasoning"},
    "relationship": {"reasoning", "summarization"},
    "general":      {"reasoning"},
}

# -- Por señales textuales (keywords en el mensaje) --
_SIGNAL_CAPABILITY_KEYWORDS: Dict[str, set] = {
    # código / herramientas / agentes → OpenAI
    "coding": {
        "código", "code", "python", "script", "función", "function", "bug",
        "api", "endpoint", "deploy", "docker", "test", "debug", "refactor",
        "github", "git", "compilar", "compile", "lint", "build",
    },
    "tool_use": {
        "herramienta", "tool", "plugin", "webhook", "integración",
        "integration", "automation", "automatiza",
    },
    "agentic": {
        "agente", "agent", "autónomo", "autonomous", "workflow", "pipeline",
        "orquesta", "orchestrate",
    },
    # análisis largo / contexto grande / multimodal → Gemini
    "long_context": {
        "documento", "document", "libro", "book", "paper", "artículo",
        "artículo largo", "long", "extenso", "completo", "pdf", "corpus",
    },
    "vision": {
        "imagen", "image", "foto", "photo", "visual", "diagrama",
        "diagram", "screenshot", "captura", "gráfico", "chart",
    },
    "summarization": {
        "resumen", "resume", "summarize", "sintetiza", "condensa",
        "extracto", "abstract", "tldr",
    },
    # razonamiento / seguridad / planificación → Anthropic
    "reasoning": {
        "razona", "reason", "lógica", "logic", "deduce", "infiere",
        "evalúa", "evaluate", "compara", "compare", "analiza profundo",
        "deep analysis", "crítico", "critical",
    },
    "safety": {
        "seguridad", "security", "riesgo", "risk", "vulnerable",
        "vulnerability", "audit", "audita", "compliance", "cumplimiento",
        "sensible", "sensitive", "confidencial", "privado",
    },
    "planning": {
        "plan", "planifica", "roadmap", "estrategia", "strategy",
        "diseño", "design", "arquitectura", "architecture",
        "paso a paso", "step by step", "fases", "phases", "hitos",
    },
}


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

    # -- Threshold de confianza semántica ------------------------------------
    _SEMANTIC_CONFIDENCE_THRESHOLD = 0.5

    # -- 1. Clasificación de Intent -----------------------------------------

    def classify_intent(self, text: str) -> Tuple[Intent, Dict[str, Any]]:
        """
        Clasifica el intent expandido del mensaje.

        Pipeline semantic-first:
          1. Comandos explícitos (/ai, /multi, etc.) → return inmediato
          2. Ejecutar SIEMPRE clasificador semántico
          3. Ejecutar SIEMPRE evaluación regex
          4. Resolver: semántico si conf >= threshold, sino regex fallback;
             si ambos coinciden se refuerza confianza.

        Fail-safe: si el clasificador semántico falla (exception),
        el regex decide solo.

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

        # --- Capa 1: Clasificación semántica (siempre se ejecuta) ---
        semantic_result = self._classify_semantic(stripped)
        semantic_intent: Optional[Intent] = None
        if semantic_result is not None:
            semantic_intent = _SEMANTIC_TO_INTENT.get(semantic_result.intent)
            # Guardar señales semánticas en signals (siempre)
            signals["semantic_intent"] = semantic_result.intent.value
            signals["semantic_confidence"] = semantic_result.confidence
            signals["semantic_frame"] = semantic_result.frame
            signals["semantic_entities"] = [
                {"type": e.type, "value": e.value}
                for e in semantic_result.entities
            ]
            signals["semantic_tools"] = semantic_result.suggested_tools
            signals["semantic_scores"] = semantic_result.scores

        # --- Capa 2: Evaluación regex (siempre se ejecuta) ---
        regex_intent, regex_signals = self._classify_regex(stripped)
        signals.update(regex_signals)

        # --- Capa 3: Decisión unificada ---
        return self._resolve_intent(
            semantic_result, semantic_intent,
            regex_intent, regex_signals,
            signals, stripped,
        )

    # -- 1a. Clasificación regex (fallback seguro) --------------------------

    def _classify_regex(self, text: str) -> Tuple[Intent, Dict[str, Any]]:
        """
        Clasificación por señales textuales (regex legacy).

        Siempre se ejecuta como segunda opinión / fallback.
        Returns:
            (Intent, regex_signals_dict)
        """
        has_question_mark = "?" in text or "¿" in text
        has_question_start = bool(_QUESTION_STARTS.match(text))
        has_query_intent = bool(_QUERY_INTENT.search(text))
        has_local_keywords = bool(_LOCAL_KEYWORDS.search(text))
        has_cognitive = bool(_COGNITIVE_PATTERN.search(text))
        is_long = len(text) > _LONG_PROMPT_CHARS
        word_count = len(text.split())

        regex_signals: Dict[str, Any] = {
            "question_mark": has_question_mark,
            "question_start": has_question_start,
            "query_intent": has_query_intent,
            "local_keywords": has_local_keywords,
            "cognitive_pattern": has_cognitive,
            "is_long": is_long,
            "word_count": word_count,
        }

        is_question = has_question_mark or has_question_start or has_query_intent
        has_market = bool(_MARKET_PATTERN.search(text))
        regex_signals["market_query"] = has_market

        if has_market:
            return Intent.MARKET, regex_signals
        if has_local_keywords:
            return Intent.LOCAL, regex_signals
        if has_cognitive and word_count >= _COGNITIVE_MIN_WORDS:
            return Intent.COGNITIVE, regex_signals
        if is_question:
            return Intent.ONLINE, regex_signals
        return Intent.MEMORY, regex_signals

    # -- 1b. Resolución unificada de intent ---------------------------------

    def _resolve_intent(
        self,
        semantic_result: Any,
        semantic_intent: Optional[Intent],
        regex_intent: Intent,
        regex_signals: Dict[str, Any],
        signals: Dict[str, Any],
        text: str,
    ) -> Tuple[Intent, Dict[str, Any]]:
        """
        Resuelve el intent final combinando semántico + regex.

        Reglas de decisión:
          1. Semántico con conf >= threshold → usar semántico
          2. Ambos coinciden → usar semántico + reforzar confianza
          3. Semántico bajo pero regex fuerte → regex como fallback
          4. Semántico None (fallo) → regex solo

        Registra alineación/conflicto en signals para aprendizaje.
        """
        threshold = self._SEMANTIC_CONFIDENCE_THRESHOLD
        sem_conf = semantic_result.confidence if semantic_result else 0.0
        word_count = regex_signals.get("word_count", len(text.split()))
        signals["word_count"] = word_count

        # --- Semántico con confianza suficiente ---
        if semantic_intent is not None and sem_conf >= threshold:
            aligned = (semantic_intent == regex_intent)

            # Excepción: si regex detecta COGNITIVE (patrón + longitud),
            # tiene prioridad sobre semántico de confianza media-baja
            # porque COGNITIVE es una señal más específica.
            if (
                not aligned
                and regex_intent == Intent.COGNITIVE
                and sem_conf < 0.7
            ):
                signals["classification_method"] = "regex_override"
                signals["regex_agrees"] = False
                signals["decision_note"] = (
                    f"regex COGNITIVE overrides semantic={semantic_intent.value} "
                    f"(sem_conf={sem_conf:.2f} < 0.7)"
                )
                logger.info(
                    "classify_intent: COGNITIVE via regex override (sem=%s conf=%.2f)",
                    semantic_intent.value, sem_conf,
                )
                return regex_intent, signals

            # Excepción: si regex detecta MARKET, siempre tiene prioridad.
            # El clasificador semántico no conoce el dominio MARKET y lo
            # clasifica como ONLINE/MEMORY. El regex _MARKET_PATTERN es
            # específico y fiable — override incondicional.
            if not aligned and regex_intent == Intent.MARKET:
                signals["classification_method"] = "regex_override"
                signals["regex_agrees"] = False
                signals["decision_note"] = (
                    f"regex MARKET overrides semantic={semantic_intent.value} "
                    f"(sem_conf={sem_conf:.2f}) — domain-specific override"
                )
                logger.info(
                    "classify_intent: MARKET via regex override (sem=%s conf=%.2f)",
                    semantic_intent.value, sem_conf,
                )
                return regex_intent, signals

            signals["classification_method"] = "semantic"
            signals["regex_agrees"] = aligned
            if aligned:
                signals["decision_note"] = "semantic+regex aligned"
            else:
                signals["decision_note"] = (
                    f"semantic={semantic_intent.value} "
                    f"regex={regex_intent.value} → semantic wins (conf={sem_conf:.2f})"
                )
            logger.info(
                "classify_intent: %s via semantic (conf=%.2f, frame=%s, regex_agrees=%s)",
                semantic_intent.value, sem_conf,
                signals.get("semantic_frame", "?"), aligned,
            )
            return semantic_intent, signals

        # --- Semántico por debajo del threshold ---
        # Caso especial: si ambas capas coinciden, la concordancia
        # independiente es señal fuerte. Promover a decisión semántica
        # con nota de alineación (semántico-first aún con conf baja).
        if semantic_intent is not None and semantic_intent == regex_intent and sem_conf > 0:
            signals["classification_method"] = "semantic_aligned"
            signals["regex_agrees"] = True
            signals["decision_note"] = (
                f"aligned below threshold: both={regex_intent.value} "
                f"(semantic_conf={sem_conf:.2f}, promoted by agreement)"
            )
            logger.info(
                "classify_intent: %s via semantic_aligned (conf=%.2f, regex agrees)",
                semantic_intent.value, sem_conf,
            )
            return semantic_intent, signals

        # Conflicto o semántico ausente → regex decide solo
        #
        # Excepción 1: semántico=MEMORY vs regex=ONLINE → semántico gana.
        # Preguntas personales con ? que el regex ve como ONLINE.
        # IDEA-6B42AC11: 78 conflictos memory→online resueltos aquí.
        if (
            semantic_intent is not None
            and semantic_intent.value == "memory"
            and regex_intent == Intent.ONLINE
            and sem_conf > 0
        ):
            signals["classification_method"] = "semantic_memory_rescue"
            signals["regex_agrees"] = False
            signals["decision_note"] = (
                f"memory rescue: semantic=memory(conf={sem_conf:.2f}) "
                f"overrides regex=online (question-as-memory pattern)"
            )
            logger.info(
                "classify_intent: memory via semantic_memory_rescue "
                "(sem_conf=%.2f, regex was online)",
                sem_conf,
            )
            return semantic_intent, signals

        # Excepción 2: semántico=PLACE_SEARCH vs regex=MEMORY → semántico gana.
        # Place queries without explicit search verbs that regex defaults to MEMORY.
        # IDEA-316106FF: 10 conflictos place_search→memory resueltos aquí.
        if (
            semantic_intent is not None
            and semantic_intent.value == "local"
            and regex_intent == Intent.MEMORY
            and sem_conf > 0.25
        ):
            signals["classification_method"] = "semantic_place_rescue"
            signals["regex_agrees"] = False
            signals["decision_note"] = (
                f"place rescue: semantic=local(conf={sem_conf:.2f}) "
                f"overrides regex=memory (place-without-verb pattern)"
            )
            logger.info(
                "classify_intent: local via semantic_place_rescue "
                "(sem_conf=%.2f, regex was memory)",
                sem_conf,
            )
            return semantic_intent, signals

        signals["classification_method"] = "regex_fallback"

        if semantic_intent is not None and sem_conf > 0:
            signals["semantic_below_threshold"] = True
            signals["semantic_would_have_been"] = semantic_intent.value
            signals["decision_note"] = (
                f"conflict: semantic={semantic_intent.value}(conf={sem_conf:.2f}) "
                f"vs regex={regex_intent.value} → regex wins (below threshold)"
            )

        logger.info(
            "classify_intent: %s via regex_fallback (words=%d, sem_conf=%.2f)",
            regex_intent.value, word_count, sem_conf,
        )
        return regex_intent, signals

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

        # Intentar detección por strategic_router SOLO como fallback cuando los
        # keywords no encontraron un tópico específico. La detección por keyword
        # es la fuente determinista de verdad para los tópicos sensibles
        # documentados (trading/financial/security/health/code); el strategic
        # router (embeddings) puede devolver ruido — sobre todo con un vault
        # vacío — y no debe sobrescribir una coincidencia clara de keywords.
        strategic_topic = None
        strategic_conf = 0.0
        if best_topic == "general":
            try:
                sr = self._get_strategic_router()
                if sr is not None:
                    strategic_topic, strategic_conf = sr.detect_topic(text)
                    if strategic_topic and strategic_conf > topic_confidence:
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

        result = {
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
        logger.info(
            "detect_context: topic=%s conf=%.2f risk=%s sensitive=%s",
            best_topic, topic_confidence, risk_level.value, sensitive,
        )
        return result

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
            logger.debug("evaluate_policy: AUTO_EXECUTE (passive intent=%s)", intent.value)
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
                logger.info("evaluate_policy: REQUIERE_REVISION (risk=HIGH, policy_router unavailable)")
                return PolicyAction.REQUIERE_REVISION

        logger.debug("evaluate_policy: AUTO_EXECUTE (intent=%s, risk=%s)", intent.value, risk_level.value)
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

        # Memoria (statement/nota) — contrato: MEMORY siempre RESOLVE_MEMORY.
        # La profundidad de memoria del usuario solo ajusta la CONFIANZA, no
        # cambia la estrategia: un statement es memoria por definición. (Antes
        # se degradaba a ROUTE_SINGLE cuando depth<5, lo que rompe el contrato
        # y, con memoria vacía, mandaba todo statement al LLM.)
        if intent == Intent.MEMORY:
            _owner = signals.get("owner", "")
            _mem_depth = self._estimate_memory_depth(_owner)
            _affinity = self._get_user_topic_affinity(_owner)
            _topic_match = topic in _affinity
            if _mem_depth >= 5:
                _conf = 0.95 if _topic_match else 0.9
            else:
                _conf = 0.7
            return (
                Strategy.RESOLVE_MEMORY, [], _conf,
                "Statement/nota → memory (depth=%d%s)" % (
                    _mem_depth,
                    ", topic_match" if _topic_match else "",
                ),
            )

        # Búsqueda de lugar físico
        if intent == Intent.PLACE_SEARCH:
            return (
                Strategy.RESOLVE_PLACES, [], 0.9,
                "Búsqueda de lugar/negocio → Google Places",
            )

        # Consulta de identidad
        if intent == Intent.IDENTITY:
            return (
                Strategy.RESOLVE_IDENTITY, [], 0.95,
                "Consulta de identidad → memoria de usuario",
            )

        # Consulta de mercado (crypto, stocks, análisis)
        if intent == Intent.MARKET:
            return (
                Strategy.RESOLVE_MARKET, [], 0.95,
                "Consulta de mercado → market_vigilance + market_router",
            )

        # Consulta local (memoria propia) — contrato: LOCAL siempre RESOLVE_LOCAL.
        # La pregunta es explícitamente sobre la memoria del usuario ("qué dije
        # sobre X"); resolverla localmente es lo correcto aunque la memoria esté
        # vacía (el resolver local maneja el caso sin resultados). La profundidad
        # solo ajusta la confianza. (Antes degradaba a RESOLVE_ONLINE con
        # depth<3, rompiendo el contrato cuando la memoria estaba vacía.)
        if intent == Intent.LOCAL:
            _owner = signals.get("owner", "")
            _mem_depth = self._estimate_memory_depth(_owner)
            _affinity = self._get_user_topic_affinity(_owner)
            _topic_match = topic in _affinity
            if _mem_depth >= 3:
                _conf = 0.92 if _topic_match else 0.85
            else:
                _conf = 0.7
            return (
                Strategy.RESOLVE_LOCAL, [], _conf,
                "Memoria propia → local (depth=%d%s)" % (
                    _mem_depth,
                    ", topic_match" if _topic_match else "",
                ),
            )

        # AI explícita (single)
        if intent == Intent.AI_SINGLE:
            providers = self._suggest_providers(topic, single=True, text=signals.get("prompt", ""))
            return (
                Strategy.ROUTE_SINGLE, providers, 0.9,
                f"Ruta AI explícita (/ai) → {providers[0] if providers else '?'} — topic={topic}",
            )

        # AI explícita (multi)
        if intent == Intent.AI_MULTI:
            providers = self._suggest_providers(topic, single=False, text=signals.get("prompt", ""))
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
                providers = self._suggest_providers(topic, single=False, text=signals.get("prompt", ""))
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
        logger.info("route: start (channel=%s, owner=%s, len=%d)", channel, owner, len(text))

        # Paso 1: Clasificar intent
        intent, intent_signals = self.classify_intent(text)

        # Paso 2: Detectar contexto
        context = self.detect_context(text, channel, owner)
        topic = context["topic"]
        risk_level = context["risk_level"]

        # Paso 3: Evaluar política
        policy_action = self.evaluate_policy(intent, topic, risk_level)

        # Paso 4: Seleccionar estrategia
        # Inject owner into signals so select_strategy can check memory depth
        intent_signals["owner"] = owner
        strategy, providers, confidence, reason = self.select_strategy(
            intent, topic, risk_level, policy_action, intent_signals,
        )
        logger.info(
            "select_strategy: %s providers=%s conf=%.2f reason=%s",
            strategy.value, providers, confidence, reason,
        )

        # Construir metadata agregada (sin contenido del mensaje)
        provider_scores = {}
        if _CAP_AVAILABLE:
            provider_scores = self._score_providers(topic, text)

        metadata = {
            "intent_signals": {
                k: v for k, v in intent_signals.items()
                if k not in ("prompt",)  # no almacenar contenido
            },
            "topic_confidence": context["topic_confidence"],
            "sensitive": context["sensitive"],
            "provider_scores": provider_scores,
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

        latency_ms = (time.time() - t0) * 1000
        logger.info("SmartRoute: %s (total=%.1fms)", route.summary(), latency_ms)

        # Emit real telemetry (persisted JSONL, no message content)
        _mem_depth = self._estimate_memory_depth(owner)
        _topic_affinity = self._get_user_topic_affinity(owner)
        _topic_match = topic in _topic_affinity
        try:
            from core.observability.router_telemetry import record_decision
            record_decision(
                user_id=owner,
                intent=intent.value,
                strategy=strategy.value,
                topic=topic,
                confidence=confidence,
                risk=risk_level.value,
                memory_depth=_mem_depth,
                latency_ms=latency_ms,
                classification_method=intent_signals.get("classification_method", ""),
                reason=reason,
                topic_match=_topic_match,
            )
        except Exception as _te:
            logger.debug("telemetry emit failed: %s", _te)

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

    def record_feedback(
        self,
        route: SmartRoute,
        success: bool,
        response_latency_ms: float = 0.0,
        used_fallback: bool = False,
        fallback_strategy: str = "",
        resolution_error: str = "",
        was_generic_response: bool = False,
        word_count: int = 0,
    ) -> None:
        """
        Registra feedback detallado para el sistema de autoaprendizaje.

        Delega a core.router_learning para persistencia en JSONL
        y evaluación post-resolución. Fail-safe: si el módulo de
        aprendizaje falla, el router sigue operando normalmente.

        NUNCA almacena contenido del mensaje, identidad ni credenciales.
        """
        # Registrar en métricas in-memory (siempre)
        self.record_outcome(route, success, response_latency_ms)

        # Registrar en ledger de aprendizaje (fail-safe)
        try:
            from core.router_learning import (
                create_feedback_from_route,
                get_ledger,
            )
            feedback = create_feedback_from_route(
                route=route,
                success=success,
                response_latency_ms=response_latency_ms,
                used_fallback=used_fallback,
                fallback_strategy=fallback_strategy,
                resolution_error=resolution_error,
                was_generic_response=was_generic_response,
                word_count=word_count,
            )
            get_ledger().append(feedback)
            logger.debug(
                "Router feedback recorded: intent=%s quality=%s",
                feedback.intent, feedback.quality_score,
            )
        except Exception as exc:
            # Fail-safe: si el aprendizaje falla, el sistema sigue
            logger.debug("Router learning feedback failed (non-critical): %s", exc)

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
        """Lazy init del StrategicRouter (necesita embedder)."""
        if self._strategic_router is not None:
            return self._strategic_router
        try:
            from core.strategic_router import StrategicRouter
            from vectrax.embeddings import get_embedder
            embedder = get_embedder()
            self._strategic_router = StrategicRouter(embedder)
            return self._strategic_router
        except Exception as exc:
            logger.debug("StrategicRouter not available: %s", exc)
            return None

    def _suggest_providers(
        self, topic: str, single: bool = True,
        text: str = "",
    ) -> List[str]:
        """
        Selecciona proveedores AI por capacidades requeridas.

        Pipeline:
          1. Detectar capacidades requeridas del task (topic + keywords)
          2. Puntuar cada proveedor disponible por match de capacidades
          3. Aplicar bonus/penalty (locality, costo, latencia)
          4. Ordenar por score descendente

        Si el capability registry no está disponible, fallback al
        strategic router o defaults.
        """
        # ── Capability-based selection ─────────────────────────
        if _CAP_AVAILABLE:
            scores = self._score_providers(topic, text)
            if scores:
                ranked = sorted(scores.items(), key=lambda x: -x[1])
                providers = [name for name, _ in ranked]
                logger.debug("_suggest_providers: capability-based scores=%s", scores)
                if single:
                    return providers[:1]
                return providers

        # ── Fallback: strategic router ────────────────────────
        try:
            sr = self._get_strategic_router()
            if sr is not None:
                profile = sr.evaluate_history(topic)
                decision = sr.decide(topic, profile, topic)
                providers = decision.providers
                logger.debug("_suggest_providers: strategic_router → %s", providers)
                if single:
                    return providers[:1]
                return providers
        except Exception:
            pass

        # ── Fallback: defaults ────────────────────────────────
        defaults = ["openai", "gemini", "anthropic"]
        logger.debug("_suggest_providers: fallback defaults → %s", defaults)
        if single:
            return defaults[:1]
        return defaults

    def _score_providers(
        self, topic: str, text: str = "",
    ) -> Dict[str, float]:
        """
        Puntúa proveedores disponibles por match de capacidades.

        Scoring:
          - capability_match: cuántas capacidades requeridas tiene el proveedor
          - strength_bonus:  +0.3 si la capacidad es fortaleza primaria del proveedor
          - locality_bonus:  +0.2 para proveedores locales si el tópico es sensible
          - cost_factor:     ligera preferencia por menor costo (-0.1 si caro)
          - latency_factor:  ligera preferencia por menor latencia (-0.05 si lento)

        Returns:
            Dict[provider_name, score] para proveedores disponibles.
        """
        if not _CAP_AVAILABLE:
            return {}

        # 1. Detectar capacidades requeridas
        required = self._detect_required_capabilities(topic, text)

        if not required:
            required = {"reasoning", "summarization"}

        # 2. Obtener modelos disponibles
        _refresh_avail()
        available = _get_available()
        if not available:
            return {}

        # 3. Agrupar por proveedor (mejor modelo por proveedor)
        provider_best: Dict[str, _ModelProfile] = {}
        for profile in available.values():
            prov = profile.provider
            if prov not in provider_best or profile.priority < provider_best[prov].priority:
                provider_best[prov] = profile

        # 4. Puntuar cada proveedor
        scores: Dict[str, float] = {}
        sensitive_topic = topic in _SENSITIVE_TOPICS

        for prov_name, profile in provider_best.items():
            score = 0.0
            prov_caps = {c.value for c in profile.capabilities}
            strengths = _PROVIDER_STRENGTHS.get(prov_name, {})
            primary = strengths.get("primary", set())
            secondary = strengths.get("secondary", set())

            # Capability match
            for cap in required:
                if cap in prov_caps:
                    if cap in primary:
                        score += 1.5   # fortaleza primaria
                    elif cap in secondary:
                        score += 0.6   # fortaleza secundaria
                    else:
                        score += 0.3   # tiene la capacidad pero no es su fuerte

            # Locality bonus (privacidad / seguridad)
            if profile.is_local:
                if sensitive_topic:
                    score += 0.5   # bonus fuerte para tópicos sensibles
                else:
                    score += 0.1   # bonus leve por privacidad

            # Cost factor (menor costo = leve bonus)
            if profile.cost_per_1k_tokens == 0.0:
                score += 0.1   # gratis = bonus
            elif profile.cost_per_1k_tokens > 0.003:
                score -= 0.1   # caro = penalización leve

            # Latency factor
            if profile.avg_latency_ms < 1200:
                score += 0.05
            elif profile.avg_latency_ms > 2200:
                score -= 0.05

            scores[prov_name] = round(score, 4)

        return scores

    def _detect_required_capabilities(
        self, topic: str, text: str = "",
    ) -> set:
        """
        Infiere las capacidades requeridas a partir del tópico y keywords.

        Combina:
          - _TASK_CAPABILITY_MAP (por tópico)
          - _SIGNAL_CAPABILITY_KEYWORDS (por keywords en el texto)
        """
        required: set = set()

        # Por tópico
        topic_caps = _TASK_CAPABILITY_MAP.get(topic, set())
        required.update(topic_caps)

        # Por keywords en el texto
        lower = text.lower()
        for cap_name, keywords in _SIGNAL_CAPABILITY_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    required.add(cap_name)
                    break

        logger.debug("_detect_required_capabilities: topic=%s → %s", topic, required)
        return required

    # -- Memory depth estimation -------------------------------------------

    @staticmethod
    def _estimate_memory_depth(owner: str) -> int:
        """Estimate how many interactions a user has.

        Returns the interaction count from user_memory.db.
        Defensive: returns 0 on any failure (DB missing, user unknown, etc.).
        Used by select_strategy to decide whether memory-based routing
        will be productive or should fall back to LLM/online.
        """
        if not owner:
            return 0
        try:
            import os
            import sqlite3
            db = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "vault", "user_memory.db",
            )
            conn = sqlite3.connect(db, timeout=2)
            row = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE user_id = ?",
                (owner,),
            ).fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception:
            return 0

    @staticmethod
    def _get_user_topic_affinity(owner: str) -> Dict[str, int]:
        """Return topic distribution for a user from their patterns.

        Reads the gravitational patterns table (vectrax.db) to see what
        topics the user has discussed most. Returns {topic: count}.
        Used to boost routing confidence when the current message topic
        matches the user's history.
        """
        if not owner:
            return {}
        try:
            import os
            import sqlite3
            db = os.path.join(
                os.path.expanduser("~"), ".vectrax", "vectrax.db",
            )
            if not os.path.exists(db):
                return {}
            conn = sqlite3.connect(db, timeout=2)
            rows = conn.execute(
                "SELECT topic, COUNT(*) as cnt FROM patterns "
                "WHERE user_id = ? GROUP BY topic ORDER BY cnt DESC",
                (owner,),
            ).fetchall()
            conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    # -- Clasificación semántica (lazy) ------------------------------------

    def _classify_semantic(self, text: str):
        """
        Clasifica usando el SemanticClassifier.
        Retorna SemanticResult o None si no disponible.
        """
        try:
            from core.semantic_classifier import get_semantic_classifier
            sc = get_semantic_classifier()
            return sc.classify(text)
        except Exception as exc:
            logger.debug("SemanticClassifier not available: %s", exc)
            return None

    def __repr__(self) -> str:
        n = len(self._metrics)
        return f"SmartRouter(metrics={n})"


# ---------------------------------------------------------------------------
# Mapeo SemanticIntent → Intent (compatibilidad)
# ---------------------------------------------------------------------------

try:
    from core.semantic_classifier import SemanticIntent as _SI
    _SEMANTIC_TO_INTENT: Dict[Any, Intent] = {
        _SI.PLACE_SEARCH:   Intent.PLACE_SEARCH,
        _SI.WEB_SEARCH:     Intent.ONLINE,
        _SI.MEMORY_LOOKUP:  Intent.LOCAL,
        _SI.IDENTITY_QUERY: Intent.IDENTITY,
        _SI.GENERAL_CHAT:   Intent.MEMORY,
        _SI.SYSTEM_ACTION:  Intent.COMMAND,
        _SI.MARKET_QUERY:   Intent.MARKET,
        _SI.MIXED:          Intent.ONLINE,  # mixed → resolver online como safe default
    }
except ImportError:
    _SEMANTIC_TO_INTENT = {}


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
