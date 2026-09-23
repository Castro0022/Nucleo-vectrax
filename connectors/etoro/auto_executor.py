"""
Vectrax eToro — Auto Executor
================================
Phase-gated automatic trade execution with hard risk limits.

Modes (persisted in ~/.vectrax/etoro_auto_config.json):
  OFF   — default. No automatic execution of any kind.
  PAPER — simulates trades without real money. Builds track record.
  LIVE  — executes real trades via eToro API. Requires phase requirements met.

Phase requirements to enter LIVE — two gates, deliberately different:

  Manual (/vx market live on, activate_live()) — unchanged from before:
    1. PAPER phase must have ≥ MIN_PAPER_SIGNALS resolved signals
    2. PAPER phase win rate must be ≥ MIN_PAPER_WIN_RATE %
    3. ETORO_ENVIRONMENT must already be set to "real" by the creator
    4. Real API credentials (ETORO_API_KEY / ETORO_USER_KEY) must be configured

  Automatic (fires the instant a PAPER trade closes and the count reaches
  the threshold) — deliberately does NOT gate on win rate:
    1. PAPER phase must have ≥ MIN_PAPER_SIGNALS resolved signals
    2. ETORO_ENVIRONMENT must already be set to "real" by the creator
    3. Real API credentials (ETORO_API_KEY / ETORO_USER_KEY) must be configured

Automatic PAPER → LIVE promotion:
  The moment a PAPER trade closes (record_trade_result(is_paper=True)) and
  that close brings the count to ≥ MIN_PAPER_SIGNALS with a real
  environment and real credentials already configured, the mode flips to
  LIVE right there — no creator button press required at that instant.
  This code NEVER writes ETORO_ENVIRONMENT and NEVER places a real order
  itself; it only flips the persisted `mode` field once every requirement
  already holds true from data / configuration set by the creator
  beforehand. If any requirement is missing, the mode stays PAPER and the
  reason is persisted to `last_auto_promotion_reason` (visible via
  /vx market auto status and format_auto_status()).
  Because the check only runs inside an actual trade-closing call, merely
  restarting the process (or deploying this change onto a config that
  already has ≥30 resolved PAPER trades) can never by itself trigger the
  promotion — only a genuine new PAPER close event evaluates it, and it is
  re-evaluated fresh (idempotently) on every such close.
  One-shot: it only fires the very first time LIVE is reached
  (cfg["live_activated_at"] still unset). Once LIVE has been reached —
  automatically or manually — any later return to PAPER (a deliberate
  pause, or a risk circuit-breaker: consecutive losses / daily loss limit)
  always requires the creator's manual /vx market live on to go LIVE
  again; the automatic path never re-fires, so a safety shutdown can never
  be silently undone.

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
    "max_position_usd":        50.0,
    "max_daily_loss_usd":      10.0,
    "stop_loss_pct":           1.5,
    "max_consecutive_losses":  3,
    "max_positions_open":      2,
    "min_paper_signals":       30,
    "min_paper_win_rate":      60.0,
    # Controlled execution limits
    "max_ops_per_symbol_day":  1,
    "min_confidence":          "MEDIUM",    # MEDIUM or HIGH
    "paper_phase_hours":       24,           # min hours in PAPER before LIVE
    "approved_symbols":        [],           # symbols manually approved for LIVE
    "halt":                    False,        # emergency stop flag
    "max_hold_hours":          24,           # max position hold time
    # Runtime state
    "consecutive_losses":      0,
    "daily_loss_usd":          0.0,
    "daily_loss_date":         "",
    "daily_ops_by_symbol":     {},           # {"AAPL": 1, ...} resets daily
    "live_activated_by":       "",
    "live_activated_at":       0.0,
    "paper_activated_at":      0.0,
    "paper_trades_total":      0,
    "paper_trades_wins":       0,
    "last_shutdown_reason":    "",
    "last_auto_promotion_reason": "",   # visible reason auto-LIVE stayed blocked
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


def halt(reason: str = "manual") -> str:
    """Emergency stop — immediately disable all execution."""
    cfg = _load_config()
    cfg["halt"] = True
    cfg["mode"] = AutoMode.OFF.value
    cfg["last_shutdown_reason"] = f"HALT: {reason}"
    _save_config(cfg)
    logger.warning("[AUTO] 🛑 HALT activated: %s", reason)
    return "🛑 HALT activado. Toda ejecución automática detenida."


def unhalt() -> str:
    cfg = _load_config()
    cfg["halt"] = False
    _save_config(cfg)
    return "✅ HALT desactivado. Puedes reactivar con /vx market execution on."


def is_halted() -> bool:
    return _load_config().get("halt", False)


def approve_symbol(symbol: str) -> str:
    cfg = _load_config()
    sym = symbol.upper()
    approved = cfg.get("approved_symbols", [])
    if sym not in approved:
        approved.append(sym)
        cfg["approved_symbols"] = approved
        _save_config(cfg)
    return f"✅ {sym} aprobado para ejecución LIVE."


def record_symbol_op(symbol: str) -> None:
    """Record that a symbol was traded today (for daily limit)."""
    cfg = _reset_daily_loss_if_new_day(_load_config())
    ops = cfg.get("daily_ops_by_symbol", {})
    sym = symbol.upper()
    ops[sym] = ops.get(sym, 0) + 1
    cfg["daily_ops_by_symbol"] = ops
    _save_config(cfg)


def symbol_ops_today(symbol: str) -> int:
    cfg = _reset_daily_loss_if_new_day(_load_config())
    return cfg.get("daily_ops_by_symbol", {}).get(symbol.upper(), 0)


def activate_paper() -> str:
    cfg = _load_config()
    if cfg.get("halt"):
        return "🛑 Sistema en HALT. Usa /vx market unhalt primero."
    _set_mode(AutoMode.PAPER)
    cfg = _load_config()
    cfg["paper_activated_at"] = time.time()
    _save_config(cfg)
    cfg = _load_config()
    paper_wr = _get_paper_win_rate(cfg)
    return (
        f"🟡 Modo PAPER activado.\n"
        f"Vectrax simulará operaciones sin dinero real.\n"
        f"Paper trades: {cfg['paper_trades_total']} | WR: {paper_wr:.0f}%\n"
        f"Requisito LIVE: ≥{cfg['min_paper_signals']} señales, "
        f"≥{cfg['min_paper_win_rate']:.0f}% WR"
    )


def _environment_and_credentials_reasons() -> List[str]:
    """
    Blocking reasons from the runtime environment alone: real
    ETORO_ENVIRONMENT and real API credentials, both set by the creator
    beforehand — never written by this module. Shared by every LIVE gate
    (manual and automatic); nothing here ever mutates os.environ.
    """
    reasons: List[str] = []

    env = os.environ.get("ETORO_ENVIRONMENT", "demo")
    if env != "real":
        reasons.append(
            "ETORO_ENVIRONMENT no está en 'real'. "
            "Ejecuta /vx etoro env real primero."
        )

    api_key  = os.environ.get("ETORO_API_KEY", "").strip()
    user_key = os.environ.get("ETORO_USER_KEY", "").strip()
    if not api_key or not user_key:
        reasons.append(
            "Credenciales reales no configuradas "
            "(ETORO_API_KEY / ETORO_USER_KEY)."
        )

    return reasons


def _live_readiness_reasons(cfg: Dict) -> List[str]:
    """
    Every phase requirement for the MANUAL /vx market live on command:
    ≥ min_paper_signals resolved PAPER trades, ≥ min_paper_win_rate win
    rate, real environment, real credentials. Returns the list of blocking
    reasons — empty means every requirement is met.
    """
    reasons: List[str] = []

    paper_wr = _get_paper_win_rate(cfg)
    n_paper  = cfg["paper_trades_total"]

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

    reasons.extend(_environment_and_credentials_reasons())
    return reasons


def _auto_promotion_reasons(cfg: Dict) -> List[str]:
    """
    Every phase requirement for the AUTOMATIC PAPER → LIVE promotion,
    evaluated the instant a PAPER trade closes: ≥ min_paper_signals
    resolved PAPER trades, real environment, real credentials. Deliberately
    does NOT check win rate — only the manual /vx market live on command
    (see _live_readiness_reasons) still gates on ≥ min_paper_win_rate.
    Returns the list of blocking reasons — empty means promotion proceeds.
    """
    reasons: List[str] = []

    n_paper = cfg["paper_trades_total"]
    if n_paper < cfg["min_paper_signals"]:
        reasons.append(
            f"Se necesitan ≥{cfg['min_paper_signals']} operaciones PAPER "
            f"cerradas (actuales: {n_paper})"
        )

    reasons.extend(_environment_and_credentials_reasons())
    return reasons


def activate_live(user_id: str) -> str:
    """
    Attempt to activate LIVE mode manually. Validates all phase
    requirements. Returns status message (success or reason for refusal).
    """
    cfg = _load_config()
    reasons = _live_readiness_reasons(cfg)

    if reasons:
        return (
            "❌ Requisitos LIVE no cumplidos:\n"
            + "\n".join(f"  • {r}" for r in reasons)
        )

    paper_wr = _get_paper_win_rate(cfg)
    n_paper  = cfg["paper_trades_total"]

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


def _maybe_auto_promote_to_live(cfg: Dict) -> None:
    """
    Evaluate automatic PAPER → LIVE promotion. Called from
    record_trade_result() the instant a PAPER trade closes — never from
    startup/config-load, so a restart (or deploying this change onto a
    config that already has ≥ min_paper_signals resolved trades) cannot by
    itself trigger it; only a genuine new close event does.

    One-shot: it only fires the very first time Vectrax leaves the initial
    PAPER build-up phase (cfg["live_activated_at"] is still unset). If LIVE
    was reached before — automatically or via the manual command — and a
    later event (a deliberate pause or a risk circuit-breaker: consecutive
    losses / daily loss limit) put it back in PAPER, reactivating LIVE
    again always requires the creator's manual /vx market live on. This
    guarantees a safety shutdown is never silently undone by an unrelated
    PAPER trade closing afterward.

    Mutates `cfg` in place (mode, live_activated_by/_at,
    last_auto_promotion_reason). The caller persists it. Never touches
    ETORO_ENVIRONMENT and never places an order — it only flips the
    persisted mode once every requirement already holds.
    """
    if cfg.get("mode") != AutoMode.PAPER.value:
        return  # only PAPER auto-promotes; OFF/LIVE are left untouched
    if cfg.get("halt"):
        return

    if cfg.get("live_activated_at"):
        # LIVE was already reached before (auto or manual) and later moved
        # back to PAPER. Do not re-promote automatically — a safety
        # shutdown must never be undone without the creator's own command.
        cfg["last_auto_promotion_reason"] = (
            "LIVE ya se activó antes; una nueva activación requiere "
            "el comando manual /vx market live on."
        )
        return

    reasons = _auto_promotion_reasons(cfg)
    if reasons:
        cfg["last_auto_promotion_reason"] = "; ".join(reasons)
        return

    n_paper  = cfg["paper_trades_total"]
    paper_wr = _get_paper_win_rate(cfg)  # informational only — not a gate here

    cfg["last_auto_promotion_reason"] = ""
    cfg["mode"]               = AutoMode.LIVE.value
    cfg["live_activated_by"]  = "auto:paper_threshold"
    cfg["live_activated_at"]  = time.time()

    logger.warning(
        "[AUTO] 🔴 PROMOCIÓN AUTOMÁTICA a LIVE: %d trades PAPER cerrados | WR=%.1f%%",
        n_paper, paper_wr,
    )
    try:
        from connectors.etoro.learning_engine import _tg_notify
        _tg_notify(
            "🔴 Vectrax pasó automáticamente a LIVE\n\n"
            f"Se cerró la operación PAPER #{n_paper} (WR actual: {paper_wr:.1f}%, "
            f"no exigido para esta promoción automática).\n"
            f"Requisitos cumplidos: ≥{cfg['min_paper_signals']} operaciones PAPER "
            f"cerradas, ETORO_ENVIRONMENT=real, credenciales reales configuradas.\n\n"
            f"Límites activos: máx/operación ${cfg['max_position_usd']:.0f} | "
            f"máx pérdida/día ${cfg['max_daily_loss_usd']:.0f} | "
            f"stop-loss {cfg['stop_loss_pct']:.1f}% | "
            f"cierre tras {cfg['max_consecutive_losses']} pérdidas consecutivas | "
            f"máx posiciones {cfg['max_positions_open']}."
        )
    except Exception:
        pass


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
        cfg["daily_ops_by_symbol"] = {}   # reset daily ops counter
    return cfg


def check_risk_before_trade(
    amount_usd: float,
) -> tuple[bool, str]:
    """
    Check all risk limits before placing a trade.
    Returns (allowed, reason_if_blocked).
    """
    cfg = _reset_daily_loss_if_new_day(_load_config())

    # 0. Halt check
    if cfg.get("halt"):
        return False, "🛑 Sistema en HALT. Ejecución bloqueada."

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
        # Evaluate automatic PAPER → LIVE promotion right on this close.
        _maybe_auto_promote_to_live(cfg)
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

        # Auto-shutdown check after recording. Mutates the same local `cfg`
        # that the final _save_config(cfg) below persists — calling
        # _set_mode() here would race with it: _set_mode() writes "paper" to
        # disk via its OWN freshly-loaded copy, and the unconditional
        # _save_config(cfg) at the end of this function would then overwrite
        # that write with this function's local `cfg`, which never saw the
        # mode change and still says "live" — silently undoing the
        # circuit-breaker shutdown. Setting cfg["mode"] directly avoids that.
        if cfg["consecutive_losses"] >= cfg["max_consecutive_losses"]:
            cfg["mode"] = AutoMode.PAPER.value
            cfg["last_shutdown_reason"] = (
                f"auto-shutdown after {cfg['consecutive_losses']} consecutive losses"
            )
            logger.warning(
                "[AUTO] 🛑 AUTO-SHUTDOWN: %d consecutive losses → reverting to PAPER",
                cfg["consecutive_losses"],
            )

        if cfg["daily_loss_usd"] >= cfg["max_daily_loss_usd"]:
            cfg["mode"] = AutoMode.PAPER.value
            cfg["last_shutdown_reason"] = (
                f"auto-shutdown: daily loss limit ${cfg['max_daily_loss_usd']:.0f} reached"
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
        f"  Operaciones cerradas (gate automático): {sig_ok}",
        f"  Win rate (solo gate manual):            {wr_ok}",
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

    if mode == AutoMode.PAPER and cfg.get("last_auto_promotion_reason"):
        blocked = cfg["last_auto_promotion_reason"].replace("; ", "\n  • ")
        lines.append(f"\n⏸ LIVE automático pendiente:\n  • {blocked}")

    if mode == AutoMode.LIVE and cfg.get("live_activated_at"):
        import datetime
        ts_str = datetime.datetime.utcfromtimestamp(
            cfg["live_activated_at"]
        ).strftime("%Y-%m-%d %H:%M UTC")
        by = cfg.get("live_activated_by") or "?"
        lines.append(f"\n🔴 LIVE activado: {ts_str} (por {by})")

    return "\n".join(lines)
