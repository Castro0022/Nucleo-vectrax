"""
core/nucleus/evidence_intent.py — De la pregunta del owner a la evidencia real
===============================================================================
Etapa de reunificación del Núcleo con su observación interna (2026-09-22).

QUÉ HACE
--------
Traduce una pregunta en lenguaje natural sobre el ESTADO INTERNO de Vectrax
a una consulta concreta contra `core.nucleus.internal_evidence`, y redacta la
respuesta A PARTIR DEL RESULTADO — nunca a partir del modelo.

POR QUÉ NO ES UN HARDCODE DE FRASES
------------------------------------
El requisito de la etapa es explícito: las frases de ejemplo («¿cuál fue tu
último diagnóstico?», «¿qué propuestas tienes pendientes?») son CRITERIOS DE
COMPORTAMIENTO, no patrones a cablear uno por uno. Por eso la clasificación
es TOPICAL: cada familia de evidencia declara sus anclas léxicas (sustantivos
del dominio: diagnóstico, propuesta, auditoría, convergencia, estrella,
patrón, dominio, motor…) y gana la familia con más anclas presentes. Una
frase nueva que hable de lo mismo entra sin tocar el código; una frase de
prueba memorizada no tiene ningún camino privilegiado.

LA REGLA ANTI-ALUCINACIÓN
-------------------------
`build_answer()` es determinista y sin LLM. El texto se construye EXCLUSIVA-
MENTE con campos del `EvidenceResult`. Si el resultado es EMPTY, STALE,
UNAVAILABLE o UNAUTHORIZED, la respuesta lo dice con esas palabras y NO
afirma ningún hecho. El LLM puede reformular después, pero no puede sustituir
esta consulta ni añadir hechos que la evidencia no contenga.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.nucleus.internal_evidence import (
    EvidenceResult,
    EvidenceStatus,
    InternalEvidence,
)

# Identificador de propuesta: IDEA-XXXXXXXX (formato de `core.idea_store`).
_IDEA_ID_RE = re.compile(r"\bIDEA[-_ ]?([A-Za-z0-9]{4,12})\b", re.IGNORECASE)

# Señal de que la pregunta apunta al PROPIO sistema y no al mundo exterior.
# "¿qué hiciste hoy en el mercado?" (interno) vs "¿cómo está el mercado?"
# (dato externo, ruta MARKET del router — que NO debe secuestrarse).
_SELF_DIRECTED_RE = re.compile(
    r"\b(?:tu|tus|tuyo|tuyas?|has|hiciste|hicist|haces|tienes|tenés|estás|estas|"
    r"detectaste|detectast|observas|observaste|propusiste|confirmaste|"
    r"ejecutaste|usaste|utilizaste|sabes|conoces|llevas|registraste|"
    r"your|you|did|have|are|do)\b",
    re.IGNORECASE,
)

# Anclas léxicas por familia de evidencia. Deliberadamente sustantivos del
# dominio, no frases completas.
_ANCHORS: Dict[str, Tuple[str, ...]] = {
    "diagnostic": (
        "diagnostico", "diagnostics", "diagnosis", "chequeo", "revision",
        "problema", "problemas", "fallo", "fallos", "falla", "fallas",
        "incidencia", "incidencias", "salud", "health", "issue", "issues",
    ),
    "proposal": (
        "propuesta", "propuestas", "proposal", "proposals", "idea", "ideas",
        "pendiente", "pendientes", "pending", "sugerencia", "sugerencias",
    ),
    "audit": (
        "auditoria", "auditorias", "audit", "ledger", "registro", "registros",
        "hipotesis", "hypothesis", "evento", "eventos", "event", "events",
    ),
    "engines": (
        "motor", "motores", "engine", "engines", "servicio", "servicios",
        "service", "services", "activo", "activos", "corriendo", "running",
    ),
    "universe": ("universo", "universe"),
    "stars": ("estrella", "estrellas", "star", "stars"),
    "patterns": ("patron", "patrones", "pattern", "patterns"),
    "domains": ("dominio", "dominios", "domain", "domains"),
    "convergences": (
        "convergencia", "convergencias", "convergence", "convergences",
    ),
    "trading": (
        "mercado", "market", "trading", "trade", "trades", "bolsa", "etoro",
        "operacion", "operaciones", "posicion", "posiciones",
    ),
    "freight": (
        "freight", "logistics", "logistica", "carga", "flete", "fletes",
        "transporte",
    ),
    "approval": ("aprobar", "aprobacion", "aprobada", "aprobadas", "approve", "approval"),
    "fallback": ("fallback", "respaldo"),
}

# Familias que además exigen señal auto-referencial para no secuestrar una
# consulta legítima de datos externos.
_REQUIRES_SELF_DIRECTED = frozenset({"trading", "engines"})

_STOP = frozenset({
    "que", "qué", "cual", "cuál", "cuales", "cuáles", "como", "cómo",
    "cuando", "cuándo", "cuanto", "cuántos", "cuantos", "donde", "dónde",
    "por", "para", "del", "las", "los", "una", "unos", "unas", "con",
    "the", "and", "for", "what", "which", "your", "you", "have", "has",
})


def _normalize(text: str) -> str:
    """Minúsculas sin acentos — para que «diagnóstico» y «diagnostico»
    activen la misma ancla sin duplicar cada entrada del léxico."""
    import unicodedata
    lowered = (text or "").lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", _normalize(text)) if t not in _STOP]


@dataclass
class EvidenceIntent:
    """Intención resuelta hacia una familia de evidencia interna."""

    family: str
    score: int = 0
    idea_id: str = ""
    matched: List[str] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return bool(self.family)


def classify(text: str) -> EvidenceIntent:
    """Clasifica topicalmente. Devuelve una intención vacía si el texto no
    pregunta por el estado interno de Vectrax."""
    raw = text or ""
    tokens = set(_tokens(raw))
    if not tokens:
        return EvidenceIntent(family="")

    # Un ID explícito es la señal más fuerte que existe: gana siempre.
    match = _IDEA_ID_RE.search(raw)
    if match:
        return EvidenceIntent(
            family="proposal_detail",
            score=99,
            idea_id=f"IDEA-{match.group(1).upper()}",
            matched=[match.group(0)],
        )

    self_directed = bool(_SELF_DIRECTED_RE.search(raw))

    best: Optional[EvidenceIntent] = None
    for family, anchors in _ANCHORS.items():
        hits = [a for a in anchors if a in tokens]
        if not hits:
            continue
        if family in _REQUIRES_SELF_DIRECTED and not self_directed:
            continue
        candidate = EvidenceIntent(family=family, score=len(hits), matched=hits)
        if best is None or candidate.score > best.score:
            best = candidate

    if best is None:
        return EvidenceIntent(family="")

    # `fallback` y `approval` no son fuentes propias: refinan otra familia.
    if best.family == "fallback":
        best.family = "proposal"
    elif best.family == "approval" and "proposal" not in best.matched:
        best.family = "approval_pipeline"
    return best


# ---------------------------------------------------------------------------
# Ejecución de la intención contra la fachada de evidencia
# ---------------------------------------------------------------------------

def fetch(intent: EvidenceIntent, evidence: InternalEvidence) -> EvidenceResult:
    """Ejecuta la consulta correspondiente. Nunca lanza."""
    family = intent.family
    if family == "proposal_detail":
        return evidence.proposal_by_id(intent.idea_id)
    if family == "proposal":
        return evidence.pending_proposals()
    if family == "diagnostic":
        return evidence.latest_diagnostic()
    if family == "audit":
        return evidence.recent_audit()
    if family == "engines":
        return evidence.engine_status()
    if family == "universe":
        return evidence.universe_summary()
    if family == "stars":
        return evidence.stars()
    if family == "patterns":
        return evidence.patterns()
    if family == "domains":
        return evidence.domains()
    if family == "convergences":
        return evidence.convergences()
    if family == "trading":
        return evidence.operational_activity("trading")
    if family == "freight":
        return evidence.operational_activity("freight_logistics")
    if family == "approval_pipeline":
        return evidence.approval_pipeline()
    return EvidenceResult(
        kind=family or "unknown",
        status=EvidenceStatus.UNAVAILABLE,
        source="core.nucleus.evidence_intent.fetch",
        detail=f"familia de evidencia sin lector: {family!r}",
    )


# ---------------------------------------------------------------------------
# Redacción determinista — sin LLM, sin hechos añadidos
# ---------------------------------------------------------------------------

_KIND_LABEL = {
    "diagnostic": "diagnóstico",
    "diagnostic_history": "historial de diagnósticos",
    "proposal": "propuestas",
    "audit": "auditoría",
    "engines": "motores y servicios",
    "universe": "universo",
    "stars": "estrellas",
    "patterns": "patrones",
    "domains": "dominios",
    "convergences": "convergencias",
    "operational": "actividad operativa",
    "approval_pipeline": "circuito de aprobación",
}


def _humanize_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "sin fecha registrada en la fuente"
    seconds = int(seconds)
    if seconds < 90:
        return f"hace {seconds}s"
    if seconds < 5400:
        return f"hace {seconds // 60} min"
    if seconds < 172800:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} días"


def build_answer(result: EvidenceResult) -> str:
    """Construye la respuesta SOLO con campos del resultado.

    Los cinco estados producen cinco textos distintos y reconocibles. Ninguna
    rama afirma un hecho que la evidencia no contenga, y ninguna convierte
    `pending` en resuelto ni una aprobación en una ejecución.
    """
    label = _KIND_LABEL.get(result.kind, result.kind)

    if result.status is EvidenceStatus.UNAUTHORIZED:
        return (
            f"No estás autorizado para consultar {label}. "
            "Esa información está restringida a la identidad del owner."
        )

    if result.status is EvidenceStatus.UNAVAILABLE:
        motivo = f" Motivo: {result.detail}." if result.detail else ""
        return (
            f"El servicio que provee {label} no está disponible ahora mismo, "
            f"así que no puedo responder con evidencia.{motivo} "
            f"Fuente consultada: {result.source}."
        )

    if result.status is EvidenceStatus.EMPTY:
        motivo = f" ({result.detail})" if result.detail else ""
        return (
            f"No tengo evidencia de {label}{motivo}. "
            f"La fuente {result.source} respondió sin registros; "
            "no voy a deducir un resultado que no observé."
        )

    lines: List[str] = []
    if result.status is EvidenceStatus.STALE:
        lines.append(
            f"⚠️ La evidencia de {label} está desactualizada: {result.detail}. "
            "La reporto igualmente, marcada como vieja:"
        )

    for item in result.items[:8]:
        age = _humanize_age(item.age_seconds)
        prefix = f"[{item.scope}] " if item.scope else ""
        ref = f" · ref: {item.reference}" if item.reference else ""
        estado = f" · estado: {item.status}" if item.status else ""
        lines.append(f"- {prefix}{item.summary}{estado} · observado {age}{ref}")

    if len(result.items) > 8:
        lines.append(f"- (+{len(result.items) - 8} más)")

    lines.append(f"Fuente: {result.source}.")
    return "\n".join(lines)


def describe_for_evidence_field(intent: EvidenceIntent, result: EvidenceResult) -> Dict[str, Any]:
    """Traza interna que viaja en `NucleusResponse.evidence` — permite auditar
    a posteriori qué se consultó, contra qué fuente y con qué resultado."""
    return {
        "kind": "internal_evidence",
        "family": intent.family,
        "intent_score": intent.score,
        "intent_matched": intent.matched,
        "evidence_status": result.status.value,
        "evidence_source": result.source,
        "evidence_detail": result.detail,
        "item_count": len(result.items),
        "items": [i.to_dict() for i in result.items[:8]],
    }
