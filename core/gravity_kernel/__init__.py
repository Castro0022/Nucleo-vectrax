"""
Gravity Kernel — capa de evaluación previa a la generación (Fase 1, shadow mode).

Solo Ritmo + Causa/Efecto están activos. Observa y registra; NO altera respuestas.
Punto de entrada principal: ``observe`` (en shadow.py).
"""
from core.gravity_kernel.shadow import observe, is_enabled  # noqa: F401

__all__ = ["observe", "is_enabled"]
