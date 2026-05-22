"""
Vectrax — LawSignal
====================
Traduce violaciones de las 7 Leyes Fundamentales en ajustes de scores
para PresenciaObserver.

    enforce_all_laws()
    → LawEnforcementResult.violations
    → build_law_signal(violations)      ← este módulo
    → EmissionSignal.law_signal
    → PresenciaObserver.evaluate()      [ajusta scores antes de decidir]
    → ConvergenceLearner                [registra la decisión resultante]

Impacto por ley violada:
    Ley 2 Correspondencia → convergence  −0.15
    Ley 3 Vibración       → noise        +0.20
    Ley 4 Polaridad       → force_pause  (contradicción sin resolver → PAUSE)
    Ley 6 Causa/Efecto    → convergence  −0.20,  sovereignty −0.15

Penalización adicional cuando ≥ 3 violaciones simultáneas:
    noise +0.10  (señal del sistema en estado caótico)

Leyes 1, 5 y 7 son registradas en violated_laws pero sin ajuste de scores:
    Ley 1 Mentalismo   → siempre observable, impacto semántico
    Ley 5 Ritmo        → el Governor ya maneja los ciclos
    Ley 7 Generación   → el ConvergenceLearner maneja la verificación

Regla central:
    Los principios no responden. Los principios pesan.

Modo inicial: los scores se ajustan pero PresenciaObserver opera en
OBSERVER (enforced=False) — no bloquea todavía.

Capa: 1 — Núcleo Central
Creado: 2026-05-22
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.nucleus.law_signal")


# ---------------------------------------------------------------------------
# Impacto por ley
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LawImpact:
    """Ajuste de scores que produce una violación de una ley específica."""
    convergence_delta: float = 0.0   # negativo = reduce convergence
    sovereignty_delta: float = 0.0   # negativo = reduce sovereignty
    noise_delta:       float = 0.0   # positivo = aumenta noise
    force_pause:       bool  = False  # fuerza PAUSE si la decisión sería PERMIT


# Impacto por número de ley
# Leyes sin entrada (1, 5, 7) se registran en violated_laws pero sin ajuste de scores
_LAW_IMPACTS: Dict[int, LawImpact] = {
    2: LawImpact(convergence_delta=-0.15),                           # Correspondencia
    3: LawImpact(noise_delta=+0.20),                                 # Vibración
    4: LawImpact(force_pause=True),                                  # Polaridad
    6: LawImpact(convergence_delta=-0.20, sovereignty_delta=-0.15),  # Causa y Efecto
}

# Penalización extra por violaciones múltiples
_MULTI_VIOLATION_THRESHOLD = 3
_MULTI_VIOLATION_NOISE = 0.10


# ---------------------------------------------------------------------------
# LawSignal
# ---------------------------------------------------------------------------

@dataclass
class LawSignal:
    """
    Señal de ajuste derivada de violaciones de las 7 Leyes Fundamentales.

    Se embebe en EmissionSignal.law_signal para que PresenciaObserver
    ajuste los scores antes de tomar su decisión de inhibición.

    convergence_delta: penalización sobre convergence (siempre ≤ 0.0)
    sovereignty_delta: penalización sobre sovereignty (siempre ≤ 0.0)
    noise_delta:       incremento sobre noise          (siempre ≥ 0.0)
    force_pause:       True si Ley 4 fue violada — fuerza PAUSE
    violated_laws:     números de todas las leyes violadas
    reason:            descripción concatenada de las violaciones
    """
    convergence_delta: float     = 0.0
    sovereignty_delta: float     = 0.0
    noise_delta:       float     = 0.0
    force_pause:       bool      = False
    violated_laws:     List[int] = field(default_factory=list)
    reason:            str       = ""

    @property
    def is_severe(self) -> bool:
        """True si hay 3+ violaciones o una penalización de alta magnitud."""
        return (
            len(self.violated_laws) >= _MULTI_VIOLATION_THRESHOLD
            or self.convergence_delta <= -0.30
            or self.sovereignty_delta <= -0.15
        )

    @property
    def has_impact(self) -> bool:
        """True si la señal produce algún ajuste real de scores."""
        return (
            self.convergence_delta != 0.0
            or self.sovereignty_delta != 0.0
            or self.noise_delta != 0.0
            or self.force_pause
        )

    def summary(self) -> str:
        """Resumen legible de los ajustes."""
        parts = []
        if self.convergence_delta:
            parts.append(f"convergence{self.convergence_delta:+.2f}")
        if self.sovereignty_delta:
            parts.append(f"sovereignty{self.sovereignty_delta:+.2f}")
        if self.noise_delta:
            parts.append(f"noise{self.noise_delta:+.2f}")
        if self.force_pause:
            parts.append("force_pause")
        laws = ",".join(f"L{n}" for n in self.violated_laws)
        return f"LawSignal[{laws}]: {' | '.join(parts) or 'log_only'}"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_law_signal(violations: list) -> Optional[LawSignal]:
    """
    Construye un LawSignal a partir de una lista de LawCheck violados.

    Retorna None si no hay violaciones — no existe señal vacía.
    Acepta cualquier objeto con atributos law_number, law_name, reason.

    Args:
        violations: lista de LawCheck con passed=False

    Returns:
        LawSignal con los ajustes acumulados, o None si lista vacía.
    """
    if not violations:
        return None

    total_conv  = 0.0
    total_sov   = 0.0
    total_noise = 0.0
    force_pause = False
    numbers: List[int] = []
    reasons: List[str] = []

    for v in violations:
        num  = getattr(v, "law_number", 0)
        name = getattr(v, "law_name", f"Ley{num}")
        why  = getattr(v, "reason", "")

        numbers.append(num)
        reasons.append(f"L{num}({name}): {why}")

        impact = _LAW_IMPACTS.get(num, LawImpact())
        total_conv  += impact.convergence_delta
        total_sov   += impact.sovereignty_delta
        total_noise += impact.noise_delta
        if impact.force_pause:
            force_pause = True

    # Penalización extra por múltiples violaciones simultáneas
    if len(numbers) >= _MULTI_VIOLATION_THRESHOLD:
        total_noise += _MULTI_VIOLATION_NOISE
        reasons.append(
            f"penalizacion_multiple ({len(numbers)} leyes simultaneas)"
        )

    signal = LawSignal(
        convergence_delta=round(max(-1.0, total_conv), 3),
        sovereignty_delta=round(max(-1.0, total_sov),  3),
        noise_delta=round(min(1.0, max(0.0, total_noise)), 3),
        force_pause=force_pause,
        violated_laws=numbers,
        reason=" | ".join(reasons),
    )

    logger.info("LawSignal: %s", signal.summary())
    return signal
