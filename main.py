#!/usr/bin/env python3
"""Vectrax - Orquestador del pipeline completo.

Ejecuta los 8 pasos del sistema:
1. Captura de señales de usuarios
2. Detección de convergencia real
3. Análisis estructural completo
4. Validación contra Constitución Inmutable
5. Simulación de impacto
6. Evaluación de riesgo sistémico
7. Cálculo de sostenibilidad a largo plazo
8. Generación de Propuesta Ejecutiva
"""

from __future__ import annotations

import sys
import os

# Agregar el directorio raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.schemas import Signal, SignalCategory, ExecutiveProposal
from pipeline.signals import SignalCollector
from pipeline.convergence import detect_convergence
from pipeline.structural import analyze_structure
from pipeline.validation import validate_against_constitution
from pipeline.simulation import simulate_impact
from pipeline.risk import evaluate_risk
from pipeline.sustainability import calculate_sustainability
from pipeline.proposal import generate_proposal


def run_pipeline(title: str, signals: list[Signal]) -> ExecutiveProposal:
    """Ejecuta el pipeline completo de Vectrax.

    Args:
        title: Título de la propuesta a evaluar.
        signals: Lista de señales de entrada generadas por usuarios.

    Returns:
        ExecutiveProposal con el resultado consolidado.
    """
    print(f"\n{'='*60}")
    print(f"  VECTRAX - Pipeline de Análisis")
    print(f"  Propuesta: {title}")
    print(f"{'='*60}\n")

    # Paso 1: Captura de señales
    print("[1/8] Capturando señales...")
    collector = SignalCollector()
    collector.add_many(signals)
    print(f"      → {collector.count} señales recibidas")
    print(f"      → Categorías: {dict(collector.get_categories())}")

    # Paso 2: Detección de convergencia
    print("\n[2/8] Detectando convergencia...")
    convergence = detect_convergence(collector.signals)
    print(f"      → {convergence.details}")

    # Paso 3: Análisis estructural
    print("\n[3/8] Ejecutando análisis estructural...")
    analysis = analyze_structure(collector.signals, convergence)
    for d in analysis.dimensions:
        bar = "█" * int(d.score * 20) + "░" * (20 - int(d.score * 20))
        print(f"      → {d.name:12s} [{bar}] {d.score:.2f}")
    print(f"      → Score global: {analysis.overall_score:.4f}")

    # Paso 5: Simulación de impacto (se ejecuta antes para alimentar pasos 6-7)
    print("\n[5/8] Simulando impacto...")
    simulation = simulate_impact(analysis, convergence)
    for s in simulation.scenarios:
        print(f"      → {s.name:12s}: impacto={s.impact_score:+.4f} (prob={s.probability:.0%})")
    print(f"      → Impacto esperado: {simulation.expected_impact:+.4f}")

    # Paso 6: Evaluación de riesgo sistémico
    print("\n[6/8] Evaluando riesgo sistémico...")
    risk = evaluate_risk(analysis, convergence, simulation)
    print(f"      → {risk.details}")
    for m in risk.mitigations:
        print(f"      → Mitigación: {m}")

    # Paso 7: Sostenibilidad
    print("\n[7/8] Calculando sostenibilidad...")
    sustainability = calculate_sustainability(analysis, convergence, simulation, risk)
    print(f"      → {sustainability.details}")

    # Paso 4: Validación constitucional (necesita risk y sustainability)
    print("\n[4/8] Validando contra Constitución Inmutable...")
    validation = validate_against_constitution(analysis, risk, sustainability)
    print(f"      → {validation.details}")

    # Paso 8: Generación de Propuesta Ejecutiva
    print(f"\n[8/8] Generando Propuesta Ejecutiva...")
    proposal = generate_proposal(
        title=title,
        convergence=convergence,
        analysis=analysis,
        validation=validation,
        simulation=simulation,
        risk=risk,
        sustainability=sustainability,
    )

    # Imprimir resultado final
    _print_proposal(proposal)

    return proposal


def _print_proposal(proposal: ExecutiveProposal) -> None:
    """Imprime la propuesta ejecutiva de forma legible."""
    print(f"\n{'='*60}")
    print(f"  PROPUESTA EJECUTIVA")
    print(f"{'='*60}")
    print(f"  ID:     {proposal.id}")
    print(f"  Título: {proposal.title}")
    print(f"  Estado: {proposal.status.value.upper()}")
    print(f"  Fecha:  {proposal.generated_at:%Y-%m-%d %H:%M:%S}")
    print(f"{'─'*60}")
    print(f"  Convergencia:    {proposal.convergence.score:.4f} "
          f"({'✓' if proposal.convergence.converged else '✗'})")
    print(f"  Estructural:     {proposal.structural_analysis.overall_score:.4f}")
    print(f"  Constitucional:  {'✓ Válida' if proposal.validation.is_valid else '✗ Inválida'}")
    print(f"  Impacto esperado:{proposal.simulation.expected_impact:+.4f}")
    print(f"  Riesgo:          {proposal.risk.risk_level.value} "
          f"({proposal.risk.risk_score:.4f})")
    print(f"  Sostenibilidad:  {proposal.sustainability.overall:.4f} "
          f"({'✓' if proposal.sustainability.is_sustainable else '✗'})")
    print(f"{'─'*60}")
    print(f"  Recomendación:")
    for line in proposal.recommendation.split("\n"):
        print(f"    {line}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Ejemplo de uso
# ---------------------------------------------------------------------------

def _demo() -> None:
    """Ejecuta una demo del pipeline con datos de ejemplo."""
    signals = [
        Signal("user-001", "Necesitamos reducir costos operativos", SignalCategory.ECONOMICA, weight=8.0),
        Signal("user-002", "Automatización del proceso de facturación", SignalCategory.TECNICA, weight=7.5),
        Signal("user-003", "Reducir impacto ambiental de la operación", SignalCategory.AMBIENTAL, weight=6.0),
        Signal("user-004", "Mejorar la eficiencia del equipo", SignalCategory.ECONOMICA, weight=7.0),
        Signal("user-005", "Cumplir con nueva regulación fiscal", SignalCategory.LEGAL, weight=9.0),
        Signal("user-006", "Capacitar al personal en nuevas herramientas", SignalCategory.SOCIAL, weight=5.5),
        Signal("user-007", "Implementar sistema de monitoreo energético", SignalCategory.AMBIENTAL, weight=6.5),
        Signal("user-008", "Optimizar cadena de suministro", SignalCategory.ECONOMICA, weight=8.5),
    ]

    run_pipeline(
        title="Optimización Operativa Integral Q2-2026",
        signals=signals,
    )


if __name__ == "__main__":
    _demo()
