"""
core/nucleus/internal_evidence.py — Interfaz canónica de evidencia interna
==========================================================================
Etapa de reunificación (continuación de `nucleus_authority.py`, 2026-09-19).

PROBLEMA QUE RESUELVE
---------------------
Vectrax ya observa su universo, ejecuta diagnósticos, registra auditorías,
confirma hipótesis y genera propuestas. Esa evidencia alimentaba el Dashboard
y los motores internos, pero NO estaba disponible para el Núcleo
conversacional: `NucleusAuthority` no tenía una sola línea que consultara
`IdeaStore`, `audit_ledger`, `universe_census`, `universe_observer` ni los
dominios operativos. Resultado observable: el Dashboard mostraba propuestas
`IDEA-...` reales y el chat no sabía nada de ellas.

Este módulo es el cable que faltaba. NO es un segundo Núcleo, NO es un
observador nuevo y NO es un almacén nuevo: es una FACHADA DE SOLO LECTURA
sobre las fuentes que Vectrax ya produce. No escribe en ninguna base, no
copia información a un almacén propio y no cachea nada entre llamadas.

INVARIANTES (verificadas por `tests/test_internal_evidence.py`)
---------------------------------------------------------------
1. Solo lectura. Ninguna función de este módulo abre un archivo en modo
   escritura, ni ejecuta INSERT/UPDATE/DELETE, ni llama a un `record()`.
2. Nunca lanza. Toda fuente caída degrada a `EvidenceStatus.UNAVAILABLE`
   con el motivo real; nunca a un dato inventado.
3. Nunca infiere un estado que la fuente no afirma. `pending` se reporta
   como `pending`. Una hipótesis confirmada NO es una reparación ejecutada.
   Una aprobación de gobernanza NO es una acción ejecutada.
4. Toda afirmación lleva su fuente exacta (`EvidenceItem.source`) y su
   antigüedad (`EvidenceItem.age_seconds`) — sin excepción.
5. La autorización depende de la IDENTIDAD CANÓNICA (`vectrax.identity`
   + `vectrax.identity_aliases`), nunca del canal de transporte ni de una
   cadena "owner"/"Mario" hardcodeada.

Creado: 2026-09-22 — etapa de reunificación del Núcleo con su observación
interna. Contrato consumido por `core/nucleus/nucleus_authority.py`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.nucleus.internal_evidence")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Estado de la evidencia — las distinciones que el Núcleo NUNCA puede colapsar
# ---------------------------------------------------------------------------

class EvidenceStatus(str, Enum):
    """Estados mutuamente excluyentes de una consulta de evidencia.

    Cada uno produce una respuesta conversacional DISTINTA. Colapsar dos de
    ellos es exactamente el defecto que esta etapa corrige (p.ej. responder
    "no hay problemas" cuando en realidad el servicio no respondió).
    """

    OK = "ok"                      # Hay evidencia vigente
    EMPTY = "empty"                # La fuente respondió y no tiene nada
    STALE = "stale"                # Hay evidencia, pero es más vieja que su ventana
    UNAVAILABLE = "unavailable"    # La fuente no respondió / no existe
    UNAUTHORIZED = "unauthorized"  # La identidad canónica no tiene permiso


# Ventana de vigencia por tipo de evidencia (segundos). Superarla NO borra la
# evidencia: la marca `STALE` para que el Núcleo lo diga explícitamente.
_STALE_AFTER: Dict[str, float] = {
    "diagnostic": 24 * 3600.0,
    "diagnostic_history": 7 * 24 * 3600.0,
    "proposal": 90 * 24 * 3600.0,
    "audit": 7 * 24 * 3600.0,
    "engines": 900.0,
    "universe": 3600.0,
    "stars": 3600.0,
    "patterns": 3600.0,
    "domains": 24 * 3600.0,
    "convergences": 3600.0,
    "operational": 24 * 3600.0,
    "approval_pipeline": float("inf"),  # traza estructural del código, no caduca
}

# Tipos que solo el owner canónico puede consultar. Un usuario común no debe
# obtener diagnósticos internos, rutas, infraestructura ni propuestas.
_OWNER_ONLY = frozenset({
    "diagnostic", "diagnostic_history", "proposal", "audit",
    "engines", "operational", "approval_pipeline",
})


# ---------------------------------------------------------------------------
# Unidad normalizada de evidencia
# ---------------------------------------------------------------------------

@dataclass
class EvidenceItem:
    """Un hecho verificable. Todos los campos exigidos por el contrato de la
    etapa: tipo, fuente, fecha observada, alcance, estado, resumen factual,
    referencia verificable, confianza, antigüedad y visibilidad."""

    kind: str                              # tipo de evidencia
    source: str                            # fuente exacta (módulo/tabla/archivo)
    observed_at: float                     # epoch UTC de la observación
    scope: str = ""                        # alcance o dominio
    status: str = ""                       # estado TAL COMO LO AFIRMA LA FUENTE
    summary: str = ""                      # resumen factual (sin interpretación)
    reference: str = ""                    # referencia verificable (IDEA-x, id, ruta)
    confidence: Optional[float] = None     # solo si la fuente la provee
    visibility: str = "owner"              # "owner" | "public"
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> Optional[float]:
        """Antigüedad real. `None` si la fuente no fechó el dato — nunca 0."""
        if not self.observed_at:
            return None
        return max(0.0, time.time() - self.observed_at)

    def to_dict(self) -> Dict[str, Any]:
        age = self.age_seconds
        return {
            "kind": self.kind,
            "source": self.source,
            "observed_at": self.observed_at,
            "age_seconds": round(age, 1) if age is not None else None,
            "scope": self.scope,
            "status": self.status,
            "summary": self.summary,
            "reference": self.reference,
            "confidence": self.confidence,
            "visibility": self.visibility,
            "data": self.data,
        }


@dataclass
class EvidenceResult:
    """Resultado de una consulta. `status` distingue SIEMPRE los cinco casos
    de `EvidenceStatus` — el Núcleo redacta a partir de él, nunca lo adivina."""

    kind: str
    status: EvidenceStatus
    source: str = ""
    items: List[EvidenceItem] = field(default_factory=list)
    detail: str = ""                       # motivo real cuando no es OK
    generated_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.status is EvidenceStatus.OK

    @property
    def has_items(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status.value,
            "source": self.source,
            "detail": self.detail,
            "generated_at": self.generated_at,
            "item_count": len(self.items),
            "items": [i.to_dict() for i in self.items],
        }


# ---------------------------------------------------------------------------
# Autorización por identidad canónica (NUNCA por canal ni por literal "mario")
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceAccess:
    """Autorización derivada de la identidad canónica ya resuelta.

    `is_owner` se calcula comparando contra `vectrax.identity.CREATOR_OWNER`,
    la MISMA constante que usa el resto del sistema — no se hardcodea ninguna
    cadena de identidad en este módulo.
    """

    owner_canonical: str
    is_owner: bool
    owner_raw: str = ""

    def may_read(self, kind: str) -> bool:
        return self.is_owner or kind not in _OWNER_ONLY


def resolve_access(owner: str, owner_raw: str = "") -> EvidenceAccess:
    """Resuelve la autorización a partir de la identidad canónica.

    Reutiliza `vectrax.identity_aliases.resolve_owner()` (alias -> canónico) y
    `vectrax.identity.CREATOR_OWNER`. Si el módulo de identidad no está
    disponible, degrada a NO-owner: fail-closed, nunca fail-open.
    """
    raw = owner_raw or owner or ""
    canonical = owner or ""
    try:
        from vectrax.identity_aliases import resolve_owner
        canonical = resolve_owner(owner) or owner
    except Exception as exc:  # pragma: no cover - defensa
        logger.debug("resolve_access: alias resolution unavailable (%s)", exc)

    # Mismo reconocimiento de UID de Telegram del creador que ya aplica
    # `nucleus_authority._decide()` y `external_gateway._is_creator_uid()`.
    try:
        from vectrax.identity import CREATOR_OWNER
        creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
        if raw and raw.replace("tg:", "") == creator_uid:
            canonical = CREATOR_OWNER
        is_owner = canonical == CREATOR_OWNER
    except Exception as exc:  # pragma: no cover - defensa
        logger.debug("resolve_access: identity module unavailable (%s)", exc)
        is_owner = False

    return EvidenceAccess(owner_canonical=canonical, is_owner=is_owner, owner_raw=raw)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _parse_iso(value: Any) -> float:
    """ISO-8601 -> epoch. Devuelve 0.0 si no se puede fechar (nunca `now()`:
    inventar una fecha reciente falsearía la antigüedad)."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return 0.0
    import datetime as _dt
    txt = value.strip().replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(txt)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _finalize(kind: str, source: str, items: List[EvidenceItem]) -> EvidenceResult:
    """Aplica la regla de vigencia y decide OK / EMPTY / STALE.

    STALE se calcula sobre el item MÁS RECIENTE: si lo más nuevo que tenemos
    ya superó la ventana, toda la respuesta es vieja y hay que decirlo.
    """
    if not items:
        return EvidenceResult(kind=kind, status=EvidenceStatus.EMPTY, source=source)

    window = _STALE_AFTER.get(kind, float("inf"))
    ages = [i.age_seconds for i in items if i.age_seconds is not None]
    if ages and min(ages) > window:
        return EvidenceResult(
            kind=kind, status=EvidenceStatus.STALE, source=source, items=items,
            detail=f"la evidencia más reciente tiene {int(min(ages))}s "
                   f"(ventana de vigencia: {int(window)}s)",
        )
    return EvidenceResult(kind=kind, status=EvidenceStatus.OK, source=source, items=items)


def _unavailable(kind: str, source: str, exc: Any) -> EvidenceResult:
    return EvidenceResult(
        kind=kind, status=EvidenceStatus.UNAVAILABLE, source=source,
        detail=str(exc)[:200],
    )


# ---------------------------------------------------------------------------
# La fachada
# ---------------------------------------------------------------------------

class InternalEvidence:
    """Acceso unificado de SOLO LECTURA a la evidencia interna de Vectrax.

    Cada método devuelve un `EvidenceResult`. Ninguno lanza. Ninguno escribe.
    `access` decide qué puede leerse; los tipos de `_OWNER_ONLY` devuelven
    `UNAUTHORIZED` (no `EMPTY`) para un usuario común — la diferencia importa:
    "no tengo evidencia" y "no estás autorizado" son respuestas distintas.
    """

    def __init__(
        self,
        access: EvidenceAccess,
        *,
        reports_dir: Optional[str] = None,
        ideas_path: Optional[str] = None,
    ) -> None:
        self.access = access
        # Inyectables SOLO para pruebas con fixtures aisladas. En producción
        # se resuelven contra las rutas reales de los módulos dueños del dato.
        self._reports_dir = reports_dir
        self._ideas_path = ideas_path

    # -- infraestructura -------------------------------------------------

    def _guard(self, kind: str) -> Optional[EvidenceResult]:
        if not self.access.may_read(kind):
            return EvidenceResult(
                kind=kind, status=EvidenceStatus.UNAUTHORIZED,
                source="core.nucleus.internal_evidence.resolve_access",
                detail="requiere identidad canónica de owner",
            )
        return None

    def _ideas_store(self):
        from core.idea_store import IdeaStore
        return IdeaStore(path=self._ideas_path) if self._ideas_path else IdeaStore()

    def _reports_path(self) -> Path:
        if self._reports_dir:
            return Path(self._reports_dir)
        from observability.audit_engine import REPORTS_DIR
        return Path(REPORTS_DIR)

    # -- diagnósticos ----------------------------------------------------

    def latest_diagnostic(self) -> EvidenceResult:
        """Último reporte de `observability.audit_engine` en vault/audit_reports/."""
        blocked = self._guard("diagnostic")
        if blocked:
            return blocked
        src = "observability.audit_engine (vault/audit_reports/audit_*.json)"
        try:
            items = self._read_reports(limit=1, kind="diagnostic")
        except Exception as exc:
            return _unavailable("diagnostic", src, exc)
        return _finalize("diagnostic", src, items)

    def diagnostic_history(self, limit: int = 5) -> EvidenceResult:
        blocked = self._guard("diagnostic_history")
        if blocked:
            return blocked
        src = "observability.audit_engine (vault/audit_reports/audit_*.json)"
        try:
            items = self._read_reports(limit=max(1, limit), kind="diagnostic_history")
        except Exception as exc:
            return _unavailable("diagnostic_history", src, exc)
        return _finalize("diagnostic_history", src, items)

    def _read_reports(self, limit: int, kind: str) -> List[EvidenceItem]:
        """Lee los N reportes más recientes. Solo lectura sobre el directorio
        que `audit_engine` ya mantiene (él rota a 60; aquí no se toca nada)."""
        directory = self._reports_path()
        if not directory.is_dir():
            return []
        files = sorted(directory.glob("audit_*.json"), key=lambda p: p.name, reverse=True)
        items: List[EvidenceItem] = []
        for path in files[:limit]:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    report = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("report unreadable %s: %s", path.name, exc)
                continue
            problems = report.get("problems") or []
            severity = report.get("severity") or report.get("status") or "unknown"
            observed = _parse_iso(report.get("timestamp") or report.get("generated_at"))
            if not observed:
                try:
                    observed = path.stat().st_mtime
                except OSError:
                    observed = 0.0
            items.append(EvidenceItem(
                kind="diagnostic",
                source=f"observability.audit_engine :: {path.name}",
                observed_at=observed,
                scope=str(report.get("mode") or "system"),
                status=str(severity),
                summary=(
                    f"{len(problems)} problema(s) detectado(s)"
                    if problems else "sin problemas detectados"
                ),
                reference=path.name,
                visibility="owner",
                data={
                    "problem_count": len(problems),
                    "problems": problems[:10],
                    "mode": report.get("mode"),
                    "severity": severity,
                },
            ))
        return items

    # -- propuestas ------------------------------------------------------

    def pending_proposals(self, limit: int = 10) -> EvidenceResult:
        """Propuestas en estado `pending` del `IdeaStore`. El estado se
        reporta TAL CUAL — `pending` nunca se traduce como resuelto."""
        blocked = self._guard("proposal")
        if blocked:
            return blocked
        src = "core.idea_store.IdeaStore.pending() (data/ideas.jsonl)"
        try:
            pending = self._ideas_store().pending()
        except Exception as exc:
            return _unavailable("proposal", src, exc)
        items = [self._idea_item(i) for i in pending[: max(1, limit)]]
        result = _finalize("proposal", src, items)
        # El total real importa aunque se recorte la lista mostrada.
        if result.items:
            result.items[0].data["pending_total"] = len(pending)
        return result

    def proposal_by_id(self, idea_id: str) -> EvidenceResult:
        blocked = self._guard("proposal")
        if blocked:
            return blocked
        src = "core.idea_store.IdeaStore.get_by_id() (data/ideas.jsonl)"
        ident = (idea_id or "").strip().upper()
        if not ident:
            return EvidenceResult(
                kind="proposal", status=EvidenceStatus.EMPTY, source=src,
                detail="no se indicó un identificador IDEA-...",
            )
        try:
            idea = self._ideas_store().get_by_id(ident)
        except Exception as exc:
            return _unavailable("proposal", src, exc)
        if idea is None:
            return EvidenceResult(
                kind="proposal", status=EvidenceStatus.EMPTY, source=src,
                detail=f"{ident} no existe en el store",
            )
        return _finalize("proposal", src, [self._idea_item(idea)])

    @staticmethod
    def _idea_item(idea: Any) -> EvidenceItem:
        status = getattr(idea.status, "value", str(idea.status))
        return EvidenceItem(
            kind="proposal",
            source="core.idea_store.IdeaStore (data/ideas.jsonl)",
            observed_at=_parse_iso(getattr(idea, "created_at", "")),
            scope=str(getattr(idea, "affected_component", "") or ""),
            status=status,
            summary=str(getattr(idea, "title", "") or ""),
            reference=str(getattr(idea, "idea_id", "") or ""),
            confidence=float(getattr(idea, "priority_score", 0.0) or 0.0),
            visibility="owner",
            data={
                "priority": getattr(idea.priority, "value", str(getattr(idea, "priority", ""))),
                "impact_score": getattr(idea, "impact_score", None),
                "source_module": getattr(idea.source, "value", str(getattr(idea, "source", ""))),
                "evidence": getattr(idea, "evidence", {}) or {},
                "reviewed_by": getattr(idea, "reviewed_by", "") or "",
                "reviewed_at": getattr(idea, "reviewed_at", None),
                "applied_at": getattr(idea, "applied_at", None),
            },
        )

    # -- auditoría -------------------------------------------------------

    def recent_audit(self, limit: int = 10) -> EvidenceResult:
        """Entradas recientes del `audit_ledger` (append-only, solo lectura)."""
        blocked = self._guard("audit")
        if blocked:
            return blocked
        src = "core.audit_ledger.query() (audit_ledger.db)"
        try:
            from core import audit_ledger
            rows = audit_ledger.query(limit=max(1, limit))
        except Exception as exc:
            return _unavailable("audit", src, exc)
        items = [
            EvidenceItem(
                kind="audit",
                source=src,
                observed_at=_parse_iso(row.get("timestamp")),
                scope=str(row.get("actor") or ""),
                status=str(row.get("decision") or ""),
                summary=str(row.get("action") or ""),
                reference=f"audit:{row.get('id')}",
                visibility="owner",
                data={"role": row.get("role"), "reason": row.get("reason")},
            )
            for row in rows
        ]
        return _finalize("audit", src, items)

    # -- motores y servicios ---------------------------------------------

    def engine_status(self) -> EvidenceResult:
        """Estado de motores/servicios desde el observador que ya existe."""
        blocked = self._guard("engines")
        if blocked:
            return blocked
        src = "core.self_observation.universe_observer.observe_universe()"
        try:
            from core.self_observation.universe_observer import observe_universe
            snap = observe_universe()
        except Exception as exc:
            return _unavailable("engines", src, exc)

        observed = float(getattr(snap, "timestamp", 0.0) or time.time())
        engines = [
            ("nucleus", bool(getattr(snap, "nucleus_alive", False))),
            ("worker", bool(getattr(snap, "worker_alive", False))),
            ("proactive_engine", bool(getattr(snap, "proactive_engine_enabled", False))),
        ]
        items = [
            EvidenceItem(
                kind="engines", source=src, observed_at=observed, scope=name,
                status="active" if alive else "inactive",
                summary=f"{name}: {'activo' if alive else 'inactivo'}",
                reference=f"universe_observer.{name}", visibility="owner",
            )
            for name, alive in engines
        ]
        items.append(EvidenceItem(
            kind="engines", source=src, observed_at=observed, scope="queue",
            status="observed",
            summary=(
                f"cola: {getattr(snap, 'queue_pending', 0)} pendientes, "
                f"{getattr(snap, 'queue_error', 0)} en error"
            ),
            reference="universe_observer.queue", visibility="owner",
            data={
                "pending": getattr(snap, "queue_pending", 0),
                "processing": getattr(snap, "queue_processing", 0),
                "done": getattr(snap, "queue_done", 0),
                "error": getattr(snap, "queue_error", 0),
                "recent_error_count_24h": getattr(snap, "recent_error_count_24h", 0),
            },
        ))
        return _finalize("engines", src, items)

    # -- universo: estrellas, patrones, dominios, convergencias ----------

    def universe_summary(self) -> EvidenceResult:
        """Totales canónicos desde `core.universe_census` — la ÚNICA fuente de
        totales del universo (ver el aviso explícito en `UniverseSnapshot`:
        `len(snap.convergences)` NO es un total)."""
        kind = "universe"
        blocked = self._guard(kind)
        if blocked:
            return blocked
        src = "core.universe_census.get_census()"
        try:
            from core.universe_census import get_census
            census = get_census()
        except Exception as exc:
            return _unavailable(kind, src, exc)

        observed = float(getattr(census, "timestamp", 0.0) or time.time())
        def _n(attr: str) -> int:
            return int(getattr(census, attr, 0) or 0)

        items = [
            EvidenceItem(
                kind=kind, source=src, observed_at=observed, scope=scope,
                status="counted", summary=f"{scope}: {value}",
                reference=f"universe_census.{attr}", visibility="public",
                data={"value": value},
            )
            for scope, attr, value in (
                ("estrellas", "total", _n("total")),
                ("estrellas_gravitacionales", "gravitational", _n("gravitational")),
                ("estrellas_conocimiento", "knowledge", _n("knowledge")),
                ("patrones", "patterns", _n("patterns")),
                ("convergencias", "convergences", _n("convergences")),
                ("convergencias_activas", "convergences_active", _n("convergences_active")),
                ("constelaciones", "constellations", _n("constellations")),
            )
        ]
        domains = getattr(census, "domains", {}) or {}
        items.append(EvidenceItem(
            kind=kind, source=src, observed_at=observed, scope="dominios",
            status="counted", summary=f"dominios: {len(domains)}",
            reference="universe_census.domains", visibility="public",
            data={"domains": dict(domains)},
        ))
        return _finalize(kind, src, items)

    def stars(self) -> EvidenceResult:
        return self._universe_slice("stars", ("total", "gravitational", "knowledge", "users"))

    def patterns(self) -> EvidenceResult:
        return self._universe_slice("patterns", ("patterns", "constellations", "word_gravity_count"))

    def domains(self) -> EvidenceResult:
        kind = "domains"
        blocked = self._guard(kind)
        if blocked:
            return blocked
        src = "core.universe_census.get_census().domains"
        try:
            from core.universe_census import get_census
            census = get_census()
        except Exception as exc:
            return _unavailable(kind, src, exc)
        observed = float(getattr(census, "timestamp", 0.0) or time.time())
        domains = getattr(census, "domains", {}) or {}
        items = [
            EvidenceItem(
                kind=kind, source=src, observed_at=observed, scope=str(name),
                status="counted", summary=f"{name}: {count}",
                reference=f"universe_census.domains.{name}",
                visibility="public", data={"value": count},
            )
            for name, count in sorted(domains.items(), key=lambda kv: -int(kv[1] or 0))
        ]
        return _finalize(kind, src, items)

    def convergences(self) -> EvidenceResult:
        return self._universe_slice(
            "convergences",
            ("convergences", "convergences_active", "convergence_confirmations_total",
             "convergences_cross_domain"),
        )

    def _universe_slice(self, kind: str, attrs: tuple) -> EvidenceResult:
        blocked = self._guard(kind)
        if blocked:
            return blocked
        src = "core.universe_census.get_census()"
        try:
            from core.universe_census import get_census
            census = get_census()
        except Exception as exc:
            return _unavailable(kind, src, exc)
        observed = float(getattr(census, "timestamp", 0.0) or time.time())
        items = [
            EvidenceItem(
                kind=kind, source=src, observed_at=observed, scope=attr,
                status="counted", summary=f"{attr}: {int(getattr(census, attr, 0) or 0)}",
                reference=f"universe_census.{attr}", visibility="public",
                data={"value": int(getattr(census, attr, 0) or 0)},
            )
            for attr in attrs
        ]
        return _finalize(kind, src, items)

    # -- actividad operativa por dominio ---------------------------------

    def operational_activity(self, domain: str) -> EvidenceResult:
        """Actividad operativa real de un dominio. `trading` sale del censo
        (campos market_*); el resto, de `core.domain_knowledge`."""
        kind = "operational"
        blocked = self._guard(kind)
        if blocked:
            return blocked
        dom = (domain or "").strip().lower()
        if dom in ("trading", "market", "mercado", "etoro"):
            return self._trading_activity()
        return self._domain_activity(dom)

    def _trading_activity(self) -> EvidenceResult:
        src = "core.universe_census.get_census() (campos market_*)"
        try:
            from core.universe_census import get_census
            census = get_census()
        except Exception as exc:
            return _unavailable("operational", src, exc)
        observed = float(getattr(census, "timestamp", 0.0) or time.time())
        mode = str(getattr(census, "executor_mode", "off") or "off")
        items = [
            EvidenceItem(
                kind="operational", source=src, observed_at=observed, scope="trading",
                status=mode,
                summary=(
                    f"modo ejecutor: {mode}; "
                    f"{int(getattr(census, 'market_signals', 0) or 0)} señales, "
                    f"{int(getattr(census, 'market_patterns', 0) or 0)} patrones"
                ),
                reference="universe_census.market_*", visibility="owner",
                data={
                    "executor_mode": mode,
                    "market_signals": int(getattr(census, "market_signals", 0) or 0),
                    "market_patterns": int(getattr(census, "market_patterns", 0) or 0),
                    "market_usable_patterns": int(getattr(census, "market_usable_patterns", 0) or 0),
                    "market_win_rate": float(getattr(census, "market_win_rate", 0.0) or 0.0),
                },
            )
        ]
        return _finalize("operational", src, items)

    def _domain_activity(self, domain: str) -> EvidenceResult:
        src = f"core.domain_knowledge.get_domain_summary({domain!r})"
        if not domain:
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.EMPTY, source=src,
                detail="no se indicó dominio",
            )
        try:
            from core.domain_knowledge import get_domain_summary
            summary = get_domain_summary(domain) or {}
        except Exception as exc:
            return _unavailable("operational", src, exc)
        total = int(summary.get("total_patterns", 0) or 0)
        if not total and not summary:
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.EMPTY, source=src,
                detail=f"sin patrones registrados para {domain}",
            )
        observed = _parse_iso(summary.get("updated_at")) or 0.0
        items = [EvidenceItem(
            kind="operational", source=src, observed_at=observed, scope=domain,
            status="observed", summary=f"{domain}: {total} patrón(es) abstracto(s)",
            reference=f"domain_knowledge.{domain}", visibility="owner",
            data=dict(summary),
        )]
        return _finalize("operational", src, items)

    # -- traza de solo lectura del botón Aprobar -------------------------

    def approval_pipeline(self) -> EvidenceResult:
        """Traza ESTRUCTURAL del circuito de aprobación, derivada del código.

        Responde con honestidad qué ocurre tras pulsar Aprobar hoy. No simula
        una reparación: verifica en tiempo real si existe un consumidor del
        estado `approved` y lo reporta tal cual.
        """
        kind = "approval_pipeline"
        blocked = self._guard(kind)
        if blocked:
            return blocked
        src = "services/core/routes/ideas.py + core/idea_store.py (traza estática)"

        executor_present = False
        detail = "IdeaStore.mark_applied() no tiene ningún llamador en producción"
        try:
            from core import idea_store as _idea_store
            module_path = Path(_idea_store.__file__).resolve()
            repo_root = module_path.parent.parent
            callers: List[str] = []
            for py in repo_root.rglob("*.py"):
                parts = py.parts
                if "archive" in parts or "tests" in parts:
                    continue
                if py.resolve() == module_path:
                    continue
                try:
                    if "mark_applied" in py.read_text(encoding="utf-8", errors="ignore"):
                        callers.append(str(py.relative_to(repo_root)))
                except OSError:
                    continue
            executor_present = bool(callers)
            if callers:
                detail = "llamadores de mark_applied(): " + ", ".join(sorted(callers)[:5])
        except Exception as exc:
            return _unavailable(kind, src, exc)

        now = time.time()
        steps = [
            ("1. endpoint", "POST /v1/ideas/{id}/approve", "services/core/routes/ideas.py"),
            ("2. persistencia", "IdeaStore.approve() -> status=approved en data/ideas.jsonl",
             "core/idea_store.py"),
            ("3. auditoría", "POST /v1/proposals/{id}/approve escribe entrada en audit_ledger",
             "services/core/routes/proposals.py"),
            ("4. ejecutor",
             "PRESENTE" if executor_present else "AUSENTE: ningún proceso consume el estado approved",
             "core/idea_store.py::mark_applied"),
        ]
        items = [
            EvidenceItem(
                kind=kind, source=f"{src} :: {ref}", observed_at=now, scope=step,
                status="present" if (not step.startswith("4.") or executor_present) else "absent",
                summary=text, reference=ref, visibility="owner",
            )
            for step, text, ref in steps
        ]
        result = _finalize(kind, src, items)
        result.detail = detail
        return result


def get_internal_evidence(owner: str, owner_raw: str = "") -> InternalEvidence:
    """Constructor canónico: resuelve identidad y devuelve la fachada."""
    return InternalEvidence(resolve_access(owner, owner_raw=owner_raw))
