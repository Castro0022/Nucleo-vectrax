"""
core/learn/outcome_adapter.py — Contrato UNIVERSAL de verificación de resultados.

Segunda mitad (la difícil) del ciclo de aprendizaje de Vectrax, común a TODO
dominio: convierte una RECOMENDACIÓN/criterio en un RESULTADO VERIFICADO contra
la verdad objetiva del dominio, y lo puntúa con una métrica única.

Invariancia del núcleo cognitivo
--------------------------------
El núcleo (Prediction, Outcome, score_outcomes, evaluate, scoreboard) es
IDÉNTICO para cualquier dominio. Lo único que cambia por dominio es un
``OutcomeAdapter`` — la pieza que sabe leer la verdad objetiva de ESE dominio.

    Núcleo invariante  ── observa / acumula / converge / da criterio / puntúa
        └── OutcomeAdapter (por dominio) ── ¿el criterio acertó?

Onboarding de un dominio nuevo = escribir un adaptador (o reutilizar uno
genérico como ``ThresholdOutcomeAdapter``). CERO cambios en el núcleo. Esa
invariancia es la evidencia de que "la arquitectura aprende", no un dominio.

Ejemplos de verdad objetiva por dominio:
    market             precio realizado (win si se movió a favor)
    freight_logistics  entrega a tiempo / retraso
    devops             tiempo de recuperación < SLA
    ventas/ecommerce   conversión / devolución
    soporte            ticket resuelto / tiempo de resolución

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger("vectrax.outcome_adapter")


# ── Estados canónicos (universales) ───────────────────────────────────

class OutcomeStatus(str, Enum):
    WIN = "win"
    LOSS = "loss"
    NEUTRAL = "neutral"      # resuelto pero no decisivo (incl. expirado/empate)
    PENDING = "pending"      # aún no verificable

    @property
    def is_decisive(self) -> bool:
        return self in (OutcomeStatus.WIN, OutcomeStatus.LOSS)

    @property
    def is_resolved(self) -> bool:
        return self is not OutcomeStatus.PENDING


# ── Predicción / Outcome (domain-agnostic) ────────────────────────────

@dataclass(frozen=True)
class Prediction:
    """Recomendación/criterio a verificar más tarde. Sin nada domain-specific:
    el significado de ``predicted`` y ``context`` lo interpreta el adaptador."""
    domain: str
    subject: str                     # entidad/patrón (ticker, lane, servicio…)
    predicted: str = ""              # etiqueta/acción recomendada
    prediction_id: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)
    created_ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Outcome:
    """Resultado verificado de una predicción contra la verdad del dominio."""
    prediction_id: str
    domain: str
    subject: str
    status: OutcomeStatus
    score: float = 0.0               # magnitud (ret_pct, +1/-1, horas, etc.)
    resolved_ts: float = field(default_factory=time.time)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "domain": self.domain,
            "subject": self.subject,
            "status": self.status.value,
            "score": self.score,
            "resolved_ts": self.resolved_ts,
            "evidence": dict(self.evidence),
        }


# ── Contrato por-dominio (LA pieza que cambia) ────────────────────────

class OutcomeAdapter(ABC):
    """Verificación específica de un dominio. ÚNICA parte NO invariante.

    ``resolve`` es PURO y determinista dado (prediction, observation): misma
    entrada → mismo Outcome, sin efectos ni red. La obtención de ``observation``
    (precio real, evento de entrega, estado del servicio…) es responsabilidad
    del dominio, fuera del adaptador, para mantenerlo testeable e invariante.
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """Clave del dominio (p. ej. 'market', 'freight_logistics')."""

    @abstractmethod
    def resolve(self, prediction: Prediction, observation: Mapping[str, Any]) -> Outcome:
        """Clasifica la predicción contra la observación realizada.
        Devuelve Outcome(status=PENDING) si todavía no es verificable."""

    # Helper común para construir Outcomes (evita repetición en adaptadores).
    def _outcome(self, prediction: Prediction, status: OutcomeStatus,
                 score: float = 0.0, **evidence: Any) -> Outcome:
        return Outcome(
            prediction_id=prediction.prediction_id,
            domain=self.domain,
            subject=prediction.subject,
            status=status,
            score=round(float(score), 4),
            evidence=evidence,
        )


# ── Adaptador genérico reutilizable (para MUCHOS dominios) ────────────

class ThresholdOutcomeAdapter(OutcomeAdapter):
    """Adaptador universal para dominios cuya verdad objetiva es un NÚMERO que
    cruza un umbral en una dirección favorable.

    Sirve tal cual para devops (recovery_time < SLA), ventas (conversión >
    baseline), soporte (resolución < objetivo), etc. — sin escribir lógica
    nueva. Los dominios complejos (trading, freight) usan adaptadores propios.

    Args:
        domain_key:        clave del dominio.
        metric_key:        campo de ``observation`` con el valor realizado.
        target:            umbral objetivo.
        higher_is_better:  True si mayor=mejor (ventas); False si menor=mejor
                           (tiempo de recuperación/resolución).
        neutral_band:      margen relativo (fracción de |target|) considerado
                           empate/neutral alrededor del objetivo.
    """

    def __init__(self, domain_key: str, metric_key: str, target: float,
                 higher_is_better: bool = True, neutral_band: float = 0.0) -> None:
        self._domain = domain_key
        self._metric = metric_key
        self._target = float(target)
        self._higher = bool(higher_is_better)
        self._band = abs(float(neutral_band))

    @property
    def domain(self) -> str:
        return self._domain

    def resolve(self, prediction: Prediction, observation: Mapping[str, Any]) -> Outcome:
        if observation.get(self._metric) is None:
            return self._outcome(prediction, OutcomeStatus.PENDING,
                                 reason=f"métrica '{self._metric}' ausente")
        try:
            value = float(observation[self._metric])
        except (TypeError, ValueError):
            return self._outcome(prediction, OutcomeStatus.PENDING,
                                 reason="métrica no numérica")
        # diff > 0 = mejor que el objetivo, en la dirección favorable del dominio.
        diff = (value - self._target) if self._higher else (self._target - value)
        tol = self._band * abs(self._target)
        if diff > tol:
            status = OutcomeStatus.WIN
        elif diff < -tol:
            status = OutcomeStatus.LOSS
        else:
            status = OutcomeStatus.NEUTRAL
        return self._outcome(prediction, status, score=diff,
                             value=value, target=self._target,
                             higher_is_better=self._higher)


# ── Registro de adaptadores ───────────────────────────────────────────

_ADAPTERS: Dict[str, OutcomeAdapter] = {}


def register_adapter(adapter: OutcomeAdapter) -> None:
    """Registra (o reemplaza) el adaptador de un dominio."""
    _ADAPTERS[adapter.domain] = adapter


def get_adapter(domain: str) -> Optional[OutcomeAdapter]:
    return _ADAPTERS.get(domain)


def registered_domains() -> List[str]:
    return sorted(_ADAPTERS.keys())


def clear_adapters() -> None:
    """Solo para tests."""
    _ADAPTERS.clear()


# ── Núcleo INVARIANTE de puntuación (idéntico para todo dominio) ──────

@dataclass
class DomainScore:
    domain: str
    n_total: int = 0
    n_resolved: int = 0
    n_decisive: int = 0
    wins: int = 0
    losses: int = 0
    neutrals: int = 0
    pending: int = 0
    win_rate: float = 0.0            # wins/decisive * 100
    expectancy: float = 0.0          # media de score sobre decisivos
    accuracy: float = 0.0            # métrica común: wins/decisive (0..1)
    baseline: float = 0.5
    lift_vs_baseline: float = 0.0    # accuracy - baseline (>0 = aprende)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "n_total": self.n_total,
            "n_resolved": self.n_resolved,
            "n_decisive": self.n_decisive,
            "wins": self.wins,
            "losses": self.losses,
            "neutrals": self.neutrals,
            "pending": self.pending,
            "win_rate": self.win_rate,
            "expectancy": self.expectancy,
            "accuracy": self.accuracy,
            "baseline": self.baseline,
            "lift_vs_baseline": self.lift_vs_baseline,
        }


def score_outcomes(domain: str, outcomes: Iterable[Outcome],
                   baseline: float = 0.5) -> DomainScore:
    """Agrega Outcomes verificados en un DomainScore.

    INVARIANTE: exactamente el mismo cálculo para cualquier dominio. Lo único
    que cambia entre dominios son los Outcome que entran (producidos por el
    adaptador del dominio). La métrica común es ``accuracy`` (aciertos sobre
    decisivos) y su ``lift_vs_baseline``.
    """
    s = DomainScore(domain=domain, baseline=round(float(baseline), 4))
    decisive_scores: List[float] = []
    for o in outcomes:
        s.n_total += 1
        if o.status is OutcomeStatus.WIN:
            s.wins += 1
            s.n_resolved += 1
            decisive_scores.append(o.score)
        elif o.status is OutcomeStatus.LOSS:
            s.losses += 1
            s.n_resolved += 1
            decisive_scores.append(o.score)
        elif o.status is OutcomeStatus.NEUTRAL:
            s.neutrals += 1
            s.n_resolved += 1
        else:  # PENDING
            s.pending += 1
    s.n_decisive = s.wins + s.losses
    if s.n_decisive > 0:
        s.win_rate = round(s.wins / s.n_decisive * 100, 1)
        s.accuracy = round(s.wins / s.n_decisive, 4)
        s.expectancy = round(sum(decisive_scores) / s.n_decisive, 4)
        s.lift_vs_baseline = round(s.accuracy - s.baseline, 4)
    return s


def evaluate(adapter: OutcomeAdapter,
             pairs: Iterable[Tuple[Prediction, Mapping[str, Any]]],
             baseline: float = 0.5) -> DomainScore:
    """Resuelve cada (Prediction, observation) vía el adaptador y puntúa con el
    núcleo invariante. MISMO flujo para cualquier dominio."""
    outcomes = [adapter.resolve(p, obs) for (p, obs) in pairs]
    return score_outcomes(adapter.domain, outcomes, baseline=baseline)


def cross_domain_scoreboard(scores: Iterable[DomainScore]) -> str:
    """Tablero comparable: la MISMA métrica por dominio. Evidencia de que el
    núcleo aprende de forma uniforme sobre dominios sin relación entre sí."""
    rows = list(scores)
    if not rows:
        return "(sin dominios evaluados)"
    lines = [
        "dominio               | dec | WR%  | acc  | lift  | exp",
        "----------------------|-----|------|------|-------|--------",
    ]
    for s in rows:
        lines.append(
            f"{s.domain[:21]:<21} | {s.n_decisive:>3} | "
            f"{s.win_rate:>4.0f} | {s.accuracy:>4.2f} | "
            f"{s.lift_vs_baseline:>+5.2f} | {s.expectancy:>+.3f}"
        )
    return "\n".join(lines)
