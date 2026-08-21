"""
core/self_observation/capability_context.py — Autoconocimiento verificable
de capacidades (Fase 3, paso 5).

Contrato de datos ESTRUCTURADO (no un string de prompt) que responde, de
forma determinista y sin LLM, a "qué sabe/puede hacer Vectrax, qué tiene
conectado/disponible/autorizado, qué está degradado o inaccesible, y qué le
falta". Este módulo NO decide texto natural (eso es el narrador, paso 6) y
NO se conecta todavía al pipeline de mensajes (eso es el paso 7, detrás del
flag `VX_CAPABILITY_SELF_AWARENESS`, que todavía no existe en el código).

Regla de fuente única (ver plan, sección "Regla de fuente única de
motores"): `core/orchestration/engine_registry.py` + su bootstrap siguen
siendo la ÚNICA fuente canónica de qué MOTORES existen — exactamente 48,
con distribución core=21/observe=17/gated_internal=5/external=5. Este
módulo NUNCA registra motores nuevos ahí ni mantiene un segundo inventario
paralelo de motores.

Los cinco elementos que en la investigación de la Fase 3 se identificaron
como "capacidades reales sin representar todavía" (búsqueda online, puente
de proveedores LLM, librería de dominios, AutoBuilder, gateway de
Telegram) se modelan aquí, DENTRO del mismo contrato compuesto, con
`kind` distinto de "engine" ("capability" | "integration" | "service" |
"gateway") — nunca como `EngineSpec` adicionales. Así hay un solo lugar
donde se enumeran capacidades (este módulo), sin bifurcar en un tercer
inventario.

Fuentes reales utilizadas (todas ya existentes, ninguna nueva):
  - `core.orchestration.bootstrap.get_engine_status()` — snapshot read-only
    de los 48 motores (total/available/by_tier/connected/health/gated_by).
  - `core.orchestration.engine_registry._env_truthy()` — misma función que
    ya usa `bootstrap._decide_and_run()` para decidir si un `gated_by` está
    activo.
  - `core.intent_ssot.IntentDecision` (`domain`, `task_type`,
    `capability_query`) — SSOT de intención (Fase 2/Fase 3, paso 4).
  - `core.self_observation.observation_ledger` — memoria persistente
    existente (SQLite, append-only), reutilizada para registrar
    incapacidades verificadas — sin ledger nuevo, sin esquema nuevo.

Creador: Mario Bravo Castro
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from core.orchestration.bootstrap import (
    HEALTH_AVAILABLE,
    HEALTH_DEGRADED,
    HEALTH_UNAVAILABLE,
    get_engine_status,
)

logger = logging.getLogger("vectrax.self_observation.capability_context")


# ---------------------------------------------------------------------------
# CapabilityEntry / CapabilityContext — contrato compuesto (NO enum único)
# ---------------------------------------------------------------------------
# `exists`, `connected` y `authorized` son banderas INDEPENDIENTES; `health`
# es el único eje de tres valores (AVAILABLE|DEGRADED|UNAVAILABLE). Una
# entrada puede existir, estar conectada y disponible, pero no autorizada —
# esa combinación se pierde si se reduce a una sola etiqueta (por eso NO es
# un enum único).

@dataclass(frozen=True)
class CapabilityEntry:
    """Una fila del inventario de capacidades, con evidencia trazable."""
    name: str              # p.ej. "smart_router" (motor) o "online_search" (capacidad)
    kind: str              # "engine" | "capability" | "integration" | "service" | "gateway"
    group: str             # EngineSpec.group para motores; grupo lógico para el resto
    exists: bool           # ¿Vectrax conoce/declara esta capacidad?
    connected: bool        # ¿el módulo asociado importa?
    authorized: bool       # ¿la política actual permite usarla (gated_by/credenciales)?
    health: str            # HEALTH_AVAILABLE | HEALTH_DEGRADED | HEALTH_UNAVAILABLE
    reason: str            # detalle determinista, saneado (nunca secretos/rutas/env)
    evidence_source: str   # qué función produjo el dato (trazabilidad)
    observed_at: float     # timestamp de la observación real (no cacheado)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "group": self.group,
            "exists": self.exists,
            "connected": self.connected,
            "authorized": self.authorized,
            "health": self.health,
            "reason": self.reason,
            "evidence_source": self.evidence_source,
            "observed_at": self.observed_at,
        }


@dataclass
class CapabilityContext:
    """Snapshot determinista de capacidades para una consulta concreta."""
    query_domain: Optional[str] = None
    query_task_type: Optional[str] = None
    query_capability: bool = False
    entries: List[CapabilityEntry] = field(default_factory=list)
    gaps: List[CapabilityEntry] = field(default_factory=list)
    fallback_sources: List[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_domain": self.query_domain,
            "query_task_type": self.query_task_type,
            "query_capability": self.query_capability,
            "entries": [e.to_dict() for e in self.entries],
            "gaps": [e.to_dict() for e in self.gaps],
            "fallback_sources": self.fallback_sources,
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# Catálogo local de capacidades/integraciones/servicios/gateways que NO son
# motores en engine_registry.py (Fase 3, requisito 2). Deliberadamente
# pequeño y explícito — NO es un registro paralelo de motores: `kind` nunca
# es "engine" aquí, y engine_registry.py no se modifica ni se lee para
# construir esta lista.
# ---------------------------------------------------------------------------

_CAPABILITY_CATALOG: Dict[str, Dict[str, Any]] = {
    "online_search": {
        "kind": "capability", "group": "proveedores",
        "module": "vectrax.resolver", "attr": "resolve_online",
    },
    "llm_providers": {
        "kind": "integration", "group": "proveedores",
        "module": "vectrax.intelligence_bridge", "attr": "is_ready",
    },
    "domain_knowledge": {
        "kind": "service", "group": "aprendizaje",
        "module": "core.domain_knowledge", "attr": "list_domains",
    },
    "auto_builder": {
        "kind": "service", "group": "otros",
        "module": "core.auto_builder", "attr": "AutoBuilder",
    },
    "telegram_gateway": {
        "kind": "gateway", "group": "otros",
        "module": "vectrax.telegram_gateway", "attr": None,
    },
    # Fuente real ya usada por external_gateway.py (PRE-ROUTER FREIGHT
    # INTERCEPT) — se cataloga aquí solo para poder ofrecerla como
    # fallback_source verificable; no es un motor nuevo.
    "freight_query": {
        "kind": "service", "group": "otros",
        "module": "intents.freight_intents", "attr": "resolve_freight_query",
    },
}

# Candidatos reales (ya existentes en el pipeline) para "dónde busco cuando
# mi memoria no alcanza". Se filtran a los que estén AVAILABLE+authorized.
_FALLBACK_CANDIDATES: Tuple[str, ...] = (
    "online_search", "criterion", "freight_query", "market_observer",
)


def _check_module_health(module: str, attr: Optional[str] = None) -> Tuple[bool, str, str]:
    """(connected, health, reason) para un módulo/atributo suelto que NO es
    un `EngineSpec` registrado. Misma semántica exacta que
    `bootstrap._check_health()` — reutilizada como PATRÓN (no se importa
    directamente porque esa función espera un `EngineSpec`) para no
    reimplementar un registro completo por 6 elementos. Defensivo: nunca
    lanza."""
    try:
        m = importlib.import_module(module)
        if attr is not None:
            getattr(m, attr)
        return True, HEALTH_AVAILABLE, ""
    except ImportError as exc:  # incluye ModuleNotFoundError
        return False, HEALTH_UNAVAILABLE, f"{type(exc).__name__}: {str(exc)[:120]}"
    except Exception as exc:  # noqa: BLE001 - defensivo a propósito
        return True, HEALTH_DEGRADED, f"{type(exc).__name__}: {str(exc)[:120]}"


def _derive_authorized_from_gated_by(gated_by: Optional[str]) -> Tuple[bool, str]:
    """Misma regla EXACTA que `bootstrap._decide_and_run()` aplica para
    decidir si un `gated_by` bloquea la activación: sin `gated_by` → sin
    bloqueo (True); `gated_by == "__never__"` → False incondicional;
    en cualquier otro caso → True solo si esa env var está activa
    (`_env_truthy`, la misma función que ya usa el bootstrap). No se
    deduce nada por el nombre del tier: `GATED_INTERNAL` sin `gated_by`
    (p.ej. `presencia_observer`, `operator_runtime`) cae en el primer caso
    y queda `authorized=True`, igual que en el código real."""
    if not gated_by:
        return True, ""
    if gated_by == "__never__":
        return False, f"gated_by={gated_by}"
    try:
        from core.orchestration.engine_registry import _env_truthy
        active = _env_truthy(gated_by)
    except Exception as exc:
        logger.debug("_env_truthy unavailable: %s", exc)
        active = False
    return active, f"gated_by={gated_by}"


_SECRET_LIKE_RE = None  # compilado lazy para no pagar el costo si no se usa


def _sanitize_reason(reason: str) -> str:
    """Nunca deja pasar valores de variables de entorno, rutas absolutas del
    filesystem del usuario, ni patrones de credenciales — solo el nombre de
    la excepción y un mensaje recortado. Defensivo: nunca lanza."""
    global _SECRET_LIKE_RE
    if not reason:
        return ""
    try:
        import re as _re
        if _SECRET_LIKE_RE is None:
            _SECRET_LIKE_RE = _re.compile(
                r"(sk-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9_.\-]+|/Users/[^\s]+|/home/[^\s]+)"
            )
        cleaned = _SECRET_LIKE_RE.sub("<redacted>", reason)
        return cleaned[:160]
    except Exception:
        return reason[:160]


def _relevant_groups_for(query_domain: Optional[str], query_task_type: Optional[str]) -> Set[str]:
    """Mapeo MÍNIMO y honesto de domain/task_type a `group` de motor.
    Deliberadamente no inventa una taxonomía domain→group que no existe en
    el código: solo mapea lo que ya es literal en `engine_registry.py`
    (`group="mercado"` para el dominio "market"). Si no hay coincidencia
    declarada, se devuelve vacío y el llamador usa el conjunto completo de
    gaps sin filtrar — más honesto que fabricar una relación inexistente."""
    groups: Set[str] = set()
    for candidate in (query_domain, query_task_type):
        if candidate and candidate.strip().lower() == "market":
            groups.add("mercado")
    return groups


def _compute_fallback_sources(by_name: Dict[str, CapabilityEntry]) -> List[str]:
    out: List[str] = []
    for name in _FALLBACK_CANDIDATES:
        entry = by_name.get(name)
        if entry is not None and entry.health == HEALTH_AVAILABLE and entry.authorized:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# build_capability_context — builder determinista (sin LLM)
# ---------------------------------------------------------------------------

def build_capability_context(decision: Any) -> CapabilityContext:
    """Construye el `CapabilityContext` para la `IntentDecision` dada.

    100% determinista, sin LLM. Lee EXCLUSIVAMENTE:
      - `get_engine_status()` (motores reales, fuente única canónica).
      - el catálogo local de capacidades/integraciones/servicios/gateways
        de este archivo (nunca engine_registry.py).
      - `decision.domain` / `decision.task_type` / `decision.capability_query`
        (SSOT de intención).

    `decision` se acepta como `Any` (no se importa `IntentDecision` a nivel
    de módulo) para no introducir una dependencia dura de import en tiempo
    de carga; en runtime siempre es una `core.intent_ssot.IntentDecision`.
    Defensivo: nunca lanza — si una fuente falla, esa sección queda vacía y
    las demás siguen.
    """
    now = time.time()
    entries: List[CapabilityEntry] = []

    # 1) Motores reales — fuente única canónica (engine_registry vía bootstrap).
    try:
        status = get_engine_status()
    except Exception as exc:
        logger.debug("get_engine_status unavailable: %s", exc)
        status = {"engines": []}
    for e in status.get("engines", []):
        authorized, auth_reason = _derive_authorized_from_gated_by(e.get("gated_by"))
        health = e.get("health") or (HEALTH_AVAILABLE if e.get("available") else HEALTH_UNAVAILABLE)
        reason = e.get("detail") or auth_reason
        entries.append(CapabilityEntry(
            name=e["name"],
            kind="engine",
            group=e.get("group", ""),
            exists=True,
            connected=bool(e.get("connected", e.get("available", False))),
            authorized=authorized,
            health=health,
            reason=_sanitize_reason(reason),
            evidence_source="engine_registry+bootstrap.get_engine_status",
            observed_at=now,
        ))

    # 2) Capacidades/integraciones/servicios/gateways NO registrados como
    # motores (requisito 2) — catálogo local de este archivo, nunca escrito
    # de vuelta a engine_registry.py.
    for name, spec in _CAPABILITY_CATALOG.items():
        connected, health, reason = _check_module_health(spec["module"], spec.get("attr"))
        entries.append(CapabilityEntry(
            name=name,
            kind=spec["kind"],
            group=spec["group"],
            exists=True,
            connected=connected,
            authorized=True,  # sin gated_by declarado en el catálogo local
            health=health,
            reason=_sanitize_reason(reason),
            evidence_source="capability_context._CAPABILITY_CATALOG",
            observed_at=now,
        ))

    by_name = {entry.name: entry for entry in entries}

    all_gaps = [e for e in entries if e.health != HEALTH_AVAILABLE or not e.authorized]
    query_domain = getattr(decision, "domain", "") or None
    query_task_type = getattr(decision, "task_type", "") or None
    relevant_groups = _relevant_groups_for(query_domain, query_task_type)
    if relevant_groups:
        filtered = [g for g in all_gaps if g.group in relevant_groups]
        gaps = filtered if filtered else all_gaps
    else:
        gaps = all_gaps

    return CapabilityContext(
        query_domain=query_domain,
        query_task_type=query_task_type,
        query_capability=bool(getattr(decision, "capability_query", False)),
        entries=entries,
        gaps=gaps,
        fallback_sources=_compute_fallback_sources(by_name),
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# record_capability_gap — hecho verificable, deduplicado, sin generar ideas
# ---------------------------------------------------------------------------

def _compute_fingerprint(
    capability_name: str,
    query_domain: Optional[str],
    query_task_type: Optional[str],
    obs_type: str,
) -> str:
    """Huella ESTABLE entre ejecuciones (hashlib, no `hash()` de Python que
    es aleatorio por proceso salvo PYTHONHASHSEED fijo)."""
    raw = "|".join([
        obs_type or "",
        capability_name or "",
        (query_domain or "").strip().lower(),
        (query_task_type or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _has_recent_duplicate(fingerprint: str, window_seconds: float) -> bool:
    """True si ya existe un registro con este `fingerprint` en
    `observation_ledger` (domain=self_knowledge) dentro de la ventana de
    deduplicación. Defensivo: si el ledger falla o el timestamp no se puede
    parsear, se asume duplicado (mejor no escribir de más que espamear)."""
    try:
        from core.self_observation.observation_ledger import get_by_domain
        records = get_by_domain("self_knowledge", limit=200)
    except Exception as exc:
        logger.debug("_has_recent_duplicate: ledger unavailable: %s", exc)
        return False

    now = datetime.now(timezone.utc)
    for r in records:
        evidence = r.get("evidence") or {}
        if evidence.get("fingerprint") != fingerprint:
            continue
        ts = r.get("timestamp")
        try:
            rec_time = datetime.fromisoformat(ts)
            if rec_time.tzinfo is None:
                rec_time = rec_time.replace(tzinfo=timezone.utc)
        except Exception:
            return True  # timestamp ilegible → conservador, evita duplicar
        age = (now - rec_time).total_seconds()
        if age <= window_seconds:
            return True
    return False


def record_capability_gap(
    *,
    capability_name: str,
    health: str,
    authorized: bool,
    reason: str = "",
    query_domain: Optional[str] = None,
    query_task_type: Optional[str] = None,
    obs_type: str = "capability_gap",
    severity: str = "info",
    dedup_window_seconds: float = 86400.0,
) -> bool:
    """Registra un gap de capacidad VERIFICADO (no especulativo) como
    observación persistente, deduplicado por huella determinista.

    Reutiliza `core.self_observation.observation_ledger` (mismo API, mismo
    esquema — sin tabla ni columnas nuevas): `domain="self_knowledge"`,
    `evidence` con el `fingerprint` + los datos concretos de la incapacidad.

    NUNCA genera una `CognitiveProposal` ni aprueba nada — Fase 3 solo
    detecta y registra; Fase 4 (fuera de alcance aquí) decidirá si estos
    registros se convierten en idea.

    Devuelve True si escribió un registro nuevo, False si ya existía uno
    equivalente reciente (deduplicado) o si el ledger no está disponible.
    Defensivo: nunca lanza.
    """
    fingerprint = _compute_fingerprint(capability_name, query_domain, query_task_type, obs_type)
    try:
        if _has_recent_duplicate(fingerprint, dedup_window_seconds):
            logger.debug(
                "record_capability_gap: duplicado reciente (fingerprint=%s), no se re-escribe",
                fingerprint,
            )
            return False

        from core.self_observation.observation_ledger import init_ledger, record
        init_ledger()
        summary = (
            f"Capacidad '{capability_name}' no disponible/autorizada "
            f"(health={health}, authorized={authorized})"
        )
        record(
            domain="self_knowledge",
            obs_type=obs_type,
            summary=summary,
            evidence={
                "fingerprint": fingerprint,
                "capability_name": capability_name,
                "query_domain": query_domain or "",
                "query_task_type": query_task_type or "",
                "health": health,
                "authorized": authorized,
                "reason": _sanitize_reason(reason),
            },
            severity=severity,
        )
        return True
    except Exception as exc:
        logger.debug("record_capability_gap failed: %s", exc)
        return False
