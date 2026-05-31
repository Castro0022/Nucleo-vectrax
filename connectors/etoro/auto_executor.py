"""
Vectrax eToro — Auto Executor
================================
Phase-gated automatic trade execution with hard risk limits.

Modes (persisted in ~/.vectrax/etoro_auto_config.json):
  OFF   — default. No automatic execution of any kind.
  PAPER — simulates trades without real money. Builds track record.
  LIVE  — executes real trades via eToro API. Requires phase requirements met.

Phase requirements to enter LIVE:
  1. Creator must explicitly activate LIVE
  2. PAPER phase must have ≥ MIN_PAPER_SIGNALS resolved signals
  3. PAPER phase win rate must be ≥ MIN_PAPER_WIN_RATE %
  4. ETORO_ENVIRONMENT must be set to "real" by creator

Hard risk limits (enforced on every LIVE trade):
  MAX_POSITION_USD      = 100    per trade max exposure
  MAX_DAILY_LOSS_USD    = 50     cumulative daily loss before shutdown
  STOP_LOSS_PCT         = 1.5    mandatory stop-loss distance (%)
  MAX_CONSECUTIVE_LOSSES = 3     consecutive losses → auto-shutdown to PAPER
  MAX_POSITIONS_OPEN    = 2      max simultaneous open positions

All limits are configurable by the creator via Telegram.
Any safety breach reverts the mode to PAPER and logs the reason.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.etoro.auto_executor")

# ── Config persistence ────────────────────────────────────────────────
_CONFIG_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_auto_config.json"
)
_PAPER_LOG_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_paper_trades.jsonl"
)

# ── Default risk limits ───────────────────────────────────────────────
DEFAULTS = {
    "mode":                    "off",
    "max_position_usd":        100.0,
    "max_daily_loss_usd":      50.0,
    "stop_loss_pct":           1.5,
    "max_consecutive_losses":  3,
    "max_positions_open":      2,
    "min_paper_signals":       30,
    "min_paper_win_rate":      60.0,
    # Runtime state
    "consecutive_losses":      0,
    "daily_loss_usd":          0.0,
    "daily_loss_date":         "",
    "live_activated_by":       "",
    "live_activated_at":       0.0,
    "paper_trades_total":      0,
    "paper_trades_wins":       0,
    "last_shutdown_reason":    "",
}


# ── Enums ─────────────────────────────────────────────────────────────

class AutoMode(str, Enum):
    OFF   = "off"
    PAPER = "paper"
    LIVE  = "live"


# ── Config I/O ────────────────────────────────────────────────────────

def _load_config() -> Dict:
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE) as f:
                stored = json.load(f)
            cfg = dict(DEFAULTS)
            cfg.update(stored)
            return cfg
    except Exception:
        pass
    return dict(DEFAULTS)


def _save_config(cfg: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
        with open(_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("save_config error: %s", e)


def get_config() -> Dict:
    return _load_config()


def update_config(updates: Dict) -> Dict:
    cfg = _load_config()
    cfg.update(updates)
    _save_config(cfg)
    return cfg


# ── Phase management ──────────────────────────────────────────────────

def get_mode() -> AutoMode:
    return AutoMode(_load_config().get("mode", "off"))


def _set_mode(mode: AutoMode, reason: str = "", activated_by: str = "") -> None:
    cfg = _load_config()
    cfg["mode"] = mode.value
    if reason:
        cfg["last_shutdown_reason"] = reason
    if activated_by:
        cfg["live_activated_by"] = activated_by
        cfg["live_activated_at"] = time.time()
    _save_config(cfg)
    logger.info("[AUTO] Mode changed to %s | reason=%s", mode.value.upper(), reason or "manual")


def activate_paper() -> str:
    _set_mode(AutoMode.PAPER)
    cfg = _load_config()
    paper_wr = _get_paper_win_rate(cfg)
    return (
        f"🟡 Modo PAPER activado.\n"
        f"Vectrax simulará operaciones sin dinero real.\n"
        f"Paper trades: {cfg['paper_trades_total']} | WR: {paper_wr:.0f}%\n"
        f"Requisito LIVE: ≥{cfg['min_paper_signals']} señales, "
        f"≥{cfg['min_paper_win_rate']:.0f}% WR"
    )


def activate_live(user_id: str) -> str:
    """
    Attempt to activate LIVE mode. Validates all phase requirements.
    Returns status message (success or reason for refusal).
    """
    cfg = _load_config()

    # Check paper phase requirements
    paper_wr = _get_paper_win_rate(cfg)
    n_paper  = cfg["paper_trades_total"]

    reasons = []
    if n_paper < cfg["min_paper_signals"]:
        reasons.append(
            f"Se necesitan ≥{cfg['min_paper_signals']} señales PAPER "
            f"(actuales: {n_paper})"
        )
    if paper_wr < cfg["min_paper_win_rate"]:
        reasons.append(
            f"Win rate PAPER insuficiente: {paper_wr:.1f}% "
            f"(mínimo: {cfg['min_paper_win_rate']:.0f}%)"
        )

    # Check environment
    env = os.environ.get("ETORO_ENVIRONMENT", "demo")
    if env != "real":
        reasons.append(
            "ETORO_ENVIRONMENT no está en 'real'. "
            "Ejecuta /vx etoro env real primero."
        )

    if reasons:
        return (
            "❌ Requisitos LIVE no cumplidos:\n"
            + "\n".join(f"  • {r}" for r in reasons)
        )

    _set_mode(AutoMode.LIVE, activated_by=user_id)
    return (
        f"🔴 Modo LIVE activado por {user_id}\n"
        f"Paper record: {n_paper} trades | WR: {paper_wr:.0f}%\n"
        f"Límites activos:\n"
        f"  • Max por operación: ${cfg['max_position_usd']:.0f}\n"
        f"  • Max pérdida diaria: ${cfg['max_daily_loss_usd']:.0f}\n"
        f"  • Stop-loss: {cfg['stop_loss_pct']:.1f}%\n"
        f"  • Cierre por pérdidas consecutivas: {cfg['max_consecutive_losses']}\n"
        f"  • Max posiciones simultáneas: {cfg['max_positions_open']}"
    )


def deactivate(reason: str = "manual") -> None:
    _set_mode(AutoMode.OFF, reason=reason)


# ── Risk checks ───────────────────────────────────────────────────────

def _get_paper_win_rate(cfg: Dict) -> float:
    total = cfg.get("paper_trades_total", 0)
    wins  = cfg.get("paper_trades_wins",  0)
    return round(wins / max(1, total) * 100, 1)


def _reset_daily_loss_if_new_day(cfg: Dict) -> Dict:
    today = time.strftime("%Y-%m-%d")
    if cfg.get("daily_loss_date") != today:
        cfg["daily_loss_usd"]    = 0.0
        cfg["daily_loss_date"]   = today
    return cfg


def check_risk_before_trade(
    amount_usd: float,
) -> tuple[bool, str]:
    """
    Check all risk limits before placing a trade.
    Returns (allowed, reason_if_blocked).
    """
    cfg = _reset_daily_loss_if_new_day(_load_config())

    # 1. Mode must be PAPER or LIVE
    mode = AutoMode(cfg["mode"])
    if mode == AutoMode.OFF:
        return False, "Auto-executor está en modo OFF."

    # 2. Position size
    if amount_usd > cfg["max_position_usd"]:
        return False, (
            f"Monto ${amount_usd:.0f} excede límite máximo "
            f"${cfg['max_position_usd']:.0f}."
        )

    # 3. Daily loss limit
    if cfg["daily_loss_usd"] >= cfg["max_daily_loss_usd"]:
        return False, (
            f"Límite de pérdida diaria alcanzado "
            f"(${cfg['daily_loss_usd']:.2f} / ${cfg['max_daily_loss_usd']:.2f})."
        )

    # 4. Consecutive losses
    if cfg["consecutive_losses"] >= cfg["max_consecutive_losses"]:
        _set_mode(AutoMode.PAPER, reason="auto-shutdown: consecutive losses limit")
        return False, (
            f"🛑 AUTO-SHUTDOWN: {cfg['consecutive_losses']} pérdidas consecutivas. "
            f"Revertido a PAPER."
        )

    return True, ""


def record_trade_result(
    pnl_usd: float,
    is_paper: bool = True,
) -> None:
    """
    Record the result of an executed trade and update risk counters.
    """
    cfg = _reset_daily_loss_if_new_day(_load_config())
    won = pnl_usd > 0

    if is_paper:
        cfg["paper_trades_total"] += 1
        if won:
            cfg["paper_trades_wins"] += 1
        logger.info("[AUTO] PAPER trade result: PnL=%.2f USD | won=%s", pnl_usd, won)
    else:
        if pnl_usd < 0:
            cfg["daily_loss_usd"] += abs(pnl_usd)
            cfg["consecutive_losses"] += 1
        else:
            cfg["consecutive_losses"] = 0  # reset on any win

        logger.info(
            "[AUTO] LIVE trade result: PnL=%.2f USD | consecutive_losses=%d | "
            "daily_loss=%.2f",
            pnl_usd, cfg["consecutive_losses"], cfg["daily_loss_usd"],
        )

        # Auto-shutdown check after recording
        if cfg["consecutive_losses"] >= cfg["max_consecutive_losses"]:
            _set_mode(
                AutoMode.PAPER,
                reason=f"auto-shutdown after {cfg['consecutive_losses']} consecutive losses",
            )
            logger.warning(
                "[AUTO] 🛑 AUTO-SHUTDOWN: %d consecutive losses → reverting to PAPER",
                cfg["consecutive_losses"],
            )

        if cfg["daily_loss_usd"] >= cfg["max_daily_loss_usd"]:
            _set_mode(
                AutoMode.PAPER,
                reason=f"auto-shutdown: daily loss limit ${cfg['max_daily_loss_usd']:.0f} reached",
            )
            logger.warning("[AUTO] 🛑 DAILY LOSS LIMIT reached → reverting to PAPER")

    _save_config(cfg)


# ── Paper trade log ───────────────────────────────────────────────────

@dataclass
class PaperTrade:
    trade_id:    str
    timestamp:   float
    symbol:      str
    direction:   str
    amount_usd:  float
    entry_price: float
    stop_loss:   float
    take_profit: float
    proposal_id: str
    status:      str = "open"   # open / closed_win / closed_loss / closed_neutral
    exit_price:  Optional[float] = None
    pnl_usd:     Optional[float] = None
    pnl_pct:     Optional[float] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def record_paper_trade(
    symbol: str,
    direction: str,
    amount_usd: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    proposal_id: str,
) -> PaperTrade:
    """Record a simulated (paper) trade."""
    tid = f"PAPER-{int(time.time())}-{symbol[:4].upper()}"
    trade = PaperTrade(
        trade_id=tid,
        timestamp=time.time(),
        symbol=symbol,
        direction=direction,
        amount_usd=amount_usd,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        proposal_id=proposal_id,
    )
    try:
        os.makedirs(os.path.dirname(_PAPER_LOG_FILE), exist_ok=True)
        with open(_PAPER_LOG_FILE, "a") as f:
            f.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("record_paper_trade error: %s", e)
    logger.info(
        "[PAPER] %s | %s %s $%.0f @ %.5g | SL=%.5g TP=%.5g",
        tid, direction.upper(), symbol, amount_usd, entry_price, stop_loss, take_profit,
    )
    return trade


def get_paper_trades(limit: int = 20) -> List[PaperTrade]:
    if not os.path.exists(_PAPER_LOG_FILE):
        return []
    trades = []
    try:
        with open(_PAPER_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    trades.append(PaperTrade(**{
                        k: v for k, v in d.items()
                        if k in PaperTrade.__dataclass_fields__
                    }))
                except Exception:
                    pass
    except Exception:
        pass
    return trades[-limit:]


# ── Execute proposal ──────────────────────────────────────────────────

def execute_proposal(
    proposal_id: str,
    amount_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Execute a pending proposal automatically (PAPER or LIVE mode).
    Enforces all risk checks. Returns execution result dict.
    """
    from connectors.etoro.learning_engine import load_proposals, update_proposal_status

    # Load proposal
    proposals = load_proposals(limit=200, status_filter="pending")
    proposal = next((p for p in proposals if p.proposal_id == proposal_id), None)
    if not proposal:
        return {"success": False, "error": f"Propuesta {proposal_id} no encontrada o ya ejecutada."}

    cfg = _load_config()
    mode = AutoMode(cfg["mode"])

    if mode == AutoMode.OFF:
        return {"success": False, "error": "Auto-executor en modo OFF."}

    # Determine trade size
    trade_amount = amount_usd or min(cfg["max_position_usd"], 100.0)

    # Risk check
    allowed, block_reason = check_risk_before_trade(trade_amount)
    if not allowed:
        return {"success": False, "error": block_reason}

    # Compute stop-loss (mandatory)
    sl_pct  = cfg["stop_loss_pct"] / 100
    if proposal.direction == "buy":
        stop_loss = round(proposal.entry_price * (1 - sl_pct), 5)
    else:
        stop_loss = round(proposal.entry_price * (1 + sl_pct), 5)

    take_profit = proposal.take_profit

    if mode == AutoMode.PAPER:
        # Simulate the trade
        record_paper_trade(
            symbol=proposal.symbol,
            direction=proposal.direction,
            amount_usd=trade_amount,
            entry_price=proposal.entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            proposal_id=proposal_id,
        )
        update_proposal_status(proposal_id, "executed")
        return {
            "success":     True,
            "mode":        "paper",
            "symbol":      proposal.symbol,
            "direction":   proposal.direction,
            "amount_usd":  trade_amount,
            "entry_price": proposal.entry_price,
            "stop_loss":   stop_loss,
            "take_profit": take_profit,
        }

    # LIVE mode — real execution
    from connectors.etoro.trade_executor import execute_open
    import os as _os
    creator_id = _os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "2030762343")

    result = execute_open(
        user_id=f"tg:{creator_id}",
        symbol=proposal.symbol,
        amount=trade_amount,
        is_buy=(proposal.direction == "buy"),
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    if result.success:
        update_proposal_status(proposal_id, "executed")
        return {
            "success":     True,
            "mode":        "live",
            "symbol":      proposal.symbol,
            "direction":   proposal.direction,
            "amount_usd":  trade_amount,
            "order_id":    result.order_id,
            "position_id": result.position_id,
            "stop_loss":   stop_loss,
            "take_profit": take_profit,
        }
    else:
        return {"success": False, "mode": "live", "error": result.error}


# ── Status panel ──────────────────────────────────────────────────────

def format_auto_status() -> str:
    cfg   = _load_config()
    mode  = AutoMode(cfg["mode"])
    paper_wr = _get_paper_win_rate(cfg)

    mode_icons = {AutoMode.OFF: "⚫", AutoMode.PAPER: "🟡", AutoMode.LIVE: "🔴"}
    icon = mode_icons.get(mode, "?")

    req_signals = cfg["min_paper_signals"]
    req_wr      = cfg["min_paper_win_rate"]
    n_paper     = cfg["paper_trades_total"]
    sig_ok  = "✅" if n_paper >= req_signals else f"❌ ({n_paper}/{req_signals})"
    wr_ok   = "✅" if paper_wr >= req_wr    else f"❌ ({paper_wr:.0f}%/{req_wr:.0f}%)"

    lines = [
        f"{icon} Auto-Executor: {mode.value.upper()}\n",
        "📊 Fase PAPER:",
        f"  Señales resueltas: {sig_ok}",
        f"  Win rate:          {wr_ok}",
        "",
        "🛡 Límites de riesgo:",
        f"  Max/operación:    ${cfg['max_position_usd']:.0f}",
        f"  Max pérdida/día:  ${cfg['max_daily_loss_usd']:.0f}",
        f"  Stop-loss:        {cfg['stop_loss_pct']:.1f}%",
        f"  Pérdidas consec:  {cfg['consecutive_losses']}/{cfg['max_consecutive_losses']}",
        f"  Max posiciones:   {cfg['max_positions_open']}",
    ]

    if cfg.get("last_shutdown_reason"):
        lines.append(f"\n🔔 Último shutdown: {cfg['last_shutdown_reason']}")

    if mode == AutoMode.LIVE and cfg.get("live_activated_at"):
        import datetime
        ts_str = datetime.datetime.utcfromtimestamp(
            cfg["live_activated_at"]
        ).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"\n🔴 LIVE activado: {ts_str}")

    return "\n".join(lines)
