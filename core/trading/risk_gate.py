"""
core/trading/risk_gate.py — La jaula de seguridad. Punto público.

RiskGate puede anular a TradeDecisionEngine. TradeDecisionEngine nunca
puede anular a RiskGate.

Toda la lógica (matriz de reglas, precedencia de NORMAL / HALT_NEW_RISK /
RECONCILE / EMERGENCY_FLATTEN) vive en `risk_rules.py`, ya fijada y
probada en `tests/test_risk_rules.py`. Este módulo es la fachada estable
que el resto del sistema importa — el resto del código NUNCA debe
importar `risk_rules` directamente, para que el punto de entrada de la
jaula sea uno solo.

RiskGate no consulta ningún servicio por sí mismo: recibe únicamente los
snapshots tipados que el caller (`position_manager.py`, la futura
integración con `auto_executor.py`) ya construyó. Cero imports de
`core.nucleus` o `core.learn` — si algún día aparecen aquí, es una señal
de que se coló criterio donde no pertenece; `tests/test_risk_gate.py`
verifica esto de forma estática.

API pública:
    check_entry(proposal, account, market, limits) -> RiskVerdict
    check_open_position(position, account, limits) -> RiskVerdict

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from core.trading.contracts import (
    AccountRiskSnapshot,
    MarketExecutionSnapshot,
    PositionRiskSnapshot,
    RiskLimits,
    RiskMode,
    RiskVerdict,
    TradeDecision,
)
from core.trading.risk_rules import check_entry as _check_entry
from core.trading.risk_rules import check_open_position as _check_open_position
from core.trading.risk_rules import compute_risk_mode as _compute_risk_mode


def check_entry(
    proposal: TradeDecision,
    account: AccountRiskSnapshot,
    market: MarketExecutionSnapshot,
    limits: RiskLimits,
) -> RiskVerdict:
    """¿Puede abrirse esta propuesta ENTER? Ver precedencia completa en
    `core.trading.risk_rules` (módulo)."""
    return _check_entry(proposal, account, market, limits)


def check_open_position(
    position: PositionRiskSnapshot,
    account: AccountRiskSnapshot,
    limits: RiskLimits,
) -> RiskVerdict:
    """¿Qué debe pasar con esta posición ya abierta? Ver precedencia
    completa en `core.trading.risk_rules` (módulo)."""
    return _check_open_position(position, account, limits)


def current_risk_mode(account: AccountRiskSnapshot, limits: RiskLimits) -> RiskMode:
    """Conveniencia de solo lectura — p.ej. para mostrar el estado de la
    jaula en un dashboard/Telegram sin duplicar la precedencia."""
    mode, _rule_id = _compute_risk_mode(account, limits)
    return mode
