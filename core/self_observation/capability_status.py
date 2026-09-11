"""
core/self_observation/capability_status.py — Estado de capacidad DERIVADO,
nunca escrito a mano.

Este módulo es la ÚNICA fuente de verdad de las tres categorías con las que
Vectrax habla de sí mismo:

    1. confirmada / operativa      (STATUS_CONFIRMED)
    2. existe, pero no confirmada  (STATUS_DECLARED)
    3. condicional                 (STATUS_CONDITIONAL)

Regla de diseño no negociable (motivo del ticket): las tres categorías NO se
escriben como texto fijo en ningún punto del pipeline. Se derivan del estado
real del sistema en CADA consulta:

    ESTADO REAL DEL SISTEMA → SELF_KNOWLEDGE → RESPUESTA NATURAL

y nunca:

    lista escrita a mano → prompt → respuesta

Consecuencia práctica: el día que una credencial aparezca o un módulo empiece
a importar, esa capacidad pasa de categoría 2/3 a categoría 1 SOLA, porque el
estado real cambió — sin editar una frase de "no puedo" a "puedo". Por eso
aquí no hay ninguna lista de nombres por categoría: solo la función de
derivación y el renderizado de lo que la derivación produjo.

Fuentes (todas existentes, ninguna nueva):
  - `core.self_observation.capability_context.build_capability_context()` —
    contrato compuesto verificado (exists/connected/authorized/health/
    condition) construido en vivo, sin caché.
  - `core.intent_ssot.resolve_intent()` — SSOT de intención, para saber si la
    consulta es de capacidad y con qué dominio/tarea.

No escribe nada, no activa nada, no llama al LLM. Nunca lanza.

Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from core.orchestration.bootstrap import (
    HEALTH_AVAILABLE,
    HEALTH_DEGRADED,
    HEALTH_UNAVAILABLE,
)
from core.self_observation.capability_context import (
    CapabilityContext,
    CapabilityEntry,
    build_capability_context,
)

logger = logging.getLogger("vectrax.self_observation.capability_status")


# ---------------------------------------------------------------------------
# Las tres categorías (y el detalle interno del que se derivan)
# ---------------------------------------------------------------------------

STATUS_CONFIRMED = "confirmed"      # 1) confirmada / operativa AHORA
STATUS_DECLARED = "declared"        # 2) existe, pero su operación no está confirmada
STATUS_CONDITIONAL = "conditional"  # 3) existe y funciona, pero condicionada

STATUS_ORDER = (STATUS_CONFIRMED, STATUS_DECLARED, STATUS_CONDITIONAL)

# Detalle de cuatro valores del contrato compuesto. Se conserva porque
# `capability_narrator` necesita distinguir "no conecta" de "conecta pero su
# health falla" al narrar; las tres categorías se derivan de él.
DETAIL_READY = "ready"
DETAIL_DEGRADED = "degraded"
DETAIL_UNAUTHORIZED = "unauthorized"
DETAIL_UNAVAILABLE = "unavailable"

_DETAIL_TO_STATUS: Dict[str, str] = {
    DETAIL_READY: STATUS_CONFIRMED,
    DETAIL_UNAUTHORIZED: STATUS_CONDITIONAL,
    DETAIL_DEGRADED: STATUS_DECLARED,
    DETAIL_UNAVAILABLE: STATUS_DECLARED,
}


def derive_detail(entry: CapabilityEntry) -> str:
    """Clasifica UNA entrada en exactamente uno de los cuatro detalles, a
    partir ÚNICAMENTE de los campos compuestos del contrato verificado:

      - DETAIL_UNAVAILABLE : no existe, no conecta, o health=UNAVAILABLE.
      - DETAIL_DEGRADED    : conecta pero health=DEGRADED (existe, sin confirmar).
      - DETAIL_UNAUTHORIZED: conecta + health=AVAILABLE pero authorized=False —
        la combinación compuesta que un enum único perdería.
      - DETAIL_READY       : existe, conecta, disponible y autorizada.

    Función pura: no consulta el entorno ni ninguna otra fuente. Nunca lanza.
    """
    try:
        if not entry.exists or not entry.connected or entry.health == HEALTH_UNAVAILABLE:
            return DETAIL_UNAVAILABLE
        if entry.health == HEALTH_DEGRADED:
            return DETAIL_DEGRADED
        if not entry.authorized:
            return DETAIL_UNAUTHORIZED
        if entry.health == HEALTH_AVAILABLE and entry.authorized:
            return DETAIL_READY
    except Exception as exc:  # pragma: no cover - defensivo
        logger.debug("derive_detail failed: %s", exc)
    return DETAIL_UNAVAILABLE  # combinación inesperada → honesto por defecto


def derive_status(entry: CapabilityEntry) -> str:
    """Categoría de las TRES (confirmed/declared/conditional) para `entry`.

    Derivada del detalle, que a su vez se deriva del estado verificado. No hay
    ninguna tabla nombre→categoría: la pertenencia se recalcula siempre.
    """
    return _DETAIL_TO_STATUS.get(derive_detail(entry), STATUS_DECLARED)


# ---------------------------------------------------------------------------
# Contrato de salida
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityStatusEntry:
    """Estado de UNA capacidad en el instante en que se consultó."""
    name: str
    kind: str
    group: str
    status: str        # STATUS_CONFIRMED | STATUS_DECLARED | STATUS_CONDITIONAL
    detail: str        # DETAIL_* (grano fino, para el narrador)
    condition: str     # NOMBRE de la condición pendiente (flag/credencial), nunca su valor
    observed_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "group": self.group,
            "status": self.status,
            "detail": self.detail,
            "condition": self.condition,
            "observed_at": self.observed_at,
        }


@dataclass
class CapabilityStatusReport:
    """Resultado de consultar el estado real de las capacidades."""
    entries: List[CapabilityStatusEntry] = field(default_factory=list)
    fallback_sources: List[str] = field(default_factory=list)
    query_capability: bool = False
    query_domain: Optional[str] = None
    query_task_type: Optional[str] = None
    generated_at: float = field(default_factory=time.time)

    def by_status(self) -> Dict[str, List[CapabilityStatusEntry]]:
        """Agrupa por categoría, con orden estable por nombre."""
        buckets: Dict[str, List[CapabilityStatusEntry]] = {s: [] for s in STATUS_ORDER}
        for entry in self.entries:
            buckets.setdefault(entry.status, []).append(entry)
        for names in buckets.values():
            names.sort(key=lambda e: e.name)
        return buckets

    def counts(self) -> Dict[str, int]:
        return {status: len(items) for status, items in self.by_status().items()}

    def status_of(self, name: str) -> Optional[CapabilityStatusEntry]:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def names(self, status: str) -> List[str]:
        return [e.name for e in self.by_status().get(status, [])]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "fallback_sources": self.fallback_sources,
            "query_capability": self.query_capability,
            "query_domain": self.query_domain,
            "query_task_type": self.query_task_type,
            "counts": self.counts(),
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True)
class _AnonymousDecision:
    """Decisión mínima para consultar estado sin pasar por el SSOT (p.ej. una
    consulta programática por nombre). Mismo `duck typing` que
    `IntentDecision` en los tres campos que `build_capability_context()` lee."""
    domain: str = ""
    task_type: str = ""
    capability_query: bool = False


# ---------------------------------------------------------------------------
# Mecanismo de consulta — estado real, en vivo, sin caché
# ---------------------------------------------------------------------------

def _resolve_decision(query: str, decision: Any) -> Any:
    if decision is not None:
        return decision
    if query:
        try:
            from core.intent_ssot import resolve_intent
            return resolve_intent(query)
        except Exception as exc:
            logger.debug("resolve_intent unavailable for status query: %s", exc)
    return _AnonymousDecision()


def _to_status_entry(entry: CapabilityEntry) -> CapabilityStatusEntry:
    detail = derive_detail(entry)
    return CapabilityStatusEntry(
        name=entry.name,
        kind=entry.kind,
        group=entry.group,
        status=_DETAIL_TO_STATUS.get(detail, STATUS_DECLARED),
        detail=detail,
        condition=getattr(entry, "condition", "") or "",
        observed_at=entry.observed_at,
    )


def query_capability_status(
    query: str = "",
    *,
    decision: Any = None,
    names: Optional[Iterable[str]] = None,
    context: Optional[CapabilityContext] = None,
) -> CapabilityStatusReport:
    """Consulta el estado REAL de las capacidades ahora mismo.

    Este es el mecanismo que sustituye a cualquier descripción fija: construye
    un `CapabilityContext` nuevo (sin caché, sin memoización) y deriva de él
    las tres categorías. Dos llamadas separadas por un cambio de entorno
    devuelven categorías distintas — que es exactamente el comportamiento que
    se pide.

    Args:
        query: texto del usuario, si lo hay (se resuelve con el SSOT de
            intención para saber dominio/tarea y si es pregunta de capacidad).
        decision: `IntentDecision` ya resuelta, para no re-clasificar.
        names: si se indica, filtra el reporte a esas capacidades.
        context: `CapabilityContext` ya construido en este mismo mensaje (p.ej.
            por el gate de observación del gateway) — se reutiliza para no
            repetir 48 health-checks en la misma consulta. Si se omite, se
            construye uno nuevo.

    Nunca lanza: ante cualquier fallo devuelve un reporte vacío, que los
    consumidores deben interpretar como "sin evidencia" (nunca como "todo
    bien" ni como "nada disponible").
    """
    try:
        resolved = _resolve_decision(query, decision)
        ctx = context if context is not None else build_capability_context(resolved)
        wanted = set(names) if names is not None else None
        entries = [
            _to_status_entry(e) for e in ctx.entries
            if wanted is None or e.name in wanted
        ]
        return CapabilityStatusReport(
            entries=entries,
            fallback_sources=list(ctx.fallback_sources),
            query_capability=bool(ctx.query_capability),
            query_domain=ctx.query_domain,
            query_task_type=ctx.query_task_type,
            generated_at=ctx.generated_at,
        )
    except Exception as exc:
        logger.debug("query_capability_status failed: %s", exc)
        return CapabilityStatusReport()


def get_capability_status(name: str) -> Optional[CapabilityStatusEntry]:
    """Estado real de UNA capacidad por nombre, o None si Vectrax no la
    declara. None significa "no la conozco" — nunca se inventa una entrada
    para un nombre desconocido."""
    if not name:
        return None
    report = query_capability_status(names=[name])
    return report.status_of(name)


def is_capability_available(name: str) -> bool:
    """True SOLO si la capacidad está confirmada y operativa ahora mismo.

    Sesgo deliberado: "no verificable" cuenta como no disponible, para que
    ninguna afirmación de capacidad se apoye en una suposición.
    """
    entry = get_capability_status(name)
    return entry is not None and entry.status == STATUS_CONFIRMED


# ---------------------------------------------------------------------------
# Renderizado para SELF_KNOWLEDGE — etiquetas de categoría, membresía derivada
# ---------------------------------------------------------------------------
# Lo único fijo aquí son las ETIQUETAS de las tres categorías y el idioma. Qué
# capacidad cae en cada una, y cuántas hay, sale siempre de la derivación de
# arriba. Nunca se nombra una condición concreta (flag/credencial) en el texto:
# eso es detalle interno y vive en el contrato estructurado.

_HEADER: Dict[str, str] = {
    "es": "[MIS CAPACIDADES — estado verificado en este instante, no una lista fija]",
    "en": "[MY CAPABILITIES — verified state right now, not a fixed list]",
}

_LABELS: Dict[str, Dict[str, str]] = {
    "es": {
        STATUS_CONFIRMED: "Confirmadas y operativas ahora ({n})",
        STATUS_DECLARED: "Existen pero sin confirmar ahora ({n})",
        STATUS_CONDITIONAL: "Condicionadas: existen y funcionan, pero hoy no están autorizadas ({n})",
    },
    "en": {
        STATUS_CONFIRMED: "Confirmed and operational right now ({n})",
        STATUS_DECLARED: "Exist but unconfirmed right now ({n})",
        STATUS_CONDITIONAL: "Conditional: exist and work, but not authorized today ({n})",
    },
}

_FALLBACK_LABEL: Dict[str, str] = {
    "es": "Alternativas verificadas para lo que falta",
    "en": "Verified fallbacks for what's missing",
}

_RULE: Dict[str, str] = {
    "es": (
        "No afirmes ninguna capacidad que no aparezca arriba, y no la afirmes "
        "en una categoría distinta a la suya: esto se recalcula en cada "
        "mensaje desde el estado real."
    ),
    "en": (
        "Do not claim any capability that isn't listed above, and don't move "
        "one to a different category: this is recomputed from real state on "
        "every message."
    ),
}

_MORE: Dict[str, str] = {"es": "+{n} más", "en": "+{n} more"}

_DEFAULT_LANG = "es"
# Presupuesto de nombres por categoría. Alto a propósito: con el inventario
# actual (48 motores + catálogo) cabe entero, de modo que el bloque no oculta
# capacidades reales; el límite solo existe para acotar un crecimiento
# patológico del prompt, y cuando recorta lo dice con el conteo derivado.
_DEFAULT_MAX_NAMES = 60


def render_status_block(
    report: CapabilityStatusReport,
    lang: str = _DEFAULT_LANG,
    max_names: int = _DEFAULT_MAX_NAMES,
) -> str:
    """Bloque de texto derivado del reporte. Cadena vacía si no hay evidencia
    (sin entradas) — nunca una frase inventada de relleno.

    Determinista y puro: mismo reporte → mismo texto.
    """
    if lang not in ("es", "en"):
        lang = _DEFAULT_LANG
    if report is None or not report.entries:
        return ""

    buckets = report.by_status()
    lines: List[str] = [_HEADER[lang]]
    for status in STATUS_ORDER:
        items = buckets.get(status) or []
        if not items:
            continue
        shown = [e.name for e in items[:max_names]]
        rest = len(items) - len(shown)
        if rest > 0:
            shown.append(_MORE[lang].format(n=rest))
        lines.append(f"{_LABELS[lang][status].format(n=len(items))}: {', '.join(shown)}")

    if report.fallback_sources:
        lines.append(
            f"{_FALLBACK_LABEL[lang]}: {', '.join(sorted(report.fallback_sources))}"
        )
    lines.append(_RULE[lang])
    return "\n".join(lines)


def build_capability_status_context(
    query: str = "",
    lang: str = _DEFAULT_LANG,
    *,
    decision: Any = None,
    context: Optional[CapabilityContext] = None,
    max_names: int = _DEFAULT_MAX_NAMES,
) -> str:
    """Punto de entrada para el auto-contexto (`vectrax/self_context.py`).

    Consulta el estado real y lo renderiza. Cadena vacía si no hay evidencia.
    Nunca lanza.
    """
    try:
        report = query_capability_status(query, decision=decision, context=context)
        return render_status_block(report, lang=lang, max_names=max_names)
    except Exception as exc:
        logger.debug("build_capability_status_context failed: %s", exc)
        return ""
