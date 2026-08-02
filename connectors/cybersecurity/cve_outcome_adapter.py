"""
connectors/cybersecurity/cve_outcome_adapter.py — OutcomeAdapter del dominio.

Verdad objetiva del dominio (decisión del creador):
    confirmado en CISA KEV  → WIN  (SIEMPRE; nunca LOSS)
        timing por days_to_kev: 0–30 EARLY | 31–180 LATE | >180 NONE
    no en KEV y edad >180d  → LOSS   (maduró sin explotación conocida)
    no en KEV y edad ≤180d  → PENDING
    sin ninguna dimensión   → NEUTRAL (no decisivo)

El timing mide CAPACIDAD PREDICTIVA, no el hecho de explotación (por eso una CVE
en KEV nunca degrada a LOSS). `score`: WIN=+1, LOSS=−1, NEUTRAL=0 → win_rate por
subject = P(explotada). El núcleo de scoring (score_outcomes/DomainScore) es el
invariante común.

`resolve` es PURO y determinista dado (prediction, observation): la observación
(in_kev, days_to_kev, age_days, has_dims) la calcula la capa de verificación.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

from typing import Any, Mapping

from core.learn.outcome_adapter import Outcome, OutcomeAdapter, OutcomeStatus, Prediction

_DOMAIN = "cybersecurity"

EARLY_MAX_DAYS = 30
LATE_MAX_DAYS = 180
MATURE_DAYS = 180


def classify_timing(days_to_kev: int) -> str:
    d = max(0, int(days_to_kev))
    if d <= EARLY_MAX_DAYS:
        return "EARLY"
    if d <= LATE_MAX_DAYS:
        return "LATE"
    return "NONE"


class CyberOutcomeAdapter(OutcomeAdapter):
    """Verificación del dominio de ciberseguridad vía KEV + maduración temporal."""

    @property
    def domain(self) -> str:
        return _DOMAIN

    def resolve(self, prediction: Prediction, observation: Mapping[str, Any]) -> Outcome:
        has_dims = bool(observation.get("has_dims", True))
        in_kev = bool(observation.get("in_kev"))
        days_to_kev = observation.get("days_to_kev")
        age_days = observation.get("age_days")

        # 1. Sin dimensiones → NEUTRAL (no atribuible a ningún subject).
        if not has_dims:
            return self._outcome(
                prediction, OutcomeStatus.NEUTRAL, score=0.0,
                timing="NONE", in_kev=in_kev, reason="sin dimensiones",
            )

        # 2. Confirmado en KEV → WIN (siempre), con timing por days_to_kev.
        if in_kev:
            d = max(0, int(days_to_kev if days_to_kev is not None else 0))
            return self._outcome(
                prediction, OutcomeStatus.WIN, score=1.0,
                timing=classify_timing(d), days_to_kev=d, in_kev=True,
            )

        # 3. No en KEV: LOSS si maduró (>180d), PENDING si aún no.
        if age_days is not None and age_days > MATURE_DAYS:
            return self._outcome(
                prediction, OutcomeStatus.LOSS, score=-1.0,
                timing="NONE", in_kev=False, age_days=age_days,
            )
        return self._outcome(
            prediction, OutcomeStatus.PENDING,
            timing="NONE", in_kev=False, age_days=age_days,
        )
