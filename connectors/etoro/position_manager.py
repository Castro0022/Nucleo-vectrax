"""
connectors/etoro/position_manager.py — Gestor de posiciones abiertas.

`position_manager.py` no decide por qué salir. Solo administra estado +
transición + persistencia:

    RiskGate ──┐
               ├──> position_manager.py ──> core/trading/position_state.py
    criterio ──┘         │
                          v
                   OrderIntent
                          │
              ┌───────────┴───────────┐
              v                       v
      _simulate_paper_fill    execution_adapter.submit_close
      (PAPER, instantáneo)    (LIVE, trade_executor real)
              │                       │
              └───────────┬───────────┘
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

`check_open_live_positions()` sigue exactamente el mismo orden para
posiciones LIVE (`live_position_store.py`), pero el fill viene de
`execution_adapter.py` (broker real vía `trade_executor.py`), nunca
simulado. Limitación conocida: no hay reconciliación ACTIVA contra el
broker todavía — eToro no expone un endpoint de "consultar orden por
id" en `etoro_client.py`, solo `get_portfolio()` (lista posiciones, no
órdenes). Una posición en `RECONCILING` se reintenta cada ciclo vía el
mismo `intent_id` — el guard de idempotencia impide que eso reenvíe al
broker mientras siga sin resolverse, pero sin una consulta real puede
quedarse en `RECONCILING` indefinidamente. Cerrar ese hueco (comparar
contra `get_portfolio()`) queda fuera de este paso.

API pública:
    check_open_positions() -> List[Dict]        (PAPER, acciones tomadas)
    check_open_live_positions(user_id) -> List[Dict]  (LIVE, ídem)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from connectors.etoro import execution_adapter, live_position_store, trade_decision_engine
from core.trading import risk_gate
from core.trading.contracts import (
    AccountRiskSnapshot,
    EntryThesis,
    ExecutionStatus,
    OrderAction,
    OrderExecution,
    OrderIntent,
    PositionRecord,
    PositionRiskSnapshot,
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

    account = _build_account_snapshot(
        cfg, len(open_trades), sum(t.amount_usd for t in open_trades),
    )
    limits = _build_risk_limits(cfg)
    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)

    for trade in open_trades:
        current_price = _get_current_price(trade.symbol)
        position_snapshot = _build_position_risk_snapshot(trade, current_price)
        risk_verdict = risk_gate.check_open_position(position_snapshot, account, limits)

        thesis = _legacy_entry_thesis(trade)

        if risk_verdict.forced_action != RiskAction.PASS:
            decision = _decision_from_risk_verdict(trade.trade_id, risk_verdict)
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

def _build_account_snapshot(
    cfg: Dict[str, Any],
    open_positions_count: int,
    total_exposure_usd: float,
    *,
    positions_sync_ok: bool = True,
    broker_execution_ok: bool = True,
) -> AccountRiskSnapshot:
    # PAPER no tiene broker real: no hay desconexión ni desincronización
    # posible — ambas quedan en True por defecto. LIVE (check_open_live_
    # positions) puede pasar estado real de conexión el día que exista
    # una fuente para eso; hoy también queda en True (ver limitación
    # documentada ahí: no hay reconciliación activa contra el broker
    # todavía, solo el guard de idempotencia).
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
        open_positions_count=open_positions_count,
        total_exposure_usd=total_exposure_usd,
        kill_switch=kill_switch,
        positions_sync_ok=positions_sync_ok,
        broker_execution_ok=broker_execution_ok,
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


def _build_position_risk_snapshot(trade, current_price: Optional[float]) -> PositionRiskSnapshot:
    return PositionRiskSnapshot(
        position_id=trade.trade_id,
        symbol=trade.symbol,
        side=trade.direction,
        entry_price=trade.entry_price,
        current_price=current_price,
        hard_stop_price=trade.stop_loss,
        size_usd=trade.amount_usd,
    )


def _decision_from_risk_verdict(position_id: str, verdict: RiskVerdict) -> TradeDecision:
    action = (
        TradeAction.EXIT if verdict.forced_action == RiskAction.OVERRIDE_EXIT
        else TradeAction.REDUCE
    )
    return TradeDecision(
        action=action,
        position_id=position_id,
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


# ---------------------------------------------------------------------------
# LIVE — mismo orden (RiskGate -> criterio -> position_state) que PAPER,
# pero el fill viene de execution_adapter.py (broker real), nunca
# simulado. ENTER (abrir la posición) sigue siendo el pipeline de
# propuestas existente (auto_executor.execute_proposal) — este archivo
# solo administra lo que ya está abierto, igual que con PAPER.
# ---------------------------------------------------------------------------

def check_open_live_positions(user_id: Any = None) -> List[Dict[str, Any]]:
    """Equivalente LIVE de `check_open_positions()`. Ver limitación de
    reconciliación activa en el docstring del módulo."""
    from connectors.etoro.auto_executor import get_config, record_trade_result

    cfg = get_config()
    max_hold_h = cfg.get("max_hold_hours", 24)
    if user_id is None:
        creator_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "2030762343")
        user_id = f"tg:{creator_id}"

    actions: List[Dict[str, Any]] = []
    open_entries = live_position_store.open_positions()
    if not open_entries:
        return actions

    account = _build_account_snapshot(
        cfg, len(open_entries),
        sum(e.record.remaining_quantity for e in open_entries),
    )
    limits = _build_risk_limits(cfg)
    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)

    for entry in open_entries:
        record = entry.record
        current_price = _get_current_price(record.entry_thesis.symbol)

        if record.status in (PositionStatus.CLOSING, PositionStatus.RECONCILING):
            # Ya hay un CLOSE en curso — nunca se decide de nuevo. Solo se
            # reintenta avanzar el mismo intent_id (idempotente: no
            # reenvía al broker si sigue sin resolverse).
            new_record = _advance_in_flight_live_close(record, entry, user_id, now_dt)
            _persist_live_update(entry, new_record)
            if new_record.status == PositionStatus.CLOSED:
                result = _finalize_live_close(entry, new_record, "reconciliación", current_price)
                if result:
                    actions.append(result)
                    record_trade_result(result["pnl_usd"], is_paper=False)
            continue

        position_snapshot = PositionRiskSnapshot(
            position_id=record.position_id,
            symbol=record.entry_thesis.symbol,
            side=record.entry_thesis.side,
            entry_price=entry.entry_price,
            current_price=current_price,
            hard_stop_price=entry.hard_stop_price,
            size_usd=record.remaining_quantity,
        )
        risk_verdict = risk_gate.check_open_position(position_snapshot, account, limits)

        if risk_verdict.forced_action != RiskAction.PASS:
            decision = _decision_from_risk_verdict(record.position_id, risk_verdict)
        else:
            decision = trade_decision_engine.propose_decision(
                record.entry_thesis, current_price, now_ts, max_hold_h,
            )

        if decision.action == TradeAction.HOLD:
            continue

        if decision.action == TradeAction.REDUCE:
            # etoro_client.close_position() cierra la posición COMPLETA —
            # no hay endpoint de cierre parcial que adaptar. No se inventa
            # esa semántica aquí, igual que en PAPER.
            logger.warning(
                "[POSITION_MGR][LIVE] %s (%s): decisión REDUCE (%s) — eToro "
                "no expone cierre parcial, no se actúa",
                record.position_id, record.entry_thesis.symbol, decision.reason,
            )
            continue

        # EXIT: intent_id NUEVO (la posición no tenía ninguno en curso —
        # venía de HOLDING), CLOSING, primer intento de submit_close.
        intent = OrderIntent(
            intent_id=f"{record.position_id}-close-{int(now_ts)}",
            position_id=record.position_id,
            action=OrderAction.CLOSE,
            quantity=record.remaining_quantity,
            created_at=now_dt,
        )
        new_record = apply_decision(record, decision, intent, now_dt)
        execution = execution_adapter.submit_close(
            intent, user_id=user_id, broker_position_id=entry.broker_position_id,
            instrument_id=entry.instrument_id, symbol=record.entry_thesis.symbol,
        )
        new_record = apply_execution_update(new_record, execution, now_dt)
        _persist_live_update(entry, new_record)

        if new_record.status == PositionStatus.CLOSED:
            result = _finalize_live_close(entry, new_record, decision.reason, current_price)
            if result:
                actions.append(result)
                record_trade_result(result["pnl_usd"], is_paper=False)

    return actions


def _advance_in_flight_live_close(
    record: PositionRecord, entry: "live_position_store.LivePositionEntry",
    user_id: Any, now_dt: datetime,
) -> PositionRecord:
    """Reintenta el MISMO intent_id de un CLOSE ya en curso. Nunca mina
    uno nuevo — `execution_adapter.submit_close` ya se niega a llamar al
    broker de nuevo mientras el anterior siga sin resolverse; esto solo
    le da la oportunidad de devolver un desenlace más reciente si ya se
    resolvió."""
    if record.current_intent is None:
        return record
    execution = execution_adapter.submit_close(
        record.current_intent, user_id=user_id,
        broker_position_id=entry.broker_position_id,
        instrument_id=entry.instrument_id, symbol=record.entry_thesis.symbol,
    )
    if execution.intent_id != record.current_intent.intent_id:
        return record
    return apply_execution_update(record, execution, now_dt)


def _persist_live_update(entry: "live_position_store.LivePositionEntry", new_record: PositionRecord) -> None:
    live_position_store.save(
        live_position_store.LivePositionEntry(
            record=new_record,
            broker_position_id=entry.broker_position_id,
            instrument_id=entry.instrument_id,
            entry_price=entry.entry_price,
            hard_stop_price=entry.hard_stop_price,
        )
    )


def _finalize_live_close(
    entry: "live_position_store.LivePositionEntry",
    closed_record: PositionRecord,
    reason: str,
    current_price: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Registra el desenlace de un cierre LIVE ya confirmado por
    `position_state.py` (`status == CLOSED`). Nunca decide el cierre —
    solo lo audita, igual que `_close_paper_trade` para PAPER."""
    try:
        thesis = closed_record.entry_thesis
        symbol, side = thesis.symbol, thesis.side
        entry_price = entry.entry_price
        if current_price is None:
            current_price = entry_price

        if side == "buy":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # remaining_quantity ya quedó en 0 tras el CLOSE — el tamaño
        # cerrado es el que llevaba el intent de cierre.
        size_usd = closed_record.current_intent.quantity if closed_record.current_intent else 0.0
        pnl_usd = size_usd * (pnl_pct / 100)
        status = "closed_win" if pnl_usd > 0 else "closed_loss" if pnl_usd < 0 else "closed_neutral"

        try:
            from core.learn.causal_learning import resolve_decision_outcome
            resolve_decision_outcome(
                thesis.evidence_snapshot_id, outcome_status=status, outcome_value=pnl_usd,
            )
        except Exception as exc:
            logger.debug("causal outcome trace error (LIVE): %s", exc)

        logger.info(
            "[LIVE_CLOSE] %s | %s %s | PnL=$%.2f (%.2f%%) | reason=%s",
            closed_record.position_id, side.upper(), symbol, pnl_usd, pnl_pct, reason,
        )

        try:
            from core.self_observation.observation_ledger import record
            icon = "✅" if pnl_usd > 0 else "❌" if pnl_usd < 0 else "➖"
            record(
                domain="market",
                obs_type="position_closed",
                summary=(
                    f"{icon} Posición LIVE cerrada: {side.upper()} {symbol} "
                    f"PnL=${pnl_usd:.2f} | Razón: {reason[:60]}"
                ),
                star_id=f"market:{symbol}",
                evidence={
                    "position_id": closed_record.position_id,
                    "pnl_usd": pnl_usd, "reason": reason, "status": status,
                },
            )
        except Exception:
            pass

        return {
            "position_id": closed_record.position_id,
            "symbol": symbol,
            "direction": side,
            "entry_price": entry_price,
            "exit_price": current_price,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 3),
            "status": status,
            "reason": reason,
        }
    except Exception as exc:
        logger.error("_finalize_live_close error: %s", exc)
        return None
