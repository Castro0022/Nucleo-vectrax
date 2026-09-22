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

# ---------------------------------------------------------------------------
# Señal de sujeto: ¿se habla de VECTRAX o del mundo del usuario?
# ---------------------------------------------------------------------------
# Un ancla léxica sola NO basta. Auditoría 2026-09-22: exigir solo el ancla
# secuestraba conversaciones legítimas, porque las mismas palabras describen
# el mundo del usuario:
#
#   "Tengo un problema con mi carro"        -> ancla `problema`  -> diagnóstico
#   "Dame ideas para mi negocio"            -> ancla `ideas`     -> propuestas
#   "Cuál es el dominio de esta función"    -> ancla `dominio`   -> dominios
#   "Quiero hacer una auditoria de mi empresa" -> ancla `auditoria` -> auditoría
#
# Las cuatro son conversación normal y ninguna pregunta por el estado interno
# de Vectrax. Por eso el ancla es condición NECESARIA pero no SUFICIENTE: hace
# falta además una señal explícita de que el sujeto es el propio sistema.

# (a) Segunda persona dirigida a Vectrax: "¿qué DETECTASTE?", "¿TIENES...?".
_SECOND_PERSON_RE = re.compile(
    r"\b(?:tu|tus|tuyo|tuyos|tuya|tuyas|contigo|ti|"
    r"has|hiciste|haces|tienes|tenés|tenes|estás|estas|estuviste|"
    r"detectaste|detectas|observas|observaste|observando|"
    r"propusiste|propones|confirmaste|confirmas|"
    r"ejecutaste|ejecutas|usaste|utilizaste|usas|utilizas|"
    r"sabes|conoces|llevas|registraste|registras|aprendiste|aprendes|"
    r"your|yours|you|did\s+you|have\s+you|are\s+you|do\s+you)\b",
    re.IGNORECASE,
)

# (b) Mención explícita del sistema.
_SYSTEM_MENTION_RE = re.compile(
    r"\b(?:vectrax|n[úu]cleo|nucleus|el\s+sistema|the\s+system)\b",
    re.IGNORECASE,
)

# (c) Vocabulario de GOBERNANZA: describe la cola de revisión del propio
# Vectrax y no tiene lectura en el mundo del usuario ("propuestas sin
# revisar" es la bandeja del owner, no una tarea personal).
_GOVERNANCE_RE = re.compile(
    r"\b(?:sin\s+(?:revisar|aprobar|resolver)|"
    r"por\s+(?:revisar|aprobar)|"
    r"pendientes?\s+de\s+(?:revisi[óo]n|aprobaci[óo]n)|"
    r"pendientes?)\b",
    re.IGNORECASE,
)

# (d) Vocabulario de PROCESO sobre el circuito de aprobación. Cuando aparece
# junto a las anclas de aprobación, la pregunta es sobre el MECANISMO
# ("¿qué sucede después de aprobar una propuesta?"), no sobre la cola.
_PROCESS_RE = re.compile(
    r"\b(?:qu[ée]\s+(?:sucede|pasa|ocurre|hace)|c[óo]mo\s+funciona|"
    r"despu[ée]s\s+de|tras\s+(?:aprobar|la\s+aprobaci[óo]n)|bot[óo]n|"
    r"what\s+happens|how\s+does)\b",
    re.IGNORECASE,
)

# (e) Sujeto AJENO: posesivo de primera persona. "mi negocio", "mis ventas",
# "mi empresa" delimitan el mundo del usuario. Bloquea salvo que el texto
# nombre explícitamente a Vectrax ("los dominios de mi Vectrax").
_FOREIGN_SUBJECT_RE = re.compile(
    r"\b(?:mi|mis|m[íi]o|m[íi]a|m[íi]os|m[íi]as|nuestro|nuestra|nuestros|"
    r"nuestras|my|our|ours)\b",
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

# (f) Nombres de dominio REGISTRADOS en Vectrax. Preguntar "¿qué está
# ocurriendo en Freight Logistics?" nombra un dominio operativo propio, así
# que la mención vale por sí misma como señal de sujeto. Se leen del
# catálogo real (`config/domain_templates/`), no de una lista inventada aquí.
def _registered_domain_tokens() -> frozenset:
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    templates = os.path.join(root, "config", "domain_templates")
    names = set()
    try:
        for entry in os.listdir(templates):
            if not entry.endswith(".json"):
                continue
            for part in os.path.splitext(entry)[0].split("_"):
                if len(part) > 3:
                    names.add(_normalize(part))
    except OSError:
        pass
    return frozenset(names)

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
    subject_reason: str = ""   # por qué se decidió que el sujeto es (o no) Vectrax

    @property
    def detected(self) -> bool:
        return bool(self.family)


def _subject_is_vectrax(raw: str, tokens: set) -> Tuple[bool, str]:
    """¿El sujeto de la frase es el propio Vectrax? Devuelve (sí/no, motivo).

    Regla de dos partes:
      1. Debe haber al menos una señal positiva de que se habla del sistema.
      2. Un posesivo de primera persona ("mi empresa") delimita el mundo del
         usuario y bloquea, salvo que el texto nombre a Vectrax de forma
         explícita.
    """
    if _SYSTEM_MENTION_RE.search(raw):
        return True, "mención explícita del sistema"

    if _FOREIGN_SUBJECT_RE.search(raw):
        return False, "posesivo de primera persona: el sujeto es el usuario"

    if _SECOND_PERSON_RE.search(raw):
        return True, "segunda persona dirigida a Vectrax"
    if _GOVERNANCE_RE.search(raw):
        return True, "vocabulario de gobernanza (cola de revisión propia)"
    if tokens & _registered_domain_tokens():
        return True, "nombre de un dominio registrado en Vectrax"

    # Preguntar por el MECANISMO de aprobación ("¿qué sucede después de
    # aprobar una propuesta?", "¿qué hace el botón Aprobar?") solo tiene
    # sentido sobre Vectrax. Se exigen TRES elementos, no dos: ancla de
    # aprobación, vocabulario de proceso Y un objeto que sea de Vectrax (una
    # propuesta/idea, o el propio botón). Sin el tercero, "cómo funciona la
    # aprobación de un préstamo" también entraba — se comprobó.
    if (tokens & set(_ANCHORS["approval"])) and _PROCESS_RE.search(raw):
        own_object = (tokens & set(_ANCHORS["proposal"])) or {"boton", "button"} & tokens
        if own_object:
            return True, "pregunta por el mecanismo de aprobación de Vectrax"

    return False, "sin señal de que el sujeto sea Vectrax"


def classify(text: str) -> EvidenceIntent:
    """Clasifica topicalmente. Devuelve una intención vacía si el texto no
    pregunta por el estado interno de Vectrax.

    Exige DOS condiciones, no una: un ancla léxica del dominio Y una señal de
    que el sujeto es el propio Vectrax (ver `_subject_is_vectrax`). Con solo
    el ancla se secuestraban conversaciones normales — auditoría 2026-09-22.
    """
    raw = text or ""
    tokens = set(_tokens(raw))
    if not tokens:
        return EvidenceIntent(family="")

    # Un ID explícito es la señal más fuerte que existe: gana siempre, y vale
    # por sí mismo como señal de sujeto (IDEA-xxxx solo existe en Vectrax).
    match = _IDEA_ID_RE.search(raw)
    if match:
        return EvidenceIntent(
            family="proposal_detail",
            score=99,
            idea_id=f"IDEA-{match.group(1).upper()}",
            matched=[match.group(0)],
            subject_reason="identificador IDEA-... explícito",
        )

    is_internal, reason = _subject_is_vectrax(raw, tokens)
    if not is_internal:
        return EvidenceIntent(family="", subject_reason=reason)

    best: Optional[EvidenceIntent] = None
    for family, anchors in _ANCHORS.items():
        hits = [a for a in anchors if a in tokens]
        if not hits:
            continue
        candidate = EvidenceIntent(family=family, score=len(hits), matched=hits)
        if best is None or candidate.score > best.score:
            best = candidate

    if best is None:
        return EvidenceIntent(family="", subject_reason="sin ancla de evidencia interna")

    best.subject_reason = reason

    # El circuito de aprobación gana sobre la cola de propuestas cuando la
    # pregunta es por el MECANISMO ("¿qué sucede después de aprobar una
    # propuesta?"): antes caía en `proposal` y respondía con la cola, que no
    # es lo que se preguntó.
    approval_hits = [a for a in _ANCHORS["approval"] if a in tokens]
    if approval_hits and _PROCESS_RE.search(raw):
        best.family = "approval_pipeline"
        best.matched = approval_hits
        return best

    # `fallback` y `approval` no son fuentes propias: refinan otra familia.
    if best.family == "fallback":
        best.family = "proposal"
    elif best.family == "approval":
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
