"""Vectrax - Configuración global del sistema."""


# ---------------------------------------------------------------------------
# Umbrales de Convergencia (Paso 2)
# ---------------------------------------------------------------------------
CONVERGENCE_THRESHOLD = 0.6  # Score mínimo para considerar convergencia real
MIN_SIGNALS_FOR_CONVERGENCE = 3  # Mínimo de señales necesarias

# ---------------------------------------------------------------------------
# Análisis Estructural (Paso 3)
# ---------------------------------------------------------------------------
STRUCTURAL_DIMENSIONS = [
    "economica",
    "social",
    "legal",
    "tecnica",
    "ambiental",
]
MIN_STRUCTURAL_SCORE = 0.4  # Puntuación mínima aceptable por dimensión

# ---------------------------------------------------------------------------
# Simulación de Impacto (Paso 5)
# ---------------------------------------------------------------------------
SCENARIO_WEIGHTS = {
    "optimista": 0.25,
    "base": 0.50,
    "pesimista": 0.25,
}

# ---------------------------------------------------------------------------
# Riesgo Sistémico (Paso 6)
# ---------------------------------------------------------------------------
MAX_ACCEPTABLE_RISK = 0.7  # Riesgo máximo permitido (probabilidad × impacto)

# ---------------------------------------------------------------------------
# Sostenibilidad (Paso 7)
# ---------------------------------------------------------------------------
SUSTAINABILITY_WEIGHTS = {
    "short_term": 0.2,
    "medium_term": 0.3,
    "long_term": 0.5,
}
MIN_SUSTAINABILITY_SCORE = 0.5  # Puntuación mínima para ser sostenible

# ---------------------------------------------------------------------------
# Propuesta Ejecutiva (Paso 8)
# ---------------------------------------------------------------------------
AUTO_APPROVE_THRESHOLD = 0.8  # Score global para aprobación automática
AUTO_REJECT_THRESHOLD = 0.3  # Score global para rechazo automático

# ---------------------------------------------------------------------------
# PolicyRouter — Umbrales de Autonomía (nuevos módulos)
# ---------------------------------------------------------------------------
ROUTER_MAX_RISK_SCORE = 0.02           # Riesgo máximo para AUTO_EXECUTE
ROUTER_MIN_PRECEDENT_CONFIDENCE = 0.75  # Confianza mínima de precedente histórico
ROUTER_MAX_TENSION = 0.35              # Tensión máxima (varianza dimensional)
ROUTER_REQUIRED_POLARITY = "ATRACCION"  # Estado de polaridad requerido

# Tipos de acción permitidos para ejecución autónoma
WHITELISTED_ACTION_TYPES = [
    "create_module",
    "add_tests",
    "refactor_small",
    "docs",
]
