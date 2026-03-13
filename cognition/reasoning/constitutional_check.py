"""
Constitutional Validator — validates proposals against the 4 immutable
principles defined in constitution.py.

Does NOT reimplement the principles; wraps the existing checks.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger("vectrax.cognition.reasoning.constitution")


class ConstitutionalValidator:
    """
    Wraps ``constitution.IMMUTABLE_PRINCIPLES`` to validate cognitive
    proposals.

    Returns (passed: bool, reasons: List[str]).
    """

    def validate(
        self,
        risk_score: float = 0.0,
        sustainability_score: float = 1.0,
        social_score: float = 1.0,
        legal_score: float = 1.0,
    ) -> Tuple[bool, List[str]]:
        """
        Validate against all constitutional principles.

        Args:
            risk_score: systemic risk (0-1)
            sustainability_score: sustainability (0-1)
            social_score: social dimension (0-1)
            legal_score: legal/transparency dimension (0-1)

        Returns:
            (passed, list_of_violation_reasons)
        """
        violations: List[str] = []

        # CF-001: Fundamental rights
        if social_score < 0.3:
            violations.append(
                f"CF-001 Derechos Fundamentales: social score "
                f"({social_score:.2f}) below minimum (0.30)"
            )

        # CF-002: Transparency
        if legal_score < 0.4:
            violations.append(
                f"CF-002 Transparencia: legal score "
                f"({legal_score:.2f}) below minimum (0.40)"
            )

        # CF-003: Minimum sustainability
        if sustainability_score < 0.3:
            violations.append(
                f"CF-003 Sostenibilidad: sustainability score "
                f"({sustainability_score:.2f}) below minimum (0.30)"
            )

        # CF-004: Maximum systemic risk
        if risk_score > 0.85:
            violations.append(
                f"CF-004 Riesgo Sistémico: risk score "
                f"({risk_score:.2f}) exceeds maximum (0.85)"
            )

        passed = len(violations) == 0
        if not passed:
            logger.warning("Constitutional violations: %s", violations)

        return passed, violations
