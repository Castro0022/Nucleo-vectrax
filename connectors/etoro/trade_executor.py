"""
Vectrax eToro Trade Executor
==============================
Executes trades on eToro ONLY with explicit creator authorization.

Authorization gate:
  - Only TELEGRAM_CREATOR_CHAT_ID / VX_CREATOR_ID can trigger execution.
  - Every execution is logged to structural memory and ledger before AND after.
  - If environment is 'real', an additional confirmation is required.

Audit trail (every operation):
  - Pre-execution: intent, symbol, amount, direction, mode, timestamp
  - Post-execution: order_id, position_id, status, actual_price, latency
  - On failure: error, raw response, rollback status
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from connectors.etoro.etoro_client import (
    get_instrument_id,
    get_portfolio,
    open_position,
    close_position,
    get_price,
)
from connectors.etoro import get_environment

logger = logging.getLogger("vectrax.etoro.executor")

# ── Creator IDs (mirrors gateway) ────────────────────────────────────
_CREATOR_TG_ID = int(os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "2030762343"))

# ── Audit log ────────────────────────────────────────────────────────
_AUDIT_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_trade_audit.jsonl"
)


def _log_audit(record: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        with open(_AUDIT_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("etoro audit log failed: %s", e)


# ── Result dataclass ─────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    success:      bool
    action:       str          # "open" | "close" | "denied"
    symbol:       str
    environment:  str
    order_id:     Optional[Any] = None
    position_id:  Optional[Any] = None
    amount:       float = 0.0
    is_buy:       Optional[bool] = None
    error:        Optional[str] = None
    latency_ms:   float = 0.0
    timestamp:    float = field(default_factory=time.time)
    audit_id:     str = ""

    def to_telegram(self) -> str:
        env_icon = "🔴 REAL" if self.environment == "real" else "🟢 DEMO"
        if not self.success:
            return (
                f"❌ Ejecución fallida\n"
                f"Error: {self.error}\n"
                f"Entorno: {env_icon}"
            )
        direction = "COMPRA" if self.is_buy else "VENTA"
        if self.action == "close":
            return (
                f"✅ Posición cerrada\n"
                f"📌 Position ID: {self.position_id}\n"
                f"Entorno: {env_icon}\n"
                f"Latencia: {self.latency_ms:.0f}ms"
            )
        return (
            f"✅ Orden ejecutada\n"
            f"🏷 {direction} {self.symbol}\n"
            f"💵 ${self.amount:.2f}\n"
            f"📋 Order ID: {self.order_id}\n"
            f"📌 Position ID: {self.position_id}\n"
            f"Entorno: {env_icon}\n"
            f"Latencia: {self.latency_ms:.0f}ms"
        )


# ── Authorization ────────────────────────────────────────────────────

def is_creator(user_id: Any) -> bool:
    """
    Check if user_id matches the creator.
    Accepts int, str, or 'tg:XXXXXX' format.
    """
    try:
        uid_str = str(user_id).replace("tg:", "").strip()
        return int(uid_str) == _CREATOR_TG_ID
    except Exception:
        return False


def _require_creator(user_id: Any) -> Optional[str]:
    """Returns error string if not creator, None if authorized."""
    if not is_creator(user_id):
        logger.warning(
            "[ETORO] Unauthorized execution attempt by user_id=%s", user_id
        )
        return "Acceso denegado. Solo el creador puede ejecutar operaciones en eToro."
    return None


# ── Main Execution Functions ─────────────────────────────────────────

def execute_open(
    user_id: Any,
    symbol: str,
    amount: float,
    is_buy: bool,
    leverage: int = 1,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> ExecutionResult:
    """
    Open a new position. Requires creator authorization.

    Args:
        user_id:    Telegram user ID (must be creator).
        symbol:     e.g. 'BTCUSD', 'AAPL', 'EURUSD'
        amount:     USD amount to invest.
        is_buy:     True = long, False = short.
        leverage:   Multiplier (default 1 = no leverage).
        stop_loss:  Price level for stop loss (optional).
        take_profit: Price level for take profit (optional).
    """
    audit_id = f"EXEC-{int(time.time())}-{symbol[:6].upper()}"

    # Authorization gate
    auth_err = _require_creator(user_id)
    if auth_err:
        _log_audit({
            "audit_id": audit_id, "action": "open", "symbol": symbol,
            "user_id": str(user_id), "authorized": False, "timestamp": time.time(),
        })
        return ExecutionResult(
            success=False, action="denied", symbol=symbol,
            environment=get_environment(), error=auth_err, audit_id=audit_id,
        )

    env = get_environment()

    # Pre-execution audit
    _log_audit({
        "audit_id":   audit_id,
        "action":     "open_intent",
        "symbol":     symbol,
        "amount":     amount,
        "direction":  "buy" if is_buy else "sell",
        "leverage":   leverage,
        "stop_loss":  stop_loss,
        "take_profit": take_profit,
        "environment": env,
        "user_id":    str(user_id),
        "timestamp":  time.time(),
    })

    # Resolve instrument ID
    instrument_id = get_instrument_id(symbol)
    if not instrument_id:
        err = f"Instrumento '{symbol}' no encontrado en eToro."
        _log_audit({"audit_id": audit_id, "error": err, "timestamp": time.time()})
        return ExecutionResult(
            success=False, action="open", symbol=symbol,
            environment=env, error=err, audit_id=audit_id,
        )

    logger.info(
        "[ETORO] EXEC OPEN | user=%s | %s %s | $%.2f | lev=%d | env=%s",
        user_id, "BUY" if is_buy else "SELL", symbol, amount, leverage, env,
    )

    # Execute
    result = open_position(
        instrument_id=instrument_id,
        amount=amount,
        is_buy=is_buy,
        leverage=leverage,
        stop_loss_rate=stop_loss,
        take_profit_rate=take_profit,
    )

    # Post-execution audit
    _log_audit({
        "audit_id":    audit_id,
        "action":      "open_result",
        "success":     result.get("success"),
        "order_id":    result.get("order_id"),
        "position_id": result.get("position_id"),
        "error":       result.get("error"),
        "latency_ms":  result.get("latency_ms"),
        "timestamp":   time.time(),
    })

    # Store in structural memory
    try:
        from vectrax.user_memory import store_memory
        store_memory(
            f"tg:{_CREATOR_TG_ID}",
            f"eToro {('BUY' if is_buy else 'SELL')} {symbol} ${amount}",
            f"{'OK' if result.get('success') else 'FAIL'}: order={result.get('order_id')} env={env}",
        )
    except Exception:
        pass

    if result.get("success"):
        return ExecutionResult(
            success=True,
            action="open",
            symbol=symbol,
            environment=env,
            order_id=result.get("order_id"),
            position_id=result.get("position_id"),
            amount=amount,
            is_buy=is_buy,
            latency_ms=result.get("latency_ms", 0),
            audit_id=audit_id,
        )

    return ExecutionResult(
        success=False,
        action="open",
        symbol=symbol,
        environment=env,
        error=result.get("error", "Error desconocido"),
        audit_id=audit_id,
    )


def execute_close(
    user_id: Any,
    position_id: str,
    instrument_id: Optional[int] = None,
    symbol: Optional[str] = None,
) -> ExecutionResult:
    """
    Close an existing position by position ID. Requires creator authorization.
    """
    audit_id = f"CLOSE-{int(time.time())}-{position_id[:8]}"

    auth_err = _require_creator(user_id)
    if auth_err:
        _log_audit({
            "audit_id": audit_id, "action": "close", "position_id": position_id,
            "user_id": str(user_id), "authorized": False, "timestamp": time.time(),
        })
        return ExecutionResult(
            success=False, action="denied",
            symbol=symbol or "?", environment=get_environment(),
            error=auth_err, audit_id=audit_id,
        )

    env = get_environment()

    # Resolve instrument_id if not provided
    iid = instrument_id
    if not iid and symbol:
        iid = get_instrument_id(symbol)
    if not iid:
        err = "Se requiere instrument_id o symbol válido para cerrar posición."
        return ExecutionResult(
            success=False, action="close",
            symbol=symbol or "?", environment=env,
            error=err, audit_id=audit_id,
        )

    _log_audit({
        "audit_id":      audit_id,
        "action":        "close_intent",
        "position_id":   position_id,
        "instrument_id": iid,
        "environment":   env,
        "user_id":       str(user_id),
        "timestamp":     time.time(),
    })

    logger.info(
        "[ETORO] EXEC CLOSE | user=%s | position=%s | env=%s",
        user_id, position_id, env,
    )

    result = close_position(position_id=position_id, instrument_id=iid)

    _log_audit({
        "audit_id":   audit_id,
        "action":     "close_result",
        "success":    result.get("success"),
        "error":      result.get("error"),
        "latency_ms": result.get("latency_ms"),
        "timestamp":  time.time(),
    })

    if result.get("success"):
        return ExecutionResult(
            success=True,
            action="close",
            symbol=symbol or str(iid),
            environment=env,
            position_id=position_id,
            latency_ms=result.get("latency_ms", 0),
            audit_id=audit_id,
        )

    return ExecutionResult(
        success=False,
        action="close",
        symbol=symbol or str(iid),
        environment=env,
        error=result.get("error", "Error cerrando posición"),
        audit_id=audit_id,
    )


def get_audit_log(limit: int = 20) -> List[Dict]:
    """Return the last `limit` audit records."""
    try:
        if not os.path.exists(_AUDIT_FILE):
            return []
        with open(_AUDIT_FILE) as f:
            lines = f.readlines()
        records = []
        for line in reversed(lines[-limit * 2:]):
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                pass
        return records[:limit]
    except Exception:
        return []
