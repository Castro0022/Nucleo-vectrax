"""
Vectrax eToro — Auto Executor
================================
Phase-gated automatic trade execution with hard risk limits.

Modes (persisted in ~/.vectrax/etoro_auto_config.json):
  OFF   — default. No automatic execution of any kind.
  PAPER — simulates trades without real money. Builds track record.
  LIVE  — executes real trades via eToro API. Requires phase requirements met.

Phase requirements to enter LIVE — ONE gate, shared by the manual
/vx market live on command and the automatic promotion below (explicit
creator instruction, 2026-09-23: neither path gates on win rate):
  1. PAPER phase must have ≥ MIN_PAPER_SIGNALS resolved (closed) trades,
     each counted at most once — see `record_trade_result`'s `trade_id`
     idempotency key.
  2. ETORO_ENVIRONMENT must already be set to "real" by the creator.
  3. Real API credentials (ETORO_API_KEY / ETORO_USER_KEY) must be
     configured.
`min_paper_win_rate` still exists in config and is still tracked
(paper_trades_wins) and reported informationally (status panel, Telegram,
dashboards, convergence_learner's adaptive gap analysis) — it is simply not
a condition for either LIVE gate.

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
  automatically or manually — any later DELIBERATE return to PAPER (the
  creator's own choice, e.g. /vx market ... off/paper) always requires the
  creator's manual /vx market live on to go LIVE again; the automatic path
  never re-fires. The risk circuit-breakers (consecutive losses / daily
  loss limit) do NOT return to PAPER at all anymore — see "Circuit
  breakers" below — so this one-shot gate simply never engages for them.

Risk limits — default values (DEFAULTS, above the Enums section) are
configurable by the creator via `/vx etoro auto config <key> <value>`
(telegram_gateway.py), but every value it sends is validated by
`validate_risk_limit()` against the HARD CEILINGS below FIRST — those
ceilings are module-level constants, not config, and Telegram can never
raise them (corrección 2026-09-26; before this, the command accepted any
number that parsed, with no range at all):
  max_position_usd       default $50   — hard ceiling $MAX_POSITION_USD_CEILING (200)
  max_daily_loss_usd     default $10   — hard ceiling $MAX_DAILY_LOSS_USD_CEILING (50)
  stop_loss_pct          default 1.5   — range STOP_LOSS_PCT_RANGE (0.1–10)
  max_consecutive_losses default 3     — range MAX_CONSECUTIVE_LOSSES_RANGE (1–10)
  max_positions_open     default 2     — range MAX_POSITIONS_OPEN_RANGE (1–5)
  min_paper_signals      default 30    — floor MIN_PAPER_SIGNALS_MIN (10)

What is actually enforced, and since when (corrección 2026-09-26 —
before it, several of these existed only as config fields that nothing
ever read):
  - max_position_usd, max_daily_loss_usd, max_consecutive_losses:
    checked in `check_risk_before_trade()`. Daily loss and consecutive
    losses only ever ADVANCE via `record_trade_result()` — which, for
    LIVE, requires the close to actually have been recorded (see below).
  - max_positions_open: checked in `check_risk_before_trade()`. In LIVE
    it counts the BROKER's real open positions (`get_portfolio()`), not
    this module's own log — a failed broker query blocks the trade
    (fail closed), it never lets one through uncounted.
  - LIVE trade lifecycle now has its own log, mirroring the PAPER one:
    `record_live_trade_open()` writes ~/.vectrax/etoro_live_trades.jsonl
    the instant `execute_proposal()` opens a LIVE position;
    `position_manager.check_live_positions_closed()` (same periodic
    cycle as PAPER's `check_open_positions()`) detects when a registered
    LIVE position is no longer in the broker's open list and marks it
    `closed_pnl_pending` — it never invents a PnL, so
    `record_trade_result(is_paper=False, ...)` (and therefore
    consecutive_losses / daily_loss_usd / the PAPER-vs-LIVE risk
    counters) only advances for a LIVE close whose realized PnL is
    actually known. `record_trade_result()` requires `trade_id` and
    deduplicates by it for BOTH is_paper=True and is_paper=False — a
    retried/overlapping close is a no-op in either mode.

Circuit breakers — 24h pause, NOT a return to PAPER (corrección
2026-09-26, second change in this PR: the creator explicitly asked for
this instead of the original "revert to PAPER" behavior of the first
change above):
  - Consecutive losses (≥ max_consecutive_losses) and the daily loss
    limit (daily_loss_usd ≥ max_daily_loss_usd) each activate a 24h pause
    via `_activate_pause()` — `mode` is NEVER touched by either breaker;
    LIVE stays LIVE the entire time.
  - While `cfg["paused_until"] > now`, `check_risk_before_trade()` blocks
    every new entry (existing open positions are never touched — their
    own stop-loss/take-profit still live at the broker). If both breakers
    fire on the same close, the second one is a no-op — it never extends
    the pause or overwrites the first one's `pause_reason`.
  - `_maybe_lift_expired_pause()` (called lazily from
    `check_risk_before_trade()`, no separate scheduled job) lifts an
    expired pause with no creator action: it clears `paused_until` /
    `pause_reason` and resets `consecutive_losses` / `daily_loss_usd` to
    0. Telegram is notified both when a pause is activated (reason +
    resume time) and when it lifts.
  - `paused_until` / `pause_reason` live in the same persisted config
    JSON as everything else — a process restart cannot shorten, lose, or
    silently extend an active pause.
  - Completely independent of the manual HALT (`cfg["halt"]`): HALT is
    only ever set/cleared by the creator's own /vx market halt / unhalt
    commands; an expiring pause never touches it, and HALT blocks trades
    regardless of any pause state.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("vectrax.etoro.auto_executor")

# ── Config persistence ────────────────────────────────────────────────
_CONFIG_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_auto_config.json"
)
_PAPER_LOG_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_paper_trades.jsonl"
)
_LIVE_LOG_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_live_trades.jsonl"
)

# How many recent PAPER/LIVE trade_ids record_trade_result() remembers for
# duplicate-close detection. Bounded so the config file cannot grow forever;
# far larger than any realistic double-delivery window (a retried close
# lands within seconds/minutes, not hundreds of trades later).
_MAX_RECORDED_TRADE_IDS = 500

# ── Hard ceilings — NOT configurable via Telegram ───────────────────────
# `/vx etoro auto config` (telegram_gateway.py) validates every value
# against these before writing it. Changing them requires editing this
# file and redeploying — never something a Telegram message can do, by
# design: a fat-fingered or hijacked chat message must not be able to
# raise the money the system is allowed to risk.
MAX_POSITION_USD_CEILING     = 200.0   # techo confirmado por el creador 2026-09-26
MAX_DAILY_LOSS_USD_CEILING   = 50.0    # techo confirmado por el creador 2026-09-26
STOP_LOSS_PCT_RANGE          = (0.1, 10.0)
MAX_CONSECUTIVE_LOSSES_RANGE = (1, 10)
MAX_POSITIONS_OPEN_RANGE     = (1, 5)
MIN_PAPER_SIGNALS_MIN        = 10

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
    "recorded_paper_trade_ids": [],     # dedup horizon — see record_trade_result
    "recorded_live_trade_ids": [],      # mismo dedup horizon, para cierres LIVE
    "paused_until":            0.0,     # corte de circuito 2026-09-26 — ver _activate_pause
    "pause_reason":            "",
}


# ── Enums ─────────────────────────────────────────────────────────────

class AutoMode(str, Enum):
    OFF   = "off"
    PAPER = "paper"
    LIVE  = "live"


# ── Config I/O ────────────────────────────────────────────────────────

def _load_config() -> Dict:
    """
    Load the persisted config, backfilled with DEFAULTS for any key a
    stored file predates. Uses copy.deepcopy(DEFAULTS), not dict(DEFAULTS):
    a shallow copy shares mutable values (approved_symbols,
    daily_ops_by_symbol, recorded_paper_trade_ids — all lists/dicts) with
    the DEFAULTS module global itself. A caller that then mutates one
    in place (record_trade_result's dedup list, record_symbol_op's daily
    counter) was silently corrupting DEFAULTS for the rest of the process:
    proven to leak between wholly unrelated, individually-isolated tests
    the moment both happened to hit this no-stored-value fallback in the
    same pytest run — exactly a "passes alone, fails with the full suite"
    symptom. deepcopy makes every returned cfg's containers independent.
    """
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE) as f:
                stored = json.load(f)
            cfg = copy.deepcopy(DEFAULTS)
            cfg.update(stored)
            return cfg
    except Exception:
        pass
    return copy.deepcopy(DEFAULTS)


def _save_config(cfg: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
        with open(_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("save_config error: %s", e)


@contextlib.contextmanager
def _locked_config() -> Iterator[None]:
    """
    Exclusive, inter-process (and inter-thread) lock guarding a
    read-modify-write critical section over the persisted config.

    Production runs 4 SEPARATE OS PROCESSES under supervisor — Telegram
    Gateway, Pipeline Worker, Core API, Meta Loop (see Dockerfile) — any
    of which can reach record_trade_result(): the Pipeline Worker's
    periodic 30-minute learning cycle (core/transport/pipeline_worker.py,
    via a ThreadPoolExecutor) and a creator-triggered
    /vx etoro learn run / /vx market ... command handled by the Telegram
    Gateway can both call check_open_positions() -> record_trade_result()
    around the same time, from different processes. A plain
    threading.Lock only serializes threads inside ONE process — it does
    nothing across process boundaries — so it cannot make
    record_trade_result()'s load -> check trade_id -> increment -> save
    sequence atomic against that real scenario: two callers could both
    load the config before either saves, both see the trade_id as unseen,
    and both count it.

    Same fcntl.flock() pattern already used for this identical class of
    bug in core/learn/gravity_engine.py's GravityIndex._locked() (the
    2026-09-20 production incident write-up there has the full history).
    flock() attaches to the OPEN FILE DESCRIPTION, not the process or
    thread, so a fresh os.open() + flock() on every call correctly
    serializes both across processes and across threads of the same
    process — unlike a threading.Lock, or reusing one shared fd, which
    would only correctly serialize the threads of a single process and
    give a false sense of safety against the real, multi-process
    deployment. If the holder dies (SIGKILL/OOM/crash) the kernel
    releases the flock when its file descriptors close — no orphaned
    lock can survive a restart and wedge every process forever.
    """
    lock_path = f"{_CONFIG_FILE}.lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def get_config() -> Dict:
    return _load_config()


def update_config(updates: Dict) -> Dict:
    cfg = _load_config()
    cfg.update(updates)
    _save_config(cfg)
    return cfg


def validate_risk_limit(key: str, value: float) -> "tuple[bool, Any, str]":
    """Valida un valor propuesto para una clave de límite de riesgo contra
    los techos duros del módulo (MAX_POSITION_USD_CEILING y compañía,
    arriba) — NUNCA valores que el propio Telegram pudiera proponer.
    Corrección 2026-09-26: antes, `/vx etoro auto config <clave> <valor>`
    (telegram_gateway.py) solo comprobaba que la clave existiera y que el
    valor parseara como número — sin rango. Devuelve
    `(válido, valor_coercido_al_tipo_correcto, error_si_inválido)`.

    Los techos son constantes de ESTE módulo a propósito: cambiarlos
    exige editar el código y redesplegar, nunca un mensaje de chat — un
    mensaje mal tipeado o de una cuenta comprometida no puede subir
    cuánto dinero o cuántas posiciones el sistema puede arriesgar.

    Una clave sin techo definido aquí (p.ej. `min_paper_win_rate`, que es
    solo informativa, no un límite de ejecución) se acepta tal cual.
    """
    try:
        if key == "max_position_usd":
            if value <= 0 or value > MAX_POSITION_USD_CEILING:
                return False, None, (
                    f"max_position_usd debe ser >0 y ≤ ${MAX_POSITION_USD_CEILING:.0f} "
                    f"(techo fijo, no configurable por Telegram)."
                )
            return True, float(value), ""

        if key == "max_daily_loss_usd":
            if value <= 0 or value > MAX_DAILY_LOSS_USD_CEILING:
                return False, None, (
                    f"max_daily_loss_usd debe ser >0 y ≤ ${MAX_DAILY_LOSS_USD_CEILING:.0f} "
                    f"(techo fijo, no configurable por Telegram)."
                )
            return True, float(value), ""

        if key == "stop_loss_pct":
            lo, hi = STOP_LOSS_PCT_RANGE
            if not (lo <= value <= hi):
                return False, None, f"stop_loss_pct debe estar entre {lo} y {hi}."
            return True, float(value), ""

        if key == "max_consecutive_losses":
            lo, hi = MAX_CONSECUTIVE_LOSSES_RANGE
            ivalue = int(value)
            if not (lo <= ivalue <= hi):
                return False, None, f"max_consecutive_losses debe estar entre {lo} y {hi}."
            return True, ivalue, ""

        if key == "max_positions_open":
            lo, hi = MAX_POSITIONS_OPEN_RANGE
            ivalue = int(value)
            if not (lo <= ivalue <= hi):
                return False, None, f"max_positions_open debe estar entre {lo} y {hi}."
            return True, ivalue, ""

        if key == "min_paper_signals":
            ivalue = int(value)
            if ivalue < MIN_PAPER_SIGNALS_MIN:
                return False, None, f"min_paper_signals debe ser ≥ {MIN_PAPER_SIGNALS_MIN}."
            return True, ivalue, ""
    except (TypeError, ValueError) as exc:
        return False, None, f"Valor inválido para {key}: {exc}"

    return True, value, ""


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
        f"Paper trades: {cfg['paper_trades_total']} | WR: {paper_wr:.0f}% (informativo)\n"
        f"Requisito LIVE: ≥{cfg['min_paper_signals']} operaciones PAPER cerradas, "
        f"entorno real y credenciales reales configuradas."
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
    Every phase requirement for entering LIVE — shared by the MANUAL
    /vx market live on command and the AUTOMATIC promotion evaluated on
    every PAPER close: ≥ min_paper_signals resolved (closed) PAPER trades,
    real environment, real credentials. Deliberately does NOT check win
    rate on either path (explicit creator instruction, 2026-09-23 session:
    "quítalo también de la vía manual"). `min_paper_win_rate` remains in
    config and is still tracked/reported — it is just not a gate here.
    Returns the list of blocking reasons — empty means every requirement
    is met.
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

    reasons = _live_readiness_reasons(cfg)
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


# ── Pausa automática de 24h (cortacircuitos) ────────────────────────────
# Corrección 2026-09-26: antes, un cortacircuito (pérdidas consecutivas o
# límite de pérdida diaria) revertía `mode` a PAPER. El creador pidió lo
# contrario: el modo se queda en LIVE todo el tiempo -- lo que se bloquea
# es la ENTRADA de operaciones nuevas, por 24h, con reanudación automática
# sin intervención manual. El HALT manual (`cfg["halt"]`) es un mecanismo
# completamente aparte y esta pausa nunca lo toca ni lo levanta.

def _activate_pause(cfg: Dict, reason: str) -> None:
    """Activa (o mantiene) la pausa de 24h. Muta `cfg` EN EL LUGAR -- se
    llama SIEMPRE desde dentro del `with _locked_config():` de
    record_trade_result(), nunca adquiere su propio lock (haría deadlock:
    flock() en un fd nuevo del mismo proceso se bloquearía esperando al
    fd que ya sostiene record_trade_result). El caller persiste `cfg`.

    Si ya hay una pausa vigente, NO reinicia el reloj ni pisa el motivo
    original -- dos cortacircuitos pueden dispararse en el mismo cierre
    (una pérdida grande puede agotar el límite diario Y el de pérdidas
    consecutivas a la vez); el primero en activarla manda.
    """
    now = time.time()
    if cfg.get("paused_until", 0) > now:
        return  # pausa ya vigente -- no se extiende ni se pisa el motivo
    cfg["paused_until"] = now + 24 * 3600
    cfg["pause_reason"] = reason
    logger.warning(
        "[AUTO] ⏸️ PAUSA 24h activada (modo permanece LIVE): %s", reason,
    )
    try:
        from connectors.etoro.learning_engine import _tg_notify
        resume_at = time.strftime(
            "%Y-%m-%d %H:%M UTC", time.gmtime(cfg["paused_until"])
        )
        _tg_notify(
            f"⏸️ Entradas LIVE pausadas 24h\n\n"
            f"Motivo: {reason}\n"
            f"Reanuda automáticamente: {resume_at}\n\n"
            f"El modo sigue en LIVE — las posiciones ya abiertas no se "
            f"tocan, siguen con su SL/TP en el bróker. Solo se bloquean "
            f"entradas nuevas."
        )
    except Exception:
        pass


def _maybe_lift_expired_pause() -> None:
    """Levanta una pausa vencida y reinicia los contadores que la
    dispararon. A diferencia de `_activate_pause`, ESTA función adquiere
    su propio `_locked_config()` -- se llama desde `check_risk_before_trade()`,
    que no sostiene ningún lock al entrar. El doble chequeo (una vez
    afuera en el caller antes de decidir llamar, y otra vez adentro bajo
    el lock) evita que dos llamadas casi simultáneas (el mismo escenario
    multi-proceso de siempre) reanuden y avisen dos veces.

    Sobrevive un reinicio de procesos sin más esfuerzo: `paused_until` y
    `pause_reason` viven en el mismo JSON persistido que el resto de la
    config -- un proceso nuevo los lee del disco tal cual quedaron, nunca
    de un estado en memoria que un reinicio pudiera perder.
    """
    now = time.time()
    with _locked_config():
        cfg = _load_config()
        paused_until = cfg.get("paused_until", 0) or 0
        if not paused_until or paused_until > now:
            return  # sin pausa activa, o todavía vigente
        reason = cfg.get("pause_reason", "")
        cfg["paused_until"] = 0.0
        cfg["pause_reason"] = ""
        cfg["daily_loss_usd"] = 0.0
        cfg["consecutive_losses"] = 0
        _save_config(cfg)

    logger.warning(
        "[AUTO] ▶️ Pausa levantada automáticamente (motivo original: %s)",
        reason,
    )
    try:
        from connectors.etoro.learning_engine import _tg_notify
        _tg_notify(
            f"▶️ Pausa levantada — entradas LIVE reanudadas\n\n"
            f"Motivo original: {reason}\n"
            f"Contadores de pérdida reiniciados."
        )
    except Exception:
        pass


def check_risk_before_trade(
    amount_usd: float,
) -> tuple[bool, str]:
    """
    Check all risk limits before placing a trade.
    Returns (allowed, reason_if_blocked).
    """
    # Levanta una pausa vencida ANTES de leer cfg para esta decisión --
    # así una llamada justo después de las 24h ya ve daily_loss_usd/
    # consecutive_losses reiniciados y paused_until en 0.
    _maybe_lift_expired_pause()
    cfg = _reset_daily_loss_if_new_day(_load_config())

    # 0. Halt check (manual, aparte de la pausa automática -- ver abajo)
    if cfg.get("halt"):
        return False, "🛑 Sistema en HALT. Ejecución bloqueada."

    # 0.5 Pausa automática de 24h (corrección 2026-09-26: el modo YA NO
    #     vuelve a PAPER cuando dispara un cortacircuito -- ver
    #     _activate_pause/_maybe_lift_expired_pause arriba). Se chequea
    #     antes que nada más porque, mientras esté vigente, ninguna otra
    #     condición importa: no se deja entrar nada.
    if cfg.get("paused_until", 0) > time.time():
        remaining_h = (cfg["paused_until"] - time.time()) / 3600
        return False, (
            f"⏸️ Pausado por seguridad ({cfg.get('pause_reason', '')}). "
            f"Reanuda en {remaining_h:.1f}h (automático, sin intervención manual)."
        )

    # 0.6 Fail-closed: mientras exista una posición LIVE con PnL solo
    #     ESTIMADO (pnl_source == "estimated", ver check_live_positions_closed
    #     y worst_case_pnl_estimate) -- no confirmado por el bróker ni por
    #     el creador -- no se abre nada nuevo. Una estimación de peor caso
    #     es una medida de seguridad para los contadores de riesgo, no una
    #     confirmación en la que apoyarse para seguir operando. Corrección
    #     2026-09-26 (tercera vuelta del PR #134). No tiene su propio
    #     aviso por Telegram aquí a propósito -- ya se avisó UNA vez cuando
    #     se detectó y estimó el cierre; repetirlo en cada chequeo de
    #     riesgo sería spam.
    pending_estimates = [
        t for t in get_live_trades(limit=200) if t.pnl_source == "estimated"
    ]
    if pending_estimates:
        ids = ", ".join(t.trade_id for t in pending_estimates)
        return False, (
            f"⏸️ {len(pending_estimates)} posición(es) LIVE con PnL "
            f"estimado, sin confirmar ({ids}). Bloqueado hasta resolver "
            f"con /vx etoro live_pnl <trade_id> <pnl>."
        )

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

    # 3. Daily loss limit. En el camino normal esto ya está cubierto por
    #    la pausa de arriba (record_trade_result activa la pausa en el
    #    mismo instante en que daily_loss_usd cruza el límite) -- queda
    #    como red de seguridad adicional, no como mecanismo principal.
    if cfg["daily_loss_usd"] >= cfg["max_daily_loss_usd"]:
        return False, (
            f"Límite de pérdida diaria alcanzado "
            f"(${cfg['daily_loss_usd']:.2f} / ${cfg['max_daily_loss_usd']:.2f})."
        )

    # 4. Consecutive losses. Chequeo defensivo aparte del de
    #    record_trade_result -- cubre el caso de que el umbral se baje por
    #    Telegram por debajo de un consecutive_losses ya alcanzado, sin
    #    que medie un cierre nuevo. Mismo mecanismo de pausa (corrección
    #    2026-09-26): activa la pausa de 24h en vez de revertir a PAPER.
    if cfg["consecutive_losses"] >= cfg["max_consecutive_losses"]:
        with _locked_config():
            fresh_cfg = _load_config()
            _activate_pause(
                fresh_cfg,
                reason=(
                    f"{fresh_cfg['consecutive_losses']} pérdidas consecutivas "
                    f"(límite {fresh_cfg['max_consecutive_losses']})"
                ),
            )
            _save_config(fresh_cfg)
        return False, (
            f"⏸️ Pausado por seguridad: {cfg['consecutive_losses']} pérdidas "
            f"consecutivas. Reanuda en 24h (automático)."
        )

    # 5. Max positions open. Antes, `max_positions_open` solo aparecía en
    #    texto de estado -- nunca se aplicaba (corrección 2026-09-26). En
    #    LIVE se cuenta contra el bróker real (get_portfolio()), no contra
    #    lo que el propio sistema CREE que tiene abierto: si la consulta
    #    al bróker falla, se bloquea (fail closed) -- abrir una posición
    #    nueva sin saber cuántas hay ya abiertas es exactamente el riesgo
    #    que este límite existe para evitar.
    if mode == AutoMode.LIVE:
        try:
            from connectors.etoro.etoro_client import get_portfolio
            portfolio = get_portfolio()
        except Exception as exc:
            return False, (
                f"No se pudo consultar el bróker para contar posiciones "
                f"abiertas: {exc}"
            )
        if not portfolio.get("success"):
            return False, (
                "No se pudo confirmar cuántas posiciones LIVE hay abiertas "
                f"(bróker: {portfolio.get('error', 'error desconocido')}) "
                "— bloqueado por seguridad."
            )
        n_open = portfolio.get("n_positions", 0)
    else:
        n_open = sum(1 for t in get_paper_trades(limit=200) if t.status == "open")

    if n_open >= cfg["max_positions_open"]:
        return False, (
            f"Máximo de posiciones simultáneas alcanzado "
            f"({n_open}/{cfg['max_positions_open']})."
        )

    return True, ""


def record_trade_result(
    pnl_usd: float,
    is_paper: bool = True,
    trade_id: Optional[str] = None,
) -> None:
    """
    Record the result of an executed trade and update risk counters.

    `trade_id` is REQUIRED regardless of `is_paper` (corrección
    2026-09-26: antes solo era obligatorio en PAPER, y la rama LIVE no
    deduplicaba en absoluto). Es la clave de idempotencia que evita que un
    cierre duplicado/reintentado de LA MISMA operación (e.g. dos corridas
    superpuestas de position_manager.check_open_positions(), o el chequeo
    de 5 min y el ciclo de 30 min solapándose) infle
    paper_trades_total/paper_trades_wins, o -- en LIVE -- cuente la misma
    pérdida dos veces contra consecutive_losses/daily_loss_usd y dispare
    un auto-shutdown por un evento fantasma. Un trade_id ya visto es un
    no-op: nada se cuenta ni se reevalúa, en ninguno de los dos modos.

    The load -> check trade_id -> increment -> save sequence below runs
    inside _locked_config(), an exclusive fcntl.flock() — see that
    function's docstring for why two genuinely concurrent callers (real
    production runs 4 separate OS processes under supervisor) cannot both
    read the config before either writes, which a threading.Lock would
    not have prevented.
    """
    if not trade_id:
        raise ValueError(
            "record_trade_result() requires trade_id — ni los contadores "
            "PAPER ni los de LIVE (consecutive_losses/daily_loss_usd) "
            "deben avanzar desde un cierre sin identificar"
        )

    # The whole load -> check trade_id -> increment -> save sequence is one
    # atomic critical section — see _locked_config() for why a plain
    # threading.Lock cannot do this job across the real (multi-process)
    # deployment.
    with _locked_config():
        cfg = _reset_daily_loss_if_new_day(_load_config())
        won = pnl_usd > 0

        if is_paper:
            recorded = cfg.setdefault("recorded_paper_trade_ids", [])
            if trade_id in recorded:
                logger.warning(
                    "[AUTO] Resultado PAPER duplicado ignorado para %s "
                    "(ya contabilizado — paper_trades_total sin cambios)",
                    trade_id,
                )
                return
            recorded.append(trade_id)
            if len(recorded) > _MAX_RECORDED_TRADE_IDS:
                del recorded[: len(recorded) - _MAX_RECORDED_TRADE_IDS]

            cfg["paper_trades_total"] += 1
            if won:
                cfg["paper_trades_wins"] += 1
            logger.info(
                "[AUTO] PAPER trade result: %s | PnL=%.2f USD | won=%s",
                trade_id, pnl_usd, won,
            )
            # Evaluate automatic PAPER → LIVE promotion right on this close.
            _maybe_auto_promote_to_live(cfg)
        else:
            # Mismo dedupe que la rama PAPER arriba, mismo motivo — ver
            # docstring: antes de esta corrección esta rama no tenía
            # ninguna protección contra un cierre LIVE contado dos veces.
            recorded_live = cfg.setdefault("recorded_live_trade_ids", [])
            if trade_id in recorded_live:
                logger.warning(
                    "[AUTO] Resultado LIVE duplicado ignorado para %s "
                    "(ya contabilizado — contadores de riesgo sin cambios)",
                    trade_id,
                )
                return
            recorded_live.append(trade_id)
            if len(recorded_live) > _MAX_RECORDED_TRADE_IDS:
                del recorded_live[: len(recorded_live) - _MAX_RECORDED_TRADE_IDS]

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

            # Cortacircuito. Corrección 2026-09-26: el creador pidió que el
            # modo YA NO vuelva a PAPER -- se queda en LIVE, pero
            # _activate_pause() bloquea entradas nuevas por 24h (ver esa
            # función arriba). Mutates the same local `cfg` that the final
            # _save_config(cfg) below persists — _activate_pause() nunca
            # adquiere su propio lock a propósito, por la misma razón que
            # antes hacía falta mutar cfg["mode"] directamente en vez de
            # pasar por _set_mode(): una segunda adquisición del lock
            # DENTRO de este bloque (que ya lo sostiene) sería un deadlock.
            if cfg["consecutive_losses"] >= cfg["max_consecutive_losses"]:
                _activate_pause(
                    cfg,
                    reason=(
                        f"{cfg['consecutive_losses']} pérdidas consecutivas "
                        f"(límite {cfg['max_consecutive_losses']})"
                    ),
                )

            if cfg["daily_loss_usd"] >= cfg["max_daily_loss_usd"]:
                _activate_pause(
                    cfg,
                    reason=(
                        f"límite de pérdida diaria alcanzado "
                        f"(${cfg['daily_loss_usd']:.2f} / ${cfg['max_daily_loss_usd']:.2f})"
                    ),
                )

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


# ── Live trade log ───────────────────────────────────────────────────
# Mismo patrón exacto que PaperTrade/record_paper_trade/get_paper_trades
# arriba, con dos campos propios de una orden real: `position_id` (con lo
# que el bróker identifica la posición — es lo que se compara contra
# get_portfolio() para detectar un cierre) y `order_id` (referencia de la
# orden de apertura). Antes de esta corrección, una apertura LIVE exitosa
# no se registraba en ningún lado propio del auto-executor -- solo vivía
# en el ExecutionResult de esa llamada y en el audit log de
# trade_executor.py, así que nada podía después preguntar "¿sigue abierta
# esta posición?" ni cerrar el círculo hacia record_trade_result().

@dataclass
class LiveTrade:
    trade_id:    str            # mismo espacio de id que dedupe record_trade_result
    position_id: str            # id del bróker -- clave de comparación contra el portfolio real
    order_id:    Optional[str]
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
    # Corrección 2026-09-26 (segunda y tercera vuelta del PR #134):
    # `pnl_source` reemplaza el status "closed_pnl_pending" que existía
    # antes -- ahora SIEMPRE hay un pnl_usd en cuanto se detecta el cierre
    # (al menos una estimación de peor caso), y lo que distingue si es
    # confiable es este campo, no un status aparte.
    #   None        -> todavía abierta.
    #   "estimated" -> pnl_usd es una estimación de peor caso, NO
    #                  confirmada por el bróker. check_risk_before_trade()
    #                  bloquea toda entrada nueva mientras exista al
    #                  menos una posición en este estado (fail-closed).
    #   "real"      -> pnl_usd confirmado (bróker o corrección manual del
    #                  creador). Definitivo: nada lo puede reemplazar.
    pnl_source:  Optional[str] = None
    closed_at:   Optional[float] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def record_live_trade_open(
    position_id: str,
    order_id: Optional[str],
    symbol: str,
    direction: str,
    amount_usd: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    proposal_id: str,
) -> LiveTrade:
    """Registra la apertura de una operación LIVE real.

    `trade_id` usa el MISMO prefijo/formato que `record_paper_trade` (para
    que el dedupe de `record_trade_result` trate ambos espacios de forma
    uniforme), pero con `position_id` embebido -- es lo único que el
    bróker devuelve de forma estable para volver a identificar esta
    posición después, así que `trade_id` y `position_id` deben poder
    reconstruirse el uno del otro sin ambigüedad.
    """
    tid = f"LIVE-{position_id}"
    trade = LiveTrade(
        trade_id=tid,
        position_id=str(position_id),
        order_id=str(order_id) if order_id is not None else None,
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
        os.makedirs(os.path.dirname(_LIVE_LOG_FILE), exist_ok=True)
        with open(_LIVE_LOG_FILE, "a") as f:
            f.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("record_live_trade_open error: %s", e)
    logger.warning(
        "[LIVE] %s | %s %s $%.0f @ %.5g | SL=%.5g TP=%.5g | position_id=%s",
        tid, direction.upper(), symbol, amount_usd, entry_price, stop_loss,
        take_profit, position_id,
    )
    return trade


def get_live_trades(limit: int = 20, status_filter: Optional[str] = None) -> List[LiveTrade]:
    if not os.path.exists(_LIVE_LOG_FILE):
        return []
    trades = []
    try:
        with open(_LIVE_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if status_filter and d.get("status") != status_filter:
                        continue
                    trades.append(LiveTrade(**{
                        k: v for k, v in d.items()
                        if k in LiveTrade.__dataclass_fields__
                    }))
                except Exception:
                    pass
    except Exception:
        pass
    return trades[-limit:] if limit else trades


def _update_live_trade(position_id: str, updates: Dict) -> bool:
    """Reescribe la entrada de `position_id` en el log LIVE (mismo patrón
    de reescritura-en-lugar que `signal_recorder.update_signal`). Devuelve
    True si encontró y actualizó la posición."""
    if not os.path.exists(_LIVE_LOG_FILE):
        return False
    lines = []
    found = False
    try:
        with open(_LIVE_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("position_id") == position_id:
                        d.update(updates)
                        found = True
                    lines.append(json.dumps(d, ensure_ascii=False))
                except Exception:
                    lines.append(line)
        with open(_LIVE_LOG_FILE, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.error("_update_live_trade error: %s", e)
        return False
    return found


def worst_case_pnl_estimate(trade: "LiveTrade") -> float:
    """Estimación de PnL de peor caso para una posición LIVE que
    desapareció del bróker sin PnL realizado disponible: asume que se
    tocó el propio stop-loss registrado de ESTA operación (no el
    `stop_loss_pct` global de la config, que pudo cambiar desde que se
    abrió). `entry_price`/`stop_loss` ya son niveles de precio absolutos
    guardados en la propia LiveTrade -- más preciso que releer un
    porcentaje genérico."""
    if not trade.entry_price:
        return 0.0
    stop_pct = abs(trade.entry_price - trade.stop_loss) / trade.entry_price
    return -abs(trade.amount_usd * stop_pct)


def _recompute_live_risk_counters(live_trades: "List[LiveTrade]") -> "tuple[float, int]":
    """`daily_loss_usd` (de HOY) y `consecutive_losses` (racha), RECALCULADOS
    desde cero a partir de los registros de resultado LIVE -- nunca
    acumulados. Corrección 2026-09-26 (segunda vuelta del PR #134): esto es
    lo que permite que una corrección posterior (estimado -> real) reescriba
    el pasado sin duplicar ni arrastrar un número viejo que ya no es cierto.

    - `daily_loss_usd`: suma de pérdidas (`pnl_usd < 0`) de cierres LIVE
      cuyo `closed_at` cae en el día de HOY (hora local, mismo criterio que
      `_reset_daily_loss_if_new_day`). Un cierre de otro día nunca contribuye,
      así que corregir el PnL de una operación de ayer no mueve el número de
      hoy.
    - `consecutive_losses`: la racha reconstruida recorriendo los cierres
      resueltos en orden cronológico por `closed_at`, contando hacia atrás
      desde el más reciente hasta el primer resultado que NO sea pérdida
      (pnl_usd >= 0 rompe la racha, mismo criterio que ya usaba
      record_trade_result).

    Solo considera cierres con `pnl_usd is not None` -- una posición sin
    resultado todavía (imposible en la práctica, pero defensivo) no cuenta
    como nada.
    """
    resolved = sorted(
        (t for t in live_trades if t.pnl_usd is not None and t.closed_at),
        key=lambda t: t.closed_at,
    )
    today = time.strftime("%Y-%m-%d")
    daily_loss = 0.0
    for t in resolved:
        if t.pnl_usd < 0 and time.strftime("%Y-%m-%d", time.localtime(t.closed_at)) == today:
            daily_loss += abs(t.pnl_usd)

    consecutive = 0
    for t in reversed(resolved):
        if t.pnl_usd < 0:
            consecutive += 1
        else:
            break

    return round(daily_loss, 2), consecutive


def record_live_trade_result(
    trade_id: str, pnl_usd: float, source: str, closed_at: Optional[float] = None,
) -> bool:
    """Registra (o corrige) el resultado de UN cierre LIVE ya detectado por
    `record_live_trade_open()`. Corrección 2026-09-26 (segunda vuelta del
    PR #134): reemplaza el diseño anterior de "closed_pnl_pending sin
    registrar nada en absoluto" -- ahora SIEMPRE hay un resultado (al menos
    una estimación de peor caso, ver `worst_case_pnl_estimate`), y
    `daily_loss_usd`/`consecutive_losses` se recalculan desde cero cada vez
    (`_recompute_live_risk_counters`) en vez de acumularse en el momento.

    Precedencia -- NUNCA se suma, siempre se reemplaza o se ignora:
      - sin registro previo de resultado (`pnl_source is None`)  -> se escribe.
      - previo 'estimated', nuevo 'estimated'  -> no-op (estimación duplicada).
      - previo 'estimated', nuevo 'real'        -> reemplaza (nunca suma).
      - previo 'real' (cualquier fuente nueva)  -> no-op (ya es definitivo,
        nada lo puede pisar -- ni siquiera otro "real").

    Tras escribir, SIEMPRE recalcula y persiste daily_loss_usd/
    consecutive_losses desde el log completo, y evalúa los cortacircuitos:
      - Si el recálculo cruza un límite, activa la pausa de 24h
        (`_activate_pause`, idempotente -- no la extiende si ya estaba
        activa ni pisa su motivo).
      - Si el recálculo MEJORA (ya no cruza el límite), NO se toca ninguna
        pausa ya activa -- este código nunca "levanta" una pausa por
        mejorar; solo `_maybe_lift_expired_pause()` la levanta, por tiempo.

    Todo bajo el mismo `_locked_config()` -- lectura, escritura del log LIVE,
    recálculo y evaluación del cortacircuito son una sola sección crítica.

    Devuelve True si el registro cambió (nuevo o reemplazado por uno real),
    False si fue un no-op (duplicado, ya definitivo, o trade_id desconocido).
    """
    if source not in ("estimated", "real"):
        raise ValueError(f"source debe ser 'estimated' o 'real', no {source!r}")
    closed_at = closed_at if closed_at is not None else time.time()

    changed = False
    with _locked_config():
        trades = get_live_trades(limit=0)
        existing = next((t for t in trades if t.trade_id == trade_id), None)
        if existing is None:
            logger.warning(
                "[LIVE] record_live_trade_result: trade_id %s no existe en "
                "el log -- no se puede registrar su resultado.", trade_id,
            )
            return False

        if existing.pnl_source == "real":
            logger.debug(
                "[LIVE] %s ya tiene PnL real definitivo -- ignorado (%s)",
                trade_id, source,
            )
            return False
        if existing.pnl_source == "estimated" and source == "estimated":
            logger.debug("[LIVE] %s: estimación duplicada ignorada", trade_id)
            return False

        status = (
            "closed_win" if pnl_usd > 0 else
            "closed_loss" if pnl_usd < 0 else "closed_neutral"
        )
        _update_live_trade(existing.position_id, {
            "status": status,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_source": source,
            "closed_at": closed_at,
        })
        changed = True
        logger.warning(
            "[LIVE] %s | resultado %s registrado: PnL=%.2f USD (%s)",
            trade_id, source, pnl_usd, status,
        )

        fresh_trades = get_live_trades(limit=0)
        daily_loss, consecutive = _recompute_live_risk_counters(fresh_trades)

        cfg = _load_config()
        cfg["daily_loss_usd"] = daily_loss
        cfg["consecutive_losses"] = consecutive

        if consecutive >= cfg["max_consecutive_losses"]:
            _activate_pause(cfg, reason=(
                f"{consecutive} pérdidas consecutivas LIVE "
                f"(límite {cfg['max_consecutive_losses']})"
            ))
        if daily_loss >= cfg["max_daily_loss_usd"]:
            _activate_pause(cfg, reason=(
                f"límite de pérdida diaria LIVE alcanzado "
                f"(${daily_loss:.2f} / ${cfg['max_daily_loss_usd']:.2f})"
            ))
        _save_config(cfg)

    return changed


def record_live_trade_real_pnl(trade_id: str, pnl_usd: float) -> bool:
    """Atajo para que el creador registre manualmente el PnL REAL de una
    posición LIVE (p. ej. leyéndolo del historial en la app de eToro,
    mientras este cliente no tenga un endpoint de historial de posiciones
    cerradas que funcione -- ver la investigación en el PR). Reemplaza
    cualquier estimación previa de ESE trade_id; nunca se suma. Expuesto
    por Telegram vía `/vx etoro live_pnl <trade_id> <pnl>`."""
    return record_live_trade_result(trade_id, pnl_usd, source="real", closed_at=time.time())


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
        # Registro de apertura LIVE (item 1 de la corrección de seguridad
        # 2026-09-26): sin esto, una orden real abierta con éxito no
        # quedaba en ningún lado propio del auto-executor, y por lo tanto
        # nunca podía cerrarse el círculo hacia record_trade_result() --
        # ver LiveTrade / record_live_trade_open arriba. Si el bróker no
        # devolvió position_id (no debería pasar en un success=True, pero
        # nunca se asume), no se puede rastrear esta posición después: se
        # registra el fallo y se avisa por log, sin romper la ejecución ya
        # ocurrida (el dinero ya se movió; ocultar eso sería peor).
        if result.position_id:
            record_live_trade_open(
                position_id=str(result.position_id),
                order_id=result.order_id,
                symbol=proposal.symbol,
                direction=proposal.direction,
                amount_usd=trade_amount,
                entry_price=proposal.entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                proposal_id=proposal_id,
            )
        else:
            logger.error(
                "[LIVE] Orden ejecutada con éxito pero SIN position_id — "
                "no se puede rastrear para cierre/PnL. proposal=%s order_id=%s",
                proposal_id, result.order_id,
            )
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
    n_paper     = cfg["paper_trades_total"]
    sig_ok  = "✅" if n_paper >= req_signals else f"❌ ({n_paper}/{req_signals})"

    lines = [
        f"{icon} Auto-Executor: {mode.value.upper()}\n",
        "📊 Fase PAPER:",
        f"  Operaciones cerradas (requisito LIVE): {sig_ok}",
        f"  Win rate actual: {paper_wr:.0f}% (informativo — no es requisito para LIVE)",
        "",
        "🛡 Límites de riesgo:",
        f"  Max/operación:    ${cfg['max_position_usd']:.0f}",
        f"  Max pérdida/día:  ${cfg['max_daily_loss_usd']:.0f}",
        f"  Stop-loss:        {cfg['stop_loss_pct']:.1f}%",
        f"  Pérdidas consec:  {cfg['consecutive_losses']}/{cfg['max_consecutive_losses']}",
        f"  Max posiciones:   {cfg['max_positions_open']}",
    ]

    if cfg.get("paused_until", 0) > time.time():
        import datetime
        resume_str = datetime.datetime.utcfromtimestamp(
            cfg["paused_until"]
        ).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(
            f"\n⏸️ PAUSADO por seguridad: {cfg.get('pause_reason', '')}\n"
            f"  Reanuda automáticamente: {resume_str}"
        )

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
