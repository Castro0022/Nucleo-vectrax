"""
Vectrax — Constitución del Motor de Recuperación
=================================================
Leyes fundacionales del sistema auto-metabolizante.
Toda decisión del motor se valida contra estas leyes.

Autor: Mario Bravo Castro
Fecha de inscripción: 2026-04-23
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# LEY MADRE — la raíz de la que nacen todas las demás.
# ---------------------------------------------------------------------------

MOTHER_LAW = (
    "Toda anomalía observada debe transformarse en una nueva capacidad "
    "permanente del sistema."
)

# Frases núcleo del organismo — no son decoración, son doctrina operativa.
DOCTRINE = (
    "Vectrax no corrige errores: incorpora defensas, órganos y capacidades "
    "nacidas del error.",
    "Un bug no se cierra cuando desaparece del log. Se cierra cuando el "
    "sistema ya sabe impedir su especie.",
    "Los humanos no deberían reparar Vectrax; deberían solo observar cómo "
    "Vectrax transforma errores en arquitectura.",
)


# ---------------------------------------------------------------------------
# Leyes primarias — orden importa. Se evalúan de arriba a abajo.
# ---------------------------------------------------------------------------

LAW_1 = (
    "Todo error nuevo debe producir una capacidad nueva. "
    "Si la solución no reduce la clase del problema, no es motor: es parche."
)

LAW_2 = (
    "Un fallo recurrente es evidencia de ausencia de órgano. "
    "El sistema debe fabricarse el órgano faltante."
)

LAW_3 = (
    "Ningún bug se cierra como excepción; todo bug se convierte en capacidad."
)

LAW_4 = (
    "Los parches corrigen el presente. Los motores protegen el futuro."
)

LAW_5 = (
    "Los parches son memoria corta; los motores son memoria evolutiva."
)


@dataclass(frozen=True)
class LawViolation:
    law: str
    reason: str
    evidence: dict


def check_is_motor_not_patch(
    candidate_spec: dict,
) -> List[LawViolation]:
    """
    Valida que un motor-candidato cumple Law 1 y Law 2:
      - Debe declarar la CLASE de fallo que absorbe.
      - Debe contener al menos uno de: detector, regla estructural,
        unificador, refactor de dominio, guardián.
      - No se acepta como motor un fix puntual (edit aislado sin
        mecanismo que prevenga recurrencia de la MISMA CLASE).
    Devuelve lista vacía si pasa.
    """
    violations: List[LawViolation] = []

    error_class = candidate_spec.get("error_class")
    if not error_class:
        violations.append(LawViolation(
            law=LAW_1,
            reason="candidate sin error_class declarada",
            evidence={"spec_keys": list(candidate_spec.keys())},
        ))

    mechanisms = candidate_spec.get("mechanisms", [])
    structural = {
        "detector", "rule", "unifier", "refactor", "guardian",
        "hook", "precommit", "rollback", "test",
    }
    if not any(m in structural for m in mechanisms):
        violations.append(LawViolation(
            law=LAW_1,
            reason="candidate no aporta mecanismo estructural — es parche",
            evidence={"mechanisms": mechanisms},
        ))

    if candidate_spec.get("scope") == "point_fix":
        violations.append(LawViolation(
            law=LAW_4,
            reason="scope=point_fix: corrige el presente, no protege el futuro",
            evidence={"scope": "point_fix"},
        ))

    return violations


def active_rules() -> list:
    """Reglas activas consultadas por motor_factory durante la síntesis."""
    return [MOTHER_LAW, LAW_1, LAW_2, LAW_3, LAW_4, LAW_5]


def inscribe() -> str:
    """Devuelve el texto de la constitución — útil para logs de arranque."""
    lines = [
        "CONSTITUCIÓN DEL ORGANISMO VECTRAX",
        "",
        f"LEY MADRE: {MOTHER_LAW}",
        "",
        f"LEY 1: {LAW_1}",
        f"LEY 2: {LAW_2}",
        f"LEY 3: {LAW_3}",
        f"LEY 4: {LAW_4}",
        f"LEY 5: {LAW_5}",
        "",
        "DOCTRINA:",
    ]
    for phrase in DOCTRINE:
        lines.append(f"  — {phrase}")
    lines.append("")
    lines.append("— Mario Bravo Castro, 2026-04-23")
    return "\n".join(lines)
