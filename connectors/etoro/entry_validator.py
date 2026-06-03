"""
connectors/etoro/entry_validator.py — Validador de entrada.

Antes de que el auto-executor abra cualquier operación (PAPER o LIVE),
TODAS estas condiciones deben cumplirse simultáneamente:

  1. Convergencia fuerte (gravity engine cross-domain)
  2. Patrón registrado y usable
  3. Señal repetida (≥2 en 24h para mismo símbolo+dirección)
  4. Confianza mínima configurable (MEDIUM o HIGH)
  5. Spread aceptable (implícito en mercado activo)
  6. Mercado activo (dentro de sesión de trading)
  7. Sin alerta crítica del sistema
  8. Símbolo no operado hoy (max_ops_per_symbol_day)
  9. Símbolo aprobado (solo en modo LIVE)
 10. Evidencia registrada en observation ledger

Retorna (allowed: bool, reasons: list[str]) donde reasons explica
cada condición no cumplida.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("vectrax.etoro.entry_validator")

# Confidence hierarchy for comparison
_CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def validate_entry(
    symbol: str,
    direction: str,
    proposal: Any,
    *,
    mode: str = "paper",
) -> Tuple[bool, List[str]]:
    """
    Validate ALL conditions before allowing a trade.

    Returns (allowed, reasons_if_blocked).
    If allowed is True, reasons is empty.
    """
    reasons: List[str] = []
    evidence: Dict[str, Any] = {}

    from connectors.etoro.auto_executor import (
        get_config, is_halted, symbol_ops_today, AutoMode,
    )
    cfg = get_config()
    sym = symbol.upper()

    # 0. HALT check
    if is_halted():
        reasons.append("🛑 Sistema en HALT.")
        return False, reasons

    # 1. Convergencia fuerte (gravity engine)
    convergence_ok = _check_convergence(sym)
    evidence["convergence"] = convergence_ok
    if not convergence_ok:
        reasons.append(f"Sin convergencia fuerte para {sym} en gravity engine.")

    # 2. Patrón registrado y usable
    pattern_ok, pattern_info = _check_pattern(sym, direction)
    evidence["pattern"] = pattern_info
    if not pattern_ok:
        reasons.append(f"Sin patrón usable para {sym}/{direction}.")

    # 3. Señal repetida (≥2 en 24h)
    signal_count = _count_recent_signals(sym, direction)
    evidence["signal_count_24h"] = signal_count
    if signal_count < 2:
        reasons.append(
            f"Señal insuficiente: {signal_count}/2 requeridas "
            f"en 24h para {sym}/{direction}."
        )

    # 4. Confianza mínima
    min_conf = cfg.get("min_confidence", "MEDIUM")
    proposal_conf = getattr(proposal, "confidence", "LOW") if proposal else "LOW"
    if _CONFIDENCE_RANK.get(proposal_conf, 0) < _CONFIDENCE_RANK.get(min_conf, 1):
        reasons.append(
            f"Confianza insuficiente: {proposal_conf} < {min_conf}."
        )

    # 5. Mercado activo (sesión de trading)
    market_active = _check_market_active(sym)
    evidence["market_active"] = market_active
    if not market_active:
        reasons.append(f"Mercado no activo para {sym} en este momento.")

    # 6. Sin alerta crítica del sistema
    critical_alert = _check_critical_alerts()
    evidence["critical_alert"] = critical_alert
    if critical_alert:
        reasons.append(f"Alerta crítica del sistema: {critical_alert}")

    # 7. Símbolo no operado hoy
    ops_today = symbol_ops_today(sym)
    max_ops = cfg.get("max_ops_per_symbol_day", 1)
    evidence["ops_today"] = ops_today
    if ops_today >= max_ops:
        reasons.append(
            f"{sym} ya operado {ops_today}/{max_ops} veces hoy."
        )

    # 8. Símbolo aprobado (solo LIVE)
    if mode == "live":
        approved = cfg.get("approved_symbols", [])
        if sym not in approved:
            reasons.append(
                f"{sym} no aprobado para LIVE. "
                f"Usa /vx market approve {sym}."
            )

    # 9. Evidencia registrada
    _log_validation(sym, direction, mode, reasons, evidence)

    allowed = len(reasons) == 0
    return allowed, reasons


# ---------------------------------------------------------------------------
# Condition checkers
# ---------------------------------------------------------------------------

def _check_convergence(symbol: str) -> bool:
    """Check if the symbol has a convergence in the gravity engine."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        convergences = gi.cross_domain_convergences()
        for c in convergences:
            domains = c.get("domains", [])
            intent = c.get("intent", "")
            if symbol in intent.upper() or "market" in domains:
                return True
        # Also check if the market star itself has significant mass
        rec = gi.get(f"market:{symbol}")
        if rec and rec.hits >= 5 and rec.cc_score >= 0.3:
            return True
    except Exception as exc:
        logger.debug("convergence check failed: %s", exc)
    return False


def _check_pattern(symbol: str, direction: str) -> Tuple[bool, str]:
    """Check if a usable pattern exists for symbol+direction."""
    try:
        from connectors.etoro.pattern_memory import get_patterns
        patterns = get_patterns(usable_only=True)
        matching = [
            p for p in patterns
            if p.symbol == symbol.upper() and p.direction == direction
        ]
        if matching:
            best = max(matching, key=lambda p: p.expectancy)
            return True, f"WR={best.win_rate:.0f}% E={best.expectancy:+.3f}% N={best.n_total}"
    except Exception as exc:
        logger.debug("pattern check failed: %s", exc)
    return False, "none"


def _count_recent_signals(symbol: str, direction: str) -> int:
    """Count signals in last 24h for symbol+direction."""
    try:
        from connectors.etoro.signal_recorder import load_signals
        cutoff = time.time() - 86400  # 24h
        signals = load_signals()
        return sum(
            1 for s in signals
            if s.symbol == symbol.upper()
            and s.direction == direction
            and s.timestamp > cutoff
        )
    except Exception:
        return 0


def _check_market_active(symbol: str) -> bool:
    """Check if we're within a trading session for this symbol."""
    import datetime
    now = datetime.datetime.utcnow()
    hour = now.hour
    weekday = now.weekday()  # 0=Monday, 6=Sunday

    # Crypto: 24/7
    if symbol.upper().endswith("USD") and symbol.upper()[:3] in ("BTC", "ETH"):
        return True

    # Forex: Sunday 22:00 to Friday 22:00 UTC
    if any(symbol.upper().startswith(c) for c in ("EUR", "GBP", "JPY", "AUD", "CHF")):
        if weekday == 5:  # Saturday
            return False
        if weekday == 6 and hour < 22:  # Sunday before 22:00
            return False
        if weekday == 4 and hour >= 22:  # Friday after 22:00
            return False
        return True

    # Stocks: Monday-Friday, ~14:30-21:00 UTC (US market hours)
    if weekday >= 5:  # Weekend
        return False
    if 14 <= hour < 21:
        return True
    return False


def _check_critical_alerts() -> str:
    """Return critical alert message if any, empty string if clear."""
    try:
        from core.operator.system_monitor import collect_metrics
        m = collect_metrics()
        if m.status == "critical":
            return f"Sistema en estado CRITICAL (RAM={m.memory_mb}MB, errors={m.queue_error})"
        if not m.worker_alive:
            return "Worker no responde"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Observation logging
# ---------------------------------------------------------------------------

def _log_validation(
    symbol: str,
    direction: str,
    mode: str,
    reasons: List[str],
    evidence: Dict[str, Any],
) -> None:
    """Log the validation decision to the observation ledger."""
    try:
        from core.self_observation.observation_ledger import record
        allowed = len(reasons) == 0
        summary = (
            f"Validación {'APROBADA' if allowed else 'RECHAZADA'}: "
            f"{direction.upper()} {symbol} (modo {mode}) — "
            f"{', '.join(reasons[:3]) if reasons else 'todas las condiciones cumplidas'}"
        )
        record(
            domain="market",
            obs_type="trade_validation",
            summary=summary[:200],
            star_id=f"market:{symbol}",
            evidence={
                "symbol": symbol,
                "direction": direction,
                "mode": mode,
                "allowed": allowed,
                "reasons": reasons[:5],
                **{k: v for k, v in evidence.items() if k != "pattern"},
            },
            severity="info" if allowed else "warning",
        )
    except Exception as exc:
        logger.debug("observation log failed: %s", exc)
