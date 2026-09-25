"""
core/trading/risk_rules.py — Matriz de reglas y precedencia de la jaula
de seguridad, ANTES de escribir `risk_gate.py` como módulo público.

Funciones puras y deterministas: solo reciben los snapshots tipados de
`contracts.py` y `RiskLimits`. Nunca consultan gravity engine, memoria,
señales ni ningún servicio — eso es exactamente lo que NO pertenece a la
jaula (pertenece a `TradeDecisionEngine`).

Lo que SÍ pertenece aquí (y nada más):
  - stop duro por operación
  - pérdida máxima diaria
  - máximo de posiciones simultáneas
  - exposición máxima total
  - kill switch
  - estado del broker / datos inválidos (fail-safe)

-----------------------------------------------------------------------
compute_risk_mode() — precedencia (la primera regla que aplica, gana):

  1. EMERGENCY_FLATTEN
     — `account.kill_switch == EMERGENCY_FLATTEN` (kill switch manual en
       modo liquidar), O
     — `account.broker_state_ok is False` o `positions_sync_ok is False`:
       si el sistema no puede confiar en su propio estado, ningún otro
       límite (exposición, nº de posiciones) es evaluable con certeza —
       liquidar es la única respuesta fail-safe.

  2. HALT_NEW_RISK
     — `account.kill_switch == HALT_NEW_RISK` (kill switch manual en modo
       halt), O
     — `account.realized_pnl_today_usd <= -limits.max_daily_loss_usd`
       (pérdida diaria máxima alcanzada).
     Esto NUNCA cierra ni reduce posiciones abiertas por sí solo — ver
     nota en `check_open_position()`.

  3. NORMAL — ningún límite global activado.

-----------------------------------------------------------------------
check_entry() — precedencia (la primera regla que aplica, gana):

  1. risk_mode != NORMAL                        -> BLOCK_ENTRY
  2. datos de mercado inválidos/stale/no operable -> BLOCK_ENTRY (fail-safe)
  3. `account.open_positions_count >= limits.max_open_positions`
                                                  -> BLOCK_ENTRY
  4. `account.total_exposure_usd >= limits.max_total_exposure_usd`
                                                  -> BLOCK_ENTRY
  5. -> PASS

-----------------------------------------------------------------------
check_open_position() — precedencia (la primera regla que aplica, gana):

  1. risk_mode == EMERGENCY_FLATTEN              -> OVERRIDE_EXIT
  2. stop duro cruzado (side-aware)               -> OVERRIDE_EXIT
  3. `account.total_exposure_usd > limits.max_total_exposure_usd`
     (el límite ya está excedido, no solo alcanzado)
                                                  -> OVERRIDE_REDUCE
  4. -> PASS

  Nota deliberada: `HALT_NEW_RISK` NO aparece en esta lista. Alcanzar la
  pérdida diaria máxima impide abrir riesgo nuevo, pero nunca cierra ni
  reduce una posición ya abierta por sí solo — eso sigue bajo su propio
  stop duro (regla 2 arriba) y bajo el criterio de `TradeDecisionEngine`.
  Mezclar ambas cosas es precisamente el error que esta separación evita.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

from core.trading.contracts import (
    AccountRiskSnapshot,
    MarketExecutionSnapshot,
    PositionRiskSnapshot,
    RiskAction,
    RiskLimits,
    RiskMode,
    RiskVerdict,
    TradeDecision,
)

# ---------------------------------------------------------------------------
# rule_id — identificadores estables, usados en RiskVerdict.rule_id para
# auditoría. Nunca se reutilizan para dos condiciones distintas.
# ---------------------------------------------------------------------------

RULE_KILL_SWITCH_FLATTEN = "kill_switch_flatten"
RULE_BROKER_STATE_INVALID = "broker_state_invalid"
RULE_KILL_SWITCH_HALT = "kill_switch_halt"
RULE_MAX_DAILY_LOSS = "max_daily_loss"
RULE_INVALID_MARKET_DATA = "invalid_market_data"
RULE_MAX_OPEN_POSITIONS = "max_open_positions"
RULE_MAX_TOTAL_EXPOSURE = "max_total_exposure"
RULE_HARD_STOP = "hard_stop"
RULE_EXPOSURE_BREACHED = "exposure_breached"
RULE_NONE = "none"


# ---------------------------------------------------------------------------
# compute_risk_mode
# ---------------------------------------------------------------------------

def compute_risk_mode(
    account: AccountRiskSnapshot, limits: RiskLimits
) -> tuple[RiskMode, str]:
    """Deriva el `RiskMode` global. Devuelve `(mode, rule_id)`."""

    if account.kill_switch == RiskMode.EMERGENCY_FLATTEN:
        return RiskMode.EMERGENCY_FLATTEN, RULE_KILL_SWITCH_FLATTEN
    if not account.broker_state_ok or not account.positions_sync_ok:
        return RiskMode.EMERGENCY_FLATTEN, RULE_BROKER_STATE_INVALID

    if account.kill_switch == RiskMode.HALT_NEW_RISK:
        return RiskMode.HALT_NEW_RISK, RULE_KILL_SWITCH_HALT
    if account.realized_pnl_today_usd <= -limits.max_daily_loss_usd:
        return RiskMode.HALT_NEW_RISK, RULE_MAX_DAILY_LOSS

    return RiskMode.NORMAL, RULE_NONE


# ---------------------------------------------------------------------------
# check_entry
# ---------------------------------------------------------------------------

def check_entry(
    proposal: TradeDecision,
    account: AccountRiskSnapshot,
    market: MarketExecutionSnapshot,
    limits: RiskLimits,
) -> RiskVerdict:
    """Evalúa si una propuesta ENTER puede abrirse. Nunca evalúa criterio
    — solo los límites duros listados en el docstring del módulo."""

    mode, mode_rule_id = compute_risk_mode(account, limits)
    if mode != RiskMode.NORMAL:
        return RiskVerdict(
            forced_action=RiskAction.BLOCK_ENTRY,
            reason=f"risk_mode={mode.value}",
            rule_id=mode_rule_id,
        )

    if market.price is None or market.price_is_stale or not market.broker_tradable:
        return RiskVerdict(
            forced_action=RiskAction.BLOCK_ENTRY,
            reason=f"datos de mercado inválidos para {market.symbol}",
            rule_id=RULE_INVALID_MARKET_DATA,
        )

    if account.open_positions_count >= limits.max_open_positions:
        return RiskVerdict(
            forced_action=RiskAction.BLOCK_ENTRY,
            reason=(
                f"{account.open_positions_count} posiciones abiertas "
                f">= máximo {limits.max_open_positions}"
            ),
            rule_id=RULE_MAX_OPEN_POSITIONS,
        )

    if account.total_exposure_usd >= limits.max_total_exposure_usd:
        return RiskVerdict(
            forced_action=RiskAction.BLOCK_ENTRY,
            reason=(
                f"exposición ${account.total_exposure_usd:.2f} "
                f">= máximo ${limits.max_total_exposure_usd:.2f}"
            ),
            rule_id=RULE_MAX_TOTAL_EXPOSURE,
        )

    return RiskVerdict(forced_action=RiskAction.PASS, reason="dentro de límites", rule_id=RULE_NONE)


# ---------------------------------------------------------------------------
# check_open_position
# ---------------------------------------------------------------------------

def _hard_stop_crossed(position: PositionRiskSnapshot) -> bool:
    if position.current_price is None:
        return False
    if position.side == "buy":
        return position.current_price <= position.hard_stop_price
    return position.current_price >= position.hard_stop_price


def check_open_position(
    position: PositionRiskSnapshot,
    account: AccountRiskSnapshot,
    limits: RiskLimits,
) -> RiskVerdict:
    """Evalúa una posición ya abierta. `HALT_NEW_RISK` deliberadamente no
    produce ningún override aquí — ver nota de precedencia en el docstring
    del módulo."""

    mode, mode_rule_id = compute_risk_mode(account, limits)
    if mode == RiskMode.EMERGENCY_FLATTEN:
        return RiskVerdict(
            forced_action=RiskAction.OVERRIDE_EXIT,
            reason=f"risk_mode={mode.value}",
            rule_id=mode_rule_id,
        )

    if _hard_stop_crossed(position):
        return RiskVerdict(
            forced_action=RiskAction.OVERRIDE_EXIT,
            reason=(
                f"stop duro cruzado: precio {position.current_price} vs "
                f"stop {position.hard_stop_price} ({position.side})"
            ),
            rule_id=RULE_HARD_STOP,
        )

    if account.total_exposure_usd > limits.max_total_exposure_usd:
        return RiskVerdict(
            forced_action=RiskAction.OVERRIDE_REDUCE,
            reason=(
                f"exposición ${account.total_exposure_usd:.2f} "
                f"> máximo ${limits.max_total_exposure_usd:.2f}"
            ),
            rule_id=RULE_EXPOSURE_BREACHED,
        )

    return RiskVerdict(forced_action=RiskAction.PASS, reason="dentro de límites", rule_id=RULE_NONE)
