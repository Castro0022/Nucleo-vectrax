"""
connectors/etoro/client.py — eToro Public API Client
======================================================
Authenticated HTTP client for the eToro REST API.

Features:
  - x-api-key + x-user-key auth on every request
  - Unique x-request-id per call
  - Exponential backoff on 429 (rate limit)
  - Demo/real environment routing
  - Fail-safe: never raises on network errors (returns None)

Env vars:
  ETORO_API_KEY      — public API key
  ETORO_USER_KEY     — user key (treat as password)
  ETORO_ENVIRONMENT  — "demo" or "real" (default: demo)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("vectrax.etoro.client")

BASE_URL = "https://public-api.etoro.com/api/v1"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds


class EtoroClient:
    """Authenticated eToro API client."""

    def __init__(
        self,
        api_key: str = "",
        user_key: str = "",
        environment: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("ETORO_API_KEY", "")
        self._user_key = user_key or os.environ.get("ETORO_USER_KEY", "")
        self._env = (
            environment
            or os.environ.get("ETORO_ENVIRONMENT", "demo")
        ).lower()
        self._http = httpx.Client(timeout=20)

        if not self._api_key or not self._user_key:
            logger.warning(
                "eToro client created without credentials "
                "(ETORO_API_KEY / ETORO_USER_KEY not set)"
            )

    @property
    def is_real(self) -> bool:
        return self._env == "real"

    @property
    def execution_prefix(self) -> str:
        """Trading execution path: demo or real."""
        if self.is_real:
            return "trading/execution"
        return "trading/execution/demo"

    @property
    def info_prefix(self) -> str:
        """Trading info path: demo or real."""
        if self.is_real:
            return "trading/info/real"
        return "trading/info/demo"

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "x-user-key": self._user_key,
            "x-request-id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """GET request with retry on 429."""
        url = f"{BASE_URL}/{path.lstrip('/')}"
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self._http.get(url, headers=self._headers(), params=params)
                if r.status_code == 429:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning("eToro 429 rate limit, retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                logger.error("eToro GET %s failed: %s", path, exc)
                return None
        return None

    def post(self, path: str, body: Optional[Dict] = None) -> Optional[Dict]:
        """POST request with retry on 429."""
        url = f"{BASE_URL}/{path.lstrip('/')}"
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self._http.post(
                    url, headers=self._headers(), json=body or {},
                )
                if r.status_code == 429:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning("eToro 429 rate limit, retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                logger.error("eToro POST %s failed: %s", path, exc)
                return None
        return None

    def close(self) -> None:
        self._http.close()


# Module-level singleton
_client: Optional[EtoroClient] = None


def get_etoro_client() -> EtoroClient:
    """Return the global eToro client singleton."""
    global _client
    if _client is None:
        _client = EtoroClient()
    return _client
