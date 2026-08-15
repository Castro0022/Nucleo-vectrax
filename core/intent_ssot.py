"""
Vectrax — SSOT de Intent (Fase 2)
===================================
Punto único de resolución de intención. Los 5 clasificadores que hoy
operan de forma independiente (SmartRouter, SemanticClassifier,
criterion.detect_domain, voice/intent, task_classifier) pasan a ser
proveedores de evidencia de una única `IntentDecision`, en vez de decidir
cada uno por su cuenta y alimentar call sites distintos sin verse entre sí.

Principio de diseño — NO es un sexto clasificador:
  Este módulo no clasifica nada por sí mismo. `resolve_intent()` reutiliza
  DIRECTAMENTE la fusión semántico+regex que `SmartRouter._resolve_intent()`
  ya implementa y que incluye 4 reglas afinadas empíricamente sobre
  conflictos reales (ver docstring de `_gather_primary_intent_evidence`).
  Esas reglas se PRESERVAN exactamente — no se reescriben, no se limpian,
  no se "simplifican". Domain/task_type/voice_intent son ejes de evidencia
  independientes, recolectados de sus fuentes existentes sin tocarlas.

Import lazy en todo punto de contacto con `core.smart_router` — evita el
ciclo de import con `SmartRouter.route()`, que en este mismo paso (Fase 2,
paso 5) pasa a consumir `resolve_intent()` en vez de clasificar por su
cuenta. `SmartRouter.classify_intent()` sigue intacto como proveedor de
evidencia; nunca llama a `resolve_intent()` (evitaría la recursión).

Creado: 2026-08-14
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.intent_ssot")


# ---------------------------------------------------------------------------
# Evidence — normaliza la salida de cada clasificador, sin tocar su lógica
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    """Un dato de evidencia aportado por UNO de los clasificadores existentes."""
    source: str          # p.ej. "smart_router", "semantic_classifier", "criterion_domain",
                          # "voice_intent", "task_classifier"
    claim: str            # el valor que ese proveedor propone (intent/domain/task/tono)
    confidence: float     # 0.0 - 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "claim": self.claim,
            "confidence": round(self.confidence, 4),
        }


# ---------------------------------------------------------------------------
# IntentDecision — resultado agregado
# ---------------------------------------------------------------------------

@dataclass
class IntentDecision:
    """
    Decisión unificada de intención para un mensaje.

    primary_intent — valor de `core.smart_router.Intent` (memory/local/online/
                     market/place_search/cognitive/identity/...), decidido
                     por la fusión semántico+regex YA existente de SmartRouter.
    domain         — dominio de conocimiento (market, freight_logistics, ...),
                     misma prioridad que ya usa external_gateway._resolve_query_domain
                     (semántico si conf >= umbral, si no léxico determinista).
    task_type      — valor de `core.routing.task_classifier.TaskType`, para
                     routing de capacidad de modelo.
    voice_intent   — valor crudo de `core.voice.intent.classify_intent()`
                     (greeting/casual/technical/...), el string exacto que
                     `ScaleGovernor.select_minimal_pipeline(intent=...)` espera.
    confidence     — confianza del primary_intent (la misma que ya calcula
                     SmartRouter internamente).
    evidence       — lista de `Evidence`, una por proveedor que respondió.
    conflicts      — descripciones legibles de desacuerdos detectados entre
                     proveedores (p.ej. semántico vs regex en primary_intent).
    raw_signals    — el mismo dict de señales que `SmartRouter.classify_intent()`
                     devolvería para el mismo texto (comando detectado, o
                     semantic_*/regex_*/resolución de `_resolve_intent()`).
                     Lo consume `SmartRouter.route()` (Fase 2, paso 5) para
                     alimentar `select_strategy()`/telemetría/`router_learning`
                     sin recalcular la clasificación. No es parte del
                     contrato ligero cross-módulo (usar `evidence` para eso);
                     es detalle de implementación que route() necesita para
                     preservar su contrato exacto.
    """
    primary_intent: str
    domain: str = ""
    task_type: str = ""
    voice_intent: str = ""
    confidence: float = 0.0
    evidence: List[Evidence] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    raw_signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_intent": self.primary_intent,
            "domain": self.domain,
            "task_type": self.task_type,
            "voice_intent": self.voice_intent,
            "confidence": round(self.confidence, 4),
            "evidence": [e.to_dict() for e in self.evidence],
            "conflicts": self.conflicts,
            "raw_signals": self.raw_signals,
        }

    def summary(self) -> str:
        return (
            f"IntentDecision[intent={self.primary_intent} domain={self.domain or '-'} "
            f"task={self.task_type or '-'} voice={self.voice_intent or '-'} "
            f"conf={self.confidence:.2f} conflicts={len(self.conflicts)}]"
        )


# ---------------------------------------------------------------------------
# Umbral de dominio — mismo que external_gateway._domain_gate_floor()
# ---------------------------------------------------------------------------

def _domain_floor() -> float:
    import os
    raw = os.getenv(
        "VX_SEMANTIC_DOMAIN_GATE_FLOOR",
        os.getenv("VX_SEMANTIC_DOMAIN_FLOOR", "0.45"),
    )
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.45


def _semantic_domain_enabled() -> bool:
    """True si el paso semántico de dominio está activo (VX_SEMANTIC_DOMAIN).
    Mismo flag que ya gobierna el fallback semántico dentro de
    `semantic_classifier._detect_domain` y que antes duplicaba
    `external_gateway._semantic_domain_enabled()`."""
    import os
    return os.getenv("VX_SEMANTIC_DOMAIN", "1") not in ("0", "false", "False", "")


# ---------------------------------------------------------------------------
# Evidencia 1+2: primary_intent — reutiliza la fusión YA existente de
# SmartRouter (semántico + regex), sin reescribirla.
# ---------------------------------------------------------------------------
# Las 4 reglas de `SmartRouter._resolve_intent()` que este wrapper preserva
# EXACTAMENTE (no son bugs, son correcciones ya validadas en producción):
#   1. Override COGNITIVE: si regex detecta COGNITIVE y sem_conf < 0.7,
#      regex gana sobre semántico (patrón + longitud es más específico).
#   2. Override MARKET: si regex detecta MARKET, siempre gana — el
#      clasificador semántico no conoce ese dominio.
#   3. Rescate memory→online: semántico=memory + regex=online + sem_conf>0
#      → semántico gana (resolvió 78 conflictos reales, IDEA-6B42AC11).
#   4. Rescate local→memory: semántico=local + regex=memory + sem_conf>0.25
#      → semántico gana (resolvió 10 conflictos reales, IDEA-316106FF).

def _gather_primary_intent_evidence(
    text: str,
) -> Tuple[Optional[str], float, List[Evidence], List[str], Optional[Any], Dict[str, Any]]:
    """
    Devuelve (primary_intent, confidence, evidence[], conflicts[], semantic_result,
    raw_signals).

    semantic_result se devuelve también para que _gather_domain_evidence lo
    reutilice sin volver a llamar al clasificador semántico (evita encodear
    dos veces el mismo texto).

    raw_signals es el mismo dict de señales que produciría
    `SmartRouter.classify_intent()` para el mismo texto — lo necesita
    `SmartRouter.route()` (Fase 2, paso 5) para alimentar `select_strategy()`
    sin recalcular la clasificación.

    Comandos explícitos (/ai, /multi, /router, ...) se detectan PRIMERO,
    reutilizando `SmartRouter._classify_command()` — la MISMA primera capa
    que `classify_intent()` evalúa antes de tocar semántico/regex. Un
    comando nunca llega a clasificación semántica (igual que en
    `classify_intent()`), así que se resuelve y retorna aquí sin ejecutar el
    resto de la fusión.
    """
    evidence: List[Evidence] = []
    conflicts: List[str] = []
    semantic_result = None
    stripped = text.strip()

    # --- Comandos explícitos: MISMA detección de primera capa que
    # SmartRouter.classify_intent() usa antes de tocar semántico/regex.
    try:
        from core.smart_router import SmartRouter as _CmdRouterCls
        cmd_result = _CmdRouterCls()._classify_command(stripped)
    except Exception as exc:
        logger.debug("smart_router command detection unavailable: %s", exc)
        cmd_result = None
    if cmd_result is not None:
        cmd_intent, cmd_signals = cmd_result
        evidence.append(Evidence(
            source="smart_router_command",
            claim=cmd_intent.value,
            confidence=1.0,
        ))
        return cmd_intent.value, 1.0, evidence, conflicts, None, cmd_signals

    try:
        from core.semantic_classifier import get_semantic_classifier
        semantic_result = get_semantic_classifier().classify(text)
    except Exception as exc:
        logger.debug("semantic_classifier no disponible: %s", exc)
        semantic_result = None

    if semantic_result is not None:
        evidence.append(Evidence(
            source="semantic_classifier",
            claim=semantic_result.intent.value,
            confidence=semantic_result.confidence,
        ))

    try:
        from core.smart_router import SmartRouter, _SEMANTIC_TO_INTENT

        router = SmartRouter()
        regex_intent, regex_signals = router._classify_regex(stripped)
        evidence.append(Evidence(
            source="smart_router_regex",
            claim=regex_intent.value,
            confidence=0.6,
        ))

        semantic_intent = None
        if semantic_result is not None:
            semantic_intent = _SEMANTIC_TO_INTENT.get(semantic_result.intent)

        # Réplica exacta del orden en que classify_intent() construye
        # signals: semantic_* primero (si hay semantic_result), luego
        # regex_signals, luego lo que _resolve_intent() añada/mute.
        signals: Dict[str, Any] = {}
        if semantic_result is not None:
            signals["semantic_intent"] = semantic_result.intent.value
            signals["semantic_confidence"] = semantic_result.confidence
            signals["semantic_frame"] = semantic_result.frame
            signals["semantic_entities"] = [
                {"type": e.type, "value": e.value}
                for e in semantic_result.entities
            ]
            signals["semantic_tools"] = semantic_result.suggested_tools
            signals["semantic_scores"] = semantic_result.scores
        signals.update(regex_signals)

        final_intent, signals = router._resolve_intent(
            semantic_result, semantic_intent,
            regex_intent, regex_signals,
            signals, stripped,
        )

        note = signals.get("decision_note", "")
        if note and ("conflict" in note or "override" in note or "rescue" in note):
            conflicts.append(f"primary_intent: {note}")
        if signals.get("regex_agrees") is False:
            conflicts.append(
                f"primary_intent: semantic vs regex disagreed "
                f"(method={signals.get('classification_method', '?')})"
            )

        confidence = (
            semantic_result.confidence
            if semantic_result is not None and final_intent == semantic_intent
            else 0.6
        )
        return final_intent.value, confidence, evidence, conflicts, semantic_result, signals

    except Exception as exc:
        logger.debug("smart_router fusion unavailable, falling back: %s", exc)
        # Fail-safe: si SmartRouter no está disponible, usar lo mejor que
        # tengamos (semántico si respondió, si no ninguna decisión).
        if semantic_result is not None:
            try:
                from core.smart_router import _SEMANTIC_TO_INTENT
                mapped = _SEMANTIC_TO_INTENT.get(semantic_result.intent)
                if mapped is not None:
                    return mapped.value, semantic_result.confidence, evidence, conflicts, semantic_result, {}
            except Exception:
                pass
        return None, 0.0, evidence, conflicts, semantic_result, {}


# ---------------------------------------------------------------------------
# Evidencia 3: domain — misma prioridad que external_gateway._resolve_query_domain
# ---------------------------------------------------------------------------

def _gather_domain_evidence(text: str, semantic_result: Optional[Any]) -> Tuple[str, List[Evidence]]:
    evidence: List[Evidence] = []

    dom_semantic = ""
    conf_semantic = 0.0
    if semantic_result is not None:
        dom_semantic = (getattr(semantic_result, "domain", "") or "").strip()
        conf_semantic = float(getattr(semantic_result, "domain_confidence", 0.0) or 0.0)
        if dom_semantic:
            evidence.append(Evidence(
                source="semantic_classifier_domain", claim=dom_semantic, confidence=conf_semantic,
            ))

    try:
        from core.learn.criterion import detect_domain
        dom_lexical = detect_domain(text)
    except Exception as exc:
        logger.debug("criterion.detect_domain unavailable: %s", exc)
        dom_lexical = None
    if dom_lexical:
        evidence.append(Evidence(source="criterion_domain", claim=dom_lexical, confidence=0.5))

    if dom_semantic and conf_semantic >= _domain_floor():
        return dom_semantic, evidence
    if dom_lexical:
        return dom_lexical, evidence
    return "", evidence


# ---------------------------------------------------------------------------
# resolve_domain — atajo público de un solo eje (dominio), para call sites
# que hoy no necesitan primary_intent/task_type/voice_intent (p.ej. el gate
# de criterio en external_gateway). Reutiliza EXACTAMENTE la misma evidencia
# y prioridad (semántico si domain_confidence >= umbral, si no léxico
# determinista, si no ninguno) que expone `IntentDecision.domain`, sin pagar
# el costo de resolver los otros 3 ejes cuando no hacen falta.
# ---------------------------------------------------------------------------

def resolve_domain(text: str) -> Tuple[str, float, str]:
    """Resuelve el dominio de conocimiento de `text` → (domain, confidence, source).

    source ∈ {"semantic", "keyword", "none"}. Migración de
    `external_gateway._resolve_query_domain()` (Fase 2, paso 2): mismo
    comportamiento observable, ahora respaldado por la evidencia única del
    SSOT en vez de una copia paralela de la misma lógica de prioridad.
    Defensivo: nunca lanza.
    """
    if not text or not text.strip():
        return "", 0.0, "none"

    semantic_result = None
    if _semantic_domain_enabled():
        try:
            from core.semantic_classifier import classify as _sem_classify
            semantic_result = _sem_classify(text)
        except Exception as exc:
            logger.debug("semantic_classifier no disponible (resolve_domain): %s", exc)
            semantic_result = None

    domain, evidence = _gather_domain_evidence(text, semantic_result)
    if not domain:
        return "", 0.0, "none"

    for ev in evidence:
        if ev.claim == domain and ev.source == "semantic_classifier_domain":
            return domain, ev.confidence, "semantic"
    for ev in evidence:
        if ev.claim == domain and ev.source == "criterion_domain":
            return domain, ev.confidence, "keyword"
    return domain, 0.0, "keyword"


# ---------------------------------------------------------------------------
# Evidencia 4: task_type (routing de capacidad de modelo)
# ---------------------------------------------------------------------------

def _gather_task_type_evidence(text: str) -> Tuple[str, List[Evidence]]:
    try:
        from core.routing.task_classifier import classify_task
        tc = classify_task(text)
        return tc.task_type.value, [Evidence(
            source="task_classifier", claim=tc.task_type.value, confidence=tc.confidence,
        )]
    except Exception as exc:
        logger.debug("task_classifier unavailable: %s", exc)
        return "", []


# ---------------------------------------------------------------------------
# resolve_task_classification — atajo público de alta fidelidad (Fase 2,
# paso 4: migración de ModelRouter.route(), único caller de classify_task()).
# A diferencia de IntentDecision.task_type (string plano, el eje liviano que
# expone resolve_intent()), este atajo devuelve el objeto TaskClassification
# COMPLETO (task_type + confidence + features + method) porque ModelRouter
# necesita las cuatro cosas: confidence/method alimentan
# AdaptiveAutonomyPolicy (OperationEnvelope) y el objeto entero queda
# embebido en RoutingDecision.task_classification (contrato público ya
# consumido por tests/otros módulos vía .task_type.value, .confidence,
# .features, .method). Ambos ejes (el string de IntentDecision.task_type y
# este objeto completo) provienen SIEMPRE de la misma llamada subyacente a
# classify_task() — nunca pueden discrepar para el mismo texto.
# ---------------------------------------------------------------------------

def resolve_task_classification(text: str, context: Optional[str] = None):
    """Resuelve la clasificación de tarea completa (TaskType + confidence +
    features + method) de `text`/`context`, delegando en
    `core.routing.task_classifier.classify_task()` sin duplicar su lógica.

    Devuelve el mismo tipo `TaskClassification` (mismo contrato exacto) que
    `classify_task()` ya devolvía. No es defensivo a propósito: preserva el
    comportamiento exacto que tenía `ModelRouter.route()` al llamar a
    `classify_task()` directamente (propaga cualquier excepción en vez de
    introducir un fallback silencioso nuevo que esa llamada no tenía).
    """
    from core.routing.task_classifier import classify_task
    return classify_task(text, context)


# ---------------------------------------------------------------------------
# Evidencia 5: voice_intent (perfil de pipeline/modelo del Scale Governor)
# ---------------------------------------------------------------------------

def _gather_voice_intent_evidence(text: str) -> Tuple[str, List[Evidence]]:
    try:
        from core.voice.intent import classify_intent as _voice_classify_intent
        value = _voice_classify_intent(text)
        return value, [Evidence(source="voice_intent", claim=value, confidence=0.7)]
    except Exception as exc:
        logger.debug("voice/intent unavailable: %s", exc)
        return "", []


# ---------------------------------------------------------------------------
# resolve_voice_intent — atajo público de un solo eje (Fase 2, paso 3:
# migración de pipeline_worker._quick_intent()). Mismo string crudo que
# `IntentDecision.voice_intent`, sin pagar el costo de resolver
# primary_intent/domain/task_type cuando el preflight del Scale Governor solo
# necesita el perfil de pipeline/modelo. `_quick_intent()` tenía su propio
# fallback "casual" ante cualquier fallo (incl. clasificador caído); se
# preserva aquí exactamente para no cambiar el comportamiento observable.
# ---------------------------------------------------------------------------

def resolve_voice_intent(text: str) -> str:
    """Resuelve el voice_intent (taxonomía cruda de `core.voice.intent`, el
    string exacto que `ScaleGovernor.select_minimal_pipeline(intent=...)` ya
    espera) de `text`. Defensivo: nunca lanza, cae a "casual" si el
    clasificador subyacente falla o no está disponible — mismo fallback que
    tenía `pipeline_worker._quick_intent()` antes de esta migración.
    """
    value, _evidence = _gather_voice_intent_evidence(text or "")
    return value or "casual"


# ---------------------------------------------------------------------------
# resolve_intent — punto único de resolución
# ---------------------------------------------------------------------------

def resolve_intent(text: str, channel: str = "user", owner: str = "") -> IntentDecision:
    """
    Resuelve la intención unificada de un mensaje agregando evidencia de los
    5 clasificadores existentes. Nunca lanza — un proveedor caído solo deja
    su campo vacío, nunca bloquea a los demás.

    channel/owner se aceptan para paridad de firma con SmartRouter.route()
    (Fase 2, paso 5). No afectan la resolución de primary_intent/domain/
    task_type/voice_intent — igual que antes, `SmartRouter.classify_intent()`
    tampoco los usaba; es `detect_context()` quien los consume para
    tópico/riesgo, no la resolución de intención.
    """
    if not text or not text.strip():
        return IntentDecision(primary_intent="", confidence=0.0)

    primary_intent, confidence, intent_evidence, conflicts, semantic_result, raw_signals = (
        _gather_primary_intent_evidence(text)
    )

    domain, domain_evidence = _gather_domain_evidence(text, semantic_result)
    task_type, task_evidence = _gather_task_type_evidence(text)
    voice_intent, voice_evidence = _gather_voice_intent_evidence(text)

    all_evidence = intent_evidence + domain_evidence + task_evidence + voice_evidence

    decision = IntentDecision(
        primary_intent=primary_intent or "",
        domain=domain,
        task_type=task_type,
        voice_intent=voice_intent,
        confidence=confidence,
        evidence=all_evidence,
        conflicts=conflicts,
        raw_signals=raw_signals or {},
    )
    logger.info("resolve_intent: %s", decision.summary())
    return decision
