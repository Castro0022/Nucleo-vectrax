"""
Vectrax Market Data — Market Alerts Service.

Simple alert system for price levels and breakout events.
Stores alerts in memory (no external persistence).
Designed to be checked on demand or by the stream callback.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("vectrax.market.alerts")


class PriceAlert:
    """A price level alert."""

    def __init__(
        self,
        symbol: str,
        level: float,
        direction: str,  # "above" or "below"
        label: str = "",
    ):
        self.symbol = symbol.upper()
        self.level = level
        self.direction = direction  # "above" | "below"
        self.label = label or f"{symbol} {'>' if direction == 'above' else '<'} {level}"
        self.created_at = time.time()
        self.triggered = False
        self.triggered_at: Optional[float] = None
        self.triggered_price: Optional[float] = None

    def check(self, price: float) -> bool:
        """Check if alert condition is met."""
        if self.triggered:
            return False
        if self.direction == "above" and price >= self.level:
            self._trigger(price)
            return True
        if self.direction == "below" and price <= self.level:
            self._trigger(price)
            return True
        return False

    def _trigger(self, price: float) -> None:
        self.triggered = True
        self.triggered_at = time.time()
        self.triggered_price = price
        logger.info("[ALERT] %s triggered at %.2f", self.label, price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "level": self.level,
            "direction": self.direction,
            "label": self.label,
            "triggered": self.triggered,
            "triggered_at": self.triggered_at,
            "triggered_price": self.triggered_price,
            "created_at": self.created_at,
        }


class MarketAlerts:
    """Manages price alerts for the market module."""

    def __init__(self) -> None:
        self._alerts: List[PriceAlert] = []
        self._lock = threading.Lock()
        self._triggered_history: List[Dict[str, Any]] = []
        self._on_trigger: Optional[Callable[[PriceAlert], None]] = None

    def set_callback(self, callback: Callable[[PriceAlert], None]) -> None:
        """Set a callback function to be called when an alert triggers."""
        self._on_trigger = callback

    def add_alert(
        self,
        symbol: str,
        level: float,
        direction: str = "above",
        label: str = "",
    ) -> Dict[str, Any]:
        """Add a new price alert."""
        alert = PriceAlert(symbol, level, direction, label)
        with self._lock:
            self._alerts.append(alert)
        logger.info("[ALERTS] Added: %s", alert.label)
        return alert.to_dict()

    def check_price(self, symbol: str, price: float) -> List[Dict[str, Any]]:
        """Check all active alerts for a symbol against a price. Returns triggered alerts."""
        triggered = []
        with self._lock:
            for alert in self._alerts:
                if alert.symbol == symbol.upper() and not alert.triggered:
                    if alert.check(price):
                        triggered.append(alert.to_dict())
                        self._triggered_history.append(alert.to_dict())
                        if self._on_trigger:
                            try:
                                self._on_trigger(alert)
                            except Exception as exc:
                                logger.warning("[ALERTS] Callback error: %s", exc)
        return triggered

    def on_tick(self, tick: Dict[str, Any]) -> None:
        """
        Stream callback: check alerts on every tick.
        Designed to be passed to BinanceStream.start(on_tick=alerts.on_tick)
        """
        symbol = tick.get("symbol", "")
        price = tick.get("price", 0)
        if symbol and price > 0:
            self.check_price(symbol, price)

    def list_active(self) -> List[Dict[str, Any]]:
        """List all active (not triggered) alerts."""
        with self._lock:
            return [a.to_dict() for a in self._alerts if not a.triggered]

    def list_triggered(self) -> List[Dict[str, Any]]:
        """List all triggered alerts."""
        return list(self._triggered_history)

    def remove_alert(self, symbol: str, level: float) -> bool:
        """Remove an alert by symbol and level."""
        with self._lock:
            before = len(self._alerts)
            self._alerts = [
                a for a in self._alerts
                if not (a.symbol == symbol.upper() and a.level == level and not a.triggered)
            ]
            return len(self._alerts) < before

    def clear_all(self) -> int:
        """Clear all active alerts."""
        with self._lock:
            count = len(self._alerts)
            self._alerts.clear()
            return count

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active": sum(1 for a in self._alerts if not a.triggered),
                "triggered": len(self._triggered_history),
                "total": len(self._alerts),
            }


# ── Module-level singleton ──────────────────────────────────────────
_alerts: Optional[MarketAlerts] = None


def get_market_alerts() -> MarketAlerts:
    """Return the global MarketAlerts singleton."""
    global _alerts
    if _alerts is None:
        _alerts = MarketAlerts()
    return _alerts
