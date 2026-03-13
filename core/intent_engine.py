"""
Vectrax Intent Engine — Motor de Intenciones
===============================================
Clasifica la intención del usuario a nivel de acción ejecutable.

Compone:
  - SmartRouter.classify_intent → clasificación base (memory/local/online/ai/cognitive)
  - PromptEngine.detect_intent  → intent semántico (codigo/analisis/automatizacion/etc.)
  - Extracción de verbo + target + restricciones

Pipeline:
  texto usuario → detección dual → extracción de target → IntentPackage

Privacidad:
  IntentPackage.metadata NO contiene contenido del mensaje.
  Solo almacena: categoría de acción, target abstracto, confianza.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.intent_engine")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ActionIntent(str, Enum):
    """Intención ejecutable de alto nivel."""
    CREATE    = "create"      # crear módulo, archivo, test, componente
    MODIFY    = "modify"      # modificar, refactorizar, actualizar
    CONNECT   = "connect"     # conectar servicios, APIs, módulos
    DEPLOY    = "deploy"      # desplegar, publicar, release
    ANALYZE   = "analyze"     # analizar código, datos, rendimiento
    QUERY     = "query"       # pregunta que no requiere acción sobre código
    CONFIGURE = "configure"   # configurar, ajustar parámetros


class IntentScope(str, Enum):
    """Alcance estimado de la intención."""
    SINGLE_FILE = "single_file"       # afecta 1 archivo
    MODULE      = "module"            # afecta 1 módulo (directorio)
    CROSS_MODULE = "cross_module"     # afecta múltiples módulos
    SYSTEM      = "system"            # afecta todo el sistema


# ---------------------------------------------------------------------------
# IntentPackage
# ---------------------------------------------------------------------------

@dataclass
class IntentPackage:
    """Paquete completo de intención parseada."""
    action: ActionIntent
    target: str                              # qué se va a crear/modificar/etc.
    scope: IntentScope
    description: str                         # descripción limpia de la acción
    confidence: float                        # 0.0 – 1.0
    constraints: List[str] = field(default_factory=list)
    semantic_intent: str = ""                # intent del PromptEngine (codigo, analisis, etc.)
    smart_intent: str = ""                   # intent del SmartRouter (memory, online, etc.)
    topic: str = "general"
    channel: str = ""
    owner: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    parsed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "target": self.target,
            "scope": self.scope.value,
            "description": self.description,
            "confidence": round(self.confidence, 4),
            "constraints": self.constraints,
            "semantic_intent": self.semantic_intent,
            "smart_intent": self.smart_intent,
            "topic": self.topic,
        }

    def summary(self) -> str:
        return (
            f"[{self.action.value}] {self.target} "
            f"(scope={self.scope.value}, conf={self.confidence:.2f})"
        )


# ---------------------------------------------------------------------------
# Patrones de extracción de acción
# ---------------------------------------------------------------------------

# Verbos → ActionIntent (español + inglés)
_ACTION_VERBS: Dict[ActionIntent, List[str]] = {
    ActionIntent.CREATE: [
        "crea", "crear", "genera", "generar", "construye", "construir",
        "implementa", "implementar", "añade", "añadir", "agrega", "agregar",
        "escribe", "escribir", "nuevo", "nueva",
        "create", "build", "generate", "implement", "add", "write", "new",
    ],
    ActionIntent.MODIFY: [
        "modifica", "modificar", "cambia", "cambiar", "actualiza", "actualizar",
        "refactoriza", "refactorizar", "corrige", "corregir", "arregla", "arreglar",
        "mejora", "mejorar", "optimiza", "optimizar", "ajusta", "ajustar",
        "modify", "change", "update", "refactor", "fix", "improve", "optimize",
    ],
    ActionIntent.CONNECT: [
        "conecta", "conectar", "integra", "integrar", "enlaza", "enlazar",
        "vincula", "vincular", "une", "unir",
        "connect", "integrate", "link", "bridge", "wire", "hook",
    ],
    ActionIntent.DEPLOY: [
        "despliega", "desplegar", "publica", "publicar", "lanza", "lanzar",
        "release", "deploy", "publish", "ship", "push",
    ],
    ActionIntent.ANALYZE: [
        "analiza", "analizar", "evalúa", "evaluar", "inspecciona", "inspeccionar",
        "revisa", "revisar", "diagnostica", "diagnosticar", "audita", "auditar",
        "analyze", "evaluate", "inspect", "review", "diagnose", "audit", "scan",
    ],
    ActionIntent.CONFIGURE: [
        "configura", "configurar", "establece", "establecer", "define", "definir",
        "setea", "setear", "habilita", "deshabilita",
        "configure", "setup", "set", "enable", "disable", "toggle",
    ],
}

# Compilar patrones de verbos
_VERB_PATTERNS: Dict[ActionIntent, re.Pattern] = {}
for action, verbs in _ACTION_VERBS.items():
    pattern = r"\b(" + "|".join(re.escape(v) for v in verbs) + r")\b"
    _VERB_PATTERNS[action] = re.compile(pattern, re.IGNORECASE)

# Patrones para extraer el target (lo que viene después del verbo)
_TARGET_PATTERN = re.compile(
    r"(?:un|una|el|la|los|las|the|a|an)\s+"
    r"([\w\s\-/\.]+?)(?:\s+(?:que|para|con|from|to|for|with|in)\b|$)",
    re.IGNORECASE,
)

# Palabras clave de target directo
_TARGET_KEYWORDS = re.compile(
    r"\b(módulo|module|archivo|file|test|api|endpoint|clase|class|"
    r"función|function|servicio|service|componente|component|"
    r"router|pipeline|engine|motor|sistema|system|"
    r"conector|connector|adaptador|adapter|interfaz|interface)\b",
    re.IGNORECASE,
)

# Patrones para detectar restricciones
_CONSTRAINT_PATTERNS = [
    (re.compile(r"\bsin\s+(romper|modificar|tocar|cambiar)\b", re.I), "no_break"),
    (re.compile(r"\breutilizar?\b", re.I), "reuse_existing"),
    (re.compile(r"\bvalidar\s+con\s+tests?\b", re.I), "validate_tests"),
    (re.compile(r"\bno\s+(romper?|break)\b", re.I), "no_break"),
    (re.compile(r"\b(backward.?compatible|retrocompatible)\b", re.I), "backward_compat"),
    (re.compile(r"\b(dry.?run|simulación|simulacion)\b", re.I), "dry_run"),
]


# ---------------------------------------------------------------------------
# Intent Engine
# ---------------------------------------------------------------------------

class IntentEngine:
    """
    Motor de intenciones de Vectrax.

    Interpreta texto en lenguaje natural y produce un IntentPackage
    que describe la acción ejecutable, el target y las restricciones.

    Compone SmartRouter (clasificación base) + PromptEngine (intent semántico)
    para detección dual con mayor precisión.

    Usage::

        engine = IntentEngine()
        pkg = engine.parse("crea un módulo de autenticación con JWT")
        print(pkg.action)   # → CREATE
        print(pkg.target)   # → módulo de autenticación
    """

    def __init__(self) -> None:
        self._smart_router = None
        self._prompt_engine = None

    # -- Parse principal ----------------------------------------------------

    def parse(
        self,
        text: str,
        channel: str = "creator",
        owner: str = "",
    ) -> IntentPackage:
        """
        Parsea texto de usuario en un IntentPackage ejecutable.

        Args:
            text: Mensaje del usuario en lenguaje natural.
            channel: Canal (creator/user).
            owner: Propietario del canal.

        Returns:
            IntentPackage con acción, target, scope, constraints.
        """
        t0 = time.time()

        # 1. Detectar verbo de acción
        action, verb_confidence = self._detect_action(text)

        # 2. Extraer target
        target = self._extract_target(text)

        # 3. Detectar restricciones
        constraints = self._detect_constraints(text)

        # 4. Intent semántico del PromptEngine
        semantic_intent = self._get_semantic_intent(text)

        # 5. Intent del SmartRouter
        smart_intent, smart_topic = self._get_smart_context(text)

        # 6. Estimar scope
        scope = self._estimate_scope(text, target, action)

        # 7. Calcular confianza combinada
        confidence = self._compute_confidence(verb_confidence, semantic_intent, smart_intent)

        # 8. Generar descripción limpia
        description = self._build_description(action, target, text)

        pkg = IntentPackage(
            action=action,
            target=target,
            scope=scope,
            description=description,
            confidence=confidence,
            constraints=constraints,
            semantic_intent=semantic_intent,
            smart_intent=smart_intent,
            topic=smart_topic,
            channel=channel,
            owner=owner,
            metadata={
                "verb_confidence": round(verb_confidence, 4),
                "parse_latency_ms": round((time.time() - t0) * 1000, 2),
            },
        )

        logger.info("IntentEngine: %s", pkg.summary())
        return pkg

    # -- Detección de acción ------------------------------------------------

    def _detect_action(self, text: str) -> Tuple[ActionIntent, float]:
        """Detecta el verbo de acción principal."""
        scores: Dict[ActionIntent, int] = {}

        for action, pattern in _VERB_PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                scores[action] = len(matches)

        if scores:
            best = max(scores, key=scores.get)
            confidence = min(1.0, scores[best] / 3.0)
            return best, max(confidence, 0.6)

        # Si no hay verbo claro → inferir del contexto
        if "?" in text or "¿" in text:
            return ActionIntent.QUERY, 0.5

        return ActionIntent.QUERY, 0.3

    # -- Extracción de target -----------------------------------------------

    def _extract_target(self, text: str) -> str:
        """Extrae el objeto/target de la acción."""
        # Buscar keywords de target directos
        kw_match = _TARGET_KEYWORDS.search(text)
        if kw_match:
            # Extraer contexto alrededor del keyword
            start = kw_match.start()
            # Buscar el fragmento relevante (hasta 60 chars después)
            fragment = text[start:start + 60].strip()
            # Limpiar: tomar hasta el primer delimitador
            clean = re.split(r"[,;.\n\-—]", fragment)[0].strip()
            if clean:
                return clean[:80]

        # Intentar patrón article + noun phrase
        target_match = _TARGET_PATTERN.search(text)
        if target_match:
            return target_match.group(1).strip()[:80]

        # Fallback: primeras palabras significativas después del verbo
        for pattern in _VERB_PATTERNS.values():
            verb_match = pattern.search(text)
            if verb_match:
                after = text[verb_match.end():].strip()
                # Tomar hasta 60 chars o primer delimitador
                fragment = re.split(r"[,;.\n\-—]", after)[0].strip()
                if fragment:
                    return fragment[:80]

        return "general"

    # -- Detección de restricciones -----------------------------------------

    def _detect_constraints(self, text: str) -> List[str]:
        """Detecta restricciones explícitas e implícitas."""
        constraints = []
        for pattern, label in _CONSTRAINT_PATTERNS:
            if pattern.search(text):
                constraints.append(label)
        return constraints

    # -- Integración con PromptEngine (lazy) --------------------------------

    def _get_semantic_intent(self, text: str) -> str:
        """Obtiene el intent semántico del VX PromptEngine."""
        try:
            if self._prompt_engine is None:
                from core.prompt_engine import detect_intent
                self._prompt_engine = detect_intent
            result = self._prompt_engine(text)
            if isinstance(result, dict):
                return result.get("intent", "general")
            return str(result) if result else "general"
        except Exception as exc:
            logger.debug("PromptEngine intent detection failed: %s", exc)
            return "general"

    # -- Integración con SmartRouter (lazy) ---------------------------------

    def _get_smart_context(self, text: str) -> Tuple[str, str]:
        """Obtiene intent y tópico del SmartRouter."""
        try:
            if self._smart_router is None:
                from core.smart_router import SmartRouter
                self._smart_router = SmartRouter()
            intent, _ = self._smart_router.classify_intent(text)
            context = self._smart_router.detect_context(text, "", "")
            return intent.value, context.get("topic", "general")
        except Exception as exc:
            logger.debug("SmartRouter context detection failed: %s", exc)
            return "memory", "general"

    # -- Estimación de scope ------------------------------------------------

    def _estimate_scope(
        self, text: str, target: str, action: ActionIntent,
    ) -> IntentScope:
        """Estima el alcance de la acción."""
        combined = (text + " " + target).lower()

        # Señales de scope amplio
        if any(w in combined for w in [
            "sistema", "system", "arquitectura", "architecture",
            "todo", "todos", "all", "global", "pipeline",
        ]):
            return IntentScope.SYSTEM

        if any(w in combined for w in [
            "conectar", "connect", "integrar", "integrate",
            "unificar", "cross", "entre módulos", "between modules",
        ]):
            return IntentScope.CROSS_MODULE

        if any(w in combined for w in [
            "módulo", "module", "motor", "engine", "servicio", "service",
            "componente", "component", "paquete", "package",
        ]):
            return IntentScope.MODULE

        # Default por tipo de acción
        if action in (ActionIntent.DEPLOY, ActionIntent.CONFIGURE):
            return IntentScope.SYSTEM
        if action == ActionIntent.CONNECT:
            return IntentScope.CROSS_MODULE

        return IntentScope.SINGLE_FILE

    # -- Confianza combinada ------------------------------------------------

    def _compute_confidence(
        self, verb_conf: float, semantic: str, smart: str,
    ) -> float:
        """Calcula confianza combinada de las dos fuentes de detección."""
        base = verb_conf

        # Bonus si el semantic intent es accionable (no "general")
        if semantic and semantic != "general":
            base = min(1.0, base + 0.15)

        # Bonus si el smart intent no es "memory" (indica acción clara)
        if smart not in ("memory", ""):
            base = min(1.0, base + 0.1)

        return round(base, 4)

    # -- Descripción limpia -------------------------------------------------

    def _build_description(
        self, action: ActionIntent, target: str, text: str,
    ) -> str:
        """Genera una descripción limpia y legible de la intención."""
        action_labels = {
            ActionIntent.CREATE: "Crear",
            ActionIntent.MODIFY: "Modificar",
            ActionIntent.CONNECT: "Conectar",
            ActionIntent.DEPLOY: "Desplegar",
            ActionIntent.ANALYZE: "Analizar",
            ActionIntent.QUERY: "Consultar",
            ActionIntent.CONFIGURE: "Configurar",
        }
        label = action_labels.get(action, action.value.capitalize())

        if target and target != "general":
            return f"{label}: {target}"
        # Fallback: primeras 80 chars del texto
        return f"{label}: {text[:80].strip()}"

    def __repr__(self) -> str:
        return "IntentEngine()"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[IntentEngine] = None


def get_intent_engine() -> IntentEngine:
    """Return the global IntentEngine singleton."""
    global _engine
    if _engine is None:
        _engine = IntentEngine()
    return _engine
