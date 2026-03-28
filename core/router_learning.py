"""
Vectrax Router Self-Learning — Autoaprendizaje del Router
==========================================================
Sistema de aprendizaje pasivo que observa decisiones del SmartRouter,
evalúa la calidad de cada routing, y genera propuestas de mejora
basadas en datos reales de uso.

Componentes:
  - RouterFeedback:        registro de cada decisión del router
  - RouterDecisionLedger:  persistencia append-only en JSONL
  - PostResolutionEvaluator: evaluación automática post-resolución
  - RouteQualityScore:     clasificación de calidad por decisión
  - RouterLearningEngine:  análisis de patrones + propuestas de mejora

Seguridad:
  - NUNCA modifica el núcleo directamente
  - Compatible con SmartRouter actual (composición, no herencia)
  - Si falla, el sistema sigue operando con lógica actual
  - Todo ajuste es reversible y pasa por proposal flow
  - Solo almacena métricas abstractas, nunca contenido/identidad/credenciales

Privacidad:
  - No almacena texto del mensaje, identidad, credenciales ni tokens
  - Solo categorías abstractas: intent, strategy, topic, scores
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.router_learning")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
LEDGER_PATH = os.path.join(DATA_DIR, "router_feedback.jsonl")


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Route Quality Score
# ---------------------------------------------------------------------------

class RouteQualityScore(str, Enum):
    """Clasificación de calidad de una decisión de routing."""
    CORRECT_ROUTE   = "correct_route"     # ruta correcta, resolución exitosa
    WEAK_ROUTE      = "weak_route"        # resolvió pero subóptimo
    FALLBACK_BETTER = "fallback_better"   # el fallback resolvió mejor
    FAILED_ROUTE    = "failed_route"      # la ruta falló completamente
    AMBIGUOUS_ROUTE = "ambiguous_route"   # ambigüedad → confianza baja


# ---------------------------------------------------------------------------
# Router Feedback — registro de cada decisión
# ---------------------------------------------------------------------------

@dataclass
class RouterFeedback:
    """
    Registro completo de una decisión del router + resultado.

    Almacena SOLO métricas abstractas, nunca contenido del mensaje,
    identidad, credenciales ni datos personales.
    """
    # --- Decisión del router ---
    intent: str                           # categoría de intent (no texto)
    confidence: float                     # 0.0 – 1.0
    topic: str                            # tópico detectado
    strategy: str                         # estrategia seleccionada
    risk_level: str                       # LOW/MEDIUM/HIGH
    providers: List[str] = field(default_factory=list)
    entities_count: int = 0               # número de entidades (no valores)
    classification_method: str = ""       # semantic / regex_fallback

    # --- Resultado de resolución ---
    success: bool = False
    used_fallback: bool = False
    fallback_strategy: str = ""           # si hubo fallback, cuál
    response_latency_ms: float = 0.0
    resolution_error: str = ""            # tipo de error (no contenido)

    # --- Evaluación post-resolución ---
    quality_score: str = ""               # RouteQualityScore value
    was_generic_response: bool = False    # respuesta demasiado genérica
    had_ambiguity: bool = False           # ambigüedad detectada

    # --- Semantic decision path (for learning) ---
    semantic_confidence: float = 0.0      # confianza del clasificador semántico
    semantic_frame: str = ""              # frame detectado (search_info, ask_memory, etc.)
    semantic_intent: str = ""             # intent semántico (antes de resolución)
    decision_note: str = ""               # nota de decisión (aligned, conflict, etc.)
    regex_agrees: Optional[bool] = None   # ¿el regex coincidió con el semántico?
    semantic_would_have_been: str = ""    # intent que el semántico habría elegido

    # --- Metadata ---
    timestamp: float = field(default_factory=time.time)
    word_count: int = 0                   # longitud abstracta del input
    routing_latency_ms: float = 0.0       # latencia solo del routing

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a dict para JSONL (sin contenido del mensaje)."""
        d = {
            "intent": self.intent,
            "confidence": round(self.confidence, 4),
            "topic": self.topic,
            "strategy": self.strategy,
            "risk_level": self.risk_level,
            "providers": self.providers,
            "entities_count": self.entities_count,
            "classification_method": self.classification_method,
            "success": self.success,
            "used_fallback": self.used_fallback,
            "fallback_strategy": self.fallback_strategy,
            "response_latency_ms": round(self.response_latency_ms, 2),
            "resolution_error": self.resolution_error[:100],
            "quality_score": self.quality_score,
            "was_generic_response": self.was_generic_response,
            "had_ambiguity": self.had_ambiguity,
            "timestamp": self.timestamp,
            "word_count": self.word_count,
            "routing_latency_ms": round(self.routing_latency_ms, 2),
        }
        # Semantic decision path (only if present)
        if self.semantic_confidence > 0:
            d["semantic_confidence"] = round(self.semantic_confidence, 4)
        if self.semantic_frame:
            d["semantic_frame"] = self.semantic_frame
        if self.semantic_intent:
            d["semantic_intent"] = self.semantic_intent
        if self.decision_note:
            d["decision_note"] = self.decision_note[:150]
        if self.regex_agrees is not None:
            d["regex_agrees"] = self.regex_agrees
        if self.semantic_would_have_been:
            d["semantic_would_have_been"] = self.semantic_would_have_been
        return d


# ---------------------------------------------------------------------------
# Router Decision Ledger — persistencia JSONL append-only
# ---------------------------------------------------------------------------

class RouterDecisionLedger:
    """
    Ledger persistente de decisiones del router en JSONL.

    Append-only. Cada línea es un JSON independiente.
    Ubicación: data/router_feedback.jsonl
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or LEDGER_PATH

    def append(self, feedback: RouterFeedback) -> None:
        """Escribe un feedback al ledger."""
        _ensure_data_dir()
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(feedback.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("RouterDecisionLedger write failed: %s", exc)

    def read_all(self) -> List[Dict[str, Any]]:
        """Lee todos los registros del ledger."""
        if not os.path.exists(self._path):
            return []
        records: List[Dict[str, Any]] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            logger.warning("RouterDecisionLedger read failed: %s", exc)
        return records

    def read_last(self, n: int = 100) -> List[Dict[str, Any]]:
        """Lee los últimos N registros (más recientes primero)."""
        all_records = self.read_all()
        return list(reversed(all_records[-n:]))

    def count(self) -> int:
        """Cuenta total de registros."""
        return len(self.read_all())


# ---------------------------------------------------------------------------
# Post-Resolution Evaluator — evaluación automática
# ---------------------------------------------------------------------------

class PostResolutionEvaluator:
    """
    Evalúa automáticamente la calidad de cada decisión de routing
    después de la resolución.

    Criterios:
      - ¿La herramienta elegida fue correcta? (success + no fallback)
      - ¿Hubo error? (resolution_error)
      - ¿El fallback resolvió mejor? (used_fallback + success)
      - ¿La respuesta fue demasiado genérica? (was_generic_response)
      - ¿Hubo ambigüedad? (confidence < threshold)
    """

    AMBIGUITY_THRESHOLD = 0.45
    WEAK_CONFIDENCE_THRESHOLD = 0.6

    def evaluate(self, feedback: RouterFeedback) -> RouteQualityScore:
        """
        Clasifica la calidad de la decisión de routing.

        Returns:
            RouteQualityScore con la clasificación.
        """
        # Fallo completo
        if not feedback.success and not feedback.used_fallback:
            return RouteQualityScore.FAILED_ROUTE

        # El fallback resolvió (ruta principal falló)
        if feedback.used_fallback and feedback.success:
            return RouteQualityScore.FALLBACK_BETTER

        # Ambigüedad: confianza muy baja
        if feedback.confidence < self.AMBIGUITY_THRESHOLD:
            return RouteQualityScore.AMBIGUOUS_ROUTE

        # Ruta débil: resolvió pero genérica o confianza baja
        if feedback.was_generic_response:
            return RouteQualityScore.WEAK_ROUTE
        if feedback.confidence < self.WEAK_CONFIDENCE_THRESHOLD:
            return RouteQualityScore.WEAK_ROUTE

        # Éxito limpio
        return RouteQualityScore.CORRECT_ROUTE


# ---------------------------------------------------------------------------
# Learning Engine — análisis de patrones + propuestas
# ---------------------------------------------------------------------------

class RouterLearningEngine:
    """
    Motor de aprendizaje del router.

    Analiza el historial de decisiones para:
      - Detectar patrones de ambigüedad frecuentes
      - Identificar intents con alta tasa de fallo
      - Proponer ajustes de thresholds de confidence
      - Generar ejemplos reales de entrenamiento
      - Detectar rutas subóptimas frecuentes

    MODO PASIVO: observa y propone, NUNCA aplica cambios automáticos.
    Todos los ajustes pasan por proposal flow.
    """

    # Umbrales mínimos para generar propuestas
    MIN_SAMPLES_FOR_ANALYSIS = 10
    MIN_SAMPLES_PER_INTENT = 5
    FAILURE_RATE_ALERT = 0.25       # >25% fallos → alerta
    AMBIGUITY_RATE_ALERT = 0.20     # >20% ambiguos → alerta
    FALLBACK_RATE_ALERT = 0.15      # >15% fallbacks → alerta

    def __init__(self, ledger: Optional[RouterDecisionLedger] = None) -> None:
        self._ledger = ledger or RouterDecisionLedger()

    # -- Análisis principal --------------------------------------------------

    def analyze(self) -> Dict[str, Any]:
        """
        Análisis completo del historial de routing.

        Returns:
            Dict con estadísticas, patrones, y propuestas de mejora.
        """
        records = self._ledger.read_all()

        if len(records) < self.MIN_SAMPLES_FOR_ANALYSIS:
            return {
                "status": "insufficient_data",
                "total_records": len(records),
                "min_required": self.MIN_SAMPLES_FOR_ANALYSIS,
                "message": f"Se necesitan al menos {self.MIN_SAMPLES_FOR_ANALYSIS} "
                           f"registros para análisis (hay {len(records)})",
            }

        analysis = {
            "status": "ready",
            "total_records": len(records),
            "period": self._compute_period(records),
            "intent_distribution": self._intent_distribution(records),
            "quality_distribution": self._quality_distribution(records),
            "strategy_stats": self._strategy_stats(records),
            "fallback_analysis": self._fallback_analysis(records),
            "ambiguity_patterns": self._ambiguity_patterns(records),
            "confidence_analysis": self._confidence_analysis(records),
            "latency_stats": self._latency_stats(records),
            "improvement_proposals": [],
        }

        # Generar propuestas de mejora
        analysis["improvement_proposals"] = self._generate_proposals(analysis)

        return analysis

    # -- Distribución de intents ---------------------------------------------

    def _intent_distribution(self, records: List[Dict]) -> Dict[str, Any]:
        """Distribución y tasa de éxito por intent."""
        by_intent: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"total": 0, "success": 0, "failed": 0, "fallback": 0}
        )
        for r in records:
            intent = r.get("intent", "unknown")
            by_intent[intent]["total"] += 1
            if r.get("success"):
                by_intent[intent]["success"] += 1
            else:
                by_intent[intent]["failed"] += 1
            if r.get("used_fallback"):
                by_intent[intent]["fallback"] += 1

        result = {}
        for intent, stats in sorted(by_intent.items(), key=lambda x: -x[1]["total"]):
            total = stats["total"]
            result[intent] = {
                "total": total,
                "success_rate": round(stats["success"] / total, 4) if total else 0,
                "failure_rate": round(stats["failed"] / total, 4) if total else 0,
                "fallback_rate": round(stats["fallback"] / total, 4) if total else 0,
            }
        return result

    # -- Distribución de calidad ---------------------------------------------

    def _quality_distribution(self, records: List[Dict]) -> Dict[str, int]:
        """Distribución de scores de calidad."""
        counter = Counter(r.get("quality_score", "unknown") for r in records)
        return dict(counter.most_common())

    # -- Stats por estrategia ------------------------------------------------

    def _strategy_stats(self, records: List[Dict]) -> Dict[str, Any]:
        """Estadísticas por estrategia de resolución."""
        by_strategy: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"total": 0, "success": 0, "avg_latency": 0.0, "latencies": []}
        )
        for r in records:
            strat = r.get("strategy", "unknown")
            by_strategy[strat]["total"] += 1
            if r.get("success"):
                by_strategy[strat]["success"] += 1
            lat = r.get("response_latency_ms", 0)
            if lat > 0:
                by_strategy[strat]["latencies"].append(lat)

        result = {}
        for strat, stats in sorted(by_strategy.items(), key=lambda x: -x[1]["total"]):
            total = stats["total"]
            lats = stats["latencies"]
            result[strat] = {
                "total": total,
                "success_rate": round(stats["success"] / total, 4) if total else 0,
                "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else 0,
            }
        return result

    # -- Análisis de fallbacks -----------------------------------------------

    def _fallback_analysis(self, records: List[Dict]) -> Dict[str, Any]:
        """Analiza cuándo y por qué se usaron fallbacks."""
        fallbacks = [r for r in records if r.get("used_fallback")]
        total = len(records)

        # ¿Qué intents generan más fallbacks?
        fb_by_intent = Counter(r.get("intent", "unknown") for r in fallbacks)

        # ¿Qué estrategias de fallback se usaron?
        fb_strategies = Counter(r.get("fallback_strategy", "unknown") for r in fallbacks)

        return {
            "total_fallbacks": len(fallbacks),
            "fallback_rate": round(len(fallbacks) / total, 4) if total else 0,
            "by_intent": dict(fb_by_intent.most_common(10)),
            "fallback_strategies_used": dict(fb_strategies.most_common(10)),
        }

    # -- Patrones de ambigüedad ----------------------------------------------

    def _ambiguity_patterns(self, records: List[Dict]) -> Dict[str, Any]:
        """Detecta patrones frecuentes de ambigüedad."""
        ambiguous = [
            r for r in records
            if r.get("quality_score") == RouteQualityScore.AMBIGUOUS_ROUTE.value
            or r.get("had_ambiguity")
        ]

        # ¿Qué intents son más ambiguos?
        amb_by_intent = Counter(r.get("intent", "unknown") for r in ambiguous)

        # ¿Qué topics son más ambiguos?
        amb_by_topic = Counter(r.get("topic", "unknown") for r in ambiguous)

        # ¿Qué métodos de clasificación generan ambigüedad?
        amb_by_method = Counter(
            r.get("classification_method", "unknown") for r in ambiguous
        )

        # Confianza promedio en ambiguos
        confs = [r.get("confidence", 0) for r in ambiguous if r.get("confidence")]
        avg_conf = round(sum(confs) / len(confs), 4) if confs else 0

        return {
            "total_ambiguous": len(ambiguous),
            "ambiguity_rate": round(len(ambiguous) / len(records), 4) if records else 0,
            "by_intent": dict(amb_by_intent.most_common(10)),
            "by_topic": dict(amb_by_topic.most_common(10)),
            "by_classification_method": dict(amb_by_method.most_common()),
            "avg_confidence": avg_conf,
        }

    # -- Análisis de confianza -----------------------------------------------

    def _confidence_analysis(self, records: List[Dict]) -> Dict[str, Any]:
        """Analiza distribución de confianza y relación con éxito."""
        if not records:
            return {}

        confs = [r.get("confidence", 0) for r in records]
        success_confs = [r.get("confidence", 0) for r in records if r.get("success")]
        failed_confs = [r.get("confidence", 0) for r in records if not r.get("success")]

        # Distribución por rangos
        ranges = {"0.0-0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, "0.9-1.0": 0}
        for c in confs:
            if c < 0.3:
                ranges["0.0-0.3"] += 1
            elif c < 0.5:
                ranges["0.3-0.5"] += 1
            elif c < 0.7:
                ranges["0.5-0.7"] += 1
            elif c < 0.9:
                ranges["0.7-0.9"] += 1
            else:
                ranges["0.9-1.0"] += 1

        return {
            "avg_confidence": round(sum(confs) / len(confs), 4),
            "avg_success_confidence": round(
                sum(success_confs) / len(success_confs), 4
            ) if success_confs else 0,
            "avg_failed_confidence": round(
                sum(failed_confs) / len(failed_confs), 4
            ) if failed_confs else 0,
            "confidence_ranges": ranges,
        }

    # -- Latencia ------------------------------------------------------------

    def _latency_stats(self, records: List[Dict]) -> Dict[str, Any]:
        """Estadísticas de latencia de routing y resolución."""
        routing_lats = [r.get("routing_latency_ms", 0) for r in records if r.get("routing_latency_ms")]
        response_lats = [r.get("response_latency_ms", 0) for r in records if r.get("response_latency_ms")]

        return {
            "avg_routing_ms": round(sum(routing_lats) / len(routing_lats), 2) if routing_lats else 0,
            "avg_response_ms": round(sum(response_lats) / len(response_lats), 2) if response_lats else 0,
            "p95_routing_ms": round(
                sorted(routing_lats)[int(len(routing_lats) * 0.95)] if routing_lats else 0, 2
            ),
            "p95_response_ms": round(
                sorted(response_lats)[int(len(response_lats) * 0.95)] if response_lats else 0, 2
            ),
        }

    # -- Periodo del análisis ------------------------------------------------

    def _compute_period(self, records: List[Dict]) -> Dict[str, Any]:
        """Calcula el periodo cubierto por los registros."""
        if not records:
            return {"start": None, "end": None, "duration_hours": 0}
        timestamps = [r.get("timestamp", 0) for r in records]
        start = min(timestamps)
        end = max(timestamps)
        return {
            "start": time.strftime("%Y-%m-%d %H:%M", time.localtime(start)),
            "end": time.strftime("%Y-%m-%d %H:%M", time.localtime(end)),
            "duration_hours": round((end - start) / 3600, 2),
        }

    # -- Clasificación estructural de errores ---------------------------------

    def classify_errors(self) -> Dict[str, Any]:
        """
        Clasifica todos los registros no-óptimos por categoría de error.

        Categorías:
          - misclassified_intent: intent detectado incorrecto
          - fallback_resolved: la ruta primaria falló pero el fallback resolvió
          - language_mismatch: respuesta en idioma incorrecto
          - timeout_or_external: fallo externo (API, timeout)
          - low_confidence_correct: resuelto pero con confianza débil
          - semantic_regex_conflict: las dos capas no coinciden

        Returns:
            Dict con top errors, frecuencia, y sugerencias.
        """
        records = self._ledger.read_all()
        if not records:
            return {"status": "empty", "total": 0}

        classified: Dict[str, List[Dict]] = defaultdict(list)

        for r in records:
            quality = r.get("quality_score", "")
            method = r.get("classification_method", "")
            note = r.get("decision_note", "")
            error = r.get("resolution_error", "")
            fallback = r.get("used_fallback", False)
            success = r.get("success", True)
            conf = r.get("confidence", 1.0)
            sem_would = r.get("semantic_would_have_been", "")

            # Skip correct routes
            if quality == "correct_route" and success:
                continue

            # Classify
            if quality == "failed_route" and not success:
                if sem_would and sem_would != r.get("intent"):
                    classified["misclassified_intent"].append(r)
                elif error and ("timeout" in error.lower() or "connection" in error.lower()):
                    classified["timeout_or_external"].append(r)
                else:
                    classified["misclassified_intent"].append(r)

            elif quality == "fallback_better":
                classified["fallback_resolved"].append(r)

            elif quality == "weak_route":
                if conf < 0.5:
                    classified["low_confidence_correct"].append(r)
                else:
                    classified["low_confidence_correct"].append(r)

            elif quality == "ambiguous_route":
                if "conflict" in note:
                    classified["semantic_regex_conflict"].append(r)
                else:
                    classified["low_confidence_correct"].append(r)

            elif not success:
                if error and ("timeout" in error.lower() or "connection" in error.lower()):
                    classified["timeout_or_external"].append(r)
                else:
                    classified["misclassified_intent"].append(r)

        # Build summary sorted by impact (count * severity)
        severity_weights = {
            "misclassified_intent": 3,
            "timeout_or_external": 2,
            "fallback_resolved": 1.5,
            "semantic_regex_conflict": 2,
            "low_confidence_correct": 1,
            "language_mismatch": 2,
        }

        error_summary = []
        for category, items in sorted(
            classified.items(),
            key=lambda x: -len(x[1]) * severity_weights.get(x[0], 1),
        ):
            impact = len(items) * severity_weights.get(category, 1)
            # Most common intent in this category
            intent_counts = Counter(r.get("intent", "?") for r in items)
            top_intent = intent_counts.most_common(1)[0] if intent_counts else ("?", 0)

            error_summary.append({
                "category": category,
                "count": len(items),
                "impact_score": round(impact, 1),
                "top_intent": top_intent[0],
                "top_intent_count": top_intent[1],
                "avg_confidence": round(
                    sum(r.get("confidence", 0) for r in items) / max(len(items), 1), 3
                ),
                "suggestion": self._suggest_fix_for_category(category, items),
            })

        total_non_optimal = sum(len(v) for v in classified.values())
        return {
            "status": "ready",
            "total_records": len(records),
            "total_non_optimal": total_non_optimal,
            "optimal_rate": round(1 - total_non_optimal / max(len(records), 1), 4),
            "top_errors": error_summary[:3],
            "all_errors": error_summary,
            "by_category": {k: len(v) for k, v in classified.items()},
        }

    @staticmethod
    def _suggest_fix_for_category(category: str, items: List[Dict]) -> str:
        """Generate fix suggestion for an error category."""
        n = len(items)
        suggestions = {
            "misclassified_intent": (
                f"{n} clasificaciones incorrectas. Revisar patrones del "
                f"SemanticClassifier y _LOCAL_KEYWORDS. Priorizar frames "
                f"que cubran estos casos."
            ),
            "fallback_resolved": (
                f"{n} casos donde el fallback resolvió mejor que la ruta primaria. "
                f"La ruta primaria (resolve_local, resolve_identity) necesita "
                f"mejorar su resolución o el pipeline necesita mejor detección "
                f"de cuándo la ruta primaria no está respondiendo bien."
            ),
            "timeout_or_external": (
                f"{n} fallos por timeout o servicio externo. Considerar "
                f"aumentar timeouts, añadir cache, o mejorar fallback."
            ),
            "semantic_regex_conflict": (
                f"{n} conflictos entre capas semántica y regex. Expandir "
                f"cobertura de frames semánticos para reducir dependencia del regex."
            ),
            "low_confidence_correct": (
                f"{n} resoluciones correctas pero con confianza débil. "
                f"Añadir patrones más específicos para estos casos."
            ),
            "language_mismatch": (
                f"{n} respuestas en idioma incorrecto. Verificar que el "
                f"language_gate esté activo en todos los paths de salida."
            ),
        }
        return suggestions.get(category, f"{n} errores en categoría '{category}'.")

    # -- Generación de propuestas de mejora ----------------------------------

    def _generate_proposals(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Genera propuestas concretas de mejora basadas en datos reales.

        MODO PASIVO: genera propuestas, NUNCA aplica cambios.
        """
        proposals: List[Dict[str, Any]] = []

        # 1. Intents con alta tasa de fallo
        intent_dist = analysis.get("intent_distribution", {})
        for intent, stats in intent_dist.items():
            if (stats["total"] >= self.MIN_SAMPLES_PER_INTENT
                    and stats["failure_rate"] > self.FAILURE_RATE_ALERT):
                proposals.append({
                    "type": "high_failure_intent",
                    "priority": "high",
                    "intent": intent,
                    "failure_rate": stats["failure_rate"],
                    "total_samples": stats["total"],
                    "suggestion": (
                        f"Intent '{intent}' tiene {stats['failure_rate']:.0%} de fallos "
                        f"en {stats['total']} muestras. Revisar patrones de clasificación "
                        f"y considerar ajustar thresholds o añadir patrones."
                    ),
                })

        # 2. Alta tasa de fallbacks
        fb = analysis.get("fallback_analysis", {})
        if fb.get("fallback_rate", 0) > self.FALLBACK_RATE_ALERT:
            top_fb_intents = list(fb.get("by_intent", {}).items())[:3]
            proposals.append({
                "type": "high_fallback_rate",
                "priority": "medium",
                "fallback_rate": fb["fallback_rate"],
                "top_intents": top_fb_intents,
                "suggestion": (
                    f"Tasa de fallback global: {fb['fallback_rate']:.0%}. "
                    f"Los intents que más caen a fallback: "
                    f"{', '.join(f'{i}({c})' for i, c in top_fb_intents)}. "
                    f"Considerar mejorar clasificación primaria."
                ),
            })

        # 3. Alta ambigüedad
        amb = analysis.get("ambiguity_patterns", {})
        if amb.get("ambiguity_rate", 0) > self.AMBIGUITY_RATE_ALERT:
            proposals.append({
                "type": "high_ambiguity",
                "priority": "medium",
                "ambiguity_rate": amb["ambiguity_rate"],
                "avg_confidence": amb.get("avg_confidence", 0),
                "suggestion": (
                    f"Tasa de ambigüedad: {amb['ambiguity_rate']:.0%} "
                    f"(confianza promedio: {amb.get('avg_confidence', 0):.2f}). "
                    f"Considerar: bajar threshold semántico, añadir patrones regex, "
                    f"o entrenar con los ejemplos ambiguos frecuentes."
                ),
            })

        # 4. Threshold de confianza subóptimo
        conf = analysis.get("confidence_analysis", {})
        avg_success = conf.get("avg_success_confidence", 0)
        avg_failed = conf.get("avg_failed_confidence", 0)
        if avg_success > 0 and avg_failed > 0:
            gap = avg_success - avg_failed
            if gap < 0.15:
                proposals.append({
                    "type": "confidence_threshold_adjustment",
                    "priority": "low",
                    "avg_success_conf": avg_success,
                    "avg_failed_conf": avg_failed,
                    "gap": round(gap, 4),
                    "suggestion": (
                        f"La confianza promedio entre éxitos ({avg_success:.2f}) y fallos "
                        f"({avg_failed:.2f}) es muy cercana (gap={gap:.2f}). "
                        f"Ajustar el threshold de clasificación semántica puede mejorar "
                        f"la separación."
                    ),
                })

        # 5. Método de clasificación con peor rendimiento
        amb_methods = amb.get("by_classification_method", {})
        total_records = analysis.get("total_records", 1)
        for method, count in amb_methods.items():
            rate = count / total_records
            if rate > 0.10 and method:
                proposals.append({
                    "type": "weak_classification_method",
                    "priority": "medium",
                    "method": method,
                    "ambiguity_count": count,
                    "rate": round(rate, 4),
                    "suggestion": (
                        f"Método '{method}' genera {rate:.0%} de ambigüedades. "
                        f"Considerar mejorar patrones o añadir ejemplos de entrenamiento."
                    ),
                })

        return proposals

    # -- Propuestas de ajuste de thresholds ----------------------------------

    def suggest_threshold_adjustments(self) -> Dict[str, Any]:
        """
        Calcula thresholds óptimos basados en datos reales.

        SOLO sugiere, no aplica. El resultado debe pasar por proposal flow.
        """
        records = self._ledger.read_all()
        if len(records) < self.MIN_SAMPLES_FOR_ANALYSIS:
            return {"status": "insufficient_data", "total": len(records)}

        # Encontrar el threshold de confianza que maximiza separación
        success_confs = sorted(
            r.get("confidence", 0) for r in records if r.get("success")
        )
        failed_confs = sorted(
            r.get("confidence", 0) for r in records if not r.get("success")
        )

        if not success_confs or not failed_confs:
            return {"status": "no_contrast_data"}

        # Threshold óptimo: punto medio entre percentil 25 de éxitos
        # y percentil 75 de fallos
        p25_success = success_confs[len(success_confs) // 4]
        p75_failed = failed_confs[min(len(failed_confs) - 1, int(len(failed_confs) * 0.75))]
        suggested_threshold = round((p25_success + p75_failed) / 2, 3)

        current_threshold = 0.5  # valor actual en SmartRouter

        return {
            "status": "ready",
            "current_semantic_threshold": current_threshold,
            "suggested_semantic_threshold": suggested_threshold,
            "p25_success_confidence": round(p25_success, 4),
            "p75_failed_confidence": round(p75_failed, 4),
            "should_adjust": abs(suggested_threshold - current_threshold) > 0.05,
            "note": "Este ajuste debe aplicarse mediante proposal flow",
        }

    # -- Reporte compacto para CLI -------------------------------------------

    def generate_report(self) -> Dict[str, Any]:
        """
        Genera reporte compacto para `vx router-report`.

        Returns:
            Dict con secciones: summary, intents, routes, fallbacks,
            ambiguity, suggestions.
        """
        analysis = self.analyze()

        if analysis.get("status") == "insufficient_data":
            return analysis

        records = self._ledger.read_all()
        total = len(records)
        successes = sum(1 for r in records if r.get("success"))

        # Structural error classification
        error_report = self.classify_errors()

        return {
            "status": "ready",
            "summary": {
                "total_decisions": total,
                "success_rate": round(successes / total, 4) if total else 0,
                "optimal_rate": error_report.get("optimal_rate", 0),
                "period": analysis.get("period", {}),
            },
            "top_intents": dict(
                sorted(
                    analysis.get("intent_distribution", {}).items(),
                    key=lambda x: -x[1]["total"],
                )[:10]
            ),
            "quality_scores": analysis.get("quality_distribution", {}),
            "strategy_stats": analysis.get("strategy_stats", {}),
            "fallback_analysis": analysis.get("fallback_analysis", {}),
            "ambiguity_patterns": analysis.get("ambiguity_patterns", {}),
            "confidence": analysis.get("confidence_analysis", {}),
            "latency": analysis.get("latency_stats", {}),
            "improvement_proposals": analysis.get("improvement_proposals", []),
            "threshold_suggestions": self.suggest_threshold_adjustments(),
            "error_classification": error_report,
        }


# ---------------------------------------------------------------------------
# Convenience: crear feedback desde SmartRoute
# ---------------------------------------------------------------------------

def create_feedback_from_route(
    route: Any,
    success: bool,
    response_latency_ms: float = 0.0,
    used_fallback: bool = False,
    fallback_strategy: str = "",
    resolution_error: str = "",
    was_generic_response: bool = False,
    word_count: int = 0,
) -> RouterFeedback:
    """
    Crea un RouterFeedback a partir de un SmartRoute + resultado.

    NUNCA almacena contenido del mensaje. Solo métricas abstractas.
    """
    metadata = getattr(route, "metadata", {}) or {}
    signals = metadata.get("intent_signals", {})
    entities = signals.get("semantic_entities", [])

    feedback = RouterFeedback(
        intent=route.intent.value if hasattr(route.intent, "value") else str(route.intent),
        confidence=route.confidence,
        topic=route.topic,
        strategy=route.strategy.value if hasattr(route.strategy, "value") else str(route.strategy),
        risk_level=route.risk_level.value if hasattr(route.risk_level, "value") else str(route.risk_level),
        providers=route.providers,
        entities_count=len(entities),
        classification_method=signals.get("classification_method", "semantic"),
        success=success,
        used_fallback=used_fallback,
        fallback_strategy=fallback_strategy,
        response_latency_ms=response_latency_ms,
        resolution_error=resolution_error[:100] if resolution_error else "",
        was_generic_response=was_generic_response,
        had_ambiguity=route.confidence < PostResolutionEvaluator.AMBIGUITY_THRESHOLD,
        # Semantic decision path
        semantic_confidence=signals.get("semantic_confidence", 0.0),
        semantic_frame=signals.get("semantic_frame", ""),
        semantic_intent=signals.get("semantic_intent", ""),
        decision_note=signals.get("decision_note", ""),
        regex_agrees=signals.get("regex_agrees"),
        semantic_would_have_been=signals.get("semantic_would_have_been", ""),
        word_count=word_count,
        routing_latency_ms=metadata.get("routing_latency_ms", 0),
    )

    # Evaluar calidad
    evaluator = PostResolutionEvaluator()
    feedback.quality_score = evaluator.evaluate(feedback).value

    return feedback


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_ledger: Optional[RouterDecisionLedger] = None
_engine: Optional[RouterLearningEngine] = None
_evaluator: Optional[PostResolutionEvaluator] = None


def get_ledger() -> RouterDecisionLedger:
    global _ledger
    if _ledger is None:
        _ledger = RouterDecisionLedger()
    return _ledger


def get_learning_engine() -> RouterLearningEngine:
    global _engine
    if _engine is None:
        _engine = RouterLearningEngine()
    return _engine


def get_evaluator() -> PostResolutionEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = PostResolutionEvaluator()
    return _evaluator
