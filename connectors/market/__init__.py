"""
Vectrax Market Data Module — Connectors Package.

Domain: market_data_authorized
Mode: READ-ONLY — no trading, no orders, no external writes.

Allowlist:
  - api.binance.com
  - stream.binance.com
  - data-stream.binance.vision
  - api.coingecko.com
  - www.alphavantage.co
"""
from __future__ import annotations

import logging
from typing import Set

logger = logging.getLogger("vectrax.market")

# ── Governance ──────────────────────────────────────────────────────
DOMAIN = "market_data_authorized"
MODE = "read_only"

ALLOWLIST: Set[str] = frozenset({
    "api.binance.com",
    "stream.binance.com",
    "data-stream.binance.vision",
    "api.coingecko.com",
    "www.alphavantage.co",
})

# ── Default watchlist ───────────────────────────────────────────────
CRYPTO_WATCHLIST = ["BTCUSDT", "ETHUSDT"]
STOCK_WATCHLIST = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]


def validate_host(host: str) -> bool:
    """Return True only if *host* is in the market data allowlist."""
    allowed = host in ALLOWLIST
    if not allowed:
        logger.warning("[GOVERNANCE] Blocked request to non-allowed host: %s", host)
    return allowed
