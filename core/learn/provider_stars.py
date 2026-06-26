"""
core/learn/provider_stars.py — Las IAs externas como estrellas vivas.
=========================================================================
Capa delgada sobre el gravity engine. Una IA externa (openai, gemini,
anthropic, grok, ...) NO es un servicio API especial ni un ranking fijo:
es un conjunto de GravityRecords, uno por (provider, task_type), cuya masa
gravitacional crece con experiencias reales exitosas y decae cuando deja de
funcionar — exactamente como cualquier otra estrella, patrón de mercado o
usuario del universo.

Sin almacenamiento propio ni lógica paralela: cada experiencia entra por
``GravityIndex.record_event()`` y la afinidad se lee del mismo índice. Así
las IAs son ciudadanos del Universo Cognitivo, observados por el censo, el
panel y las convergencias igual que el resto.

Convención de estrella:
    fingerprint = f"ai:{provider}:{task_type}"
    domain      = "ai_provider"
    intent      = task_type        (vocabulario de capacidades: coding, vision…)
    cc_score    = calidad suavizada (EMA de la calidad por llamada)
    outcome     = success | rejected | fail | timeout | rate_limited

NO guarda prompt ni respuesta literal: solo la experiencia abstracta
(provider, task_type, outcome, calidad, latencia).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.providers.stars")

# Dominio bajo el que viven todas las AI Provider Stars en el gravity index.
PROVIDER_DOMAIN = "ai_provider"
_FINGERPRINT_PREFIX = "ai"

# Outcomes abstractos válidos (pequeños y auditables a propósito).
OUTCOMES = ("success", "rejected", "fail", "timeout", "rate_limited")

# Calidad (cc_score) por defecto cuando el caller no pasa una explícita.
_OUTCOME_QUALITY = {
    "success": 0.9,
    "rejected": 0.3,
    "fail": 0.05,
    "timeout": 0.05,
    "rate_limited": 0.0,
}

# Suavizado EMA de la calidad acumulada: la masa refleja evidencia en el
# tiempo, no el ruido de la última llamada.
_CC_EMA_ALPHA = 0.3

# Variable de entorno que controla el peso de la selección gravitacional.
# Ausente o 0 → comportamiento idéntico al actual (solo se registra masa).
_GRAVITY_ENV = "VX_PROVIDER_GRAVITY"


def _norm(s: str) -> str:
    """Normaliza nombres de provider/task a tokens estables."""
    return (s or "").strip().lower().replace(" ", "_")


def provider_fingerprint(provider: str, task_type: str) -> str:
    """Id estable de la estrella (provider, task_type): ``ai:{provider}:{task_type}``."""
    return f"{_FINGERPRINT_PREFIX}:{_norm(provider)}:{_norm(task_type)}"


def _weight(rec) -> float:
    """Masa gravitacional de un registro (misma fórmula que engine/observer)."""
    try:
        return rec.hits * max(rec.cc_score, 0.01) * max(rec.freq, 0.01) * rec.decay_factor
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Registro de experiencia (entrada al universo)
# ---------------------------------------------------------------------------

def record_provider_experience(
    provider: str,
    task_type: str,
    outcome: str = "success",
    quality: Optional[float] = None,
    latency_ms: Optional[float] = None,
    error_type: Optional[str] = None,
    *,
    model: Optional[str] = None,
    route_source: Optional[str] = None,
    fallback_used: Optional[bool] = None,
    tokens: Optional[int] = None,
    response_chars: Optional[int] = None,
    source: str = "provider_experience",
) -> bool:
    """
    Traduce UNA interacción con una IA externa en una experiencia cognitiva
    dentro del universo gravitacional.

    Best-effort: nunca lanza excepción (no debe romper el pipeline del caller).
    Devuelve True si se registró la experiencia, False si falló.
    """
    provider_n = _norm(provider)
    task_n = _norm(task_type)
    if not provider_n or not task_n:
        return False

    outcome_n = outcome if outcome in OUTCOMES else "success"
    if quality is None:
        quality = _OUTCOME_QUALITY.get(outcome_n, 0.5)
    try:
        quality = max(0.0, min(1.0, float(quality)))
    except (TypeError, ValueError):
        quality = _OUTCOME_QUALITY.get(outcome_n, 0.5)

    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        fp = provider_fingerprint(provider_n, task_n)

        # Suaviza la calidad en una coherencia acumulada (EMA) para que la masa
        # refleje evidencia a lo largo del tiempo y no solo la última llamada.
        prior = gi.get(fp)
        if prior is not None and prior.cc_score > 0:
            cc = round((1 - _CC_EMA_ALPHA) * prior.cc_score + _CC_EMA_ALPHA * quality, 4)
        else:
            cc = round(quality, 4)

        summary = f"{provider_n}:{task_n} {outcome_n}"
        if model:
            summary += f" model={model}"
        if route_source:
            summary += f" via={_norm(route_source)}"
        if fallback_used is not None:
            summary += " fallback" if fallback_used else " primary"
        if latency_ms is not None:
            try:
                summary += f" {int(latency_ms)}ms"
            except (TypeError, ValueError):
                pass
        if tokens is not None:
            try:
                summary += f" {int(tokens)}tok"
            except (TypeError, ValueError):
                pass
        if response_chars is not None:
            try:
                summary += f" {int(response_chars)}chars"
            except (TypeError, ValueError):
                pass
        if error_type:
            summary += f" [{_norm(error_type)}]"

        gi.record_event(
            fingerprint=fp,
            cc_score=cc,
            impact="low",
            domain=PROVIDER_DOMAIN,
            intent=task_n,
            outcome=outcome_n,
            summary=summary,
            source=source,
        )
        return True
    except Exception as exc:  # best-effort: jamás romper al caller
        logger.debug("record_provider_experience swallowed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Interacción enrutada (SSOT de aprendizaje a nivel router)
# ---------------------------------------------------------------------------

def record_interaction(
    provider: str,
    *,
    model: Optional[str] = None,
    task_type: Optional[str] = None,
    prompt: str = "",
    route_source: str = "intelligence_router",
    outcome: str = "success",
    quality: Optional[float] = None,
    latency_ms: Optional[float] = None,
    fallback_used: bool = False,
    error_type: Optional[str] = None,
    tokens: Optional[int] = None,
    response_chars: Optional[int] = None,
) -> bool:
    """Registra UNA interacción resuelta de proveedor (SSOT a nivel router).

    Llamado exactamente una vez por petición resuelta desde el
    IntelligenceRouter, tras completarse la ruta primaria/fallback. Los
    proveedores NO se instrumentan por separado, evitando estrellas duplicadas.

    Las estrellas de interacción se etiquetan con source="router_interaction"
    para mantenerlas separables de cualquier estrella de presencia (seeding),
    que usaría otra marca. El task_type se infiere del prompt cuando no se pasa,
    reutilizando el mismo vocabulario de capacidades que el router.
    """
    tt = task_type or infer_task_type(prompt)
    return record_provider_experience(
        provider,
        tt,
        outcome=outcome,
        quality=quality,
        latency_ms=latency_ms,
        error_type=error_type,
        model=model,
        route_source=route_source,
        fallback_used=fallback_used,
        tokens=tokens,
        response_chars=response_chars,
        source="router_interaction",
    )


# ---------------------------------------------------------------------------
# Presencia de proveedores (estrellas de presencia — separadas de interacción)
# ---------------------------------------------------------------------------

# Proveedores de IA externos que son ciudadanos del universo aunque todavía no
# se hayan usado. Se siembran con masa mínima para que sean visibles.
DEFAULT_PRESENCE_PROVIDERS = ("openai", "gemini", "anthropic")
_PRESENCE_TASK = "presence"
_PRESENCE_SOURCE = "provider_presence"


def seed_provider_stars(
    providers=DEFAULT_PRESENCE_PROVIDERS,
    quality: float = 0.5,
) -> List[str]:
    """Crea una estrella de PRESENCIA por proveedor si aún no existe.

    Idempotente y separada de las estrellas de interacción: usa el fingerprint
    ``ai:{provider}:presence`` y ``source="provider_presence"``, de modo que la
    presencia (visibilidad en el universo) NO se mezcla con la experiencia real
    acumulada en ``ai:{provider}:{capability}``. El intent "presence" no es una
    capacidad de routing, así que no afecta la selección de proveedor ni las
    convergencias.

    Best-effort: nunca lanza. Devuelve la lista de proveedores recién sembrados.
    """
    seeded: List[str] = []
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
    except Exception as exc:
        logger.debug("seed_provider_stars: gravity index unavailable: %s", exc)
        return seeded

    for p in providers:
        pn = _norm(p)
        if not pn:
            continue
        fp = provider_fingerprint(pn, _PRESENCE_TASK)
        try:
            if gi.get(fp) is not None:
                continue  # ya existe — idempotente, no re-sembrar
        except Exception:
            pass
        if record_provider_experience(
            pn,
            _PRESENCE_TASK,
            outcome="success",
            quality=quality,
            route_source="seed",
            source=_PRESENCE_SOURCE,
        ):
            seeded.append(pn)
    return seeded


# ---------------------------------------------------------------------------
# Lectura: afinidad, constelación, inventario
# ---------------------------------------------------------------------------

def _provider_records(task_type: Optional[str] = None) -> List:
    """Todos los GravityRecords de IA, opcionalmente filtrados por task_type."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        recs = [r for r in gi.all_records() if r.domain == PROVIDER_DOMAIN]
        if task_type is not None:
            tn = _norm(task_type)
            recs = [r for r in recs if _norm(r.intent) == tn]
        return recs
    except Exception as exc:
        logger.debug("_provider_records swallowed: %s", exc)
        return []


def provider_affinity(provider: str, task_type: str) -> float:
    """
    Afinidad gravitacional relativa de un proveedor para un task_type, en [0, 1].

    Es la cuota de masa del proveedor sobre la masa total de TODAS las estrellas
    de IA para ese task_type. 0.0 cuando aún no hay evidencia (arranque en frío
    → deciden las semillas estáticas del router). Es una TENDENCIA, no una regla
    fija: se reequilibra sola cuando cambia la evidencia.
    """
    provider_n = _norm(provider)
    task_n = _norm(task_type)
    recs = _provider_records(task_n)
    if not recs:
        return 0.0

    target_fp = provider_fingerprint(provider_n, task_n)
    total = 0.0
    mine = 0.0
    for r in recs:
        w = _weight(r)
        total += w
        if r.fingerprint == target_fp:
            mine += w
    if total <= 0:
        return 0.0
    return round(mine / total, 4)


def provider_constellation(provider: str) -> Dict:
    """
    Observa a un proveedor completo (OpenAI, Gemini, Claude, Grok, …) como una
    constelación agregada de sus estrellas ``ai:{provider}:*`` — no como un único
    registro global. Conserva la afinidad específica por tipo de tarea.
    """
    provider_n = _norm(provider)
    prefix = f"{_FINGERPRINT_PREFIX}:{provider_n}:"
    stars: List[Dict] = []
    total_mass = 0.0
    total_hits = 0
    for r in _provider_records():
        if not r.fingerprint.startswith(prefix):
            continue
        w = round(_weight(r), 4)
        total_mass += w
        total_hits += r.hits
        stars.append({
            "task_type": r.intent,
            "fingerprint": r.fingerprint,
            "tier": r.tier,
            "hits": r.hits,
            "cc": round(r.cc_score, 4),
            "mass": w,
            "outcomes": list(r.outcome_history[-10:]),
        })
    stars.sort(key=lambda s: s["mass"], reverse=True)
    return {
        "provider": provider_n,
        "stars": stars,
        "task_types": len(stars),
        "total_mass": round(total_mass, 4),
        "total_hits": total_hits,
    }


def list_provider_stars() -> List[Dict]:
    """Todas las estrellas de IA (para APIs, pruebas o una vista de panel)."""
    out: List[Dict] = []
    for r in _provider_records():
        parts = r.fingerprint.split(":")
        out.append({
            "fingerprint": r.fingerprint,
            "provider": parts[1] if len(parts) >= 3 else "",
            "task_type": r.intent,
            "domain": r.domain,
            "tier": r.tier,
            "hits": r.hits,
            "cc": round(r.cc_score, 4),
            "mass": round(_weight(r), 4),
        })
    out.sort(key=lambda s: s["mass"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Selección gravitacional (consumido por los routers, siempre gated)
# ---------------------------------------------------------------------------

def gravity_weight() -> float:
    """Peso de la selección gravitacional desde VX_PROVIDER_GRAVITY (0 = off)."""
    try:
        return float(os.environ.get(_GRAVITY_ENV, "0") or "0")
    except (TypeError, ValueError):
        return 0.0


def affinity_bonus(provider: str, task_types) -> float:
    """
    Término de masa para el scorer del router: ``peso × afinidad media`` sobre
    las capacidades/task_types requeridos.

    Devuelve 0.0 si la selección gravitacional está desactivada
    (VX_PROVIDER_GRAVITY ausente o 0) o si todavía no hay evidencia, de modo que
    el comportamiento por defecto del router no cambia.
    """
    w = gravity_weight()
    if w <= 0 or not task_types:
        return 0.0
    vals = [provider_affinity(provider, t) for t in task_types]
    vals = [v for v in vals if v]
    if not vals:
        return 0.0
    return round(w * (sum(vals) / len(vals)), 4)


def infer_task_type(text: str, default: str = "reasoning") -> str:
    """
    Mapea texto libre a un único token de capacidad (coding, vision, reasoning…)
    reutilizando el clasificador de tareas existente. Mantiene el vocabulario de
    registro alineado con el del router para que experiencia y selección se
    encuentren en las mismas estrellas.
    """
    try:
        from core.routing.task_classifier import classify_task, TASK_CAPABILITY_MAP
        cls = classify_task(text or "")
        cap = TASK_CAPABILITY_MAP.get(cls.task_type)
        if cap is not None:
            return cap.value
    except Exception as exc:
        logger.debug("infer_task_type swallowed: %s", exc)
    return default
