"""Vectrax - Constitución Inmutable.

Estos principios son invariantes y no pueden ser modificados ni violados
por ninguna propuesta generada por el sistema.
"""

from dataclasses import dataclass
from typing import Callable

from models.schemas import StructuralAnalysis, RiskAssessment, SustainabilityScore


@dataclass(frozen=True)
class Principle:
    """Principio constitucional inmutable."""
    id: str
    name: str
    description: str
    check: Callable  # Función que evalúa cumplimiento


# ---------------------------------------------------------------------------
# Funciones de verificación
# ---------------------------------------------------------------------------

def _check_fundamental_rights(
    analysis: StructuralAnalysis,
    risk: RiskAssessment,
    sustainability: SustainabilityScore,
) -> tuple[bool, str]:
    """No dañar derechos fundamentales."""
    social = next((d for d in analysis.dimensions if d.name == "social"), None)
    if social and social.score < 0.3:
        return False, "Impacto social demasiado bajo: posible daño a derechos fundamentales"
    return True, "Derechos fundamentales protegidos"


def _check_transparency(
    analysis: StructuralAnalysis,
    risk: RiskAssessment,
    sustainability: SustainabilityScore,
) -> tuple[bool, str]:
    """Transparencia obligatoria."""
    legal = next((d for d in analysis.dimensions if d.name == "legal"), None)
    if legal and legal.score < 0.4:
        return False, "Puntuación legal insuficiente: falta de transparencia"
    return True, "Cumple requisitos de transparencia"


def _check_min_sustainability(
    analysis: StructuralAnalysis,
    risk: RiskAssessment,
    sustainability: SustainabilityScore,
) -> tuple[bool, str]:
    """Sostenibilidad mínima requerida."""
    if sustainability.overall < 0.3:
        return False, f"Sostenibilidad ({sustainability.overall:.2f}) por debajo del mínimo absoluto (0.30)"
    return True, "Cumple sostenibilidad mínima"


def _check_max_systemic_risk(
    analysis: StructuralAnalysis,
    risk: RiskAssessment,
    sustainability: SustainabilityScore,
) -> tuple[bool, str]:
    """Riesgo sistémico máximo permitido."""
    if risk.risk_score > 0.85:
        return False, f"Riesgo sistémico ({risk.risk_score:.2f}) excede el máximo constitucional (0.85)"
    return True, "Riesgo sistémico dentro de límites constitucionales"


# ---------------------------------------------------------------------------
# Constitución: lista de principios inmutables
# ---------------------------------------------------------------------------

IMMUTABLE_PRINCIPLES: list[Principle] = [
    Principle(
        id="CF-001",
        name="Derechos Fundamentales",
        description="Ninguna propuesta puede dañar derechos fundamentales de las personas.",
        check=_check_fundamental_rights,
    ),
    Principle(
        id="CF-002",
        name="Transparencia Obligatoria",
        description="Toda propuesta debe garantizar transparencia total en su implementación.",
        check=_check_transparency,
    ),
    Principle(
        id="CF-003",
        name="Sostenibilidad Mínima",
        description="Toda propuesta debe alcanzar un nivel mínimo de sostenibilidad.",
        check=_check_min_sustainability,
    ),
    Principle(
        id="CF-004",
        name="Riesgo Sistémico Máximo",
        description="El riesgo sistémico no puede exceder el umbral constitucional.",
        check=_check_max_systemic_risk,
    ),
]
