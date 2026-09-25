"""
connectors/etoro/position_manager.py — Gestor de posiciones abiertas.

`position_manager.py` no decide por qué salir. Solo administra estado +
transición + persistencia:

    RiskGate ──┐
               ├──> position_manager.py ──> core/trading/position_state.py
    criterio ──┘         │
                          v
                   OrderIntent (simulado para PAPER; LIVE lo consume
                   auto_executor.py — pendiente de adaptar)
                          │
                          v
                   OrderExecution
                          │
                          v
                   apply_execution_update() ──> PositionRecord persistido

Cada posición abierta se evalúa así, en orden:
  1. `risk_gate.check_open_position(...)` — límites duros, ciego a
     criterio. Si no es `PASS`, su veredicto SUSTITUYE cualquier
     criterio (`RiskGate` puede anular a la capa de criterio; la capa de
     criterio nunca puede anular a `RiskGate`).
  2. Si `RiskGate` dio `PASS`, se consulta
     `trade_decision_engine.propose_decision(...)` (TradeDecisionEngine
     real — reemplaza a `legacy_criterion_engine.py`, retirado). Este
     archivo no contiene ese criterio directamente — ver
     `trade_decision_engine.py` para dónde vive ahora
     `_check_coherence_loss`/`_check_contrary_signal`, ahora conectadas
     a `EntryThesis.invalidation_conditions` en vez de a un booleano
     hardcodeado.
  3. La `TradeDecision` resultante entra en
     `core.trading.position_state.apply_decision(...)`.
  4. Para PAPER, el fill se simula al instante (no hay broker real que
     confirmar) y entra por
     `core.trading.position_state.apply_execution_update(...)` —
     exactamente el mismo camino que usará LIVE cuando
     `auto_executor.py` se adapte para producir `OrderExecution` reales.
  5. Solo cuando `PositionRecord.status == CLOSED` (decidido por
     `position_state.py`, nunca por este archivo) se persiste el cierre
     en el ledger PAPER.

No cierra posiciones LIVE automáticamente — solo propone.

API pública:
    check_open_positions() -> List[Dict]   (acciones tomadas)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from connectors.etoro import trade_decision_engine
from core.trading import risk_gate
from core.trading.contracts import (
    AccountRiskSnapshot,
    EntryThesis,
    ExecutionStatus,
    OrderAction,
    OrderExecution,
    OrderIntent,
    PositionRecord,
    PositionStatus,
    RiskAction,
    RiskLimits,
    RiskMode,
    RiskVerdict,
    TradeAction,
    TradeDecision,
)
from core.trading.position_state import apply_decision, apply_execution_update

logger = logging.getLogger("vectrax.etoro.position_manager")


def check_open_positions() -> List[Dict[str, Any]]:
    """
    Check all open paper positions for exit conditions.
    Returns list of actions taken (closed positions).
    """
    from connectors.etoro.auto_executor import (
        get_paper_trades, get_config, record_trade_result,
    )

    cfg = get_config()
    max_hold_h = cfg.get("max_hold_hours", 24)
    actions: List[Dict[str, Any]] = []

    open_trades = [t for t in get_paper_trades(limit=100) if t.status == "open"]
    if not open_trades:
        return actions

    account = _build_account_snapshot(cfg, open_trades)
    limits = _build_risk_limits(cfg)
    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)

    for trade in open_trades:
        current_price = _get_current_price(trade.symbol)
        position_snapshot = _build_position_risk_snapshot(trade, current_price)
        risk_verdict = risk_gate.check_open_position(position_snapshot, account, limits)

        thesis = _legacy_entry_thesis(trade)

        if risk_verdict.forced_action != RiskAction.PASS:
            decision = _decision_from_risk_verdict(trade, risk_verdict)
        else:
            decision = trade_decision_engine.propose_decision(
                thesis, current_price, now_ts, max_hold_h,
            )

        if decision.action == TradeAction.HOLD:
            continue

        if decision.action == TradeAction.REDUCE:
            # El ledger PAPER (PaperTrade/record_trade_result) no modela
            # cierres parciales todavía — no se inventa esa semántica
            # aquí. Se deja constancia y se sigue vigilando; la posición
            # sigue abierta con su tamaño completo.
            logger.warning(
                "[POSITION_MGR] %s (%s): decisión REDUCE (%s) — el ledger "
                "PAPER no soporta reducción parcial todavía, no se actúa",
                trade.trade_id, trade.symbol, decision.reason,
            )
            continue

        # EXIT: recorre el pipeline completo de position_state.py.
        record = _synthesize_holding_record(trade, now_dt, thesis)
        intent = OrderIntent(
            intent_id=f"{trade.trade_id}-close-{int(now_ts)}",
            position_id=trade.trade_id,
            action=OrderAction.CLOSE,
            quantity=trade.amount_usd,
            created_at=now_dt,
        )
        record = apply_decision(record, decision, intent, now_dt)
        execution = _simulate_paper_fill(intent, current_price, now_dt)
        record = apply_execution_update(record, execution, now_dt)

        if record.status != PositionStatus.CLOSED:
            # No debería ocurrir para un CLOSE simulado (siempre FILLED),
            # pero si algún día el fill no es instantáneo, no se cierra
            # nada por fuera de este chequeo.
            continue

        result = _close_paper_trade(trade, decision.reason, current_price)
        if result:
            actions.append(result)
            pnl = result.get("pnl_usd", 0)
            record_trade_result(pnl, is_paper=True, trade_id=trade.trade_id)
            _log_exit_observation(trade, decision.reason, result)

    if actions:
        logger.info(
            "[POSITION_MGR] Closed %d positions: %s",
            len(actions),
            [a.get("reason", "?") for a in actions],
        )

    return actions


# ---------------------------------------------------------------------------
# RiskGate wiring — snapshots construidos desde config/estado real. Cero
# criterio aquí: solo aritmética y lectura de config, igual que RiskGate
# exige de quien lo llama.
# ---------------------------------------------------------------------------

def _build_account_snapshot(cfg: Dict[str, Any], open_trades: list) -> AccountRiskSnapshot:
    # PAPER no tiene broker real: no hay desconexión ni desincronización
    # posible — ambas quedan en True. LIVE, al adaptar auto_executor.py,
    # las alimentará con el estado real de la conexión/reconciliación.
    #
    # cfg["halt"] es el kill switch manual existente ("emergency stop
    # flag"). Se interpreta como HALT_NEW_RISK, no EMERGENCY_FLATTEN: es
    # coherente con el resto de la arquitectura — nada liquida
    # posiciones abiertas automáticamente sin una decisión explícita de
    # ese nivel.
    kill_switch = RiskMode.HALT_NEW_RISK if cfg.get("halt") else RiskMode.NORMAL
    return AccountRiskSnapshot(
        equity_usd=cfg.get("equity_usd", 10_000.0),
        realized_pnl_today_usd=-float(cfg.get("daily_loss_usd", 0.0)),
        open_positions_count=len(open_trades),
        total_exposure_usd=sum(t.amount_usd for t in open_trades),
        kill_switch=kill_switch,
        positions_sync_ok=True,
        broker_execution_ok=True,
    )


def _build_risk_limits(cfg: Dict[str, Any]) -> RiskLimits:
    max_position_usd = float(cfg.get("max_position_usd", 50.0))
    max_positions_open = int(cfg.get("max_positions_open", 2))
    stop_loss_pct = float(cfg.get("stop_loss_pct", 1.5))
    return RiskLimits(
        max_loss_per_trade_usd=max_position_usd * stop_loss_pct / 100,
        max_daily_loss_usd=float(cfg.get("max_daily_loss_usd", 10.0)),
        max_open_positions=max_positions_open,
        # Exposición total = límite por posición × nº máximo de posiciones.
        # No hay un tercer número independiente configurado todavía.
        max_total_exposure_usd=max_position_usd * max_positions_open,
    )


def _build_position_risk_snapshot(trade, current_price: Optional[float]):
    from core.trading.contracts import PositionRiskSnapshot
    return PositionRiskSnapshot(
        position_id=trade.trade_id,
        symbol=trade.symbol,
        side=trade.direction,
        entry_price=trade.entry_price,
        current_price=current_price,
        hard_stop_price=trade.stop_loss,
        size_usd=trade.amount_usd,
    )


def _decision_from_risk_verdict(trade, verdict: RiskVerdict) -> TradeDecision:
    action = (
        TradeAction.EXIT if verdict.forced_action == RiskAction.OVERRIDE_EXIT
        else TradeAction.REDUCE
    )
    return TradeDecision(
        action=action,
        position_id=trade.trade_id,
        reason=f"RiskGate: {verdict.reason} [{verdict.rule_id}]",
        confidence=1.0,
        evidence_refs=(verdict.rule_id,),
    )


# ---------------------------------------------------------------------------
# position_state.py wiring
# ---------------------------------------------------------------------------

def _legacy_entry_thesis(trade) -> EntryThesis:
    """Sintética: los PaperTrade existentes no fueron creados por
    TradeDecisionEngine (que todavía no existe), así que no hay una
    EntryThesis real que recuperar. Se reemplaza por completo el día que
    el ENTER pase por TradeDecisionEngine de verdad."""
    return EntryThesis(
        position_id=trade.trade_id,
        symbol=trade.symbol,
        side=trade.direction,
        created_at=datetime.fromtimestamp(trade.timestamp, tz=timezone.utc),
        thesis=f"(legacy, sin TradeDecisionEngine) entrada por proposal {trade.proposal_id}",
        invalidation_conditions=(
            f"cc_score de market:{trade.symbol} cae bajo 0.1",
            "señal contraria OPERABLE detectada en las últimas 2h",
        ),
        confidence_at_entry=0.5,
        evidence_snapshot={},
        evidence_snapshot_id=f"legacy:{trade.proposal_id}",
    )


def _synthesize_holding_record(trade, now: datetime, thesis: Optional[EntryThesis] = None) -> PositionRecord:
    return PositionRecord(
        position_id=trade.trade_id,
        entry_thesis=thesis if thesis is not None else _legacy_entry_thesis(trade),
        status=PositionStatus.HOLDING,
        remaining_quantity=trade.amount_usd,
        current_intent=None,
        last_execution=None,
        updated_at=now,
    )


def _simulate_paper_fill(intent: OrderIntent, current_price: Optional[float], now: datetime) -> OrderExecution:
    """PAPER no tiene broker real que confirmar: el fill se simula al
    instante. Cuando `auto_executor.py` se adapte para LIVE, esta
    función deja de usarse — LIVE construye su `OrderExecution` a partir
    de la respuesta real del broker, por el mismo
    `apply_execution_update()`."""
    return OrderExecution(
        intent_id=intent.intent_id,
        broker_order_id=f"paper-{intent.intent_id}",
        status=ExecutionStatus.FILLED,
        filled_quantity=intent.quantity,
        avg_fill_price=current_price,
        last_update_at=now,
    )


# ---------------------------------------------------------------------------
# Mercado / persistencia — I/O, no criterio.
# ---------------------------------------------------------------------------

def _get_current_price(symbol: str) -> float | None:
    """Get current price for symbol."""
    try:
        from connectors.etoro.market import get_price
        p = get_price(symbol)
        if p and "last" in p:
            return float(p["last"])
    except Exception:
        pass
    # Fallback: try market observer
    try:
        from connectors.etoro.market_observer import evaluate
        result = evaluate(symbol=symbol, direction="buy")
        if result.price > 0:
            return result.price
    except Exception:
        pass
    return None


def _close_paper_trade(trade, reason: str, current_price: Optional[float]) -> Dict[str, Any] | None:
    """Persiste el cierre. Se llama EXCLUSIVAMENTE después de que
    `position_state.apply_execution_update()` ya resolvió el
    `PositionRecord` a CLOSED — esta función nunca decide por sí misma
    que una posición está cerrada, solo registra el desenlace."""
    try:
        from connectors.etoro.auto_executor import _PAPER_LOG_FILE
        if current_price is None:
            current_price = trade.entry_price  # fallback

        # Calculate PnL
        if trade.direction == "buy":
            pnl_pct = (current_price - trade.entry_price) / trade.entry_price * 100
        else:
            pnl_pct = (trade.entry_price - current_price) / trade.entry_price * 100
        pnl_usd = trade.amount_usd * (pnl_pct / 100)

        status = "closed_win" if pnl_usd > 0 else "closed_loss" if pnl_usd < 0 else "closed_neutral"

        # Update the trade in the JSONL file
        if os.path.exists(_PAPER_LOG_FILE):
            lines = []
            with open(_PAPER_LOG_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("trade_id") == trade.trade_id:
                            d["status"] = status
                            d["exit_price"] = current_price
                            d["pnl_usd"] = round(pnl_usd, 2)
                            d["pnl_pct"] = round(pnl_pct, 3)
                        lines.append(json.dumps(d, ensure_ascii=False))
                    except Exception:
                        lines.append(line)
            with open(_PAPER_LOG_FILE, "w") as f:
                f.write("\n".join(lines) + "\n")

        # Cierra el tramo final del recorrido causal:
        # application_id -> outcome_id. `trade.proposal_id` es el MISMO
        # `decision_id` con el que se registró la aplicación al ejecutar, así
        # que la resolución actualiza esa misma aplicación sin heurísticas.
        # Nunca propaga un fallo: cerrar la operación no depende de la traza.
        try:
            from core.learn.causal_learning import resolve_decision_outcome
            resolve_decision_outcome(
                trade.proposal_id, outcome_status=status, outcome_value=pnl_usd,
            )
        except Exception as exc:
            logger.debug("causal outcome trace error: %s", exc)

        logger.info(
            "[PAPER_CLOSE] %s | %s %s | PnL=$%.2f (%.2f%%) | reason=%s",
            trade.trade_id, trade.direction.upper(), trade.symbol,
            pnl_usd, pnl_pct, reason,
        )

        return {
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "entry_price": trade.entry_price,
            "exit_price": current_price,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 3),
            "status": status,
            "reason": reason,
        }
    except Exception as exc:
        logger.error("close_paper_trade error: %s", exc)
        return None


def _log_exit_observation(trade, reason: str, result: Dict) -> None:
    """Log position exit to observation ledger."""
    try:
        from core.self_observation.observation_ledger import record
        pnl = result.get("pnl_usd", 0)
        icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
        record(
            domain="market",
            obs_type="position_closed",
            summary=(
                f"{icon} Posición cerrada: {trade.direction.upper()} {trade.symbol} "
                f"PnL=${pnl:.2f} | Razón: {reason[:60]}"
            ),
            star_id=f"market:{trade.symbol}",
            evidence={
                "trade_id": trade.trade_id,
                "pnl_usd": pnl,
                "reason": reason,
                "status": result.get("status"),
            },
        )
    except Exception:
        pass


def get_open_positions_summary() -> str:
    """Format open positions for Telegram display."""
    from connectors.etoro.auto_executor import get_paper_trades

    open_trades = [t for t in get_paper_trades(limit=100) if t.status == "open"]
    if not open_trades:
        return "Sin posiciones abiertas."

    lines = [f"📊 {len(open_trades)} posición(es) abierta(s):\n"]
    for t in open_trades:
        elapsed_h = (time.time() - t.timestamp) / 3600
        current = _get_current_price(t.symbol)
        pnl_str = ""
        if current:
            if t.direction == "buy":
                pnl = (current - t.entry_price) / t.entry_price * 100
            else:
                pnl = (t.entry_price - current) / t.entry_price * 100
            icon = "🟢" if pnl > 0 else "🔴"
            pnl_str = f" | {icon} {pnl:+.2f}%"

        lines.append(
            f"  {t.trade_id}\n"
            f"    {t.direction.upper()} {t.symbol} ${t.amount_usd:.0f}\n"
            f"    Entry: {t.entry_price:.5g} | SL: {t.stop_loss:.5g} | TP: {t.take_profit:.5g}\n"
            f"    Tiempo: {elapsed_h:.1f}h{pnl_str}"
        )
    return "\n".join(lines)
