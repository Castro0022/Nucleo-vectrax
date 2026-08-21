"""
core/self_observation/capability_narrator.py — Narrador de capacidades
(Fase 3, paso 6).

Convierte un `CapabilityContext` (contrato de datos determinista, ver
`core/self_observation/capability_context.py`) en texto natural — SIN LLM,
SIN inventar nada. Cada frase que produce `narrate()` es trazable a un campo
real de una `CapabilityEntry` (`exists`/`connected`/`authorized`/`health`) o
a `fallback_sources`. Nunca lee `entry.reason` (puede contener detalle de
excepciones) ni ningún otro dato técnico — solo `name`/`health`/
`connected`/`authorized`/`exists`, que son suficientes para narrar con
precisión sin exponer internals.

Función PURA y determinista: mismo `CapabilityContext` → mismo texto,
siempre. No consulta ninguna otra fuente (ni engine_registry, ni
IntentDecision, ni el ledger) — solo lo que ya está en el objeto recibido.
No decide nada (no clasifica intención, no arma el contrato): eso ya lo
hizo `build_capability_context()` antes de llegar aquí.

Todavía NO está conectado al pipeline de mensajes — eso es el paso 7,
pendiente, detrás de un flag que tampoco existe todavía en el código.

Creador: Mario Bravo Castro
"""

from __future__ import annotations

from typing import Dict, List

from core.orchestration.bootstrap import HEALTH_AVAILABLE, HEALTH_DEGRADED, HEALTH_UNAVAILABLE
from core.self_observation.capability_context import CapabilityContext, CapabilityEntry

_DEFAULT_LANG = "es"

_NO_EVIDENCE: Dict[str, str] = {
    "es": (
        "No tengo evidencia suficiente sobre mis capacidades en este "
        "momento — prefiero no afirmar nada que no pueda verificar."
    ),
    "en": (
        "I don't have enough evidence about my capabilities right now — "
        "I'd rather not claim anything I can't verify."
    ),
}

_SUMMARY_TEMPLATE: Dict[str, str] = {
    "es": "Tengo {ready}/{total} capacidades conectadas, disponibles y autorizadas ahora mismo.",
    "en": "I have {ready}/{total} capabilities connected, available and authorized right now.",
}

_GAP_TEMPLATES: Dict[str, Dict[str, str]] = {
    "es": {
        "unavailable": "Sin conexión verificada todavía: {names}.",
        "degraded": "Conectado pero con el chequeo de salud fallando ahora mismo: {names}.",
        "unauthorized": "Existe y funciona, pero sin autorización para usarse ahora: {names}.",
    },
    "en": {
        "unavailable": "No verified connection yet: {names}.",
        "degraded": "Connected but failing its health check right now: {names}.",
        "unauthorized": "Exists and works, but not authorized to use right now: {names}.",
    },
}

_FALLBACK_TEMPLATES: Dict[str, str] = {
    "es": "Para lo que me falta, puedo apoyarme en: {names}.",
    "en": "For what's missing, I can rely on: {names}.",
}

_NO_FALLBACK: Dict[str, str] = {
    "es": "No tengo ahora mismo una alternativa verificada para cubrir eso.",
    "en": "I don't have a verified alternative for that right now.",
}

_CONJUNCTION: Dict[str, str] = {"es": " y ", "en": " and "}


def _join_names(names: List[str], lang: str) -> str:
    """Une nombres en una lista natural ("a, b y c" / "a, b and c").
    Nunca es una estructura JSON — es texto plano."""
    names = list(names)
    if len(names) == 1:
        return names[0]
    conj = _CONJUNCTION.get(lang, _CONJUNCTION["es"])
    return ", ".join(names[:-1]) + conj + names[-1]


def _classify_single(entry: CapabilityEntry) -> str:
    """Clasifica UNA entrada en exactamente una de 4 categorías, derivadas
    ÚNICAMENTE de los campos compuestos del contrato — nunca se colapsan a
    una sola etiqueta antes de esta clasificación final:
      - "unavailable": no existe, no conectada, o health=UNAVAILABLE.
      - "degraded": conectada pero health=DEGRADED.
      - "unauthorized": conectada + disponible (health=AVAILABLE) pero
        authorized=False — la combinación compuesta que un enum único
        perdería.
      - "ready": existe, conectada, disponible y autorizada.
    """
    if not entry.exists or not entry.connected or entry.health == HEALTH_UNAVAILABLE:
        return "unavailable"
    if entry.health == HEALTH_DEGRADED:
        return "degraded"
    if not entry.authorized:
        return "unauthorized"
    if entry.health == HEALTH_AVAILABLE and entry.authorized:
        return "ready"
    return "unavailable"  # combinación inesperada -> honesto por defecto


def _categorize(entries: List[CapabilityEntry]) -> Dict[str, List[CapabilityEntry]]:
    buckets: Dict[str, List[CapabilityEntry]] = {
        "ready": [], "unauthorized": [], "degraded": [], "unavailable": [],
    }
    for entry in entries:
        buckets[_classify_single(entry)].append(entry)
    return buckets


def _describe_gaps(lang: str, gaps: List[CapabilityEntry]) -> str:
    if not gaps:
        return ""
    buckets: Dict[str, List[str]] = {"unavailable": [], "degraded": [], "unauthorized": []}
    for entry in gaps:
        category = _classify_single(entry)
        if category in buckets:
            buckets[category].append(entry.name)

    templates = _GAP_TEMPLATES.get(lang, _GAP_TEMPLATES["es"])
    parts: List[str] = []
    # Orden estable (no depende de dict ordering accidental): unavailable
    # primero (lo más severo: ni conecta), luego degraded, luego
    # unauthorized (existe y funciona, solo falta permiso).
    for category in ("unavailable", "degraded", "unauthorized"):
        names = buckets[category]
        if names:
            parts.append(templates[category].format(names=_join_names(names, lang)))
    return " ".join(parts)


def _describe_fallback(lang: str, gaps: List[CapabilityEntry], fallback_sources: List[str]) -> str:
    """Solo se menciona si hay algo pendiente (`gaps` no vacío) — si todo
    está disponible, no tiene sentido hablar de alternativas."""
    if not gaps:
        return ""
    if fallback_sources:
        template = _FALLBACK_TEMPLATES.get(lang, _FALLBACK_TEMPLATES["es"])
        return template.format(names=_join_names(fallback_sources, lang))
    return _NO_FALLBACK.get(lang, _NO_FALLBACK["es"])


def narrate(context: CapabilityContext, lang: str = _DEFAULT_LANG) -> str:
    """Texto natural, determinista, derivado EXCLUSIVAMENTE de `context`.

    Distingue con claridad, para cada capacidad relevante:
      - si existe (`exists`),
      - si está conectada (`connected`),
      - si está autorizada (`authorized`),
      - si está disponible, degradada o inaccesible (`health`),
      - y, para lo que falta, qué fuente alternativa real (`fallback_sources`)
        puede usarse — o si honestamente no hay ninguna verificada.

    Nunca muestra JSON, nombres de función internos, rutas ni el campo
    `reason` (que puede contener detalle de excepciones) — solo nombres de
    capacidades y su estado. Nunca lanza: ante evidencia insuficiente (sin
    `entries`) devuelve una frase honesta en vez de fabricar una respuesta.
    """
    if lang not in ("es", "en"):
        lang = _DEFAULT_LANG

    if not context.entries:
        return _NO_EVIDENCE[lang]

    buckets = _categorize(context.entries)
    summary = _SUMMARY_TEMPLATE[lang].format(
        ready=len(buckets["ready"]), total=len(context.entries),
    )

    sentences = [summary]

    gap_text = _describe_gaps(lang, context.gaps)
    if gap_text:
        sentences.append(gap_text)

    fallback_text = _describe_fallback(lang, context.gaps, context.fallback_sources)
    if fallback_text:
        sentences.append(fallback_text)

    return " ".join(sentences)
