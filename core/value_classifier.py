"""
Vectrax Value Classifier — Clasificación de Valor de Output
=============================================================
Principio: "Vectrax no conversa… responde cuando lo necesitas."

Cada interacción Input → Output = Valor. Este módulo clasifica
el TIPO de valor que produce cada output y detecta cuando el input
está incompleto para solicitar datos faltantes antes de generar
un output sin valor real.

ValueType:
  MEMORY_STORE    → dato personal almacenable (nombre, preferencia, evento)
  INFO_RESPONSE   → respuesta informativa efímera (explicación, dato factual)
  ACTION_TRIGGER  → output que dispara una acción (deploy, crear, modificar)
  IDENTITY_UPDATE → actualización de identidad del usuario
  DATA_COMPLETION → input incompleto, se solicitan datos faltantes

Integración:
  - Se ejecuta DESPUÉS del routing (SmartRoute) y ANTES de enviar al usuario
  - Cada output con valor ≠ INFO_RESPONSE genera/refuerza un nodo gravitacional
  - DATA_COMPLETION interrumpe el pipeline: no genera output final,
    genera una pregunta de completado

Privacidad:
  Solo procesa categorías abstractas. No almacena contenido literal,
  identidad, credenciales ni datos personales en el ValuePackage.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.value_classifier")


# ---------------------------------------------------------------------------
# ValueType enum
# ---------------------------------------------------------------------------

class ValueType(str, Enum):
    """Tipo de valor que produce un output de Vectrax."""
    MEMORY_STORE    = "memory_store"      # dato almacenable en memoria
    INFO_RESPONSE   = "info_response"     # respuesta informativa efímera
    ACTION_TRIGGER  = "action_trigger"    # dispara acción sobre el sistema
    IDENTITY_UPDATE = "identity_update"   # actualización de identidad
    DATA_COMPLETION = "data_completion"   # input incompleto → pedir datos


class ValueWeight(str, Enum):
    """Peso gravitacional del valor."""
    CORE       = "core"        # alta masa: identidad, decisiones críticas
    STRUCTURAL = "structural"  # masa media: patrones, preferencias, eventos
    EPHEMERAL  = "ephemeral"   # masa baja: info temporal, respuestas factuales


# ---------------------------------------------------------------------------
# ValuePackage — resultado de clasificación
# ---------------------------------------------------------------------------

@dataclass
class ValuePackage:
    """Paquete de clasificación de valor para un output."""
    value_type: ValueType
    weight: ValueWeight
    gravitational_mass: float = 0.0   # 0.0–1.0, peso en el grafo
    entities_detected: int = 0        # cuántas entidades estructuradas se extrajeron
    completeness: float = 1.0         # 0.0–1.0, qué tan completo es el input
    missing_fields: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    classified_at: float = field(default_factory=time.time)

    @property
    def is_complete(self) -> bool:
        return self.completeness >= 0.8 and not self.missing_fields

    @property
    def should_store(self) -> bool:
        """¿Este valor merece un nodo en el grafo gravitacional?"""
        return self.value_type in (
            ValueType.MEMORY_STORE,
            ValueType.ACTION_TRIGGER,
            ValueType.IDENTITY_UPDATE,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value_type": self.value_type.value,
            "weight": self.weight.value,
            "gravitational_mass": round(self.gravitational_mass, 4),
            "entities_detected": self.entities_detected,
            "completeness": round(self.completeness, 4),
            "missing_fields": self.missing_fields,
            "should_store": self.should_store,
        }


# ---------------------------------------------------------------------------
# Patrones de extracción de entidades estructuradas
# ---------------------------------------------------------------------------

# Detecta personas mencionadas
_PERSON_PATTERN = re.compile(
    r"\b(?:con|para|de|a)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})\b"
)

# Detecta fechas relativas y absolutas
_DATE_PATTERN = re.compile(
    r"\b(hoy|mañana|pasado mañana|ayer|lunes|martes|miércoles|jueves|viernes"
    r"|sábado|domingo|today|tomorrow|yesterday"
    r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b",
    re.IGNORECASE,
)

# Detecta horas
_TIME_PATTERN = re.compile(
    r"\b(\d{1,2}:\d{2}(?:\s*(?:am|pm|hrs?)?)?)\b",
    re.IGNORECASE,
)

# Detecta eventos/acciones nominales
_EVENT_PATTERN = re.compile(
    r"\b(reunión|meeting|cita|appointment|llamada|call|entrega|delivery"
    r"|presentación|presentation|deadline|examen|evento|event)\b",
    re.IGNORECASE,
)

# Detecta preferencias personales ("me gusta", "prefiero", "mi X es")
_PREFERENCE_PATTERN = re.compile(
    r"\b(me gusta|prefiero|mi favorit[oa]|i like|i prefer|my favorite)\b",
    re.IGNORECASE,
)

# Detecta intención de identidad ("me llamo", "soy", "mi nombre es")
_IDENTITY_PATTERN = re.compile(
    r"\b(me llamo|mi nombre es|soy|my name is|i am|i'm)\b",
    re.IGNORECASE,
)

# Campos estructurales esperados para un evento completo
_EVENT_FIELDS = {"persona", "evento", "fecha", "hora"}


# ---------------------------------------------------------------------------
# ValueClassifier
# ---------------------------------------------------------------------------

class ValueClassifier:
    """
    Clasifica el tipo de valor de un output basándose en:
      - intent y strategy del SmartRoute
      - entidades estructuradas detectadas en el input
      - completitud del input

    Usage::
        classifier = ValueClassifier()
        pkg = classifier.classify(text, intent="memory", strategy="resolve_memory")
        if not pkg.is_complete:
            # Pedir datos faltantes al usuario
            ...
    """

    def classify(
        self,
        text: str,
        intent: str = "",
        strategy: str = "",
        user_id: str = "",
    ) -> ValuePackage:
        """
        Clasifica el valor del output.

        Args:
            text: Texto del input del usuario.
            intent: Intent del SmartRoute (memory, online, identity, etc.)
            strategy: Estrategia seleccionada (resolve_memory, route_single, etc.)
            user_id: ID del usuario (para contexto, no se almacena).

        Returns:
            ValuePackage con tipo, peso, masa gravitacional y completitud.
        """
        # 1. Extraer entidades estructuradas
        entities = self._extract_entities(text)
        entity_count = sum(len(v) for v in entities.values())

        # 2. Determinar tipo de valor
        value_type = self._determine_type(text, intent, strategy, entities)

        # 3. Evaluar completitud
        completeness, missing = self._evaluate_completeness(
            text, value_type, entities,
        )

        # 4. Si input incompleto y tiene potencial estructural → DATA_COMPLETION
        if not missing and value_type == ValueType.DATA_COMPLETION:
            # No hay campos faltantes detectables → no es realmente incompleto
            value_type = ValueType.INFO_RESPONSE

        if missing:
            value_type = ValueType.DATA_COMPLETION

        # 5. Calcular peso gravitacional
        weight, mass = self._compute_gravity(value_type, entity_count, completeness)

        pkg = ValuePackage(
            value_type=value_type,
            weight=weight,
            gravitational_mass=mass,
            entities_detected=entity_count,
            completeness=completeness,
            missing_fields=missing,
            metadata={
                "intent": intent,
                "strategy": strategy,
                "entity_types": list(entities.keys()),
            },
        )

        logger.info(
            "ValueClassifier: type=%s weight=%s mass=%.2f complete=%.2f entities=%d missing=%s",
            pkg.value_type.value, pkg.weight.value, pkg.gravitational_mass,
            pkg.completeness, pkg.entities_detected, pkg.missing_fields,
        )
        return pkg

    # -- Extracción de entidades --------------------------------------------

    def _extract_entities(self, text: str) -> Dict[str, List[str]]:
        """Extrae entidades estructuradas del texto."""
        entities: Dict[str, List[str]] = {}

        persons = _PERSON_PATTERN.findall(text)
        if persons:
            entities["persona"] = persons

        dates = _DATE_PATTERN.findall(text)
        if dates:
            entities["fecha"] = dates

        times = _TIME_PATTERN.findall(text)
        if times:
            entities["hora"] = times

        events = _EVENT_PATTERN.findall(text)
        if events:
            entities["evento"] = events

        if _PREFERENCE_PATTERN.search(text):
            entities["preferencia"] = ["detected"]

        if _IDENTITY_PATTERN.search(text):
            entities["identidad"] = ["detected"]

        return entities

    # -- Determinación de tipo ----------------------------------------------

    def _determine_type(
        self,
        text: str,
        intent: str,
        strategy: str,
        entities: Dict[str, List[str]],
    ) -> ValueType:
        """Determina el tipo de valor basado en señales combinadas."""

        # Identidad explícita
        if intent == "identity" or "identidad" in entities:
            return ValueType.IDENTITY_UPDATE

        # Acciones sobre el sistema
        action_strategies = {
            "execute_command", "route_cognitive",
        }
        action_intents = {"command", "cognitive"}
        if strategy in action_strategies or intent in action_intents:
            return ValueType.ACTION_TRIGGER

        # Memoria / datos personales
        memory_strategies = {"resolve_memory", "resolve_identity"}
        if strategy in memory_strategies or intent == "memory":
            # Si tiene entidades estructurales → es almacenable
            if entities:
                return ValueType.MEMORY_STORE
            return ValueType.MEMORY_STORE

        # Búsqueda / consulta → informativa
        info_strategies = {
            "resolve_online", "resolve_local", "resolve_places",
            "resolve_market", "route_single", "route_multi",
        }
        if strategy in info_strategies or intent in ("online", "local", "place_search", "market"):
            return ValueType.INFO_RESPONSE

        # Default: si tiene entidades → memory, sino → info
        if entities:
            return ValueType.MEMORY_STORE
        return ValueType.INFO_RESPONSE

    # -- Evaluación de completitud ------------------------------------------

    def _evaluate_completeness(
        self,
        text: str,
        value_type: ValueType,
        entities: Dict[str, List[str]],
    ) -> Tuple[float, List[str]]:
        """
        Evalúa qué tan completo es el input para producir valor.

        Para MEMORY_STORE con evento detectado, verifica que tenga:
        persona, evento, fecha, hora.

        Returns:
            (completeness_score, missing_fields)
        """
        # Solo evaluar completitud para tipos almacenables con eventos
        if value_type not in (ValueType.MEMORY_STORE, ValueType.ACTION_TRIGGER):
            return 1.0, []

        # Si no hay evento detectado, no hay campos requeridos
        if "evento" not in entities:
            return 1.0, []

        # Evento detectado → verificar campos completos
        present = set(entities.keys()) & _EVENT_FIELDS
        missing = list(_EVENT_FIELDS - present)

        if not missing:
            return 1.0, []

        completeness = len(present) / len(_EVENT_FIELDS)
        return completeness, missing

    # -- Cálculo gravitacional ----------------------------------------------

    def _compute_gravity(
        self,
        value_type: ValueType,
        entity_count: int,
        completeness: float,
    ) -> Tuple[ValueWeight, float]:
        """
        Calcula peso y masa gravitacional del valor.

        Masa base por tipo:
          IDENTITY_UPDATE  → 0.9 (core)
          ACTION_TRIGGER   → 0.7 (structural)
          MEMORY_STORE     → 0.6 (structural)
          INFO_RESPONSE    → 0.2 (ephemeral)
          DATA_COMPLETION  → 0.0 (no se almacena)

        Bonus por entidades: +0.05 por entidad (max +0.1)
        Penalización por incompletitud: ×completeness
        """
        base_mass = {
            ValueType.IDENTITY_UPDATE: 0.9,
            ValueType.ACTION_TRIGGER:  0.7,
            ValueType.MEMORY_STORE:    0.6,
            ValueType.INFO_RESPONSE:   0.2,
            ValueType.DATA_COMPLETION: 0.0,
        }

        weight_map = {
            ValueType.IDENTITY_UPDATE: ValueWeight.CORE,
            ValueType.ACTION_TRIGGER:  ValueWeight.STRUCTURAL,
            ValueType.MEMORY_STORE:    ValueWeight.STRUCTURAL,
            ValueType.INFO_RESPONSE:   ValueWeight.EPHEMERAL,
            ValueType.DATA_COMPLETION: ValueWeight.EPHEMERAL,
        }

        mass = base_mass.get(value_type, 0.2)
        weight = weight_map.get(value_type, ValueWeight.EPHEMERAL)

        # Entity bonus
        entity_bonus = min(0.1, entity_count * 0.05)
        mass += entity_bonus

        # Completeness factor
        mass *= completeness

        return weight, round(min(1.0, mass), 4)

    # -- Generador de pregunta de completado --------------------------------

    @staticmethod
    def build_completion_prompt(missing_fields: List[str]) -> str:
        """
        Genera una pregunta directa para completar datos faltantes.

        Esto NO es conversación — es completar el input para generar valor.
        """
        field_labels = {
            "persona": "¿con quién?",
            "evento": "¿qué tipo de evento?",
            "fecha": "¿cuándo?",
            "hora": "¿a qué hora?",
        }

        questions = [
            field_labels.get(f, f"¿{f}?")
            for f in missing_fields
        ]

        if len(questions) == 1:
            return f"Para registrar esto correctamente: {questions[0]}"

        joined = ", ".join(questions[:-1]) + f" y {questions[-1]}"
        return f"Para registrar esto correctamente: {joined}"


# ---------------------------------------------------------------------------
# Puente gravitacional — registra valores en el knowledge graph
# ---------------------------------------------------------------------------

def register_value_in_graph(
    value_pkg: ValuePackage,
    response_text: str,
    user_id: str = "",
) -> Optional[str]:
    """
    Registra un output con valor en el grafo de conocimiento gravitacional.

    Solo registra si value_pkg.should_store es True.
    Crea un nodo VALUE_OUTPUT con masa = gravitational_mass
    y lo conecta a la constelación del usuario si existe.

    Returns:
        node_id del nodo creado, o None si no se registró.
    """
    if not value_pkg.should_store:
        return None

    try:
        from cognition.memory.knowledge_graph import KnowledgeGraph
        from cognition.types import KnowledgeNodeType

        graph = KnowledgeGraph()

        # Generar ID determinístico basado en tipo + timestamp
        node_id = f"val_{value_pkg.value_type.value}_{hashlib.md5(str(value_pkg.classified_at).encode()).hexdigest()[:8]}"

        node = graph.add_node(
            node_id=node_id,
            node_type=KnowledgeNodeType.VALUE_OUTPUT,
            label=f"{value_pkg.value_type.value} (mass={value_pkg.gravitational_mass:.2f})",
            metadata={
                "value_type": value_pkg.value_type.value,
                "weight": value_pkg.weight.value,
                "gravitational_mass": value_pkg.gravitational_mass,
                "entities_detected": value_pkg.entities_detected,
                "completeness": value_pkg.completeness,
            },
        )

        # Si hay user_id, buscar nodo de constelación del usuario y conectar
        if user_id:
            user_node_id = f"user_{user_id}"
            existing = graph.get_node(user_node_id)
            if existing:
                graph.add_edge(
                    source=user_node_id,
                    target=node_id,
                    relation="produced_value",
                    weight=value_pkg.gravitational_mass,
                )

        logger.info(
            "Value registered in graph: node=%s type=%s mass=%.2f",
            node_id, value_pkg.value_type.value, value_pkg.gravitational_mass,
        )
        return node_id

    except Exception as exc:
        # Fail-safe: si el grafo falla, el pipeline sigue
        logger.debug("Failed to register value in graph (non-critical): %s", exc)
        return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_classifier: Optional[ValueClassifier] = None


def get_value_classifier() -> ValueClassifier:
    """Return the global ValueClassifier singleton."""
    global _classifier
    if _classifier is None:
        _classifier = ValueClassifier()
    return _classifier
