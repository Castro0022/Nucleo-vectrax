"""
Vectrax IdeaStore — Store Unificado de Ideas Operacionales
===========================================================
Agrega y prioriza propuestas de mejora provenientes de todos los
módulos de aprendizaje del sistema en un único archivo persistente.

Fuentes de ideas:
  - RouterLearningEngine  → propuestas de routing / clasificación
  - ConvergenceLearner    → recomendaciones de umbrales (ThresholdRecommendation)
  - Manual (creador)      → ideas añadidas directamente via Telegram

Cada Idea se puntúa con:
  impact_score      — severidad estimada del problema (0.0–1.0)
  convergence_level — nivel de convergencia actual del sistema (0.0–1.0)
  priority_score    — impact_score * (1 + convergence_level) / 2  [0.0–1.0]

Solo el creador puede aprobar o rechazar.
El sistema propone. Mario decide.

Privacidad:
  - Nunca almacena texto de mensajes, identidades, credenciales ni tokens
  - Solo métricas abstractas y descripciones estructurales

Capa: Core — Observabilidad y Mejora Continua
Creado: 2026-05-22
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.idea_store")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
IDEAS_PATH = os.path.join(_DATA_DIR, "ideas.jsonl")


def _ensure_data_dir() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class IdeaStatus(str, Enum):
    PENDING  = "pending"   # esperando revisión del creador
    APPROVED = "approved"  # aprobada — lista para implementar
    REJECTED = "rejected"  # rechazada — ignorar
    APPLIED  = "applied"   # implementada y en producción


class IdeaPriority(str, Enum):
    CRITICAL = "critical"  # bloqueo activo / degradación severa
    HIGH     = "high"      # impacto alto, acción urgente
    MEDIUM   = "medium"    # mejora significativa
    LOW      = "low"       # optimización menor


class IdeaSource(str, Enum):
    ROUTER_LEARNING    = "router_learning"
    CONVERGENCE_LEARNER = "convergence_learner"
    MANUAL             = "manual"
    OBSERVER           = "observer"


# ---------------------------------------------------------------------------
# Idea dataclass
# ---------------------------------------------------------------------------

@dataclass
class Idea:
    """
    Unidad de mejora propuesta por el sistema o el creador.

    idea_id          — identificador único (IDEA-xxxxxxxx)
    title            — descripción concisa (≤ 80 chars)
    description      — explicación completa con evidencia
    source           — módulo que generó la idea
    priority         — HIGH / MEDIUM / LOW / CRITICAL
    impact_score     — severidad estimada 0.0–1.0
    convergence_level— nivel de convergencia del sistema al momento de crear
    priority_score   — score compuesto para ranking (calculado)
    affected_component — componente afectado (semantic_classifier, etc.)
    evidence         — dict con datos de soporte (counts, rates, etc.)
    status           — PENDING / APPROVED / REJECTED / APPLIED
    source_id        — ID original en el módulo fuente (para trazabilidad)
    """
    idea_id:            str
    title:              str
    description:        str
    source:             IdeaSource
    priority:           IdeaPriority
    impact_score:       float       # 0.0 – 1.0
    convergence_level:  float       # 0.0 – 1.0 (estado del Observer al crear)
    affected_component: str
    evidence:           Dict[str, Any]
    created_at:         str

    priority_score:     float       = 0.0   # calculado
    status:             IdeaStatus  = IdeaStatus.PENDING
    source_id:          str         = ""    # ID original en módulo fuente
    reviewed_at:        Optional[str] = None
    reviewed_by:        str         = ""
    review_note:        str         = ""
    applied_at:         Optional[str] = None

    def __post_init__(self) -> None:
        # Calcular priority_score = impact * peso_prioridad * (1 + convergence) / 2
        priority_weights = {
            IdeaPriority.CRITICAL: 1.0,
            IdeaPriority.HIGH:     0.75,
            IdeaPriority.MEDIUM:   0.5,
            IdeaPriority.LOW:      0.25,
        }
        pw = priority_weights.get(self.priority, 0.5)
        self.priority_score = round(
            self.impact_score * pw * (1.0 + self.convergence_level) / 2.0, 4
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idea_id":            self.idea_id,
            "title":              self.title,
            "description":        self.description,
            "source":             self.source.value,
            "priority":           self.priority.value,
            "impact_score":       round(self.impact_score, 4),
            "convergence_level":  round(self.convergence_level, 4),
            "priority_score":     self.priority_score,
            "affected_component": self.affected_component,
            "evidence":           self.evidence,
            "status":             self.status.value,
            "source_id":          self.source_id,
            "created_at":         self.created_at,
            "reviewed_at":        self.reviewed_at,
            "reviewed_by":        self.reviewed_by,
            "review_note":        self.review_note,
            "applied_at":         self.applied_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Idea":
        return cls(
            idea_id=d["idea_id"],
            title=d["title"],
            description=d["description"],
            source=IdeaSource(d.get("source", "manual")),
            priority=IdeaPriority(d.get("priority", "medium")),
            impact_score=float(d.get("impact_score", 0.5)),
            convergence_level=float(d.get("convergence_level", 0.5)),
            affected_component=d.get("affected_component", ""),
            evidence=d.get("evidence", {}),
            created_at=d.get("created_at", _now_iso()),
            priority_score=float(d.get("priority_score", 0.0)),
            status=IdeaStatus(d.get("status", "pending")),
            source_id=d.get("source_id", ""),
            reviewed_at=d.get("reviewed_at"),
            reviewed_by=d.get("reviewed_by", ""),
            review_note=d.get("review_note", ""),
            applied_at=d.get("applied_at"),
        )

    def short_summary(self, idx: int = 0) -> str:
        """Resumen compacto para mostrar en Telegram."""
        status_icons = {
            IdeaStatus.PENDING:  "🔵",
            IdeaStatus.APPROVED: "✅",
            IdeaStatus.REJECTED: "❌",
            IdeaStatus.APPLIED:  "🟢",
        }
        priority_icons = {
            IdeaPriority.CRITICAL: "🔴",
            IdeaPriority.HIGH:     "🟠",
            IdeaPriority.MEDIUM:   "🟡",
            IdeaPriority.LOW:      "⚪",
        }
        icon = status_icons.get(self.status, "⚫")
        picon = priority_icons.get(self.priority, "⚫")
        prefix = f"#{idx} " if idx else ""
        return (
            f"{prefix}{icon}{picon} [{self.idea_id}]\n"
            f"   {self.title}\n"
            f"   Fuente: {self.source.value} | Score: {self.priority_score:.2f}\n"
            f"   Componente: {self.affected_component}"
        )


# ---------------------------------------------------------------------------
# _get_convergence_level — lee Observer singleton
# ---------------------------------------------------------------------------

def _get_convergence_level() -> float:
    """
    Lee el nivel de convergencia actual desde PresenciaObserver.
    Retorna 0.5 (neutro) si el Observer no está disponible.
    """
    try:
        from core.nucleus.presencia_pura import get_observer
        obs = get_observer()
        stats = obs.get_stats()
        # permit_rate es proxy de convergencia operacional
        total = stats.get("total_evaluated", 0)
        if total == 0:
            return 0.5
        permit = stats.get("decisions", {}).get("PERMIT", 0)
        return round(permit / total, 4)
    except Exception as exc:
        logger.debug("_get_convergence_level: Observer no disponible (%s)", exc)
        return 0.5


# ---------------------------------------------------------------------------
# IdeaStore — store JSONL persistente
# ---------------------------------------------------------------------------

class IdeaStore:
    """
    Almacén persistente de ideas operacionales.

    Append-only: nunca se reescriben líneas pasadas.
    Cuando una idea cambia de estado (approve/reject), se escribe una
    nueva línea con el mismo idea_id — la más reciente prevalece.

    Uso:
        store = get_idea_store()
        store.ingest_from_router_learning()
        ideas = store.get_top(n=5)
        store.approve("IDEA-abc12345", by="creator")
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or IDEAS_PATH

    # ── Persistencia ───────────────────────────────────────────────────────

    def _append(self, idea: Idea) -> None:
        _ensure_data_dir()
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(idea.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("IdeaStore._append failed: %s", exc)

    def _read_all_latest(self) -> Dict[str, Idea]:
        """Lee todas las ideas, quedándose con la versión más reciente de cada idea_id."""
        if not os.path.exists(self._path):
            return {}
        ideas: Dict[str, Idea] = {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        idea = Idea.from_dict(d)
                        ideas[idea.idea_id] = idea  # la última versión prevalece
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning("IdeaStore._read_all_latest failed: %s", exc)
        return ideas

    def all(self) -> List[Idea]:
        """Retorna todas las ideas más recientes, ordenadas por priority_score desc."""
        ideas = list(self._read_all_latest().values())
        return sorted(ideas, key=lambda i: -i.priority_score)

    def pending(self) -> List[Idea]:
        """Solo ideas en estado PENDING, ordenadas por priority_score."""
        return [i for i in self.all() if i.status == IdeaStatus.PENDING]

    def get_top(self, n: int = 5, status: Optional[IdeaStatus] = None) -> List[Idea]:
        """Top-N ideas por priority_score, opcionalmente filtrado por status."""
        base = self.all() if status is None else [i for i in self.all() if i.status == status]
        return base[:n]

    def get_by_id(self, idea_id: str) -> Optional[Idea]:
        return self._read_all_latest().get(idea_id)

    # ── Creación ───────────────────────────────────────────────────────────

    def add(
        self,
        title: str,
        description: str,
        source: IdeaSource,
        priority: IdeaPriority,
        impact_score: float,
        affected_component: str,
        evidence: Optional[Dict[str, Any]] = None,
        source_id: str = "",
    ) -> Optional[Idea]:
        """
        Añade una nueva idea al store.
        Deduplica por (source, source_id) — no inserta si ya existe con ese source_id.
        """
        # Deduplication por source_id
        if source_id:
            existing = self._read_all_latest()
            for idea in existing.values():
                if idea.source == source and idea.source_id == source_id:
                    logger.debug("IdeaStore.add: skip duplicate source_id=%s", source_id)
                    return None

        idea_id = f"IDEA-{uuid.uuid4().hex[:8].upper()}"
        convergence_level = _get_convergence_level()

        idea = Idea(
            idea_id=idea_id,
            title=title[:80],
            description=description[:500],
            source=source,
            priority=priority,
            impact_score=min(1.0, max(0.0, impact_score)),
            convergence_level=convergence_level,
            affected_component=affected_component,
            evidence=evidence or {},
            created_at=_now_iso(),
            source_id=source_id,
        )
        self._append(idea)
        logger.info(
            "IdeaStore: nueva idea %s [%s] score=%.2f | %s",
            idea.idea_id, idea.priority.value, idea.priority_score, title[:50]
        )

        # === FILTRO CONSTITUCIONAL (Fase 1 — SHADOW MODE) =====================
        # Choke point 4: generación de ideas. Solo observa y registra — crear
        # una idea sigue siendo un cambio reversible y de bajo riesgo (queda
        # PENDING hasta que el creador la apruebe), así que no se modela como
        # "is_learning_context" (eso aplica recién cuando se APLICA una idea,
        # Fase 4). `action_logged=False` refleja con honestidad que hoy la
        # creación de ideas no pasa por el audit ledger, solo por el logger de
        # Python — hallazgo real que quedará documentado en la matriz de
        # cobertura de la Fase 1.
        try:
            from core.operator.constitutional_guard import shadow_check
            from core.operator.constitutional_filter import ActionProposal
            shadow_check(ActionProposal(
                action="create_idea",
                correlation_id=idea.idea_id,
                classification=source.value,
                is_irreversible=False,
                interaction_recorded=True,
                domain=affected_component,
                action_logged=False,
                is_hypothesis_context=True,
                has_counter_evidence=False,
            ))
        except Exception as _cf_exc:
            logger.debug("Constitutional shadow check failed (passthrough): %s", _cf_exc)

        return idea

    # ── Review ────────────────────────────────────────────────────────────

    def approve(self, idea_id: str, by: str = "creator", note: str = "") -> bool:
        """Aprueba una idea. Solo el creador puede hacerlo."""
        idea = self.get_by_id(idea_id)
        if not idea:
            logger.warning("IdeaStore.approve: idea_id no encontrado: %s", idea_id)
            return False
        if idea.status != IdeaStatus.PENDING:
            logger.info("IdeaStore.approve: idea %s ya tiene estado %s", idea_id, idea.status)
            return False

        idea.status = IdeaStatus.APPROVED
        idea.reviewed_at = _now_iso()
        idea.reviewed_by = by
        idea.review_note = note
        self._append(idea)
        logger.info("IdeaStore: APPROVED %s by=%s", idea_id, by)
        return True

    def reject(self, idea_id: str, by: str = "creator", note: str = "") -> bool:
        """Rechaza una idea."""
        idea = self.get_by_id(idea_id)
        if not idea:
            return False
        if idea.status != IdeaStatus.PENDING:
            return False

        idea.status = IdeaStatus.REJECTED
        idea.reviewed_at = _now_iso()
        idea.reviewed_by = by
        idea.review_note = note
        self._append(idea)
        logger.info("IdeaStore: REJECTED %s by=%s", idea_id, by)
        return True

    def mark_applied(self, idea_id: str) -> bool:
        """Marca una idea aprobada como implementada en producción."""
        idea = self.get_by_id(idea_id)
        if not idea or idea.status != IdeaStatus.APPROVED:
            return False
        idea.status = IdeaStatus.APPLIED
        idea.applied_at = _now_iso()
        self._append(idea)
        logger.info("IdeaStore: APPLIED %s", idea_id)
        return True

    # ── Ingesta desde RouterLearningEngine ────────────────────────────────

    def ingest_from_router_learning(self) -> int:
        """
        Lee propuestas pendientes de data/router_proposals.jsonl y las
        convierte en Ideas. Deduplica por source_id.

        Retorna número de ideas nuevas añadidas.
        """
        try:
            proposals_path = os.path.join(_DATA_DIR, "router_proposals.jsonl")
            if not os.path.exists(proposals_path):
                return 0

            added = 0
            with open(proposals_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Solo propuestas pending
                    if p.get("status") not in ("pending", None):
                        continue

                    source_id = f"router_{p.get('cycle_id', 0)}_{p.get('proposal_type', '')}_{p.get('created_at', 0)}"

                    # Mapear priority
                    priority_map = {
                        "high":   IdeaPriority.HIGH,
                        "medium": IdeaPriority.MEDIUM,
                        "low":    IdeaPriority.LOW,
                    }
                    priority = priority_map.get(p.get("priority", "medium"), IdeaPriority.MEDIUM)

                    # Mapear impact basado en tipo + prioridad
                    impact_map = {
                        ("conflict_pattern", "high"):   0.80,
                        ("conflict_pattern", "medium"): 0.60,
                        ("weight_shift",     "medium"): 0.55,
                        ("threshold_adjust", "low"):    0.35,
                    }
                    proposal_type = p.get("proposal_type", "")
                    p_priority    = p.get("priority", "medium")
                    impact_score  = impact_map.get((proposal_type, p_priority), 0.50)

                    title = p.get("description", "Propuesta del router")[:80]
                    description = p.get("suggested_action", "Ver evidencia adjunta")
                    affected = p.get("affected_component", "smart_router")

                    idea = self.add(
                        title=title,
                        description=description,
                        source=IdeaSource.ROUTER_LEARNING,
                        priority=priority,
                        impact_score=impact_score,
                        affected_component=affected,
                        evidence=p.get("evidence", {}),
                        source_id=source_id,
                    )
                    if idea:
                        added += 1

            if added:
                logger.info("IdeaStore: %d ideas ingresadas desde RouterLearning", added)
            return added

        except Exception as exc:
            logger.warning("IdeaStore.ingest_from_router_learning failed: %s", exc)
            return 0

    # ── Ingesta desde ConvergenceLearner ──────────────────────────────────

    def ingest_from_convergence_learner(self) -> int:
        """
        Lee ThresholdRecommendations del ConvergenceLearner singleton
        y las convierte en Ideas. Deduplica por rec_id.

        Retorna número de ideas nuevas añadidas.
        """
        try:
            from core.nucleus.convergence_learner import get_learner
            learner = get_learner()
            added = 0

            for rec in learner.recommendations:
                if rec.status != "pending":
                    continue

                idea = self.add(
                    title=(
                        f"Ajuste umbral {rec.threshold_name} "
                        f"{'↑' if rec.direction == 'up' else '↓'} "
                        f"{rec.current_value:.2f}→{rec.recommended_value:.2f}"
                    ),
                    description=rec.reasoning[:500],
                    source=IdeaSource.CONVERGENCE_LEARNER,
                    priority=IdeaPriority.HIGH if rec.confidence >= 0.7 else IdeaPriority.MEDIUM,
                    impact_score=min(0.95, rec.confidence),
                    affected_component=f"presencia_observer.{rec.threshold_name}",
                    evidence={
                        "rec_id":         rec.rec_id,
                        "motor":          rec.motor_name or "GLOBAL",
                        "threshold":      rec.threshold_name,
                        "current":        rec.current_value,
                        "recommended":    rec.recommended_value,
                        "direction":      rec.direction,
                        "sample_size":    rec.sample_size,
                        "confidence":     round(rec.confidence, 4),
                    },
                    source_id=f"conv_{rec.rec_id}",
                )
                if idea:
                    added += 1

            if added:
                logger.info(
                    "IdeaStore: %d ideas ingresadas desde ConvergenceLearner", added
                )
            return added

        except Exception as exc:
            logger.debug("IdeaStore.ingest_from_convergence_learner: %s", exc)
            return 0

    # ── Ingesta desde RouterLearning (propuestas de error recurrentes) ────

    def ingest_from_router_analysis(self) -> int:
        """
        Ejecuta RouterLearningEngine.classify_errors() y genera Ideas
        para las categorías de error más frecuentes.
        Útil para capturar patrones que no llegan a router_proposals.jsonl.
        """
        try:
            from core.router_learning import get_learning_engine
            engine = get_learning_engine()
            error_report = engine.classify_errors()

            if error_report.get("status") in ("empty", "insufficient_data"):
                return 0

            added = 0
            for err in error_report.get("top_errors", [])[:5]:
                category   = err.get("category", "unknown")
                count      = err.get("count", 0)
                impact_raw = err.get("impact_score", 1.0)
                suggestion = err.get("suggestion", "Ver análisis del router")
                total      = error_report.get("total_records", 1)

                if count < 3:  # ignorar errores con muy pocas muestras
                    continue

                impact_score = min(0.90, impact_raw / max(total * 0.5, 1))
                priority = (
                    IdeaPriority.HIGH if impact_score >= 0.60
                    else IdeaPriority.MEDIUM if impact_score >= 0.30
                    else IdeaPriority.LOW
                )

                source_id = f"router_err_{category}_{count}"
                idea = self.add(
                    title=f"Error recurrente: {category} ({count} casos)",
                    description=suggestion,
                    source=IdeaSource.ROUTER_LEARNING,
                    priority=priority,
                    impact_score=impact_score,
                    affected_component="smart_router.classification",
                    evidence={
                        "category":         category,
                        "count":            count,
                        "impact_score_raw": round(impact_raw, 3),
                        "top_intent":       err.get("top_intent", ""),
                        "avg_confidence":   err.get("avg_confidence", 0),
                    },
                    source_id=source_id,
                )
                if idea:
                    added += 1

            return added

        except Exception as exc:
            logger.debug("IdeaStore.ingest_from_router_analysis: %s", exc)
            return 0

    # ── Ciclo completo de ingesta ──────────────────────────────────────────

    def refresh(self) -> Dict[str, int]:
        """
        Ejecuta todas las ingestas y retorna cuántas ideas nuevas añadió
        cada fuente. Idempotente — la deduplicación evita duplicados.
        """
        r = {
            "router_proposals": self.ingest_from_router_learning(),
            "router_analysis":  self.ingest_from_router_analysis(),
            "convergence":      self.ingest_from_convergence_learner(),
        }
        total = sum(r.values())
        if total:
            logger.info("IdeaStore.refresh: +%d ideas totales — %s", total, r)
        return r

    # ── Panel texto para Telegram ──────────────────────────────────────────

    def build_panel(self, n: int = 5, show_all_statuses: bool = False) -> str:
        """
        Genera el panel 'Evolución del Núcleo' para /vx ideas.
        Muestra top-N ideas pendientes por priority_score.
        """
        ideas = self.get_top(
            n=n,
            status=None if show_all_statuses else IdeaStatus.PENDING,
        )

        convergence = _get_convergence_level()
        total_all     = len(self.all())
        total_pending = len(self.pending())
        total_approved = sum(1 for i in self.all() if i.status == IdeaStatus.APPROVED)

        header = (
            f"🧬 EVOLUCIÓN DEL NÚCLEO\n"
            f"═══════════════════════\n"
            f"Convergencia actual: {convergence:.0%}\n"
            f"Ideas totales: {total_all}  |  Pendientes: {total_pending}  "
            f"|  Aprobadas: {total_approved}\n"
            f"───────────────────────\n"
        )

        if not ideas:
            return header + "Sin ideas pendientes. El núcleo está estable."

        lines = [header]
        for idx, idea in enumerate(ideas, 1):
            lines.append(idea.short_summary(idx))
            lines.append("")

        lines.append("───────────────────────")
        lines.append("aprobar IDEA-xxx  |  rechazar IDEA-xxx")
        return "\n".join(lines)

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        all_ideas = self.all()
        by_status  = {}
        by_source  = {}
        by_priority = {}
        for idea in all_ideas:
            by_status[idea.status.value]    = by_status.get(idea.status.value, 0) + 1
            by_source[idea.source.value]    = by_source.get(idea.source.value, 0) + 1
            by_priority[idea.priority.value]= by_priority.get(idea.priority.value, 0) + 1
        return {
            "total": len(all_ideas),
            "by_status": by_status,
            "by_source": by_source,
            "by_priority": by_priority,
            "convergence_level": _get_convergence_level(),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_store: Optional[IdeaStore] = None


def get_idea_store() -> IdeaStore:
    """Retorna el singleton global de IdeaStore."""
    global _store
    if _store is None:
        _store = IdeaStore()
    return _store
