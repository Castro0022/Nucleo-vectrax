"""
Observation Bias — Lo aprendido cambia cómo observa
======================================================
Cierra el loop: aprendizaje → observación → mejor aprendizaje.

Después del refinamiento, actualiza los pesos de observación:
  - Dominios con patrones fuertes → más frecuencia de observación
  - Dominios con patrones degradados → menos frecuencia
  - Convergencias cross-domain fuertes → ambos dominios priorizados

Esto NO es un filtro (no bloquea). Es un SESGO que distribuye
la atención del sistema de forma no uniforme, basada en experiencia.

Se ejecuta después de cada refinement cycle.

API:
  compute_bias() → ObservationBias  (pesos por dominio)
  get_bias()     → dict             (pesos actuales, cached)

Creador: Mario Bravo Castro
Fecha: 2026-06-14
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("vectrax.learn.observation_bias")

_BIAS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vault", "observation_bias.json",
)

# Defaults
DEFAULT_WEIGHT = 1.0    # neutral weight
MIN_WEIGHT = 0.3        # never go below this (still observe)
MAX_WEIGHT = 3.0        # max amplification
BOOST_PER_USABLE = 0.2  # boost per usable pattern in domain
DECAY_PER_DEGRADED = 0.1  # penalty per degraded star in domain


@dataclass
class ObservationBias:
    """Observation weights per domain."""
    weights: Dict[str, float] = field(default_factory=dict)
    updated_at: float = 0.0
    total_usable: int = 0
    total_degraded: int = 0

    def get_weight(self, domain: str) -> float:
        return self.weights.get(domain, DEFAULT_WEIGHT)

    def to_dict(self) -> dict:
        return {
            "weights": self.weights,
            "updated_at": self.updated_at,
            "total_usable": self.total_usable,
            "total_degraded": self.total_degraded,
        }


def compute_bias() -> ObservationBias:
    """
    Compute observation weights from the current state of the universe.

    Reads:
    - Gravity engine: domain performance (cc scores, hit counts)
    - Market patterns: usable patterns per symbol
    - User stars: active vs inactive per domain
    - Convergences: strong cross-domain links

    Returns ObservationBias with per-domain weights.
    """
    bias = ObservationBias(updated_at=time.time())

    try:
        # === GRAVITY ENGINE: domain-level performance ===
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        domain_stats = gi.domain_stats()

        for domain, stats in domain_stats.items():
            weight = DEFAULT_WEIGHT
            count = stats.get("count", 0)
            avg_cc = stats.get("avg_cc", 0)
            total_hits = stats.get("total_hits", 0)

            # More coherent domains get more attention
            if avg_cc > 0.6 and count >= 5:
                weight += BOOST_PER_USABLE * min(count, 10)
                bias.total_usable += 1

            # Low coherence domains get less attention
            if avg_cc < 0.2 and count > 3:
                weight -= DECAY_PER_DEGRADED * min(count, 5)
                bias.total_degraded += 1

            # High hit count = high activity = worth observing
            if total_hits > 20:
                weight += 0.3

            bias.weights[domain] = round(
                max(MIN_WEIGHT, min(MAX_WEIGHT, weight)), 2
            )

    except Exception as exc:
        logger.debug("gravity bias computation failed: %s", exc)

    try:
        # === MARKET PATTERNS: usable patterns boost market observation ===
        from connectors.etoro.pattern_memory import get_patterns
        patterns = get_patterns()
        usable = [p for p in patterns if p.is_usable]
        if usable:
            market_boost = len(usable) * BOOST_PER_USABLE
            current = bias.weights.get("market", DEFAULT_WEIGHT)
            bias.weights["market"] = round(
                min(MAX_WEIGHT, current + market_boost), 2
            )
            bias.total_usable += len(usable)

    except Exception:
        pass

    try:
        # === CONVERGENCES: strong cross-domain links boost both ===
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        summary = gi.universe_summary()
        convergences = summary.get("convergence_details", [])

        for conv in convergences:
            if conv.get("combined_cc", 0) > 0.6:
                domains = conv.get("domains", [])
                for d in domains:
                    if d in bias.weights:
                        bias.weights[d] = round(
                            min(MAX_WEIGHT, bias.weights[d] + 0.2), 2
                        )

    except Exception:
        pass

    # Persist
    _save_bias(bias)

    if bias.weights:
        logger.info(
            "[BIAS] Updated | %s | usable=%d degraded=%d",
            " ".join(f"{k}={v}" for k, v in sorted(bias.weights.items())),
            bias.total_usable, bias.total_degraded,
        )

    return bias


def get_bias() -> Dict[str, float]:
    """Return current bias weights (from file cache)."""
    try:
        if os.path.exists(_BIAS_FILE):
            data = json.load(open(_BIAS_FILE))
            return data.get("weights", {})
    except Exception:
        pass
    return {}


def get_domain_weight(domain: str) -> float:
    """Get observation weight for a specific domain."""
    weights = get_bias()
    return weights.get(domain, DEFAULT_WEIGHT)


def _save_bias(bias: ObservationBias) -> None:
    try:
        os.makedirs(os.path.dirname(_BIAS_FILE), exist_ok=True)
        with open(_BIAS_FILE, "w") as f:
            json.dump(bias.to_dict(), f, indent=2)
    except Exception as exc:
        logger.debug("save bias failed: %s", exc)
