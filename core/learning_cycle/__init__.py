"""
Vectrax Learning Cycle — SEÑAL → INVESTIGACIÓN → VERIFICACIÓN → INTEGRACIÓN
=============================================================================
Pipeline modular de aprendizaje continuo con separación estricta
entre señal y verdad.

Módulos:
  - AnomalyDetector: detecta sin concluir
  - InvestigationEngine: busca contexto y genera hipótesis
  - VerificationEngine: valida con evidencia cruzada
  - LearningIntegrator: persiste solo patrones confirmados
  - LearningPipeline: orquesta el ciclo completo

Reglas del sistema:
  - Nunca asumir sin verificación
  - Priorizar coherencia sobre velocidad
  - Aprender solo de patrones confirmados
  - Mantener separación entre señal y verdad

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Anomaly types
# ---------------------------------------------------------------------------

ANOMALY_TYPES = [
    "frequency_spike",       # Aumento súbito en frecuencia de un patrón
    "new_pattern",           # Patrón nunca visto antes
    "distribution_shift",    # Cambio en distribución de intents/categorías
    "repetition_burst",      # Ráfaga de entradas repetidas
    "outlier_input",         # Entrada atípica por longitud, formato o contenido
]

# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

SEVERITY_LEVELS = ["low", "medium", "high", "critical"]

# ---------------------------------------------------------------------------
# Verification strategies
# ---------------------------------------------------------------------------

VERIFICATION_STRATEGIES = [
    "temporal_consistency",       # Patrón se mantiene en el tiempo
    "cross_source",              # Confirmado por múltiples fuentes
    "statistical_significance",  # Pasa umbrales estadísticos
    "precedent_match",           # Coincide con patrones previos confirmados
]

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

MIN_EVIDENCE_FOR_VERIFICATION = 2       # Mínimo de evidencias para verificar
MIN_CONFIDENCE_FOR_INTEGRATION = 0.60   # Confianza mínima para integrar
ANOMALY_WINDOW_SIZE = 50                # Ventana de eventos para detección
FREQUENCY_SPIKE_FACTOR = 2.5            # Factor sobre la media para spike
REPETITION_BURST_THRESHOLD = 5          # Entradas idénticas para ráfaga
