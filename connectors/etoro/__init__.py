"""
Vectrax eToro Connector — Governance Module
=============================================
Domain: trading_authorized_creator_only
Mode:   read + execute (execution requires explicit creator authorization)

Allowlist:
  - public-api.etoro.com   — REST API v1

Authentication:
  - x-api-key:   ETORO_API_KEY  (env)
  - x-user-key:  ETORO_USER_KEY (env)
  - x-request-id: UUID per request (generated automatically)

Environment:
  - ETORO_ENVIRONMENT=demo   → demo endpoints (default, safe)
  - ETORO_ENVIRONMENT=real   → real trading endpoints (requires explicit set)

Security rules:
  - All execution (open/close positions) requires creator authorization.
  - ETORO_ENVIRONMENT defaults to "demo" — never real without explicit config.
  - All operations logged to structural memory and ledger.
  - Market observer is read-only and runs on demand.
"""
from __future__ import annotations

import logging
import os
from typing import Set

logger = logging.getLogger("vectrax.etoro")

# ── Governance ──────────────────────────────────────────────────────
DOMAIN = "trading_authorized_creator_only"
MODE   = "read_execute"   # read always allowed; execute requires creator auth

BASE_URL = "https://public-api.etoro.com/api/v1"

ALLOWLIST: Set[str] = frozenset({
    "public-api.etoro.com",
})


def get_environment() -> str:
    """Return 'demo' or 'real'. Defaults to 'demo' for safety."""
    env = os.environ.get("ETORO_ENVIRONMENT", "demo").strip().lower()
    return env if env in ("demo", "real") else "demo"


def validate_host(host: str) -> bool:
    """Return True only if host is in the eToro allowlist."""
    allowed = host in ALLOWLIST
    if not allowed:
        logger.warning("[GOVERNANCE] Blocked request to non-allowed host: %s", host)
    return allowed


def is_demo() -> bool:
    return get_environment() == "demo"
