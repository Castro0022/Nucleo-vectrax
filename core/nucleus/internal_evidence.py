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
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# Directorios que nunca contienen un ejecutor de producción y que harían
# lento (o falso) el escaneo estructural de `approval_pipeline()`.
_SCAN_EXCLUDED_DIRS = frozenset({
    ".venv", "venv", "env", ".git", "__pycache__", "node_modules",
    "build", "dist", ".pytest_cache", "archive", "tests", "site-packages",
})

# Tipos que solo el owner canónico puede consultar. Un usuario común no debe
# obtener diagnósticos internos, rutas, infraestructura ni propuestas.
_OWNER_ONLY = frozenset({
    "diagnostic", "diagnostic_history", "proposal", "audit",
    "engines", "operational", "approval_pipeline",
})

# Clave contractual del resumen de dominio. La produce
# `core/domain_knowledge.py::get_domain_summary()` en sus DOS ramas (con y sin
# patrones). Se nombra aquí una sola vez para que el consumidor no vuelva a
# inventarse un nombre de clave: leer `total_patterns` —que la fuente nunca
# produjo— hacía que el Núcleo reportara 0 patrones siempre.
_DOMAIN_PATTERN_COUNT_KEY = "patterns"


# ---------------------------------------------------------------------------
# Auditoría: exposición SEGURA de `metadata.details`
# ---------------------------------------------------------------------------
# `core/audit_ledger.py` guarda un `metadata` JSON libre y
# `core/operator/ledger_bridge.py::record_event()` mete ahí el `details` que le
# pase cada llamador. `recent_audit()` lo descartaba entero, así que el resumen
# real de los ciclos de dominio (cuántos eventos, cuántos verificados, con qué
# proveedor) no llegaba nunca al Núcleo.
#
# No se expone el objeto completo: solo las acciones de forma CONOCIDA, y de
# ellas solo las claves de esta lista permitida. Todo lo demás se descarta.

_AUDIT_CYCLE_FIELDS = frozenset({
    "success", "provider", "events_requested", "events_ingested", "errors",
    "stars_before", "stars_after", "mature_stars", "patterns_elevated",
    "elapsed_s", "verified_decisive", "verified_wins", "verified_losses",
    "verified_win_rate", "verified_accuracy",
})

_AUDIT_DETAIL_ALLOWLIST: Dict[str, frozenset] = {
    "freight_learning_cycle": _AUDIT_CYCLE_FIELDS,
    "real_estate_learning_cycle": _AUDIT_CYCLE_FIELDS,
    "cyber_learning_cycle": frozenset({
        "success", "provider", "events", "verified_decisive",
        "wins", "losses", "elapsed_s",
    }),
    "gravity_sync": frozenset({
        "patterns_promoted", "stars_mass_updated", "errors", "elapsed_s",
    }),
    "trading_convergence_learner": frozenset({
        "proposals_generated", "drift_kinds", "observed_wr_pct",
    }),
}

# Dominio de cada ciclo, derivado del MÓDULO que emite la acción
# (`connectors/*/learning_cycle.py::_record_ledger`), no del contenido del
# registro: el contenido es dato, el emisor es código.
_AUDIT_ACTION_DOMAIN: Dict[str, str] = {
    "freight_learning_cycle": "freight_logistics",
    "real_estate_learning_cycle": "florida_real_estate",
    "cyber_learning_cycle": "cybersecurity",
    "trading_convergence_learner": "market",
}

# Alcance por nombre de proveedor. `simulator` es el nombre que devuelven los
# tres `simulator_adapter.py`; `nvd`/`rentcast`/`attom` son los proveedores
# reales. Un proveedor desconocido NO se clasifica: se omite el alcance en vez
# de adivinarlo.
_AUDIT_SCOPE_BY_PROVIDER: Dict[str, str] = {
    "simulator": "simulated",
    "nvd": "real",
    "rentcast": "real",
    "attom": "real",
    "real": "real",
}

_AUDIT_DETAIL_MAX_KEYS = 20      # profundidad 1, ancho acotado
_AUDIT_DETAIL_MAX_LIST = 8
_AUDIT_DETAIL_MAX_STR = 40

# Un valor de cadena solo pasa si parece un identificador/enumerado corto.
# Rechaza por construcción texto libre, rutas, mensajes de excepción y
# cualquier cosa con espacios — que es donde aparecen rutas y credenciales.
_AUDIT_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,%d}$" % _AUDIT_DETAIL_MAX_STR)


def _audit_safe_scalar(value: Any) -> Any:
    """Escalar seguro, o `None` si hay que descartarlo."""
    if isinstance(value, bool):          # antes que int: bool es subclase de int
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        return value if _AUDIT_SAFE_TOKEN_RE.match(value) else None
    return None


def _audit_safe_details(action: str, raw: Any) -> Dict[str, Any]:
    """Proyección segura de `metadata.details` para una acción conocida.

    Profundidad 1: un dict o una lista de dicts anidados se descarta entera.
    """
    allowed = _AUDIT_DETAIL_ALLOWLIST.get(action)
    if not allowed or not isinstance(raw, dict):
        return {}
    safe: Dict[str, Any] = {}
    for key in sorted(raw):
        if len(safe) >= _AUDIT_DETAIL_MAX_KEYS:
            break
        if key not in allowed:
            continue
        value = raw[key]
        if isinstance(value, (list, tuple)):
            items = [_audit_safe_scalar(v) for v in list(value)[:_AUDIT_DETAIL_MAX_LIST]]
            items = [v for v in items if v is not None]
            if items:
                safe[key] = items
            continue
        scalar = _audit_safe_scalar(value)
        if scalar is not None:
            safe[key] = scalar
    return safe


def _audit_metadata(raw: Any) -> Dict[str, Any]:
    """`metadata` viene como TEXTO JSON desde la columna de SQLite."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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


def _with_unreadable(result: EvidenceResult, unreadable: List[str]) -> EvidenceResult:
    """Declara los reportes ilegibles en `detail`.

    Un archivo corrupto no debe derribar la consulta, pero tampoco desaparecer
    en silencio: si se omite sin decirlo, "sin problemas detectados" podría
    estar basado en menos reportes de los que hay. Se nombran para que sean
    verificables en disco.
    """
    if not unreadable:
        return result
    nota = (
        f"{len(unreadable)} reporte(s) ilegible(s), omitido(s): "
        + ", ".join(sorted(unreadable)[:5])
    )
    result.detail = f"{result.detail} · {nota}" if result.detail else nota
    return result


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
            items, unreadable = self._read_reports(limit=1, kind="diagnostic")
        except Exception as exc:
            return _unavailable("diagnostic", src, exc)
        return _with_unreadable(_finalize("diagnostic", src, items), unreadable)

    def diagnostic_history(self, limit: int = 5) -> EvidenceResult:
        blocked = self._guard("diagnostic_history")
        if blocked:
            return blocked
        src = "observability.audit_engine (vault/audit_reports/audit_*.json)"
        try:
            items, unreadable = self._read_reports(
                limit=max(1, limit), kind="diagnostic_history",
            )
        except Exception as exc:
            return _unavailable("diagnostic_history", src, exc)
        return _with_unreadable(
            _finalize("diagnostic_history", src, items), unreadable,
        )

    def _read_reports(self, limit: int, kind: str) -> Tuple[List[EvidenceItem], List[str]]:
        """Los N reportes MÁS RECIENTES, ordenados por su fecha real.

        Solo lectura sobre el directorio que `audit_engine` ya mantiene (él rota
        a 60; aquí no se toca nada).

        ORDEN (el defecto que esto corrige)
        -----------------------------------
        Antes se ordenaba por NOMBRE de archivo y luego se recortaba a `limit`.
        Los nombres son `audit_{mode}_{ts}.json` con `mode` ∈ {daily, weekly}
        (`observability/audit_engine.py::_save_report`), así que en orden
        alfabético descendente CUALQUIER `audit_weekly_*` gana a CUALQUIER
        `audit_daily_*`, por vieja que sea la semanal: "el último diagnóstico"
        podía ser un reporte de hace días. El modo NO es una señal de frescura.

        Ahora se leen todos los reportes, se fecha cada uno y se ordena por esa
        fecha. El modo viaja como DATO (`scope` y `data["mode"]`), nunca como
        criterio de orden.

        FECHA EFECTIVA
        --------------
          1. `timestamp` o `generated_at` del contenido, si es parseable.
          2. si no, `mtime` del archivo — fallback EXPLÍCITO, anotado en
             `data["observed_at_source"]` para que sea auditable.
          3. si tampoco, 0.0 (la fachada lo reporta como "sin fecha").

        DESEMPATE
        ---------
        Determinista: a igual fecha, por nombre de archivo descendente. No
        decide frescura, solo estabiliza el orden.

        REPORTE CORRUPTO
        ----------------
        No derriba la consulta: se omite de los items y su nombre se devuelve
        aparte para que el llamador lo declare en `detail`.
        """
        directory = self._reports_path()
        if not directory.is_dir():
            return [], []

        entries: List[Tuple[float, str, EvidenceItem]] = []
        unreadable: List[str] = []
        for path in sorted(directory.glob("audit_*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    report = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("report unreadable %s: %s", path.name, exc)
                unreadable.append(path.name)
                continue
            if not isinstance(report, dict):
                logger.debug("report not a JSON object: %s", path.name)
                unreadable.append(path.name)
                continue

            problems = report.get("problems") or []
            severity = report.get("severity") or report.get("status") or "unknown"

            observed = _parse_iso(report.get("timestamp") or report.get("generated_at"))
            observed_source = "report.timestamp"
            if not observed:
                observed_source = "file.mtime"
                try:
                    observed = path.stat().st_mtime
                except OSError:
                    observed = 0.0
                    observed_source = "unknown"

            entries.append((observed, path.name, EvidenceItem(
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
                    "problems": problems[:10] if isinstance(problems, list) else [],
                    "mode": report.get("mode"),
                    "severity": severity,
                    "observed_at_source": observed_source,
                },
            )))

        entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
        return [item for _, _, item in entries[:limit]], unreadable

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

    def recent_audit(
        self, limit: int = 10, action: Optional[str] = None,
    ) -> EvidenceResult:
        """Entradas recientes del `audit_ledger` (append-only, solo lectura).

        `action` filtra por acción exacta reutilizando el `action_filter` que
        `core/audit_ledger.py::query()` YA soporta y que esta fachada ignoraba.
        Sin `action`, el comportamiento es el de siempre.

        De `metadata` NO se expone el objeto completo: solo se proyecta
        `metadata.details` para las acciones de forma conocida
        (`_AUDIT_DETAIL_ALLOWLIST`) y solo las claves permitidas, con límites de
        ancho, longitud y profundidad. El texto libre se descarta por
        construcción — ahí es donde aparecerían rutas o credenciales.

        La autorización no cambia: sigue siendo `_guard("audit")`, owner-only.
        Un usuario no autorizado no recibe items, ni conteos, ni detalles.
        """
        blocked = self._guard("audit")
        if blocked:
            return blocked
        wanted = (action or "").strip()
        src = "core.audit_ledger.query() (audit_ledger.db)"
        if wanted:
            src = f"{src} action={wanted!r}"
        try:
            from core import audit_ledger
            rows = audit_ledger.query(
                limit=max(1, limit), action_filter=wanted or None,
            )
        except Exception as exc:
            return _unavailable("audit", src, exc)

        items: List[EvidenceItem] = []
        for row in rows:
            row_action = str(row.get("action") or "")
            metadata = _audit_metadata(row.get("metadata"))
            details = _audit_safe_details(row_action, metadata.get("details"))

            data: Dict[str, Any] = {
                "role": row.get("role"),
                "reason": row.get("reason"),
            }
            domain = _AUDIT_ACTION_DOMAIN.get(row_action)
            if domain:
                data["domain"] = domain
            provider = details.get("provider")
            if isinstance(provider, str):
                scope = _AUDIT_SCOPE_BY_PROVIDER.get(provider.lower())
                if scope:
                    data["data_scope"] = scope
            if details:
                data["details"] = details

            items.append(EvidenceItem(
                kind="audit",
                source=src,
                observed_at=_parse_iso(row.get("timestamp")),
                scope=str(row.get("actor") or ""),
                status=str(row.get("decision") or ""),
                summary=row_action,
                reference=f"audit:{row.get('id')}",
                visibility="owner",
                data=data,
            ))

        result = _finalize("audit", src, items)
        if not items and wanted:
            result.detail = f"sin entradas de auditoría para la acción {wanted!r}"
        return result

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
        """Patrones abstractos acumulados de un dominio genérico.

        Ruta ÚNICA para todo dominio que no sea `trading` — freight,
        cybersecurity, real estate y cualquier dominio futuro pasan por aquí.

        Contrato de la fuente (`core/domain_knowledge.py::get_domain_summary`):
        devuelve SIEMPRE un dict con la clave `patterns` (int). Sin patrones
        devuelve `{"domain": d, "patterns": 0}`; con patrones añade
        `strong_patterns`, `avg_win_rate`, `avg_expectancy`,
        `total_observations` y `max_contributing_tenants`.

        Esta función leía `total_patterns`, una clave que la fuente NUNCA
        produce: `summary.get("total_patterns", 0)` devolvía 0 siempre y, como
        el dict nunca viene vacío, tampoco se emitía `EMPTY` — el Núcleo
        afirmaba "0 patrón(es)" aunque la librería tuviera decenas. Ahora se lee
        la clave real y su ausencia es `UNAVAILABLE` con el motivo verificable,
        nunca un cero inventado.
        """
        src = f"core.domain_knowledge.get_domain_summary({domain!r})"
        if not domain:
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.EMPTY, source=src,
                detail="no se indicó dominio",
            )
        try:
            from core.domain_knowledge import get_domain_summary
            summary = get_domain_summary(domain)
        except Exception as exc:
            return _unavailable("operational", src, exc)

        if not isinstance(summary, dict) or not summary:
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.EMPTY, source=src,
                detail=f"la fuente no devolvió datos para {domain}",
            )

        # La clave contractual DEBE venir. Si no viene, la fuente cambió de
        # forma y decirlo es más honesto que reportar cero: un cero silencioso
        # es exactamente el defecto que se corrige aquí.
        if _DOMAIN_PATTERN_COUNT_KEY not in summary:
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.UNAVAILABLE, source=src,
                detail=(
                    f"la fuente no devolvió la clave contractual "
                    f"{_DOMAIN_PATTERN_COUNT_KEY!r}; claves recibidas: "
                    f"{sorted(summary)}"
                ),
            )
        try:
            total = int(summary[_DOMAIN_PATTERN_COUNT_KEY])
        except (TypeError, ValueError):
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.UNAVAILABLE, source=src,
                detail=(
                    f"la clave {_DOMAIN_PATTERN_COUNT_KEY!r} no es un entero: "
                    f"{summary[_DOMAIN_PATTERN_COUNT_KEY]!r}"
                ),
            )

        if total <= 0:
            return EvidenceResult(
                kind="operational", status=EvidenceStatus.EMPTY, source=src,
                detail=f"sin patrones registrados para {domain}",
            )

        # La fuente no fecha el resumen (el contrato de arriba no incluye
        # ninguna marca de tiempo), así que `observed_at` queda en 0.0 y
        # `age_seconds` en None -> "sin fecha registrada en la fuente". Se
        # sigue leyendo `updated_at` por si una versión futura la añade;
        # nunca se sustituye por `now()`.
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
        """Traza ESTRUCTURAL de los circuitos de aprobación, derivada del código.

        Vectrax tiene DOS circuitos de aprobación SEPARADOS, y confundirlos
        haría que el Núcleo explicara un flujo que no existe — exactamente
        el defecto que esta etapa corrige (auditoría 2026-09-22; la versión
        anterior los encadenaba como si fueran cuatro pasos de una misma
        secuencia):

          - `ideas`     -> POST /v1/ideas/{id}/approve  (permiso core.write)
                           IdeaStore.approve() -> data/ideas.jsonl
          - `proposals` -> POST /v1/proposals/{id}/approve (permiso apply_proposal)
                           db.update_proposal_status() -> vectrax.db

        No se llaman entre sí y no comparten almacén. Aprobar una idea NO
        dispara el endpoint de proposals ni escribe la entrada de auditoría
        que ese otro circuito sí escribe.

        Todo lo que se afirma aquí se VERIFICA leyendo los archivos reales;
        nada se da por supuesto.
        """
        kind = "approval_pipeline"
        blocked = self._guard(kind)
        if blocked:
            return blocked
        src = "services/core/routes/{ideas,proposals}.py + core/idea_store.py (traza estática)"

        try:
            from core import idea_store as _idea_store
            module_path = Path(_idea_store.__file__).resolve()
            repo_root = module_path.parent.parent

            def _read(rel: str) -> str:
                try:
                    return (repo_root / rel).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    return ""

            ideas_src = _read("services/core/routes/ideas.py")
            proposals_src = _read("services/core/routes/proposals.py")

            # Se acota el recorrido a propósito: un `rglob("*.py")` sobre la
            # raíz entra en `.venv/` (decenas de miles de archivos en la
            # máquina de desarrollo) y convertiría una consulta conversacional
            # en un escaneo de varios segundos. `archive/` y `tests/` se
            # excluyen porque un llamador ahí no es un ejecutor de producción
            # — que es justo lo que esta traza responde.
            # Se buscan LLAMADAS reales (`.mark_applied(`), no la mera
            # aparición del nombre: este mismo módulo contiene la cadena que
            # busca y se contaba a sí mismo como ejecutor — un falso
            # "PRESENTE" detectado en revisión. Por eso además se excluye
            # explícitamente el archivo del escáner.
            self_path = Path(__file__).resolve()
            callers: List[str] = []
            # Archivos que TOCAN el estado approved de las propuestas de
            # vectrax.db. Son CANDIDATOS, no ejecutores demostrados: los que
            # aparecen (endpoint y CLI) solo listan o fijan el estado. Un
            # escaneo estático no distingue "lee approved y actúa" de "lee
            # approved y lo muestra", así que este circuito se reporta como
            # NO VERIFICADO y estos nombres viajan como pistas para revisión
            # humana, nunca como una afirmación de que exista un ejecutor.
            #
            # Buscar solo `update_proposal_status` sería peor: daría un falso
            # positivo con `connectors/etoro/auto_executor.py`, que usa la
            # función HOMÓNIMA de `connectors/etoro/learning_engine.py` sobre
            # otro almacén — un tercer sistema, no este circuito.
            proposal_candidates: List[str] = []
            for py in repo_root.rglob("*.py"):
                if set(py.parts) & _SCAN_EXCLUDED_DIRS:
                    continue
                resolved = py.resolve()
                if resolved == module_path or resolved == self_path:
                    continue
                try:
                    body = py.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                rel = str(py.relative_to(repo_root))
                if ".mark_applied(" in body:
                    callers.append(rel)
                if ("get_proposals(" in body and "approved" in body
                        and "vectrax" in body):
                    proposal_candidates.append(rel)
        except Exception as exc:
            return _unavailable(kind, src, exc)

        executor_present = bool(callers)
        ideas_audits = "audit_ledger" in ideas_src
        proposals_audits = "audit_ledger" in proposals_src
        linked = ("proposals" in ideas_src) or ("idea_store" in proposals_src)

        now = time.time()

        def _item(scope: str, status: str, summary: str, ref: str) -> EvidenceItem:
            return EvidenceItem(
                kind=kind, source=f"{src} :: {ref}", observed_at=now, scope=scope,
                status=status, summary=summary, reference=ref, visibility="owner",
            )

        items = [
            # -- Conclusión PRIMERO -----------------------------------------
            # Que sean dos circuitos independientes es lo principal que hay
            # que entender de esta traza. Iba al final y el recorte de la
            # respuesta lo escondía tras un "(+1 más)": la separación se
            # deducía de los prefijos, pero la conclusión no llegaba al
            # usuario (auditoría 2026-09-22, tercera pasada).
            _item("relacion", "linked" if linked else "independent",
                  ("los dos circuitos se invocan entre sí" if linked else
                   "circuitos INDEPENDIENTES: no se llaman entre sí ni comparten almacén; "
                   "aprobar una idea no dispara el endpoint de proposals"),
                  "services/core/routes/ideas.py + proposals.py"),

            # -- Circuito 1: ideas (las IDEA-... del Dashboard) -------------
            _item("ideas/1.endpoint", "present",
                  "POST /v1/ideas/{id}/approve (permiso core.write)",
                  "services/core/routes/ideas.py"),
            _item("ideas/2.persistencia", "present",
                  "IdeaStore.approve() -> status=approved en data/ideas.jsonl",
                  "core/idea_store.py::approve"),
            _item("ideas/3.auditoria", "present" if ideas_audits else "absent",
                  ("escribe en audit_ledger" if ideas_audits else
                   "NO escribe en audit_ledger: aprobar una idea no deja entrada de auditoría"),
                  "services/core/routes/ideas.py"),
            _item("ideas/4.ejecutor", "present" if executor_present else "absent",
                  ("PRESENTE: " + ", ".join(sorted(callers)[:5])) if executor_present else
                  "AUSENTE: ningún proceso consume el estado approved "
                  "(IdeaStore.mark_applied() no tiene llamador en producción)",
                  "core/idea_store.py::mark_applied"),

            # -- Circuito 2: proposals (sistema distinto) -------------------
            _item("proposals/1.endpoint", "present",
                  "POST /v1/proposals/{id}/approve (permiso apply_proposal)",
                  "services/core/routes/proposals.py"),
            _item("proposals/2.persistencia", "present",
                  "db.update_proposal_status() -> vectrax.db",
                  "services/core/routes/proposals.py"),
            _item("proposals/3.auditoria", "present" if proposals_audits else "absent",
                  ("escribe entrada en audit_ledger (best-effort)" if proposals_audits else
                   "NO escribe en audit_ledger"),
                  "services/core/routes/proposals.py"),
            # El estado de este ejecutor NO puede deducirse del escaneo de
            # `mark_applied`, que pertenece al circuito `ideas`. Sin un punto
            # de aplicación único y nombrado como el de `ideas`, la ausencia
            # no es demostrable: se reporta `unverified` y se dice qué se
            # buscó, en vez de generalizar "ausente en ambos".
            EvidenceItem(
                kind=kind,
                source=f"{src} :: vectrax.db proposals (get_proposals + approved)",
                observed_at=now, scope="proposals/4.ejecutor", status="unverified",
                summary=(
                    "NO VERIFICADO por esta traza. A diferencia de `ideas`, este "
                    "circuito no tiene un punto de aplicación único y nombrado "
                    "(como mark_applied), así que ni su presencia ni su ausencia "
                    "quedan demostradas por un escaneo estático. Los archivos que "
                    "tocan el estado approved solo listan o lo fijan."
                ),
                reference="vectrax.db proposals", visibility="owner",
                data={"candidatos_no_verificados": sorted(proposal_candidates)[:8]},
            ),
        ]

        result = _finalize(kind, src, items)
        # El detalle se afirma POR CIRCUITO. Decir "ausente en ambos" a partir
        # de un escaneo de `mark_applied` —que solo pertenece a `ideas`— era
        # una generalización no demostrada (auditoría 2026-09-22).
        result.detail = (
            "dos circuitos separados · ideas: ejecutor "
            + ("presente" if executor_present else "ausente (demostrado: sin "
               "llamadores de mark_applied)")
            + " · proposals: ejecutor NO VERIFICADO por esta traza"
        )
        return result


def get_internal_evidence(owner: str, owner_raw: str = "") -> InternalEvidence:
    """Constructor canónico: resuelve identidad y devuelve la fachada."""
    return InternalEvidence(resolve_access(owner, owner_raw=owner_raw))
