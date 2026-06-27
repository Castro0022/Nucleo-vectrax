"""
Vectrax — Enforcement de las 7 Leyes Fundamentales
=====================================================
Valida que toda decisión del sistema respete las 7 Leyes.

Las leyes no son sugerencias — son filtros activos que se ejecutan
en el pipeline de decisiones. Una violación no bloquea (eso es para
invariantes), pero se registra en el ledger y puede ajustar la
respuesta.

Niveles de enforcement:
  - ENFORCE: la ley se aplica activamente (modifica comportamiento)
  - OBSERVE: la ley se verifica y se registra, sin modificar
  - PASSIVE: la ley se verifica solo en auditorías

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vectrax.operator.law_enforcement")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LawCheck:
    """Resultado de verificar una ley contra una decisión."""
    law_number: int
    law_name: str
    passed: bool
    reason: str = ""
    action_taken: str = ""   # qué hizo el enforcement (vacío si solo observó)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LawEnforcementResult:
    """Resultado completo de pasar una decisión por las 7 Leyes."""
    all_passed: bool = True
    checks: List[LawCheck] = field(default_factory=list)
    violations: List[LawCheck] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "checks_count": len(self.checks),
            "violations_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Law enforcement functions
# ---------------------------------------------------------------------------

def enforce_law_1_mentalismo(
    *,
    input_classified: bool = False,
    classification: str = "",
) -> LawCheck:
    """
    LEY 1 — MENTALISMO: toda entrada debe procesarse como patrón cognitivo.

    Verifica que el input fue clasificado por el SmartRouter antes de
    ser procesado. Si no fue clasificado, es una violación.
    """
    if input_classified and classification:
        return LawCheck(
            law_number=1, law_name="Mentalismo", passed=True,
            reason=f"Input clasificado: {classification}",
        )
    return LawCheck(
        law_number=1, law_name="Mentalismo", passed=False,
        reason="Input procesado sin clasificación cognitiva",
        action_taken="Forzar clasificación antes de responder",
    )


def enforce_law_2_correspondencia(
    *,
    star_updated: bool = False,
    interaction_recorded: bool = False,
) -> LawCheck:
    """
    LEY 2 — CORRESPONDENCIA: lo micro refleja lo macro.

    Verifica que la interacción se registró en la memoria
    (star creada/actualizada) para que el patrón individual
    alimente el modelo global.
    """
    if star_updated or interaction_recorded:
        return LawCheck(
            law_number=2, law_name="Correspondencia", passed=True,
            reason="Interacción registrada en memoria estructural",
        )
    return LawCheck(
        law_number=2, law_name="Correspondencia", passed=False,
        reason="Interacción no reflejada en memoria estructural",
        action_taken="Registrar en memoria post-respuesta",
    )


def enforce_law_3_vibracion(
    *,
    system_has_active_cycles: bool = True,
) -> LawCheck:
    """
    LEY 3 — VIBRACIÓN: nada está inmóvil.

    Verifica que el sistema tiene ciclos activos (learning cycle,
    gravity decay, watchdog). Si todo está parado, es violación.
    """
    if system_has_active_cycles:
        return LawCheck(
            law_number=3, law_name="Vibración", passed=True,
            reason="Sistema con ciclos activos",
        )
    return LawCheck(
        law_number=3, law_name="Vibración", passed=False,
        reason="Sistema sin ciclos activos — estancamiento detectado",
        action_taken="Registrar alerta de estancamiento",
    )


def enforce_law_4_polaridad(
    *,
    has_counter_evidence: bool = True,
    is_hypothesis_context: bool = False,
) -> LawCheck:
    """
    LEY 4 — POLARIDAD: todo tiene su opuesto.

    En contexto de hipótesis, verifica que existe evidencia en contra
    además de la evidencia a favor. En contexto normal, siempre pasa.
    """
    if not is_hypothesis_context:
        return LawCheck(
            law_number=4, law_name="Polaridad", passed=True,
            reason="No es contexto de hipótesis — ley observada",
        )
    if has_counter_evidence:
        return LawCheck(
            law_number=4, law_name="Polaridad", passed=True,
            reason="Hipótesis tiene evidencia a favor y en contra",
        )
    return LawCheck(
        law_number=4, law_name="Polaridad", passed=False,
        reason="Hipótesis sin contra-evidencia — sesgo potencial",
        action_taken="Buscar evidencia contradictoria antes de confirmar",
    )


def enforce_law_5_ritmo(
    *,
    governor_mode: str = "observe",
    forcing_during_recovery: bool = False,
) -> LawCheck:
    """
    LEY 5 — RITMO: todo fluye y refluye.

    Verifica que el sistema respeta los ciclos naturales.
    No forzar operaciones durante recovery.
    """
    if forcing_during_recovery:
        return LawCheck(
            law_number=5, law_name="Ritmo", passed=False,
            reason=f"Forzando operación durante modo {governor_mode}",
            action_taken="Pausar hasta que el ciclo natural lo permita",
        )
    return LawCheck(
        law_number=5, law_name="Ritmo", passed=True,
        reason=f"Sistema en modo {governor_mode} — ritmo respetado",
    )


def enforce_law_6_causa_efecto(
    *,
    action_logged: bool = False,
    has_correlation_id: bool = False,
) -> LawCheck:
    """
    LEY 6 — CAUSA Y EFECTO: toda acción tiene trazabilidad.

    Verifica que la acción tiene registro en el ledger y un
    correlation_id para rastreo.
    """
    if action_logged and has_correlation_id:
        return LawCheck(
            law_number=6, law_name="Causa y Efecto", passed=True,
            reason="Acción con trazabilidad completa",
        )
    issues = []
    if not action_logged:
        issues.append("sin registro en ledger")
    if not has_correlation_id:
        issues.append("sin correlation_id")
    return LawCheck(
        law_number=6, law_name="Causa y Efecto", passed=False,
        reason=f"Trazabilidad incompleta: {', '.join(issues)}",
        action_taken="Registrar en ledger antes de continuar",
    )


def enforce_law_7_generacion(
    *,
    knowledge_verified: bool = True,
    is_learning_context: bool = False,
) -> LawCheck:
    """
    LEY 7 — GENERACIÓN: la creación requiere convergencia verificada.

    En contexto de aprendizaje, verifica que el conocimiento nuevo
    pasó por señal → investigación → verificación antes de integrarse.
    """
    if not is_learning_context:
        return LawCheck(
            law_number=7, law_name="Generación", passed=True,
            reason="No es contexto de aprendizaje — ley observada",
        )
    if knowledge_verified:
        return LawCheck(
            law_number=7, law_name="Generación", passed=True,
            reason="Conocimiento verificado antes de integración",
        )
    return LawCheck(
        law_number=7, law_name="Generación", passed=False,
        reason="Intento de integrar conocimiento sin verificación",
        action_taken="Bloquear integración — requiere ciclo completo",
    )


# ---------------------------------------------------------------------------
# Colectores de señal real (defensivos, fail-safe)
# ---------------------------------------------------------------------------
# Conectan las leyes con el ESTADO REAL del sistema en vez de defaults fijos.
# Todos son best-effort: si no pueden determinar el estado, devuelven el valor
# que NO genera una violación falsa (fail-safe). Se llaman desde los call sites
# de producción (p.ej. external_gateway); enforce_all_laws conserva sus defaults
# para que los tests sigan siendo deterministas.

_RECOVER_MODES: frozenset = frozenset({"recover", "recovery", "recovering"})
_HEARTBEAT_FRESH_S = 120.0


def detect_active_cycles() -> bool:
    """LEY 3 (Vibración): ¿el sistema tiene ciclos activos?

    Lee los heartbeats reales (worker/gateway). Fail-safe True: solo retorna
    False si CONFIRMA estancamiento (heartbeats presentes pero todos viejos).
    Si no hay información, asume que vibra (no genera falsos positivos).
    """
    try:
        from core.self_observation.state_collector import collect_state
        st = collect_state()
        ages = [
            getattr(st, "worker_heartbeat_age_s", None),
            getattr(st, "gateway_heartbeat_age_s", None),
        ]
        fresh = [a for a in ages if a is not None and a < _HEARTBEAT_FRESH_S]
        if fresh:
            return True
        known = [a for a in ages if a is not None]
        if known:  # heartbeats existen pero todos viejos → estancamiento real
            return False
    except Exception:
        pass
    return True  # indeterminado → fail-safe


def detect_governor_mode() -> str:
    """LEY 5 (Ritmo): modo real del Governor. Fail-safe 'observe'."""
    try:
        from core.governor import get_current_policy
        return str(get_current_policy().get("mode", "observe") or "observe")
    except Exception:
        return "observe"


def detect_forcing_during_recovery(governor_mode: Optional[str] = None) -> bool:
    """LEY 5 (Ritmo): ¿se está forzando operación durante recovery?

    True cuando el Governor está en un modo de recuperación (cualquier
    operación que prosiga rompe el ritmo natural). Fail-safe False.
    """
    mode = (governor_mode if governor_mode is not None else detect_governor_mode())
    return (mode or "").strip().lower() in _RECOVER_MODES


def detect_knowledge_verified() -> bool:
    """LEY 7 (Generación): ¿el último conocimiento integrado fue verificado?

    Best-effort sobre el verification engine del ciclo de aprendizaje.
    Fail-safe True (no bloquea si no hay información).
    """
    try:
        from core.learning_cycle import verification_engine as _ve
        getter = getattr(_ve, "last_verification_passed", None)
        if callable(getter):
            v = getter()
            if v is not None:
                return bool(v)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Enforcement completo
# ---------------------------------------------------------------------------

def enforce_all_laws(
    *,
    input_classified: bool = False,
    classification: str = "",
    interaction_recorded: bool = False,
    action_logged: bool = False,
    has_correlation_id: bool = False,
    governor_mode: str = "observe",
    forcing_during_recovery: bool = False,
    is_hypothesis_context: bool = False,
    has_counter_evidence: bool = True,
    is_learning_context: bool = False,
    knowledge_verified: bool = True,
    system_has_active_cycles: bool = True,
) -> LawEnforcementResult:
    """
    Pasa una decisión por las 7 Leyes Fundamentales.

    Retorna un LawEnforcementResult con el estado de cada ley.
    Las violaciones se registran en el ledger automáticamente.
    """
    checks: List[LawCheck] = [
        enforce_law_1_mentalismo(
            input_classified=input_classified,
            classification=classification,
        ),
        enforce_law_2_correspondencia(
            interaction_recorded=interaction_recorded,
        ),
        enforce_law_3_vibracion(
            system_has_active_cycles=system_has_active_cycles,
        ),
        enforce_law_4_polaridad(
            has_counter_evidence=has_counter_evidence,
            is_hypothesis_context=is_hypothesis_context,
        ),
        enforce_law_5_ritmo(
            governor_mode=governor_mode,
            forcing_during_recovery=forcing_during_recovery,
        ),
        enforce_law_6_causa_efecto(
            action_logged=action_logged,
            has_correlation_id=has_correlation_id,
        ),
        enforce_law_7_generacion(
            is_learning_context=is_learning_context,
            knowledge_verified=knowledge_verified,
        ),
    ]

    violations = [c for c in checks if not c.passed]
    all_passed = len(violations) == 0

    result = LawEnforcementResult(
        all_passed=all_passed,
        checks=checks,
        violations=violations,
    )

    # Registrar violaciones en el ledger
    if violations:
        _record_violations(violations)
        logger.warning(
            "Law enforcement: %d/%d violations — %s",
            len(violations), len(checks),
            ", ".join(f"L{v.law_number}:{v.law_name}" for v in violations),
        )
    else:
        logger.debug("Law enforcement: all 7 laws passed")

    return result


def _record_violations(violations: List[LawCheck]) -> None:
    """Registra violaciones de leyes en el ledger."""
    try:
        from core.operator import ledger_bridge as ledger
        for v in violations:
            ledger.record_event(
                action=f"law_violation:L{v.law_number}:{v.law_name}",
                category=ledger.EventCategory.REASONING,
                risk_zone=ledger.RiskZone.YELLOW,
                reason=v.reason,
                details={
                    "law_number": v.law_number,
                    "law_name": v.law_name,
                    "action_taken": v.action_taken,
                },
            )
    except Exception as exc:
        logger.debug("Could not record law violations: %s", exc)
