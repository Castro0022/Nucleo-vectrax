"""
core/orchestration/bootstrap.py — Activación unificada de motores.

`activate_all(profile)` recorre el registro declarativo y activa/verifica cada
motor de forma:
  - Idempotente: reactivar no rompe (los motores usan sus singletons/estado).
  - Defensiva: cada motor va en su propio try/except — si uno falla, los demás
    siguen. NUNCA levanta una excepción que tumbe el arranque.
  - Segura por tier:
      CORE / OBSERVE   → se activan/verifican.
      GATED_INTERNAL   → se activan en su SUB-MODO SEGURO (operador GUIDED,
                         observer OBSERVER); respetan su `gated_by`.
      EXTERNAL         → SOLO health-check (conectar + verificar). Nunca "en vivo".

Estados por motor: activated | healthy | skipped_gated | unavailable | failed.

Cada corrida se registra (best-effort) en el observation_ledger bajo el dominio
`activation`, de modo que es visible en el universo (Pilares C/D).

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.orchestration import engine_registry as reg
from core.orchestration.engine_registry import EngineSpec, Tier, _env_truthy

logger = logging.getLogger("vectrax.orchestration.bootstrap")

PROFILE_SAFE = "safe"
PROFILE_FULL = "full"

# Estados
ACTIVATED = "activated"
HEALTHY = "healthy"
SKIPPED_GATED = "skipped_gated"
UNAVAILABLE = "unavailable"
FAILED = "failed"
READY = "ready"          # dry-run: se activaría, pero no se llamó a activate()


@dataclass
class EngineResult:
    name: str
    group: str
    tier: str
    status: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "tier": self.tier,
            "status": self.status,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Ordenamiento por dependencias (topológico simple, estable)
# ---------------------------------------------------------------------------

def _ordered(specs: List[EngineSpec]) -> List[EngineSpec]:
    by_name = {s.name: s for s in specs}
    ordered: List[EngineSpec] = []
    seen: set = set()

    def visit(spec: EngineSpec, stack: set) -> None:
        if spec.name in seen:
            return
        if spec.name in stack:  # ciclo: ignora la arista para no colgar
            return
        stack.add(spec.name)
        for dep in spec.deps:
            dep_spec = by_name.get(dep)
            if dep_spec is not None:
                visit(dep_spec, stack)
        stack.discard(spec.name)
        seen.add(spec.name)
        ordered.append(spec)

    for s in specs:
        visit(s, set())
    return ordered


# ---------------------------------------------------------------------------
# Health (read-only) — nunca activa, nunca muta
# ---------------------------------------------------------------------------

def _check_health(spec: EngineSpec) -> tuple:
    """(available: bool, detail: str). Defensivo."""
    if spec.health is None:
        return True, "sin health-check (se asume disponible)"
    try:
        ok = bool(spec.health())
        return ok, "" if ok else "health devolvió False"
    except Exception as exc:  # noqa: BLE001 - defensivo a propósito
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def get_engine_status() -> Dict[str, Any]:
    """Snapshot read-only del estado de todos los motores (para /v1/engines).
    No activa nada."""
    engines = []
    for spec in reg.all_specs():
        available, detail = _check_health(spec)
        engines.append({
            "name": spec.name,
            "group": spec.group,
            "tier": spec.tier.value,
            "available": available,
            "gated_by": spec.gated_by,
            "description": spec.description,
            "detail": detail,
        })
    return {
        "total": len(engines),
        "available": sum(1 for e in engines if e["available"]),
        "engines": engines,
    }


# ---------------------------------------------------------------------------
# Activación
# ---------------------------------------------------------------------------

def _decide_and_run(spec: EngineSpec, profile: str, dry_run: bool = False) -> EngineResult:
    tier = spec.tier
    base = dict(name=spec.name, group=spec.group, tier=tier.value)

    # 1) Health read-only primero.
    available, hdetail = _check_health(spec)
    if not available:
        return EngineResult(status=UNAVAILABLE, detail=hdetail, **base)

    # 2) EXTERNAL: jamás se activa desde aquí. Solo conectar+verificar.
    if tier == Tier.EXTERNAL:
        return EngineResult(status=HEALTHY, detail="external: health-check only", **base)

    # 3) Respeta gating explícito (flags). '__never__' = nunca auto-activar.
    if spec.gated_by:
        if spec.gated_by == "__never__" or not _env_truthy(spec.gated_by):
            return EngineResult(status=SKIPPED_GATED,
                                detail=f"gated_by={spec.gated_by}", **base)

    # 4) Sin hook de activación → disponible (motor de librería/on-demand).
    if spec.activate is None:
        return EngineResult(status=HEALTHY, detail="disponible (sin activación explícita)", **base)

    # 5) Activación segura e idempotente (o preview en dry_run).
    if dry_run:
        return EngineResult(status=READY, detail="dry-run: se activaría", **base)
    try:
        spec.activate()
        return EngineResult(status=ACTIVATED, detail="", **base)
    except Exception as exc:  # noqa: BLE001 - defensivo a propósito
        return EngineResult(status=FAILED,
                            detail=f"{type(exc).__name__}: {str(exc)[:120]}", **base)


def activate_all(profile: str = PROFILE_SAFE, dry_run: bool = False) -> Dict[str, Any]:
    """Activa/verifica todos los motores según el perfil. Defensivo e idempotente.

    Args:
        profile: "safe" (default) o "full". En ambos, EXTERNAL solo se verifica
                 y GATED_INTERNAL se activa en su sub-modo seguro. (El modo que
                 fuerza/LIVE NUNCA se enciende aquí.)
        dry_run: si True, no llama a ningún activate() — solo reporta qué pasaría
                 (estado READY para los que se activarían). 100% read-only.

    Returns:
        Reporte: {profile, dry_run, total, by_status, engines:[...]}.
    """
    if profile not in (PROFILE_SAFE, PROFILE_FULL):
        profile = PROFILE_SAFE

    reg._register_defaults()
    results: List[EngineResult] = []
    for spec in _ordered(reg.all_specs()):
        try:
            results.append(_decide_and_run(spec, profile, dry_run=dry_run))
        except Exception as exc:  # noqa: BLE001 - red de seguridad final
            results.append(EngineResult(
                name=spec.name, group=spec.group, tier=spec.tier.value,
                status=FAILED, detail=f"bootstrap error: {str(exc)[:100]}"))

    by_status: Dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    report = {
        "profile": profile,
        "dry_run": dry_run,
        "total": len(results),
        "by_status": by_status,
        "engines": [r.to_dict() for r in results],
    }

    if not dry_run:
        _record_to_ledger(report)
    logger.info(
        "activate_all(%s): %s",
        profile, " ".join(f"{k}={v}" for k, v in sorted(by_status.items())),
    )
    return report


def _record_to_ledger(report: Dict[str, Any]) -> None:
    """Best-effort: deja huella de la activación en el ledger (dominio 'activation').
    Visible en el universo. Nunca rompe el bootstrap."""
    try:
        from core.self_observation.observation_ledger import init_ledger, record
        init_ledger()
        bs = report["by_status"]
        failed = [e["name"] for e in report["engines"] if e["status"] == FAILED]
        sev = "warning" if failed else "info"
        record(
            "activation", "engines_bootstrap",
            f"Bootstrap {report['profile']}: " +
            " ".join(f"{k}={v}" for k, v in sorted(bs.items())),
            evidence={
                "profile": report["profile"],
                "by_status": bs,
                "failed": failed,
            },
            severity=sev,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("No se pudo registrar bootstrap en el ledger: %s", exc)
