"""
Vectrax Market Data — Binance WebSocket Stream.

Subscribes to real-time mini-ticker updates via wss://stream.binance.com.
Thread-safe, with automatic reconnection and exponential backoff.
Read-only: only receives market data, never sends orders.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from connectors.market import validate_host

logger = logging.getLogger("vectrax.market.binance_stream")

WS_BASE = "wss://stream.binance.com:9443/ws"
HOST = "stream.binance.com"

# Reconnection config
MAX_RECONNECT_DELAY = 60
INITIAL_RECONNECT_DELAY = 1


class BinanceStream:
    """
    Real-time market data stream from Binance WebSocket.

    Usage::

        stream = BinanceStream(["btcusdt", "ethusdt"])
        stream.start(on_tick=my_callback)
        # ... later ...
        stream.stop()
    """

    def __init__(self, symbols: List[str] | None = None):
        self.symbols = [s.lower() for s in (symbols or ["btcusdt", "ethusdt"])]
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._reconnect_delay = INITIAL_RECONNECT_DELAY
        self._on_tick: Optional[Callable[[Dict[str, Any]], None]] = None
        self._latest: Dict[str, Dict[str, Any]] = {}

    @property
    def latest(self) -> Dict[str, Dict[str, Any]]:
        """Return latest tick data per symbol (thread-safe copy)."""
        with self._lock:
            return dict(self._latest)

    def get_latest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest tick for a specific symbol."""
        with self._lock:
            return self._latest.get(symbol.lower())

    def start(self, on_tick: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """Start the WebSocket stream in a background thread."""
        if not validate_host(HOST):
            logger.error("[GOVERNANCE] stream.binance.com not in allowlist")
            return False

        try:
            import websocket  # noqa: F401
        except ImportError:
            logger.error(
                "websocket-client not installed. Install with: pip install websocket-client"
            )
            return False

        self._on_tick = on_tick
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="binance-stream")
        self._thread.start()
        logger.info("[STREAM] Started for symbols: %s", self.symbols)
        return True

    def stop(self) -> None:
        """Stop the WebSocket stream gracefully."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[STREAM] Stopped")

    def _build_url(self) -> str:
        streams = "/".join(f"{s}@miniTicker" for s in self.symbols)
        return f"wss://stream.binance.com:9443/stream?streams={streams}"

    def _run_loop(self) -> None:
        """Main reconnection loop."""
        import websocket

        while self._running:
            url = self._build_url()
            try:
                logger.info("[STREAM] Connecting to %s", url)
                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                logger.error("[STREAM] Connection error: %s", exc)

            if not self._running:
                break

            # Exponential backoff
            logger.info("[STREAM] Reconnecting in %ds...", self._reconnect_delay)
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_DELAY)

    def _on_open(self, ws: Any) -> None:
        logger.info("[STREAM] Connected")
        self._reconnect_delay = INITIAL_RECONNECT_DELAY

    def _on_message(self, ws: Any, message: str) -> None:
        try:
            payload = json.loads(message)
            # Combined stream format: {"stream": "...", "data": {...}}
            data = payload.get("data", payload)
            symbol = data.get("s", "").lower()
            if not symbol:
                return

            tick = {
                "symbol": symbol.upper(),
                "price": float(data.get("c", 0)),
                "open": float(data.get("o", 0)),
                "high": float(data.get("h", 0)),
                "low": float(data.get("l", 0)),
                "volume": float(data.get("v", 0)),
                "quote_volume": float(data.get("q", 0)),
                "timestamp": data.get("E", int(time.time() * 1000)),
                "source": "binance_stream",
            }

            with self._lock:
                self._latest[symbol] = tick

            if self._on_tick:
                try:
                    self._on_tick(tick)
                except Exception as exc:
                    logger.warning("[STREAM] Callback error: %s", exc)

        except json.JSONDecodeError:
            logger.warning("[STREAM] Invalid JSON received")
        except Exception as exc:
            logger.warning("[STREAM] Message processing error: %s", exc)

    def _on_error(self, ws: Any, error: Any) -> None:
        logger.error("[STREAM] WebSocket error: %s", error)

    def _on_close(self, ws: Any, close_code: Any, close_msg: Any) -> None:
        logger.info("[STREAM] Connection closed (code=%s, msg=%s)", close_code, close_msg)

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()


# ── Module-level singleton ──────────────────────────────────────────
_stream: Optional[BinanceStream] = None


def get_binance_stream(symbols: List[str] | None = None) -> BinanceStream:
    """Return the global BinanceStream singleton."""
    global _stream
    if _stream is None:
        _stream = BinanceStream(symbols)
    return _stream
